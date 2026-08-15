from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest
from supabase import Client

import market_intelligence.persistence.supabase_classification_repository as repository_module
from market_intelligence.classification import (
    ClassificationMethod,
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
)
from market_intelligence.persistence import (
    ClassificationFailure,
    ClassificationKey,
    ClassificationLineage,
    ClassificationLineageMismatchError,
    ClassificationPersistenceError,
    CompletionOutcome,
    EnqueueOutcome,
    FailureDisposition,
    FailureOutcome,
    LeaseRenewalOutcome,
    SupabaseClassificationRepository,
    create_classification_repository_from_environment,
)
from market_intelligence.source_registry import Domain, Market

NOW = datetime(2026, 8, 16, 4, 0, tzinfo=UTC)
TOKEN = UUID("00000000-0000-0000-0000-000000000001")


def row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "article_id": "article-1",
        "classifier_version": "classification-v1",
        "status": "RETRYABLE",
        "classification_method": None,
        "is_relevant": None,
        "markets": None,
        "category": None,
        "topics": None,
        "confidence": None,
        "classified_at": None,
        "requested_model": "deepseek-v4-flash",
        "provider_model": None,
        "prompt_version": "classification-prompt-v1",
        "taxonomy_version": "classification-taxonomy-v1",
        "provider_request_id": None,
        "system_fingerprint": None,
        "prompt_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": "0",
        "last_pricing_id": None,
        "last_pricing_window": None,
        "attempt_count": 0,
        "max_attempts": 3,
        "last_provider_attempts": None,
        "claim_token": None,
        "claimed_at": None,
        "lease_expires_at": None,
        "next_attempt_at": NOW.isoformat(),
        "last_error_category": None,
        "last_http_status": None,
        "last_error_retryable": None,
        "last_error_at": None,
        "quarantined_at": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    values.update(overrides)
    return values


def rpc_repository(payload: object) -> tuple[SupabaseClassificationRepository, Mock, Mock]:
    client = Mock()
    request = Mock()
    client.rpc.return_value = request
    request.execute.return_value = SimpleNamespace(data=payload)
    return SupabaseClassificationRepository(cast(Client, client)), client, request


def key() -> ClassificationKey:
    return ClassificationKey(
        article_id="article-1",
        classifier_version="classification-v1",
    )


def lineage() -> ClassificationLineage:
    return ClassificationLineage(
        requested_model="deepseek-v4-flash",
        prompt_version="classification-prompt-v1",
        taxonomy_version="classification-taxonomy-v1",
    )


def processing_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "status": "PROCESSING",
        "attempt_count": 1,
        "claim_token": str(TOKEN),
        "claimed_at": NOW.isoformat(),
        "lease_expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "next_attempt_at": None,
    }
    values.update(overrides)
    return row(**values)


def result(**overrides: object) -> ClassificationResult:
    values: dict[str, object] = {
        "classified_article": ClassifiedArticle(
            article_id="article-1",
            classifier_version="classification-v1",
            is_relevant=True,
            markets=(Market.US,),
            category=Domain.FINANCE,
            topics=(),
            confidence=0.9,
            classified_at=NOW,
        ),
        "classification_method": ClassificationMethod.DEEPSEEK,
        "requested_model": "deepseek-v4-flash",
        "provider_model": "deepseek-provider-model",
        "prompt_version": "classification-prompt-v1",
        "taxonomy_version": "classification-taxonomy-v1",
        "usage": ClassificationUsage(
            prompt_tokens=8,
            prompt_cache_hit_tokens=3,
            prompt_cache_miss_tokens=5,
            completion_tokens=2,
            total_tokens=10,
        ),
        "estimated_cost_usd": Decimal("0.000004"),
        "pricing_id": "deepseek-v4-flash-2026-08-15",
        "pricing_window": "off_peak",
        "duration_ms": 120,
        "provider_attempts": 2,
        "provider_request_id": "request-1",
        "system_fingerprint": "fingerprint-1",
    }
    values.update(overrides)
    return ClassificationResult(**values)


def claim_from_repository() -> object:
    repository, _, _ = rpc_repository({"outcome": "CLAIMED", "record": processing_row()})
    claim = repository.claim_next("classification-v1", TOKEN)
    assert claim is not None
    return claim


def test_enqueue_uses_transactional_rpc_and_returns_created_row() -> None:
    repository, client, request = rpc_repository({"outcome": "CREATED", "record": row()})

    response = repository.enqueue(key(), lineage())

    assert response.outcome is EnqueueOutcome.CREATED
    client.rpc.assert_called_once_with(
        "enqueue_article_classification",
        {
            "p_article_id": "article-1",
            "p_classifier_version": "classification-v1",
            "p_requested_model": "deepseek-v4-flash",
            "p_prompt_version": "classification-prompt-v1",
            "p_taxonomy_version": "classification-taxonomy-v1",
            "p_max_attempts": 3,
        },
    )
    request.execute.assert_called_once_with()


def test_enqueue_surfaces_lineage_mismatch_without_overwrite() -> None:
    repository, _, _ = rpc_repository(
        {
            "outcome": "LINEAGE_MISMATCH",
            "mismatched_fields": ["prompt_version"],
            "record": row(),
        }
    )

    with pytest.raises(ClassificationLineageMismatchError) as captured:
        repository.enqueue(
            key(),
            ClassificationLineage(
                requested_model="deepseek-v4-flash",
                prompt_version="different-prompt",
                taxonomy_version="classification-taxonomy-v1",
            ),
        )

    assert captured.value.mismatched_fields == ("prompt_version",)
    assert captured.value.operation == "LINEAGE_MISMATCH"


def test_claim_next_maps_fenced_claim_and_counts_one_durable_invocation() -> None:
    repository, client, _ = rpc_repository({"outcome": "CLAIMED", "record": processing_row()})

    claim = repository.claim_next("classification-v1", TOKEN, lease_seconds=120)

    assert claim is not None
    assert claim.claim_token == TOKEN
    assert claim.attempt_count == 1
    client.rpc.assert_called_once_with(
        "claim_next_article_classification",
        {
            "p_classifier_version": "classification-v1",
            "p_claim_token": str(TOKEN),
            "p_lease_seconds": 120,
        },
    )


@pytest.mark.parametrize("outcome", ["EMPTY", "RECOVERED_QUARANTINED"])
def test_claim_next_returns_none_for_no_claim(outcome: str) -> None:
    repository, _, _ = rpc_repository({"outcome": outcome, "record": None})

    assert repository.claim_next("classification-v1", TOKEN) is None


def test_renew_lease_uses_claim_token_as_fence() -> None:
    claim = claim_from_repository()
    repository, client, _ = rpc_repository({"outcome": "RENEWED", "record": processing_row()})

    response = repository.renew_lease(claim, lease_seconds=180)

    assert response.outcome is LeaseRenewalOutcome.RENEWED
    assert client.rpc.call_args.args[1]["p_claim_token"] == str(TOKEN)


def test_complete_success_persists_de008_usage_and_provider_attempts() -> None:
    claim = claim_from_repository()
    succeeded = row(
        status="SUCCEEDED",
        classification_method="DEEPSEEK",
        is_relevant=True,
        markets=["US"],
        category="FINANCE",
        topics=[],
        confidence=0.9,
        classified_at=NOW.isoformat(),
        provider_model="deepseek-provider-model",
        provider_request_id="request-1",
        system_fingerprint="fingerprint-1",
        prompt_tokens=8,
        prompt_cache_hit_tokens=3,
        prompt_cache_miss_tokens=5,
        completion_tokens=2,
        total_tokens=10,
        estimated_cost_usd="0.000004",
        last_pricing_id="deepseek-v4-flash-2026-08-15",
        last_pricing_window="off_peak",
        attempt_count=1,
        last_provider_attempts=2,
        next_attempt_at=None,
    )
    repository, client, _ = rpc_repository({"outcome": "SUCCEEDED", "record": succeeded})

    response = repository.complete_success(claim, result())

    assert response.outcome is CompletionOutcome.SUCCEEDED
    params = client.rpc.call_args.args[1]
    assert params["p_provider_attempts"] == 2
    assert params["p_classification_method"] == "DEEPSEEK"
    assert params["p_prompt_tokens"] == 8
    assert params["p_estimated_cost_usd"] == "0.000004"


def test_complete_success_rejects_local_lineage_mismatch_without_rpc() -> None:
    claim = claim_from_repository()
    repository, client, _ = rpc_repository({})

    with pytest.raises(ClassificationLineageMismatchError):
        repository.complete_success(
            claim,
            result(requested_model="different-model"),
        )

    client.rpc.assert_not_called()


def test_record_failure_keeps_provider_attempts_independent_from_attempt_count() -> None:
    claim = claim_from_repository()
    retryable = row(
        status="RETRYABLE",
        attempt_count=1,
        last_provider_attempts=3,
        next_attempt_at=(NOW + timedelta(minutes=15)).isoformat(),
        last_error_category="timeout",
        last_error_retryable=True,
        last_error_at=NOW.isoformat(),
    )
    repository, client, _ = rpc_repository({"outcome": "RETRY_SCHEDULED", "record": retryable})
    failure = ClassificationFailure(
        error_category="timeout",
        retryable=True,
        provider_attempts=3,
        last_http_status=None,
        usage=ClassificationUsage.zero(),
        estimated_cost_usd=Decimal("0"),
        pricing_id=None,
        pricing_window=None,
    )

    response = repository.record_failure(
        claim,
        failure,
        disposition=FailureDisposition.RETRY_15_MINUTES,
    )

    assert response.outcome is FailureOutcome.RETRY_SCHEDULED
    params = client.rpc.call_args.args[1]
    assert params["p_provider_attempts"] == 3
    assert "p_attempt_count" not in params


def test_non_retryable_failure_cannot_be_scheduled() -> None:
    claim = claim_from_repository()
    repository, client, _ = rpc_repository({})
    failure = ClassificationFailure(
        error_category="content_filter",
        retryable=False,
        provider_attempts=1,
        last_http_status=None,
        usage=ClassificationUsage.zero(),
        estimated_cost_usd=Decimal("0"),
        pricing_id=None,
        pricing_window=None,
    )

    with pytest.raises(ValueError, match="must be quarantined"):
        repository.record_failure(
            claim,
            failure,
            disposition=FailureDisposition.RETRY_15_MINUTES,
        )

    client.rpc.assert_not_called()


def test_get_succeeded_returns_shared_contract_only() -> None:
    client = Mock()
    request = Mock()
    client.table.return_value = request
    request.select.return_value = request
    request.eq.return_value = request
    request.limit.return_value = request
    request.execute.return_value = SimpleNamespace(
        data=[
            row(
                status="SUCCEEDED",
                classification_method="DEEPSEEK",
                is_relevant=False,
                markets=[],
                category=None,
                topics=[],
                confidence=0.75,
                classified_at=NOW.isoformat(),
                provider_model="deepseek-provider-model",
                attempt_count=1,
                last_provider_attempts=1,
                next_attempt_at=None,
            )
        ]
    )
    repository = SupabaseClassificationRepository(cast(Client, client))

    classified = repository.get_succeeded(key())

    assert classified is not None
    assert classified.is_relevant is False
    assert not hasattr(classified, "provider_model")


def test_complete_deterministic_success_maps_zero_provider_metadata() -> None:
    claim = claim_from_repository()
    deterministic = result(
        classified_article=result().classified_article.model_copy(
            update={"classifier_version": "classification-v1"}
        ),
        classification_method=ClassificationMethod.DETERMINISTIC,
        provider_model=None,
        usage=ClassificationUsage.zero(),
        estimated_cost_usd=Decimal(0),
        pricing_id=None,
        pricing_window=None,
        provider_attempts=0,
        provider_request_id=None,
        system_fingerprint=None,
    )
    succeeded = row(
        status="SUCCEEDED",
        classification_method="DETERMINISTIC",
        is_relevant=True,
        markets=["US"],
        category="FINANCE",
        topics=[],
        confidence=0.9,
        classified_at=NOW.isoformat(),
        attempt_count=1,
        last_provider_attempts=0,
        next_attempt_at=None,
    )
    repository, client, _ = rpc_repository({"outcome": "SUCCEEDED", "record": succeeded})

    repository.complete_success(claim, deterministic)

    params = client.rpc.call_args.args[1]
    assert params["p_classification_method"] == "DETERMINISTIC"
    assert params["p_provider_attempts"] == 0
    assert params["p_prompt_tokens"] == 0
    assert params["p_estimated_cost_usd"] == "0"


def test_repository_failure_is_sanitized() -> None:
    repository, _, request = rpc_repository({})
    request.execute.side_effect = httpx.ConnectError(
        "Authorization: Bearer secret; title=private article"
    )

    with pytest.raises(ClassificationPersistenceError) as captured:
        repository.enqueue(key(), lineage())

    message = str(captured.value)
    assert "secret" not in message
    assert "private article" not in message
    assert "article-1" in message


def test_factory_requires_environment_and_passes_trimmed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        create_classification_repository_from_environment({})

    client = Mock()
    create_client = Mock(return_value=client)
    monkeypatch.setattr(repository_module, "create_client", create_client)

    repository = create_classification_repository_from_environment(
        {
            "SUPABASE_URL": " https://project.example.supabase.co ",
            "SUPABASE_SERVICE_KEY": " test-service-key ",
        }
    )

    assert isinstance(repository, SupabaseClassificationRepository)
    create_client.assert_called_once_with(
        "https://project.example.supabase.co",
        "test-service-key",
    )
