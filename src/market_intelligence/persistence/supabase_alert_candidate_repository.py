"""Supabase adapter for durable alert-candidate idempotency."""

import json
import os
from collections.abc import Mapping
from typing import Any, cast

import httpx
from postgrest.exceptions import APIError
from pydantic import ValidationError
from supabase import Client, create_client

from market_intelligence.models import AlertCandidate
from market_intelligence.persistence.alert_candidates import (
    AlertCandidatePersistenceError,
    AlertCandidateSaveOutcome,
    AlertCandidateSaveResult,
)
from market_intelligence.persistence.articles import PersistenceConfigurationError

_ALERT_CANDIDATES_TABLE = "alert_candidates"
_SUPABASE_URL = "SUPABASE_URL"
_SUPABASE_SERVICE_KEY = "SUPABASE_SERVICE_KEY"

_CANDIDATE_FIELDS = (
    "candidate_id",
    "user_id",
    "article_id",
    "matched_at",
    "match_reasons",
    "importance",
    "relevance_score",
    "breaking_eligible",
)


class SupabaseAlertCandidateRepository:
    """Persist candidates through one atomic security-definer RPC."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def save(self, candidate: AlertCandidate) -> AlertCandidateSaveResult:
        payload = self._rpc(
            "save_alert_candidate",
            {
                "p_candidate_id": candidate.candidate_id,
                "p_user_id": candidate.user_id,
                "p_article_id": candidate.article_id,
                "p_matched_at": candidate.matched_at.isoformat(),
                "p_match_reasons": list(candidate.match_reasons),
                "p_importance": candidate.importance.value,
                "p_relevance_score": candidate.relevance_score,
                "p_breaking_eligible": candidate.breaking_eligible,
            },
            candidate.candidate_id,
            "save",
        )
        raw_outcome = payload.get("outcome")
        try:
            if not isinstance(raw_outcome, str):
                raise TypeError
            outcome = AlertCandidateSaveOutcome(raw_outcome)
        except (TypeError, ValueError) as error:
            raise AlertCandidatePersistenceError(candidate.candidate_id, "save") from error

        persisted = self._candidate(payload.get("record"), candidate.candidate_id, "save")
        return AlertCandidateSaveResult(outcome=outcome, candidate=persisted)

    def get(self, candidate_id: str) -> AlertCandidate | None:
        if not candidate_id.strip():
            raise ValueError("candidate_id must not be blank")
        try:
            response = (
                self._client.table(_ALERT_CANDIDATES_TABLE)
                .select(",".join(_CANDIDATE_FIELDS))
                .eq("candidate_id", candidate_id)
                .limit(1)
                .execute()
            )
        except (APIError, httpx.HTTPError) as error:
            raise AlertCandidatePersistenceError(candidate_id, "get") from error

        data = response.data
        if not isinstance(data, list):
            raise AlertCandidatePersistenceError(candidate_id, "get")
        if not data:
            return None
        return self._candidate(data[0], candidate_id, "get")

    def _rpc(
        self,
        function_name: str,
        params: dict[str, object],
        candidate_id: str,
        operation: str,
    ) -> dict[str, Any]:
        try:
            data = self._client.rpc(function_name, params).execute().data
        except (APIError, httpx.HTTPError) as error:
            raise AlertCandidatePersistenceError(candidate_id, operation) from error
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise AlertCandidatePersistenceError(candidate_id, operation)
        return cast(dict[str, Any], data)

    @staticmethod
    def _candidate(
        payload: object,
        candidate_id: str,
        operation: str,
    ) -> AlertCandidate:
        if not isinstance(payload, dict):
            raise AlertCandidatePersistenceError(candidate_id, operation)

        shared_payload = {field: payload.get(field) for field in _CANDIDATE_FIELDS}
        try:
            return AlertCandidate.model_validate_json(json.dumps(shared_payload))
        except (TypeError, ValueError, ValidationError) as error:
            raise AlertCandidatePersistenceError(candidate_id, operation) from error


def create_alert_candidate_repository_from_environment(
    environment: Mapping[str, str] | None = None,
) -> SupabaseAlertCandidateRepository:
    values = os.environ if environment is None else environment
    url = _required_environment_value(values, _SUPABASE_URL)
    service_key = _required_environment_value(values, _SUPABASE_SERVICE_KEY)
    return SupabaseAlertCandidateRepository(create_client(url, service_key))


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value.strip():
        raise PersistenceConfigurationError(f"missing required environment variable: {name}")
    return value.strip()
