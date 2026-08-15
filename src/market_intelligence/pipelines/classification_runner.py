"""Bounded DE-009B orchestration across discovery, DE-008, and DE-009."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import (
    CLASSIFIER_VERSION,
    DEEPSEEK_MODEL,
    HYBRID_CLASSIFIER_VERSION,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
    ArticleClassifier,
    ClassificationConfigurationError,
    ClassificationError,
    ClassificationErrorCategory,
    ClassificationResult,
    ClassificationUsage,
    validate_classification_rights,
)
from market_intelligence.persistence import (
    ClassificationClaim,
    ClassificationFailure,
    ClassificationKey,
    ClassificationLineage,
    ClassificationLineageMismatchError,
    ClassificationPersistenceError,
    ClassificationRepository,
    ClassificationWorkReader,
    ClassificationWorkReadError,
    CompletionOutcome,
    DiscoveryScope,
    EnqueueOutcome,
    FailureDisposition,
    FailureOutcome,
    LeaseRenewalOutcome,
)
from market_intelligence.source_registry import RightsReviewStatus, SourceConfig

logger = logging.getLogger(__name__)

type MonotonicClock = Callable[[], float]
type ClaimTokenFactory = Callable[[], UUID]
type HeartbeatWait = Callable[[asyncio.Event, float], Awaitable[bool]]


async def _wait_for_stop(event: asyncio.Event, timeout_seconds: float) -> bool:
    try:
        await asyncio.wait_for(event.wait(), timeout_seconds)
    except TimeoutError:
        return False
    return True


def _default_lineage() -> ClassificationLineage:
    return ClassificationLineage(
        requested_model=DEEPSEEK_MODEL,
        prompt_version=PROMPT_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
    )


class ClassificationRunnerConfig(BaseModel):
    """Validated bounded runtime settings for one sequential runner."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    classifier_version: str = Field(
        default=HYBRID_CLASSIFIER_VERSION,
        pattern=r"^classification-v[1-9][0-9]*$",
        max_length=64,
    )
    lineage: ClassificationLineage = Field(default_factory=_default_lineage)
    enqueue_limit: int = Field(default=100, ge=1, le=1_000)
    process_limit: int = Field(default=20, ge=1, le=1_000)
    max_attempts: int = Field(default=3, ge=1, le=12)
    lease_seconds: int = Field(default=300, ge=30, le=900)
    heartbeat_seconds: float = Field(default=60.0, ge=10)
    max_run_seconds: float = Field(default=600.0, gt=0)

    @model_validator(mode="after")
    def validate_heartbeat(self) -> "ClassificationRunnerConfig":
        if self.heartbeat_seconds >= self.lease_seconds / 2:
            raise ValueError("heartbeat_seconds must be less than half the lease")
        return self


class BatchStopReason(StrEnum):
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"
    WORK_READ = "WORK_READ"
    PERSISTENCE = "PERSISTENCE"
    CONFIGURATION = "CONFIGURATION"
    SYSTEMIC_PROVIDER = "SYSTEMIC_PROVIDER"
    LOST_CLAIM = "LOST_CLAIM"
    MISSING_ARTICLE = "MISSING_ARTICLE"
    MISSING_SOURCE = "MISSING_SOURCE"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True, slots=True)
class EnqueueBatchResult:
    eligible_source_count: int = 0
    discovered_count: int = 0
    created_count: int = 0
    existing_count: int = 0
    terminal_count: int = 0
    stop_reason: BatchStopReason | None = None


@dataclass(frozen=True, slots=True)
class ProcessingBatchResult:
    claimed_count: int = 0
    succeeded_count: int = 0
    retry_scheduled_count: int = 0
    quarantined_count: int = 0
    lost_claim_count: int = 0
    provider_attempts: int = 0
    estimated_cost_usd: Decimal = Decimal(0)
    deadline_reached: bool = False
    stop_reason: BatchStopReason | None = None


@dataclass(frozen=True, slots=True)
class ClassificationBatchResult:
    enqueue: EnqueueBatchResult
    processing: ProcessingBatchResult
    stop_reason: BatchStopReason | None


@dataclass(frozen=True, slots=True)
class _ItemResult:
    succeeded: int = 0
    retry_scheduled: int = 0
    quarantined: int = 0
    lost_claim: int = 0
    provider_attempts: int = 0
    estimated_cost_usd: Decimal = Decimal(0)
    stop_reason: BatchStopReason | None = None


@dataclass(frozen=True, slots=True)
class _FailureDecision:
    failure: ClassificationFailure
    disposition: FailureDisposition
    stop_reason: BatchStopReason | None


class ClassificationRunner:
    """Sequential bounded runner that never holds a DB transaction over provider I/O."""

    def __init__(
        self,
        classifier: ArticleClassifier,
        classification_repository: ClassificationRepository,
        work_reader: ClassificationWorkReader,
        sources: Sequence[SourceConfig],
        *,
        config: ClassificationRunnerConfig | None = None,
        monotonic: MonotonicClock = time.monotonic,
        claim_token_factory: ClaimTokenFactory = uuid4,
        heartbeat_wait: HeartbeatWait = _wait_for_stop,
    ) -> None:
        source_by_id = {source.source_id: source for source in sources}
        if len(source_by_id) != len(sources):
            raise ValueError("sources must not contain duplicate source_id values")
        self._classifier = classifier
        self._repository = classification_repository
        self._work_reader = work_reader
        self._source_by_id = source_by_id
        self._config = config or ClassificationRunnerConfig()
        self._monotonic = monotonic
        self._claim_token_factory = claim_token_factory
        self._heartbeat_wait = heartbeat_wait

    @property
    def eligible_source_ids(self) -> tuple[str, ...]:
        if self._config.classifier_version == HYBRID_CLASSIFIER_VERSION:
            return tuple(
                sorted(
                    source_id
                    for source_id, source in self._source_by_id.items()
                    if source.rights.can_store_metadata is True
                )
            )
        return tuple(
            sorted(
                source_id
                for source_id, source in self._source_by_id.items()
                if source.rights.rights_review_status is RightsReviewStatus.APPROVED
                and source.rights.can_ai_process is True
            )
        )

    async def run_once(
        self,
        scope: DiscoveryScope | None = None,
    ) -> ClassificationBatchResult:
        """Audit lineage, enqueue bounded missing work, then process bounded claims."""
        started_at = self._monotonic()
        try:
            mismatch = await asyncio.to_thread(
                self._work_reader.find_lineage_mismatch,
                classifier_version=self._config.classifier_version,
                lineage=self._config.lineage,
            )
        except ClassificationWorkReadError:
            reason = BatchStopReason.WORK_READ
            return ClassificationBatchResult(
                enqueue=EnqueueBatchResult(stop_reason=reason),
                processing=ProcessingBatchResult(stop_reason=reason),
                stop_reason=reason,
            )
        if mismatch is not None:
            reason = BatchStopReason.LINEAGE_MISMATCH
            logger.error(
                "classification_runner_lineage_mismatch",
                extra={
                    "article_id": mismatch.article_id,
                    "classifier_version": mismatch.classifier_version,
                    "pipeline_stage": "classification_orchestration",
                    "status": "STOPPED",
                },
            )
            return ClassificationBatchResult(
                enqueue=EnqueueBatchResult(stop_reason=reason),
                processing=ProcessingBatchResult(stop_reason=reason),
                stop_reason=reason,
            )

        enqueue = await self.discover_and_enqueue(scope)
        if enqueue.stop_reason is not None:
            return ClassificationBatchResult(
                enqueue=enqueue,
                processing=ProcessingBatchResult(stop_reason=enqueue.stop_reason),
                stop_reason=enqueue.stop_reason,
            )

        processing = await self.process_claims(deadline=started_at + self._config.max_run_seconds)
        return ClassificationBatchResult(
            enqueue=enqueue,
            processing=processing,
            stop_reason=processing.stop_reason,
        )

    async def discover_and_enqueue(
        self,
        scope: DiscoveryScope | None = None,
    ) -> EnqueueBatchResult:
        """Discover version-eligible source articles and enqueue idempotently."""
        eligible_source_ids = self._eligible_source_ids_for_scope(scope)
        if not eligible_source_ids:
            return EnqueueBatchResult()

        try:
            articles = await asyncio.to_thread(
                self._work_reader.list_unclassified_articles,
                classifier_version=self._config.classifier_version,
                eligible_source_ids=eligible_source_ids,
                limit=self._config.enqueue_limit,
                scope=scope,
            )
        except ClassificationWorkReadError:
            return EnqueueBatchResult(
                eligible_source_count=len(eligible_source_ids),
                stop_reason=BatchStopReason.WORK_READ,
            )

        created = 0
        existing = 0
        terminal = 0
        for article in articles:
            key = ClassificationKey(
                article_id=article.article_id,
                classifier_version=self._config.classifier_version,
            )
            try:
                result = await asyncio.to_thread(
                    self._repository.enqueue,
                    key,
                    self._config.lineage,
                    max_attempts=self._config.max_attempts,
                )
            except ClassificationLineageMismatchError:
                return EnqueueBatchResult(
                    eligible_source_count=len(eligible_source_ids),
                    discovered_count=len(articles),
                    created_count=created,
                    existing_count=existing,
                    terminal_count=terminal,
                    stop_reason=BatchStopReason.LINEAGE_MISMATCH,
                )
            except ClassificationPersistenceError:
                return EnqueueBatchResult(
                    eligible_source_count=len(eligible_source_ids),
                    discovered_count=len(articles),
                    created_count=created,
                    existing_count=existing,
                    terminal_count=terminal,
                    stop_reason=BatchStopReason.PERSISTENCE,
                )

            if result.outcome is EnqueueOutcome.CREATED:
                created += 1
            elif result.outcome in {
                EnqueueOutcome.EXISTING_RETRYABLE,
                EnqueueOutcome.EXISTING_PROCESSING,
            }:
                existing += 1
            else:
                terminal += 1

        return EnqueueBatchResult(
            eligible_source_count=len(eligible_source_ids),
            discovered_count=len(articles),
            created_count=created,
            existing_count=existing,
            terminal_count=terminal,
        )

    async def process_claims(self, *, deadline: float | None = None) -> ProcessingBatchResult:
        """Process sequential claims until the queue, item, time, or batch bound stops work."""
        resolved_deadline = (
            self._monotonic() + self._config.max_run_seconds if deadline is None else deadline
        )
        claimed = 0
        succeeded = 0
        retry_scheduled = 0
        quarantined = 0
        lost_claim = 0
        provider_attempts = 0
        estimated_cost = Decimal(0)
        deadline_reached = False
        stop_reason: BatchStopReason | None = None

        while claimed < self._config.process_limit:
            if self._monotonic() >= resolved_deadline:
                deadline_reached = True
                break
            try:
                claim = await asyncio.to_thread(
                    self._repository.claim_next,
                    self._config.classifier_version,
                    self._claim_token_factory(),
                    lease_seconds=self._config.lease_seconds,
                )
            except ClassificationPersistenceError:
                stop_reason = BatchStopReason.PERSISTENCE
                break
            if claim is None:
                # DE-009 maps EMPTY and one bounded exhausted-row recovery to None.
                break

            claimed += 1
            item = await self._process_claim(claim)
            succeeded += item.succeeded
            retry_scheduled += item.retry_scheduled
            quarantined += item.quarantined
            lost_claim += item.lost_claim
            provider_attempts += item.provider_attempts
            estimated_cost += item.estimated_cost_usd
            if item.stop_reason is not None:
                stop_reason = item.stop_reason
                break

        result = ProcessingBatchResult(
            claimed_count=claimed,
            succeeded_count=succeeded,
            retry_scheduled_count=retry_scheduled,
            quarantined_count=quarantined,
            lost_claim_count=lost_claim,
            provider_attempts=provider_attempts,
            estimated_cost_usd=estimated_cost,
            deadline_reached=deadline_reached,
            stop_reason=stop_reason,
        )
        logger.info(
            "classification_runner_batch_completed",
            extra={
                "classifier_version": self._config.classifier_version,
                "pipeline_stage": "classification_orchestration",
                "status": "STOPPED" if stop_reason is not None else "COMPLETED",
                "claimed_count": claimed,
                "succeeded_count": succeeded,
                "retry_scheduled_count": retry_scheduled,
                "quarantined_count": quarantined,
                "lost_claim_count": lost_claim,
                "provider_attempts": provider_attempts,
                "stop_reason": stop_reason.value if stop_reason is not None else None,
            },
        )
        return result

    def _eligible_source_ids_for_scope(
        self,
        scope: DiscoveryScope | None,
    ) -> tuple[str, ...]:
        eligible = set(self.eligible_source_ids)
        if scope is not None and scope.source_ids is not None:
            eligible.intersection_update(scope.source_ids)
        return tuple(sorted(eligible))

    async def _process_claim(self, claim: ClassificationClaim) -> _ItemResult:
        if claim.lineage != self._config.lineage:
            return await self._record_runner_failure(
                claim,
                "runner_lineage_mismatch",
                BatchStopReason.LINEAGE_MISMATCH,
            )

        try:
            article = await asyncio.to_thread(
                self._work_reader.get_article,
                claim.key.article_id,
            )
        except ClassificationWorkReadError:
            return await self._record_runner_failure(
                claim,
                "article_load_error",
                BatchStopReason.WORK_READ,
            )
        if article is None:
            return await self._record_runner_failure(
                claim,
                "article_not_found",
                BatchStopReason.MISSING_ARTICLE,
            )

        source = self._source_by_id.get(article.source_id)
        if source is None:
            return await self._record_runner_failure(
                claim,
                "source_config_missing",
                BatchStopReason.MISSING_SOURCE,
            )

        if self._config.classifier_version == CLASSIFIER_VERSION:
            try:
                validate_classification_rights(article, source)
            except ClassificationError as error:
                # Historical classification-v1 remains DeepSeek-first and rights-gated.
                return await self._persist_failure(claim, self._decision_for_error(claim, error))

        try:
            renewal = await asyncio.to_thread(
                self._repository.renew_lease,
                claim,
                lease_seconds=self._config.lease_seconds,
            )
        except ClassificationPersistenceError:
            return _ItemResult(stop_reason=BatchStopReason.PERSISTENCE)
        if renewal.outcome is LeaseRenewalOutcome.LOST_CLAIM:
            return _ItemResult(lost_claim=1, stop_reason=BatchStopReason.LOST_CLAIM)

        classification, classification_error, lease_stop = await self._classify_under_lease(
            claim, article, source
        )
        if lease_stop is not None:
            return _ItemResult(lost_claim=1, stop_reason=lease_stop)
        if classification_error is not None:
            if isinstance(classification_error, ClassificationError):
                return await self._persist_failure(
                    claim,
                    self._decision_for_error(claim, classification_error),
                )
            if isinstance(classification_error, ClassificationConfigurationError):
                return await self._record_runner_failure(
                    claim,
                    "configuration",
                    BatchStopReason.CONFIGURATION,
                )
            return await self._record_runner_failure(
                claim,
                "runner_internal_error",
                BatchStopReason.INTERNAL,
            )
        if classification is None:
            raise AssertionError("classifier finished without result or error")

        try:
            completion = await asyncio.to_thread(
                self._repository.complete_success,
                claim,
                classification,
            )
        except ClassificationPersistenceError:
            return _ItemResult(
                provider_attempts=classification.provider_attempts,
                estimated_cost_usd=classification.estimated_cost_usd,
                stop_reason=BatchStopReason.PERSISTENCE,
            )
        if completion.outcome is CompletionOutcome.LOST_CLAIM:
            return _ItemResult(
                lost_claim=1,
                provider_attempts=classification.provider_attempts,
                estimated_cost_usd=classification.estimated_cost_usd,
                stop_reason=BatchStopReason.LOST_CLAIM,
            )
        return _ItemResult(
            succeeded=1,
            provider_attempts=classification.provider_attempts,
            estimated_cost_usd=classification.estimated_cost_usd,
        )

    async def _classify_under_lease(
        self,
        claim: ClassificationClaim,
        article: CanonicalArticle,
        source: SourceConfig,
    ) -> tuple[ClassificationResult | None, Exception | None, BatchStopReason | None]:
        stop_event = asyncio.Event()
        classifier_task = asyncio.create_task(self._classifier.classify(article, source))
        heartbeat_task = asyncio.create_task(self._heartbeat(claim, stop_event))
        try:
            done, _ = await asyncio.wait(
                {classifier_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                lease_stop = heartbeat_task.result()
                if lease_stop is not None:
                    classifier_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await classifier_task
                    return None, None, lease_stop

            try:
                result = await classifier_task
            except (ClassificationError, ClassificationConfigurationError) as error:
                result_error: Exception | None = error
                result = None
            except Exception as error:  # noqa: BLE001 - converted to sanitized runner failure
                result_error = error
                result = None
            else:
                result_error = None

            stop_event.set()
            lease_stop = await heartbeat_task
            if lease_stop is not None:
                return None, None, lease_stop
            return result, result_error, None
        except asyncio.CancelledError:
            stop_event.set()
            classifier_task.cancel()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await classifier_task
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            raise

    async def _heartbeat(
        self,
        claim: ClassificationClaim,
        stop_event: asyncio.Event,
    ) -> BatchStopReason | None:
        while not await self._heartbeat_wait(stop_event, self._config.heartbeat_seconds):
            try:
                renewal = await asyncio.to_thread(
                    self._repository.renew_lease,
                    claim,
                    lease_seconds=self._config.lease_seconds,
                )
            except ClassificationPersistenceError:
                return BatchStopReason.PERSISTENCE
            except Exception:  # noqa: BLE001 - no raw dependency detail escapes
                return BatchStopReason.INTERNAL
            if renewal.outcome is LeaseRenewalOutcome.LOST_CLAIM:
                return BatchStopReason.LOST_CLAIM
        return None

    def _decision_for_error(
        self,
        claim: ClassificationClaim,
        error: ClassificationError,
    ) -> _FailureDecision:
        failure = ClassificationFailure.from_error(error)
        category = error.category

        terminal_item_categories = {
            ClassificationErrorCategory.RIGHTS_DENIED,
            ClassificationErrorCategory.AI_FALLBACK_NOT_ALLOWED,
            ClassificationErrorCategory.INVALID_INPUT,
            ClassificationErrorCategory.CONTENT_FILTER,
            ClassificationErrorCategory.OUTPUT_TRUNCATED,
            ClassificationErrorCategory.UNEXPECTED_TOOL_CALL,
        }
        retryable_item_categories = {
            ClassificationErrorCategory.EMPTY_CONTENT,
            ClassificationErrorCategory.INVALID_OUTPUT,
        }
        systemic_categories = {
            ClassificationErrorCategory.CONFIGURATION,
            ClassificationErrorCategory.TIMEOUT,
            ClassificationErrorCategory.CONNECTION,
            ClassificationErrorCategory.RATE_LIMIT,
            ClassificationErrorCategory.PROVIDER_HTTP,
            ClassificationErrorCategory.MALFORMED_RESPONSE,
            ClassificationErrorCategory.INVALID_USAGE,
            ClassificationErrorCategory.INSUFFICIENT_SYSTEM_RESOURCE,
        }

        if category in terminal_item_categories:
            return _FailureDecision(
                failure=failure,
                disposition=FailureDisposition.QUARANTINE,
                stop_reason=None,
            )
        if category in retryable_item_categories:
            return _FailureDecision(
                failure=failure.model_copy(update={"retryable": True}),
                disposition=self._retry_disposition(claim),
                stop_reason=None,
            )
        if category in systemic_categories:
            # DE-008 retryable=False means no more calls in this invocation. A provider
            # credential/configuration failure remains recoverable after operator repair.
            disposition = (
                FailureDisposition.RETRY_60_MINUTES
                if category is ClassificationErrorCategory.RATE_LIMIT or not error.retryable
                else self._retry_disposition(claim)
            )
            stop_reason = (
                BatchStopReason.CONFIGURATION
                if category is ClassificationErrorCategory.CONFIGURATION
                else BatchStopReason.SYSTEMIC_PROVIDER
            )
            return _FailureDecision(
                failure=failure.model_copy(update={"retryable": True}),
                disposition=disposition,
                stop_reason=stop_reason,
            )
        if error.retryable:
            return _FailureDecision(
                failure=failure,
                disposition=self._retry_disposition(claim),
                stop_reason=BatchStopReason.SYSTEMIC_PROVIDER,
            )
        return _FailureDecision(
            failure=failure,
            disposition=FailureDisposition.QUARANTINE,
            stop_reason=None,
        )

    @staticmethod
    def _retry_disposition(claim: ClassificationClaim) -> FailureDisposition:
        if claim.attempt_count <= 1:
            return FailureDisposition.RETRY_15_MINUTES
        return FailureDisposition.RETRY_60_MINUTES

    async def _record_runner_failure(
        self,
        claim: ClassificationClaim,
        error_category: str,
        stop_reason: BatchStopReason,
    ) -> _ItemResult:
        decision = _FailureDecision(
            failure=ClassificationFailure(
                error_category=error_category,
                retryable=True,
                provider_attempts=0,
                last_http_status=None,
                usage=ClassificationUsage.zero(),
                estimated_cost_usd=Decimal(0),
                pricing_id=None,
                pricing_window=None,
            ),
            disposition=FailureDisposition.RETRY_60_MINUTES,
            stop_reason=stop_reason,
        )
        return await self._persist_failure(claim, decision)

    async def _persist_failure(
        self,
        claim: ClassificationClaim,
        decision: _FailureDecision,
    ) -> _ItemResult:
        failure = decision.failure
        try:
            result = await asyncio.to_thread(
                self._repository.record_failure,
                claim,
                failure,
                disposition=decision.disposition,
            )
        except ClassificationPersistenceError:
            return _ItemResult(
                provider_attempts=failure.provider_attempts,
                estimated_cost_usd=failure.estimated_cost_usd,
                stop_reason=BatchStopReason.PERSISTENCE,
            )
        if result.outcome is FailureOutcome.LOST_CLAIM:
            return _ItemResult(
                lost_claim=1,
                provider_attempts=failure.provider_attempts,
                estimated_cost_usd=failure.estimated_cost_usd,
                stop_reason=BatchStopReason.LOST_CLAIM,
            )
        if result.outcome is FailureOutcome.QUARANTINED:
            return _ItemResult(
                quarantined=1,
                provider_attempts=failure.provider_attempts,
                estimated_cost_usd=failure.estimated_cost_usd,
                stop_reason=decision.stop_reason,
            )
        return _ItemResult(
            retry_scheduled=1,
            provider_attempts=failure.provider_attempts,
            estimated_cost_usd=failure.estimated_cost_usd,
            stop_reason=decision.stop_reason,
        )
