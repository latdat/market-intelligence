"""Supabase adapter for bounded discovery-only aggregates."""

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

import httpx
from postgrest.exceptions import APIError
from pydantic import BaseModel, ValidationError
from supabase import Client, create_client

from market_intelligence.persistence.articles import PersistenceConfigurationError
from market_intelligence.persistence.discovery_observations import (
    DiscoveryBenchmarkEvidence,
    DiscoveryBenchmarkEvidenceRecordResult,
    DiscoveryObservation,
    DiscoveryObservationRecordResult,
    DiscoveryPersistenceError,
    DiscoveryPruneResult,
    DiscoveryRetentionCutoffs,
)

_SUPABASE_URL = "SUPABASE_URL"
_SUPABASE_SERVICE_KEY = "SUPABASE_SERVICE_KEY"
_RETENTION_KEY = "retention"


class SupabaseDiscoveryRepository:
    """Persist discovery aggregates through atomic security-definer RPCs."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def record_observation(
        self,
        observation: DiscoveryObservation,
    ) -> DiscoveryObservationRecordResult:
        key = observation.key
        sample = observation.sample
        payload = self._rpc(
            "record_discovery_observation",
            {
                "p_observation_day": key.observation_day.isoformat(),
                "p_query_id": key.query_id,
                "p_domain": key.domain.value,
                "p_observation_status": key.observation_status.value,
                "p_observed_at": observation.observed_at.isoformat(),
                "p_sample_url": None if sample is None else sample.url,
                "p_sample_hostname": None if sample is None else sample.hostname,
            },
            key.label(),
            "record_observation",
        )
        return self._model(
            DiscoveryObservationRecordResult,
            payload,
            key.label(),
            "record_observation",
        )

    def record_benchmark_evidence(
        self,
        evidence: DiscoveryBenchmarkEvidence,
    ) -> DiscoveryBenchmarkEvidenceRecordResult:
        evidence_key = f"{evidence.benchmark_run_id}/{evidence.sentinel_article_key}"
        payload = self._rpc(
            "record_discovery_benchmark_evidence",
            {
                "p_benchmark_run_id": evidence.benchmark_run_id,
                "p_sentinel_article_key": evidence.sentinel_article_key,
                "p_source_id": evidence.source_id,
                "p_query_id": evidence.query_id,
                "p_evidence_day": evidence.evidence_day.isoformat(),
                "p_published_at": _isoformat(evidence.published_at),
                "p_direct_first_seen_at": _isoformat(evidence.direct_first_seen_at),
                "p_gdelt_first_usable_seen_at": _isoformat(evidence.gdelt_first_usable_seen_at),
            },
            evidence_key,
            "record_benchmark_evidence",
        )
        return self._model(
            DiscoveryBenchmarkEvidenceRecordResult,
            payload,
            evidence_key,
            "record_benchmark_evidence",
        )

    def prune(self, cutoffs: DiscoveryRetentionCutoffs) -> DiscoveryPruneResult:
        payload = self._rpc(
            "prune_discovery_records",
            {
                "p_observation_cutoff_day": cutoffs.observation_cutoff_day.isoformat(),
                "p_sample_cutoff_day": cutoffs.sample_cutoff_day.isoformat(),
                "p_evidence_cutoff_day": cutoffs.evidence_cutoff_day.isoformat(),
            },
            _RETENTION_KEY,
            "prune",
        )
        return self._model(DiscoveryPruneResult, payload, _RETENTION_KEY, "prune")

    def _rpc(
        self,
        function_name: str,
        params: dict[str, object],
        aggregate_key: str,
        operation: str,
    ) -> dict[str, Any]:
        try:
            data = self._client.rpc(function_name, params).execute().data
        except (APIError, httpx.HTTPError) as error:
            raise DiscoveryPersistenceError(operation, aggregate_key) from error
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise DiscoveryPersistenceError(operation, aggregate_key)
        return cast(dict[str, Any], data)

    @staticmethod
    def _model[ResultT: BaseModel](
        model: type[ResultT],
        payload: dict[str, Any],
        aggregate_key: str,
        operation: str,
    ) -> ResultT:
        try:
            return model.model_validate_json(json.dumps(payload))
        except (TypeError, ValueError, ValidationError) as error:
            raise DiscoveryPersistenceError(operation, aggregate_key) from error


def _isoformat(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def create_discovery_repository_from_environment(
    environment: Mapping[str, str] | None = None,
) -> SupabaseDiscoveryRepository:
    values = os.environ if environment is None else environment
    url = _required_environment_value(values, _SUPABASE_URL)
    service_key = _required_environment_value(values, _SUPABASE_SERVICE_KEY)
    return SupabaseDiscoveryRepository(create_client(url, service_key))


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value.strip():
        raise PersistenceConfigurationError(f"missing required environment variable: {name}")
    return value.strip()
