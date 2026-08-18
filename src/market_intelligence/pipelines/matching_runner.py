"""Bounded matching orchestration across matching work, preferences, and DE-012.

This runner is independently designed orchestration over approved contracts. It does not
reuse the DE-009B claim/lease/fencing lifecycle: match_article() is a local deterministic
computation with no provider access, and DE-012 already owns durable first-write-wins
idempotency for the logical (user_id, article_id) pair.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from market_intelligence.classification import HYBRID_CLASSIFIER_VERSION
from market_intelligence.matching import NORMAL_FRESHNESS, match_article
from market_intelligence.models import UserPreference
from market_intelligence.persistence import (
    AlertCandidatePersistenceError,
    AlertCandidateRepository,
    AlertCandidateSaveOutcome,
    MatchingWorkItem,
    MatchingWorkReader,
    MatchingWorkReadError,
    UserPreferenceReader,
    UserPreferenceReadError,
)

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MatchingStage(StrEnum):
    """Runner stage that produced a stop-run failure."""

    PREFERENCE_LOAD = "PREFERENCE_LOAD"
    WORK_DISCOVERY = "WORK_DISCOVERY"
    MATCH_EVALUATION = "MATCH_EVALUATION"
    CANDIDATE_PERSISTENCE = "CANDIDATE_PERSISTENCE"


class MatchingErrorCategory(StrEnum):
    """Sanitized failure category for one stopped matching run."""

    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"


class MatchingRunError(RuntimeError):
    """Sanitized stop-run failure carrying non-secret identities only."""

    def __init__(
        self,
        stage: MatchingStage,
        category: MatchingErrorCategory,
        detail: str,
        *,
        article_id: str | None = None,
        classifier_version: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.stage = stage
        self.category = category
        self.detail = detail
        self.article_id = article_id
        self.classifier_version = classifier_version
        self.user_id = user_id
        super().__init__(f"matching run stopped at {stage.value}: {detail}")

    def log_context(self) -> dict[str, str | None]:
        return {
            "stage": self.stage.value,
            "error_category": self.category.value,
            "detail": self.detail,
            "article_id": self.article_id,
            "classifier_version": self.classifier_version,
            "user_id": self.user_id,
            "pipeline_stage": "matching_orchestration",
            "status": "STOPPED",
        }


class MatchingRunnerConfig(BaseModel):
    """Validated bounded runtime settings for one matching run."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    target_classifier_version: str = Field(
        default=HYBRID_CLASSIFIER_VERSION,
        pattern=r"^classification-v[1-9][0-9]*$",
        max_length=64,
    )
    preference_page_limit: int = Field(default=100, ge=1, le=1_000)
    work_page_limit: int = Field(default=100, ge=1, le=1_000)
    max_preferences: int = Field(default=50_000, ge=1)


@dataclass(frozen=True, slots=True)
class MatchingRunResult:
    """Immutable per-run counters for tests, debugging, and structured logs."""

    run_cutoff: datetime
    freshness_cutoff: datetime
    preferences_loaded: int = 0
    preference_pages_read: int = 0
    work_pages_read: int = 0
    work_items_seen: int = 0
    pairs_evaluated: int = 0
    no_match_count: int = 0
    candidate_created_count: int = 0
    candidate_already_exists_count: int = 0


@dataclass(slots=True)
class _RunCounters:
    preferences_loaded: int = 0
    preference_pages_read: int = 0
    work_pages_read: int = 0
    work_items_seen: int = 0
    pairs_evaluated: int = 0
    no_match_count: int = 0
    candidate_created_count: int = 0
    candidate_already_exists_count: int = 0


class MatchingRunner:
    """Evaluate every eligible work item against every reachable user preference."""

    def __init__(
        self,
        work_reader: MatchingWorkReader,
        preference_reader: UserPreferenceReader,
        candidate_repository: AlertCandidateRepository,
        *,
        config: MatchingRunnerConfig | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._work_reader = work_reader
        self._preference_reader = preference_reader
        self._candidate_repository = candidate_repository
        self._config = config or MatchingRunnerConfig()
        self._clock = clock

    @property
    def config(self) -> MatchingRunnerConfig:
        return self._config

    def run_once(self) -> MatchingRunResult:
        """Exhaust the current work-set, saving one candidate per matching pair.

        The run is not one transaction. Candidates saved before a stop-run failure stay
        durable and replay as ALREADY_EXISTS on the next run.
        """
        run_cutoff = self._read_clock(MatchingStage.WORK_DISCOVERY, "run_cutoff")
        freshness_cutoff = run_cutoff - NORMAL_FRESHNESS
        counters = _RunCounters()

        preferences = self._load_preferences(counters)
        self._process_work(
            preferences=preferences,
            run_cutoff=run_cutoff,
            freshness_cutoff=freshness_cutoff,
            counters=counters,
        )

        result = MatchingRunResult(
            run_cutoff=run_cutoff,
            freshness_cutoff=freshness_cutoff,
            preferences_loaded=counters.preferences_loaded,
            preference_pages_read=counters.preference_pages_read,
            work_pages_read=counters.work_pages_read,
            work_items_seen=counters.work_items_seen,
            pairs_evaluated=counters.pairs_evaluated,
            no_match_count=counters.no_match_count,
            candidate_created_count=counters.candidate_created_count,
            candidate_already_exists_count=counters.candidate_already_exists_count,
        )
        logger.info(
            "matching_runner_run_completed",
            extra={
                "classifier_version": self._config.target_classifier_version,
                "pipeline_stage": "matching_orchestration",
                "status": "COMPLETED",
                "preferences_loaded": result.preferences_loaded,
                "work_items_seen": result.work_items_seen,
                "pairs_evaluated": result.pairs_evaluated,
                "no_match_count": result.no_match_count,
                "candidate_created_count": result.candidate_created_count,
                "candidate_already_exists_count": result.candidate_already_exists_count,
            },
        )
        return result

    def _read_clock(self, stage: MatchingStage, name: str) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise MatchingRunError(
                stage,
                MatchingErrorCategory.CONTRACT_VIOLATION,
                f"{name} must include timezone information",
                classifier_version=self._config.target_classifier_version,
            )
        return value.astimezone(UTC)

    def _load_preferences(self, counters: _RunCounters) -> tuple[UserPreference, ...]:
        """Materialize one validated snapshot of every reachable preference.

        One complete pass keeps the work-item and preference cursors independent, so no
        work item can silently miss a preference page.
        """
        preferences: list[UserPreference] = []
        cursor: str | None = None

        while True:
            try:
                page = self._preference_reader.list_page(
                    after_user_id=cursor,
                    limit=self._config.preference_page_limit,
                )
            except UserPreferenceReadError as error:
                raise self._dependency_failure(
                    MatchingStage.PREFERENCE_LOAD,
                    "user preference read failed",
                    user_id=error.user_id,
                ) from error

            counters.preference_pages_read += 1
            if cursor is not None and page.items and page.items[0].user_id <= cursor:
                raise MatchingRunError(
                    MatchingStage.PREFERENCE_LOAD,
                    MatchingErrorCategory.CONTRACT_VIOLATION,
                    "preference page did not advance past the keyset cursor",
                    classifier_version=self._config.target_classifier_version,
                    user_id=page.items[0].user_id,
                )

            preferences.extend(page.items)
            if len(preferences) > self._config.max_preferences:
                raise MatchingRunError(
                    MatchingStage.PREFERENCE_LOAD,
                    MatchingErrorCategory.CONTRACT_VIOLATION,
                    "preference snapshot exceeded max_preferences",
                    classifier_version=self._config.target_classifier_version,
                )

            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        counters.preferences_loaded = len(preferences)
        return tuple(preferences)

    def _process_work(
        self,
        *,
        preferences: tuple[UserPreference, ...],
        run_cutoff: datetime,
        freshness_cutoff: datetime,
        counters: _RunCounters,
    ) -> None:
        cursor: str | None = None

        while True:
            try:
                page = self._work_reader.list_page(
                    classifier_version=self._config.target_classifier_version,
                    run_cutoff=run_cutoff,
                    freshness_cutoff=freshness_cutoff,
                    after_article_id=cursor,
                    limit=self._config.work_page_limit,
                )
            except MatchingWorkReadError as error:
                raise self._dependency_failure(
                    MatchingStage.WORK_DISCOVERY,
                    "matching work read failed",
                    article_id=error.article_id,
                ) from error

            counters.work_pages_read += 1
            if cursor is not None and page.items and page.items[0].article_id <= cursor:
                raise MatchingRunError(
                    MatchingStage.WORK_DISCOVERY,
                    MatchingErrorCategory.CONTRACT_VIOLATION,
                    "work page did not advance past the keyset cursor",
                    article_id=page.items[0].article_id,
                    classifier_version=self._config.target_classifier_version,
                )

            for item in page.items:
                counters.work_items_seen += 1
                self._validate_item(item, run_cutoff)
                self._match_item(item, preferences, counters)

            if page.next_cursor is None:
                break
            cursor = page.next_cursor

    def _validate_item(self, item: MatchingWorkItem, run_cutoff: datetime) -> None:
        article_id = item.article.article_id
        classifier_version = item.classification.classifier_version

        if article_id != item.classification.article_id:
            raise MatchingRunError(
                MatchingStage.WORK_DISCOVERY,
                MatchingErrorCategory.CONTRACT_VIOLATION,
                "article and classification article_id values must match",
                article_id=article_id,
                classifier_version=classifier_version,
            )
        if classifier_version != self._config.target_classifier_version:
            raise MatchingRunError(
                MatchingStage.WORK_DISCOVERY,
                MatchingErrorCategory.CONTRACT_VIOLATION,
                "work item classifier_version is outside the target lineage",
                article_id=article_id,
                classifier_version=classifier_version,
            )
        if item.classification.classified_at > run_cutoff:
            raise MatchingRunError(
                MatchingStage.WORK_DISCOVERY,
                MatchingErrorCategory.CONTRACT_VIOLATION,
                "work item was classified after the run discovery snapshot",
                article_id=article_id,
                classifier_version=classifier_version,
            )

    def _match_item(
        self,
        item: MatchingWorkItem,
        preferences: tuple[UserPreference, ...],
        counters: _RunCounters,
    ) -> None:
        article = item.article
        classification = item.classification

        for preference in preferences:
            # Per-item clock read: run_cutoff is a discovery boundary, never matched_at.
            matched_at = self._read_clock(MatchingStage.MATCH_EVALUATION, "matched_at")
            counters.pairs_evaluated += 1

            try:
                candidate = match_article(
                    article=article,
                    classification=classification,
                    preference=preference,
                    matched_at=matched_at,
                )
            except ValueError as error:
                raise MatchingRunError(
                    MatchingStage.MATCH_EVALUATION,
                    MatchingErrorCategory.CONTRACT_VIOLATION,
                    "match_article rejected an approved-contract input",
                    article_id=article.article_id,
                    classifier_version=classification.classifier_version,
                    user_id=preference.user_id,
                ) from error

            if candidate is None:
                counters.no_match_count += 1
                continue

            try:
                save_result = self._candidate_repository.save(candidate)
            except AlertCandidatePersistenceError as error:
                raise self._dependency_failure(
                    MatchingStage.CANDIDATE_PERSISTENCE,
                    "alert candidate save failed",
                    article_id=article.article_id,
                    user_id=preference.user_id,
                ) from error

            if save_result.outcome is AlertCandidateSaveOutcome.CREATED:
                counters.candidate_created_count += 1
            elif save_result.outcome is AlertCandidateSaveOutcome.ALREADY_EXISTS:
                # Normal idempotent replay of durable first-write-wins persistence.
                counters.candidate_already_exists_count += 1
            else:
                raise MatchingRunError(
                    MatchingStage.CANDIDATE_PERSISTENCE,
                    MatchingErrorCategory.CONTRACT_VIOLATION,
                    "alert candidate save returned an unsupported outcome",
                    article_id=article.article_id,
                    classifier_version=classification.classifier_version,
                    user_id=preference.user_id,
                )

    def _dependency_failure(
        self,
        stage: MatchingStage,
        detail: str,
        *,
        article_id: str | None = None,
        user_id: str | None = None,
    ) -> MatchingRunError:
        error = MatchingRunError(
            stage,
            MatchingErrorCategory.DEPENDENCY_FAILURE,
            detail,
            article_id=article_id,
            classifier_version=self._config.target_classifier_version,
            user_id=user_id,
        )
        logger.error("matching_runner_stopped", extra=error.log_context())
        return error
