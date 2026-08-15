from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import httpx
import pytest
from supabase import Client

import market_intelligence.persistence.supabase_alert_candidate_repository as repository_module
from market_intelligence.models import AlertCandidate, AlertImportance
from market_intelligence.persistence import (
    AlertCandidatePersistenceError,
    AlertCandidateSaveOutcome,
    SupabaseAlertCandidateRepository,
    create_alert_candidate_repository_from_environment,
)

NOW = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)


def candidate(**overrides: object) -> AlertCandidate:
    values: dict[str, object] = {
        "candidate_id": "candidate-stable-id",
        "user_id": "user-1",
        "article_id": "article-1",
        "matched_at": NOW,
        "match_reasons": ("market:US", "category:TECHNOLOGY"),
        "importance": AlertImportance.HIGH,
        "relevance_score": 0.95,
        "breaking_eligible": True,
    }
    values.update(overrides)
    return AlertCandidate(**values)


def row(
    persisted: AlertCandidate | None = None,
    **overrides: object,
) -> dict[str, object]:
    current = persisted or candidate()
    values = current.model_dump(mode="json")
    values["created_at"] = (NOW + timedelta(seconds=1)).isoformat()
    values.update(overrides)
    return values


def rpc_repository(
    payload: object,
) -> tuple[SupabaseAlertCandidateRepository, Mock, Mock]:
    client = Mock()
    request = Mock()
    client.rpc.return_value = request
    request.execute.return_value = SimpleNamespace(data=payload)
    return SupabaseAlertCandidateRepository(cast(Client, client)), client, request


def test_save_created_maps_complete_candidate_to_atomic_rpc() -> None:
    persisted = candidate()
    repository, client, request = rpc_repository({"outcome": "CREATED", "record": row(persisted)})

    result = repository.save(persisted)

    client.rpc.assert_called_once_with(
        "save_alert_candidate",
        {
            "p_candidate_id": persisted.candidate_id,
            "p_user_id": persisted.user_id,
            "p_article_id": persisted.article_id,
            "p_matched_at": persisted.matched_at.isoformat(),
            "p_match_reasons": list(persisted.match_reasons),
            "p_importance": persisted.importance.value,
            "p_relevance_score": persisted.relevance_score,
            "p_breaking_eligible": persisted.breaking_eligible,
        },
    )
    request.execute.assert_called_once_with()
    assert result.outcome is AlertCandidateSaveOutcome.CREATED
    assert result.candidate == persisted


def test_repeated_save_returns_existing_first_seen_snapshot() -> None:
    attempted = candidate(matched_at=NOW + timedelta(hours=1), relevance_score=0.8)
    existing = candidate()
    repository, _, _ = rpc_repository({"outcome": "ALREADY_EXISTS", "record": row(existing)})

    result = repository.save(attempted)

    assert result.outcome is AlertCandidateSaveOutcome.ALREADY_EXISTS
    assert result.candidate == existing
    assert result.candidate.matched_at == NOW
    assert result.candidate.relevance_score == 0.95


def test_get_selects_only_shared_candidate_fields() -> None:
    client = Mock()
    request = Mock()
    client.table.return_value = request
    request.select.return_value = request
    request.eq.return_value = request
    request.limit.return_value = request
    request.execute.return_value = SimpleNamespace(data=[row()])
    repository = SupabaseAlertCandidateRepository(cast(Client, client))

    result = repository.get("candidate-stable-id")

    assert result == candidate()
    client.table.assert_called_once_with("alert_candidates")
    request.select.assert_called_once_with(
        "candidate_id,user_id,article_id,matched_at,match_reasons,"
        "importance,relevance_score,breaking_eligible"
    )
    request.eq.assert_called_once_with("candidate_id", "candidate-stable-id")
    request.limit.assert_called_once_with(1)


def test_get_returns_none_when_candidate_is_absent() -> None:
    client = Mock()
    request = Mock()
    client.table.return_value = request
    request.select.return_value = request
    request.eq.return_value = request
    request.limit.return_value = request
    request.execute.return_value = SimpleNamespace(data=[])
    repository = SupabaseAlertCandidateRepository(cast(Client, client))

    assert repository.get("missing-candidate") is None


def test_get_rejects_blank_candidate_id_before_network_access() -> None:
    client = Mock()
    repository = SupabaseAlertCandidateRepository(cast(Client, client))

    with pytest.raises(ValueError, match="candidate_id"):
        repository.get("   ")

    client.table.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"outcome": "UNKNOWN", "record": row()},
        {"outcome": "CREATED", "record": None},
        {"outcome": "CREATED", "record": {"candidate_id": "broken"}},
    ],
)
def test_save_rejects_malformed_rpc_payload(payload: object) -> None:
    repository, _, _ = rpc_repository(payload)

    with pytest.raises(AlertCandidatePersistenceError):
        repository.save(candidate())


def test_save_transport_failure_is_sanitized() -> None:
    repository, _, request = rpc_repository({})
    request.execute.side_effect = httpx.ConnectError(
        "Authorization: Bearer secret; match_reasons=private"
    )

    with pytest.raises(AlertCandidatePersistenceError) as captured:
        repository.save(candidate())

    message = str(captured.value)
    assert "secret" not in message
    assert "private" not in message
    assert "candidate-stable-id" in message
    assert captured.value.operation == "save"


def test_get_transport_failure_is_sanitized() -> None:
    client = Mock()
    request = Mock()
    client.table.return_value = request
    request.select.return_value = request
    request.eq.return_value = request
    request.limit.return_value = request
    request.execute.side_effect = httpx.ConnectError("Authorization: Bearer secret")
    repository = SupabaseAlertCandidateRepository(cast(Client, client))

    with pytest.raises(AlertCandidatePersistenceError) as captured:
        repository.get("candidate-stable-id")

    assert "secret" not in str(captured.value)
    assert captured.value.operation == "get"


@pytest.mark.parametrize("missing_name", ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"])
def test_factory_requires_environment_values(missing_name: str) -> None:
    environment = {
        "SUPABASE_URL": "https://project.example.supabase.co",
        "SUPABASE_SERVICE_KEY": "test-service-key",
    }
    del environment[missing_name]

    with pytest.raises(ValueError, match=missing_name):
        create_alert_candidate_repository_from_environment(environment)


def test_factory_passes_trimmed_values_to_supabase_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    create_client = Mock(return_value=client)
    monkeypatch.setattr(repository_module, "create_client", create_client)

    repository = create_alert_candidate_repository_from_environment(
        {
            "SUPABASE_URL": " https://project.example.supabase.co ",
            "SUPABASE_SERVICE_KEY": " test-service-key ",
        }
    )

    assert isinstance(repository, SupabaseAlertCandidateRepository)
    create_client.assert_called_once_with(
        "https://project.example.supabase.co",
        "test-service-key",
    )
