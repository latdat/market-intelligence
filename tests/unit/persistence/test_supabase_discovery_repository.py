from datetime import UTC, date, datetime
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
from supabase import Client

from market_intelligence.discovery import ObservationStatus
from market_intelligence.persistence import (
    DiscoveryBenchmarkEvidence,
    DiscoveryObservation,
    DiscoveryObservationKey,
    DiscoveryObservationSample,
    DiscoveryPersistenceError,
    DiscoveryRecordOutcome,
    DiscoveryRetentionCutoffs,
    SupabaseDiscoveryRepository,
)
from market_intelligence.persistence.articles import PersistenceConfigurationError
from market_intelligence.persistence.supabase_discovery_repository import (
    create_discovery_repository_from_environment,
)
from market_intelligence.source_registry import Domain

OBSERVED_AT = datetime(2026, 8, 18, 12, 30, tzinfo=UTC)
SAMPLE_URL = "https://editorial.example.test/news/business/story-1"


def repository_with_mock_client(data: object) -> tuple[SupabaseDiscoveryRepository, Mock, Mock]:
    client = Mock()
    request = Mock()
    client.rpc.return_value = request
    request.execute.return_value = Mock(data=data)
    return SupabaseDiscoveryRepository(cast(Client, client)), client, request


def build_key(
    *,
    domain: Domain = Domain.FINANCE,
    status: ObservationStatus = ObservationStatus.ADMITTED,
) -> DiscoveryObservationKey:
    return DiscoveryObservationKey(
        observation_day=date(2026, 8, 18),
        query_id="us_finance",
        domain=domain,
        observation_status=status,
    )


def build_evidence(
    *,
    gdelt_first_usable_seen_at: datetime | None = OBSERVED_AT,
) -> DiscoveryBenchmarkEvidence:
    """Build durable evidence.

    ``gdelt_first_usable_seen_at`` may only ever carry the timestamp of a sighting that was
    already ADMITTED; an unusable sighting leaves it None.
    """
    return DiscoveryBenchmarkEvidence(
        benchmark_run_id="run-1",
        sentinel_article_key="sentinel-1",
        source_id="xx_editorial_fixture_source",
        query_id="us_finance",
        evidence_day=date(2026, 8, 18),
        published_at=OBSERVED_AT,
        gdelt_first_usable_seen_at=gdelt_first_usable_seen_at,
    )


def test_record_observation_sends_the_full_aggregate_key_and_sample() -> None:
    repository, client, _ = repository_with_mock_client(
        {
            "outcome": "CREATED",
            "observation_count": 1,
            "first_seen_at": OBSERVED_AT.isoformat(),
            "last_seen_at": OBSERVED_AT.isoformat(),
            "sample_count": 1,
        }
    )

    result = repository.record_observation(
        DiscoveryObservation(
            key=build_key(),
            observed_at=OBSERVED_AT,
            sample=DiscoveryObservationSample(
                url=SAMPLE_URL,
                hostname="editorial.example.test",
            ),
        )
    )

    function_name, params = client.rpc.call_args.args
    assert function_name == "record_discovery_observation"
    assert params == {
        "p_observation_day": "2026-08-18",
        "p_query_id": "us_finance",
        "p_domain": "FINANCE",
        "p_observation_status": "ADMITTED",
        "p_observed_at": OBSERVED_AT.isoformat(),
        "p_sample_url": SAMPLE_URL,
        "p_sample_hostname": "editorial.example.test",
    }
    assert result.outcome is DiscoveryRecordOutcome.CREATED
    assert result.observation_count == 1
    assert result.first_seen_at == OBSERVED_AT
    assert result.sample_count == 1


def test_record_observation_without_a_sample_sends_no_locator() -> None:
    repository, client, _ = repository_with_mock_client(
        {
            "outcome": "UPDATED",
            "observation_count": 4,
            "first_seen_at": OBSERVED_AT.isoformat(),
            "last_seen_at": OBSERVED_AT.isoformat(),
            "sample_count": 3,
        }
    )

    result = repository.record_observation(
        DiscoveryObservation(key=build_key(), observed_at=OBSERVED_AT)
    )

    _, params = client.rpc.call_args.args
    assert params["p_sample_url"] is None
    assert params["p_sample_hostname"] is None
    assert result.outcome is DiscoveryRecordOutcome.UPDATED
    assert result.sample_count == 3


def test_record_observation_accepts_a_single_row_list_payload() -> None:
    repository, _, _ = repository_with_mock_client(
        [
            {
                "outcome": "CREATED",
                "observation_count": 1,
                "first_seen_at": OBSERVED_AT.isoformat(),
                "last_seen_at": OBSERVED_AT.isoformat(),
                "sample_count": 0,
            }
        ]
    )

    result = repository.record_observation(
        DiscoveryObservation(key=build_key(), observed_at=OBSERVED_AT)
    )

    assert result.observation_count == 1


def test_record_benchmark_evidence_sends_timestamps() -> None:
    repository, client, _ = repository_with_mock_client(
        {
            "outcome": "UPDATED",
            "published_at": OBSERVED_AT.isoformat(),
            "direct_first_seen_at": None,
            "gdelt_first_usable_seen_at": OBSERVED_AT.isoformat(),
        }
    )

    result = repository.record_benchmark_evidence(build_evidence())

    function_name, params = client.rpc.call_args.args
    assert function_name == "record_discovery_benchmark_evidence"
    assert params["p_benchmark_run_id"] == "run-1"
    assert params["p_evidence_day"] == "2026-08-18"
    assert params["p_direct_first_seen_at"] is None
    assert params["p_gdelt_first_usable_seen_at"] == OBSERVED_AT.isoformat()
    assert result.outcome is DiscoveryRecordOutcome.UPDATED
    assert result.gdelt_first_usable_seen_at == OBSERVED_AT


def test_evidence_from_an_unusable_sighting_carries_no_usable_timestamp() -> None:
    """A sighting that was not ADMITTED must reach the RPC with a null usable timestamp."""
    repository, client, _ = repository_with_mock_client(
        {
            "outcome": "CREATED",
            "published_at": OBSERVED_AT.isoformat(),
            "direct_first_seen_at": None,
            "gdelt_first_usable_seen_at": None,
        }
    )

    result = repository.record_benchmark_evidence(build_evidence(gdelt_first_usable_seen_at=None))

    _, params = client.rpc.call_args.args
    assert params["p_gdelt_first_usable_seen_at"] is None
    assert result.outcome is DiscoveryRecordOutcome.CREATED
    assert result.gdelt_first_usable_seen_at is None


def test_prune_sends_every_retention_cutoff() -> None:
    repository, client, _ = repository_with_mock_client(
        {
            "deleted_observations": 2,
            "cleared_samples": 5,
            "deleted_benchmark_evidence": 1,
        }
    )

    result = repository.prune(DiscoveryRetentionCutoffs.for_date(date(2026, 8, 18)))

    function_name, params = client.rpc.call_args.args
    assert function_name == "prune_discovery_records"
    assert params == {
        "p_observation_cutoff_day": "2026-07-19",
        "p_sample_cutoff_day": "2026-08-11",
        "p_evidence_cutoff_day": "2026-05-20",
    }
    assert result.deleted_observations == 2
    assert result.cleared_samples == 5
    assert result.deleted_benchmark_evidence == 1


def test_transport_failure_is_sanitized_to_the_aggregate_key() -> None:
    repository, _, request = repository_with_mock_client(None)
    request.execute.side_effect = httpx.HTTPError("boom")

    observation = DiscoveryObservation(
        key=build_key(),
        observed_at=OBSERVED_AT,
        sample=DiscoveryObservationSample(
            url=SAMPLE_URL,
            hostname="editorial.example.test",
        ),
    )

    with pytest.raises(DiscoveryPersistenceError) as error:
        repository.record_observation(observation)

    message = str(error.value)
    assert error.value.operation == "record_observation"
    assert error.value.aggregate_key == "2026-08-18/us_finance/FINANCE/ADMITTED"
    assert SAMPLE_URL not in message
    assert "editorial.example.test" not in message


@pytest.mark.parametrize(
    "payload",
    [None, "unexpected", {"outcome": "SOMETHING_ELSE"}, {"observation_count": 1}],
)
def test_unusable_payloads_raise_sanitized_persistence_errors(payload: Any) -> None:
    repository, _, _ = repository_with_mock_client(payload)

    with pytest.raises(DiscoveryPersistenceError):
        repository.record_observation(
            DiscoveryObservation(key=build_key(), observed_at=OBSERVED_AT)
        )


def test_retention_cutoffs_must_not_expire_samples_after_their_aggregates() -> None:
    with pytest.raises(ValueError, match="sample_cutoff_day"):
        DiscoveryRetentionCutoffs(
            observation_cutoff_day=date(2026, 8, 11),
            sample_cutoff_day=date(2026, 7, 19),
            evidence_cutoff_day=date(2026, 5, 20),
        )


def test_repository_factory_requires_supabase_environment() -> None:
    with pytest.raises(PersistenceConfigurationError, match="SUPABASE_URL"):
        create_discovery_repository_from_environment({})
