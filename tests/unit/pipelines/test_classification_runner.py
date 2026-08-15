"""Offline deterministic tests for DE-009B classification orchestration."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import (
    ArticleClassifier,
    ClassificationConfigurationError,
    ClassificationError,
    ClassificationErrorCategory,
    ClassificationMethod,
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
    DeterministicClassifier,
    HybridArticleClassifier,
    load_deterministic_rules,
)
from market_intelligence.persistence import (
    ClassificationClaim,
    ClassificationFailure,
    ClassificationKey,
    ClassificationLineage,
    ClassificationRepository,
    ClassificationWorkReader,
    CompletionOutcome,
    EnqueueOutcome,
    FailureDisposition,
    FailureOutcome,
    LeaseRenewalOutcome,
)
from market_intelligence.pipelines import (
    BatchStopReason,
    ClassificationRunner,
    ClassificationRunnerConfig,
)
from market_intelligence.source_registry import Domain, Market, SourceConfig

NOW = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
TOKEN = UUID("00000000-0000-0000-0000-000000000001")


def source(*, approved: bool = True) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "us_test_source",
            "name": "US Test Source",
            "market": "US",
            "language": "en",
            "source_type": "REGULATOR",
            "authority_level": "PRIMARY",
            "domains": ["FINANCE"],
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://source.example/feed.xml",
                "poll_interval_minutes": 15,
                "rate_limit": None,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": approved,
                "can_show_snippet": True,
                "can_redistribute_full_text": False,
                "rights_review_status": "APPROVED" if approved else "PENDING",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def article() -> CanonicalArticle:
    return CanonicalArticle(
        article_id="article-1",
        source_id="us_test_source",
        source_item_id="item-1",
        url="https://source.example/article-1?secret=no-log",
        canonical_url="https://source.example/article-1",
        title="Sensitive article title",
        description="Sensitive article description",
        language="en",
        market=Market.US,
        published_at=NOW - timedelta(minutes=5),
        discovered_at=NOW,
        content_hash="hash-1",
    )


def lineage() -> ClassificationLineage:
    return ClassificationLineage(
        requested_model="deepseek-v4-flash",
        prompt_version="classification-prompt-v1",
        taxonomy_version="classification-taxonomy-v1",
    )


def claim(
    *,
    attempt_count: int = 1,
    classifier_version: str = "classification-v1",
) -> ClassificationClaim:
    return ClassificationClaim(
        key=ClassificationKey(
            article_id="article-1",
            classifier_version=classifier_version,
        ),
        lineage=lineage(),
        claim_token=TOKEN,
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
        attempt_count=attempt_count,
        max_attempts=3,
    )


def classification_result() -> ClassificationResult:
    return ClassificationResult(
        classified_article=ClassifiedArticle(
            article_id="article-1",
            classifier_version="classification-v1",
            is_relevant=True,
            markets=(Market.US,),
            category=Domain.FINANCE,
            topics=(),
            confidence=0.9,
            classified_at=NOW,
        ),
        classification_method=ClassificationMethod.DEEPSEEK,
        requested_model="deepseek-v4-flash",
        provider_model="provider-model",
        prompt_version="classification-prompt-v1",
        taxonomy_version="classification-taxonomy-v1",
        usage=ClassificationUsage(
            prompt_tokens=8,
            prompt_cache_hit_tokens=3,
            prompt_cache_miss_tokens=5,
            completion_tokens=2,
            total_tokens=10,
        ),
        estimated_cost_usd=Decimal("0.000004"),
        pricing_id="pricing-v1",
        pricing_window="off_peak",
        duration_ms=100,
        provider_attempts=2,
        provider_request_id="request-1",
        system_fingerprint="fingerprint-1",
    )


class FakeClassifier:
    def __init__(self, result_or_error: ClassificationResult | Exception) -> None:
        self.result_or_error = result_or_error
        self.calls: list[tuple[CanonicalArticle, SourceConfig]] = []

    async def classify(
        self,
        candidate: CanonicalArticle,
        candidate_source: SourceConfig,
    ) -> ClassificationResult:
        self.calls.append((candidate, candidate_source))
        if isinstance(self.result_or_error, Exception):
            raise self.result_or_error
        return self.result_or_error


class BlockingClassifier:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def classify(
        self,
        candidate: CanonicalArticle,
        candidate_source: SourceConfig,
    ) -> ClassificationResult:
        del candidate, candidate_source
        self.calls += 1
        never = asyncio.Event()
        try:
            await never.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def dependencies(
    classifier: ArticleClassifier,
    *,
    configured_source: SourceConfig | None = None,
    config: ClassificationRunnerConfig | None = None,
    heartbeat_wait: object | None = None,
) -> tuple[ClassificationRunner, Mock, Mock]:
    repository = Mock()
    reader = Mock()
    reader.find_lineage_mismatch.return_value = None
    reader.list_unclassified_articles.return_value = (article(),)
    reader.get_article.return_value = article()
    repository.enqueue.return_value = SimpleNamespace(outcome=EnqueueOutcome.CREATED)
    repository.claim_next.side_effect = [claim(), None]
    repository.renew_lease.return_value = SimpleNamespace(outcome=LeaseRenewalOutcome.RENEWED)
    repository.complete_success.return_value = SimpleNamespace(outcome=CompletionOutcome.SUCCEEDED)
    repository.record_failure.return_value = SimpleNamespace(outcome=FailureOutcome.RETRY_SCHEDULED)
    kwargs: dict[str, object] = {}
    if heartbeat_wait is not None:
        kwargs["heartbeat_wait"] = heartbeat_wait
    runner = ClassificationRunner(
        classifier,
        cast(ClassificationRepository, repository),
        cast(ClassificationWorkReader, reader),
        [configured_source or source()],
        config=config or ClassificationRunnerConfig(classifier_version="classification-v1"),
        claim_token_factory=lambda: TOKEN,
        **kwargs,
    )
    return runner, repository, reader


def test_happy_path_enqueues_claims_classifies_and_completes() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, reader = dependencies(cast(ArticleClassifier, fake))

    result = asyncio.run(runner.run_once())

    assert result.stop_reason is None
    assert result.enqueue.discovered_count == 1
    assert result.enqueue.created_count == 1
    assert result.processing.claimed_count == 1
    assert result.processing.succeeded_count == 1
    assert result.processing.provider_attempts == 2
    assert result.processing.estimated_cost_usd == Decimal("0.000004")
    assert len(fake.calls) == 1
    reader.get_article.assert_called_once_with("article-1")
    repository.renew_lease.assert_called_once()
    repository.complete_success.assert_called_once()
    repository.record_failure.assert_not_called()


def test_empty_eligible_sources_skip_discovery_query_and_enqueue() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, reader = dependencies(
        cast(ArticleClassifier, fake),
        configured_source=source(approved=False),
    )

    result = asyncio.run(runner.discover_and_enqueue())

    assert result.eligible_source_count == 0
    assert result.discovered_count == 0
    reader.list_unclassified_articles.assert_not_called()
    repository.enqueue.assert_not_called()


def test_lineage_preflight_stops_before_discovery_claim_or_provider() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, reader = dependencies(cast(ArticleClassifier, fake))
    reader.find_lineage_mismatch.return_value = ClassificationKey(
        article_id="article-9",
        classifier_version="classification-v1",
    )

    result = asyncio.run(runner.run_once())

    assert result.stop_reason is BatchStopReason.LINEAGE_MISMATCH
    reader.list_unclassified_articles.assert_not_called()
    repository.enqueue.assert_not_called()
    repository.claim_next.assert_not_called()
    assert fake.calls == []


def test_rights_revoked_after_enqueue_quarantines_without_provider_call() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, _ = dependencies(
        cast(ArticleClassifier, fake),
        configured_source=source(approved=False),
    )
    repository.record_failure.return_value = SimpleNamespace(outcome=FailureOutcome.QUARANTINED)

    result = asyncio.run(runner.process_claims())

    assert result.quarantined_count == 1
    assert result.stop_reason is None
    assert fake.calls == []
    repository.renew_lease.assert_not_called()
    failure = cast(ClassificationFailure, repository.record_failure.call_args.args[1])
    assert failure.error_category == "rights_denied"
    assert failure.retryable is False
    assert repository.record_failure.call_args.kwargs["disposition"] is (
        FailureDisposition.QUARANTINE
    )


def classifier_error(
    category: ClassificationErrorCategory,
    *,
    retryable: bool,
    status: int | None = None,
) -> ClassificationError:
    return ClassificationError(
        "article-1",
        category=category,
        retryable=retryable,
        provider_attempts=3,
        last_http_status=status,
        usage=ClassificationUsage(
            prompt_tokens=8,
            prompt_cache_hit_tokens=3,
            prompt_cache_miss_tokens=5,
            completion_tokens=2,
            total_tokens=10,
        ),
        estimated_cost_usd=Decimal("0.000004"),
        pricing_id="pricing-v1",
    )


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
def test_nonretryable_provider_configuration_http_is_rescheduled_and_stops(
    status: int,
) -> None:
    fake = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.PROVIDER_HTTP,
            retryable=False,
            status=status,
        )
    )
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))

    result = asyncio.run(runner.process_claims())

    assert result.retry_scheduled_count == 1
    assert result.stop_reason is BatchStopReason.SYSTEMIC_PROVIDER
    assert result.provider_attempts == 3
    assert result.estimated_cost_usd == Decimal("0.000004")
    failure = cast(ClassificationFailure, repository.record_failure.call_args.args[1])
    assert failure.retryable is True
    assert failure.last_http_status == status
    assert repository.record_failure.call_args.kwargs["disposition"] is (
        FailureDisposition.RETRY_60_MINUTES
    )


def test_item_invalid_output_schedules_15_minutes_and_continues() -> None:
    fake = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.INVALID_OUTPUT,
            retryable=True,
        )
    )
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))

    result = asyncio.run(runner.process_claims())

    assert result.retry_scheduled_count == 1
    assert result.stop_reason is None
    assert repository.record_failure.call_args.kwargs["disposition"] is (
        FailureDisposition.RETRY_15_MINUTES
    )


def test_second_durable_invalid_output_schedules_60_minutes() -> None:
    fake = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.INVALID_OUTPUT,
            retryable=True,
        )
    )
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))
    repository.claim_next.side_effect = [claim(attempt_count=2), None]

    result = asyncio.run(runner.process_claims())

    assert result.retry_scheduled_count == 1
    assert repository.record_failure.call_args.kwargs["disposition"] is (
        FailureDisposition.RETRY_60_MINUTES
    )


def test_rate_limit_schedules_60_minutes_and_stops_batch() -> None:
    fake = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.RATE_LIMIT,
            retryable=True,
            status=429,
        )
    )
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))

    result = asyncio.run(runner.process_claims())

    assert result.stop_reason is BatchStopReason.SYSTEMIC_PROVIDER
    assert repository.record_failure.call_args.kwargs["disposition"] is (
        FailureDisposition.RETRY_60_MINUTES
    )


def test_configuration_failure_after_claim_is_recoverable_but_stops_batch() -> None:
    fake = FakeClassifier(ClassificationConfigurationError("missing configuration"))
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))

    result = asyncio.run(runner.process_claims())

    assert result.stop_reason is BatchStopReason.CONFIGURATION
    assert result.retry_scheduled_count == 1
    failure = cast(ClassificationFailure, repository.record_failure.call_args.args[1])
    assert failure.error_category == "configuration"
    assert failure.provider_attempts == 0
    assert repository.record_failure.call_args.kwargs["disposition"] is (
        FailureDisposition.RETRY_60_MINUTES
    )


def test_immediate_lease_loss_prevents_provider_call() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))
    repository.renew_lease.return_value = SimpleNamespace(outcome=LeaseRenewalOutcome.LOST_CLAIM)

    result = asyncio.run(runner.process_claims())

    assert result.lost_claim_count == 1
    assert result.stop_reason is BatchStopReason.LOST_CLAIM
    assert fake.calls == []
    repository.complete_success.assert_not_called()
    repository.record_failure.assert_not_called()


def test_completion_lost_claim_discards_result_and_stops() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))
    repository.complete_success.return_value = SimpleNamespace(outcome=CompletionOutcome.LOST_CLAIM)

    result = asyncio.run(runner.process_claims())

    assert result.lost_claim_count == 1
    assert result.stop_reason is BatchStopReason.LOST_CLAIM
    assert result.provider_attempts == 2


def test_already_succeeded_completion_replay_is_idempotent_success() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))
    repository.complete_success.return_value = SimpleNamespace(
        outcome=CompletionOutcome.ALREADY_SUCCEEDED
    )

    result = asyncio.run(runner.process_claims())

    assert result.succeeded_count == 1
    assert result.stop_reason is None


def test_process_limit_keeps_v1_sequential_and_bounded() -> None:
    fake = FakeClassifier(classification_result())
    config = ClassificationRunnerConfig(classifier_version="classification-v1", process_limit=1)
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake), config=config)
    repository.claim_next.side_effect = [claim(), claim()]

    result = asyncio.run(runner.process_claims())

    assert result.claimed_count == 1
    assert len(fake.calls) == 1
    assert repository.claim_next.call_count == 1


class TriggerOneHeartbeat:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, stop_event: asyncio.Event, timeout_seconds: float) -> bool:
        del timeout_seconds
        self.calls += 1
        if self.calls == 1:
            return False
        await stop_event.wait()
        return True


def test_heartbeat_lost_claim_cancels_inflight_classifier_and_never_persists() -> None:
    fake = BlockingClassifier()
    heartbeat_wait = TriggerOneHeartbeat()
    runner, repository, _ = dependencies(
        cast(ArticleClassifier, fake),
        heartbeat_wait=heartbeat_wait,
    )
    repository.renew_lease.side_effect = [
        SimpleNamespace(outcome=LeaseRenewalOutcome.RENEWED),
        SimpleNamespace(outcome=LeaseRenewalOutcome.LOST_CLAIM),
    ]

    result = asyncio.run(runner.process_claims())

    assert fake.calls == 1
    assert fake.cancelled is True
    assert result.lost_claim_count == 1
    assert result.stop_reason is BatchStopReason.LOST_CLAIM
    assert repository.renew_lease.call_count == 2
    repository.complete_success.assert_not_called()
    repository.record_failure.assert_not_called()


def test_missing_article_is_scheduled_recoverably_and_stops() -> None:
    fake = FakeClassifier(classification_result())
    runner, repository, reader = dependencies(cast(ArticleClassifier, fake))
    reader.get_article.return_value = None

    result = asyncio.run(runner.process_claims())

    assert result.stop_reason is BatchStopReason.MISSING_ARTICLE
    assert result.retry_scheduled_count == 1
    assert fake.calls == []
    failure = cast(ClassificationFailure, repository.record_failure.call_args.args[1])
    assert failure.error_category == "article_not_found"
    assert failure.provider_attempts == 0


def test_failure_record_lost_claim_stops_without_second_write() -> None:
    fake = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.INVALID_OUTPUT,
            retryable=True,
        )
    )
    runner, repository, _ = dependencies(cast(ArticleClassifier, fake))
    repository.record_failure.return_value = SimpleNamespace(outcome=FailureOutcome.LOST_CLAIM)

    result = asyncio.run(runner.process_claims())

    assert result.lost_claim_count == 1
    assert result.stop_reason is BatchStopReason.LOST_CLAIM
    assert repository.record_failure.call_count == 1


def test_runner_logs_never_include_article_content_or_url(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.RATE_LIMIT,
            retryable=True,
            status=429,
        )
    )
    runner, _, _ = dependencies(cast(ArticleClassifier, fake))

    with caplog.at_level("INFO"):
        asyncio.run(runner.process_claims())

    rendered = caplog.text
    assert "Sensitive article title" not in rendered
    assert "Sensitive article description" not in rendered
    assert "secret=no-log" not in rendered


def test_v2_discovery_requires_metadata_rights_not_ai_rights() -> None:
    fake = FakeClassifier(classification_result())
    config = ClassificationRunnerConfig(classifier_version="classification-v2")
    runner, repository, reader = dependencies(
        cast(ArticleClassifier, fake),
        configured_source=source(approved=False),
        config=config,
    )

    result = asyncio.run(runner.discover_and_enqueue())

    assert result.eligible_source_count == 1
    assert result.discovered_count == 1
    reader.list_unclassified_articles.assert_called_once()
    repository.enqueue.assert_called_once()


def test_v2_runner_persists_deterministic_success_without_provider_call() -> None:
    fallback = FakeClassifier(classification_result())
    hybrid = HybridArticleClassifier(
        DeterministicClassifier(
            load_deterministic_rules(Path("config/classification/deterministic_rules.toml"))
        ),
        cast(ArticleClassifier, fallback),
        clock=lambda: NOW,
        monotonic=lambda: 1.0,
    )
    config = ClassificationRunnerConfig(classifier_version="classification-v2")
    runner, repository, reader = dependencies(
        hybrid,
        configured_source=source(approved=False),
        config=config,
    )
    confident = article().model_copy(update={"title": "Federal Reserve interest rate decision"})
    reader.get_article.return_value = confident
    repository.claim_next.side_effect = [
        claim(classifier_version="classification-v2"),
        None,
    ]

    result = asyncio.run(runner.process_claims())

    assert result.succeeded_count == 1
    assert result.provider_attempts == 0
    assert fallback.calls == []
    persisted = cast(ClassificationResult, repository.complete_success.call_args.args[1])
    assert persisted.classification_method is ClassificationMethod.DETERMINISTIC


def test_v2_ambiguous_without_ai_rights_is_quarantined_without_provider_call() -> None:
    fallback = FakeClassifier(classification_result())
    hybrid = HybridArticleClassifier(
        DeterministicClassifier(
            load_deterministic_rules(Path("config/classification/deterministic_rules.toml"))
        ),
        cast(ArticleClassifier, fallback),
    )
    config = ClassificationRunnerConfig(classifier_version="classification-v2")
    runner, repository, _ = dependencies(
        hybrid,
        configured_source=source(approved=False),
        config=config,
    )
    repository.claim_next.side_effect = [
        claim(classifier_version="classification-v2"),
        None,
    ]
    repository.record_failure.return_value = SimpleNamespace(outcome=FailureOutcome.QUARANTINED)

    result = asyncio.run(runner.process_claims())

    assert result.quarantined_count == 1
    assert result.stop_reason is None
    assert result.provider_attempts == 0
    assert fallback.calls == []
    failure = cast(ClassificationFailure, repository.record_failure.call_args.args[1])
    assert failure.error_category == "AI_FALLBACK_NOT_ALLOWED"


def test_v2_systemic_deepseek_fallback_failure_preserves_stop_batch() -> None:
    fallback = FakeClassifier(
        classifier_error(
            ClassificationErrorCategory.RATE_LIMIT,
            retryable=True,
            status=429,
        )
    )
    hybrid = HybridArticleClassifier(
        DeterministicClassifier(
            load_deterministic_rules(Path("config/classification/deterministic_rules.toml"))
        ),
        cast(ArticleClassifier, fallback),
    )
    config = ClassificationRunnerConfig(classifier_version="classification-v2")
    runner, repository, _ = dependencies(
        hybrid,
        configured_source=source(approved=True),
        config=config,
    )
    repository.claim_next.side_effect = [
        claim(classifier_version="classification-v2"),
        None,
    ]

    result = asyncio.run(runner.process_claims())

    assert len(fallback.calls) == 1
    assert result.stop_reason is BatchStopReason.SYSTEMIC_PROVIDER
    assert result.retry_scheduled_count == 1
