"""Supabase adapter for the durable classification lifecycle RPCs."""

import json
import os
import re
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

import httpx
from postgrest.exceptions import APIError
from pydantic import ValidationError
from supabase import Client, create_client

from market_intelligence.classification import (
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
)
from market_intelligence.persistence.articles import PersistenceConfigurationError
from market_intelligence.persistence.classifications import (
    ClassificationClaim,
    ClassificationFailure,
    ClassificationKey,
    ClassificationLineage,
    ClassificationLineageMismatchError,
    ClassificationPersistenceError,
    ClassificationRecord,
    ClassificationStatus,
    CompletionOutcome,
    CompletionResult,
    EnqueueOutcome,
    EnqueueResult,
    FailureDisposition,
    FailureOutcome,
    FailureResult,
    LeaseRenewalOutcome,
    LeaseRenewalResult,
)

_CLASSIFICATIONS_TABLE = "article_classifications"
_SUPABASE_URL = "SUPABASE_URL"
_SUPABASE_SERVICE_KEY = "SUPABASE_SERVICE_KEY"
_CLASSIFIER_VERSION_PATTERN = re.compile(r"^classification-v[1-9][0-9]*$")


_OutcomeT = TypeVar(
    "_OutcomeT", EnqueueOutcome, CompletionOutcome, LeaseRenewalOutcome, FailureOutcome
)


class SupabaseClassificationRepository:
    """Persist lifecycle changes only through transactional PostgreSQL RPCs."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def enqueue(
        self,
        key: ClassificationKey,
        lineage: ClassificationLineage,
        *,
        max_attempts: int = 3,
    ) -> EnqueueResult:
        if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 12:
            raise ValueError("max_attempts must be between 1 and 12")
        payload = self._rpc(
            "enqueue_article_classification",
            {
                "p_article_id": key.article_id,
                "p_classifier_version": key.classifier_version,
                "p_requested_model": lineage.requested_model,
                "p_prompt_version": lineage.prompt_version,
                "p_taxonomy_version": lineage.taxonomy_version,
                "p_max_attempts": max_attempts,
            },
            key,
            "enqueue",
        )
        outcome = self._enum_value(EnqueueOutcome, payload.get("outcome"), key, "enqueue")
        record = self._record(payload.get("record"), key, "enqueue")
        if outcome is EnqueueOutcome.LINEAGE_MISMATCH:
            raw_fields = payload.get("mismatched_fields", [])
            fields = (
                tuple(value for value in raw_fields if isinstance(value, str))
                if isinstance(raw_fields, list)
                else ()
            )
            raise ClassificationLineageMismatchError(key, fields)
        return EnqueueResult(outcome=outcome, record=record)

    def get(self, key: ClassificationKey) -> ClassificationRecord | None:
        try:
            response = (
                self._client.table(_CLASSIFICATIONS_TABLE)
                .select("*")
                .eq("article_id", key.article_id)
                .eq("classifier_version", key.classifier_version)
                .limit(1)
                .execute()
            )
        except (APIError, httpx.HTTPError) as error:
            raise ClassificationPersistenceError(key, "get") from error
        data = response.data
        if not isinstance(data, list):
            raise ClassificationPersistenceError(key, "get")
        if not data:
            return None
        return self._record(data[0], key, "get")

    def get_succeeded(self, key: ClassificationKey) -> ClassifiedArticle | None:
        record = self.get(key)
        if record is None or record.status is not ClassificationStatus.SUCCEEDED:
            return None
        return record.to_classified_article()

    def claim_next(
        self,
        classifier_version: str,
        claim_token: UUID,
        *,
        lease_seconds: int = 300,
    ) -> ClassificationClaim | None:
        self._validate_classifier_version(classifier_version)
        self._validate_lease_seconds(lease_seconds)
        operation_key = ClassificationKey(
            article_id="queue-claim",
            classifier_version=classifier_version,
        )
        payload = self._rpc(
            "claim_next_article_classification",
            {
                "p_classifier_version": classifier_version,
                "p_claim_token": str(claim_token),
                "p_lease_seconds": lease_seconds,
            },
            operation_key,
            "claim_next",
        )
        outcome = payload.get("outcome")
        if outcome in {"EMPTY", "RECOVERED_QUARANTINED"}:
            return None
        if outcome != "CLAIMED":
            raise ClassificationPersistenceError(operation_key, "claim_next")
        record_payload = payload.get("record")
        record_key = self._key_from_record_payload(record_payload, operation_key, "claim_next")
        record = self._record(record_payload, record_key, "claim_next")
        return ClassificationClaim.from_record(record)

    def renew_lease(
        self,
        claim: ClassificationClaim,
        *,
        lease_seconds: int = 300,
    ) -> LeaseRenewalResult:
        self._validate_lease_seconds(lease_seconds)
        payload = self._rpc(
            "renew_article_classification_lease",
            {
                **self._claim_params(claim),
                "p_lease_seconds": lease_seconds,
            },
            claim.key,
            "renew_lease",
        )
        outcome = self._enum_value(
            LeaseRenewalOutcome,
            payload.get("outcome"),
            claim.key,
            "renew_lease",
        )
        record = (
            self._record(payload.get("record"), claim.key, "renew_lease")
            if payload.get("record") is not None
            else None
        )
        return LeaseRenewalResult(outcome=outcome, record=record)

    def complete_success(
        self,
        claim: ClassificationClaim,
        result: ClassificationResult,
    ) -> CompletionResult:
        self._validate_result_matches_claim(claim, result)
        article = result.classified_article
        usage = result.usage
        payload = self._rpc(
            "complete_article_classification",
            {
                **self._claim_params(claim),
                "p_is_relevant": article.is_relevant,
                "p_markets": [value.value for value in article.markets],
                "p_category": article.category.value if article.category is not None else None,
                "p_topics": [value.value for value in article.topics],
                "p_confidence": article.confidence,
                "p_classified_at": article.classified_at.isoformat(),
                "p_classification_method": result.classification_method.value,
                "p_provider_model": result.provider_model,
                "p_provider_request_id": result.provider_request_id,
                "p_system_fingerprint": result.system_fingerprint,
                **self._usage_params(usage),
                "p_estimated_cost_usd": str(result.estimated_cost_usd),
                "p_pricing_id": result.pricing_id,
                "p_pricing_window": result.pricing_window,
                "p_provider_attempts": result.provider_attempts,
            },
            claim.key,
            "complete_success",
        )
        outcome = self._enum_value(
            CompletionOutcome,
            payload.get("outcome"),
            claim.key,
            "complete_success",
        )
        record = (
            self._record(payload.get("record"), claim.key, "complete_success")
            if payload.get("record") is not None
            else None
        )
        return CompletionResult(outcome=outcome, record=record)

    def record_failure(
        self,
        claim: ClassificationClaim,
        failure: ClassificationFailure,
        *,
        disposition: FailureDisposition,
    ) -> FailureResult:
        if not failure.retryable and disposition is not FailureDisposition.QUARANTINE:
            raise ValueError("non-retryable failures must be quarantined")
        payload = self._rpc(
            "fail_article_classification",
            {
                **self._claim_params(claim),
                "p_error_category": failure.error_category,
                "p_last_http_status": failure.last_http_status,
                "p_error_retryable": failure.retryable,
                "p_disposition": disposition.value,
                **self._usage_params(failure.usage),
                "p_estimated_cost_usd": str(failure.estimated_cost_usd),
                "p_pricing_id": failure.pricing_id,
                "p_pricing_window": failure.pricing_window,
                "p_provider_attempts": failure.provider_attempts,
            },
            claim.key,
            "record_failure",
        )
        outcome = self._enum_value(
            FailureOutcome,
            payload.get("outcome"),
            claim.key,
            "record_failure",
        )
        record = (
            self._record(payload.get("record"), claim.key, "record_failure")
            if payload.get("record") is not None
            else None
        )
        return FailureResult(outcome=outcome, record=record)

    def _rpc(
        self,
        function_name: str,
        params: dict[str, object],
        key: ClassificationKey,
        operation: str,
    ) -> dict[str, Any]:
        try:
            data = self._client.rpc(function_name, params).execute().data
        except (APIError, httpx.HTTPError) as error:
            raise ClassificationPersistenceError(key, operation) from error
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise ClassificationPersistenceError(key, operation)
        return cast(dict[str, Any], data)

    @staticmethod
    def _record(
        payload: object,
        key: ClassificationKey,
        operation: str,
    ) -> ClassificationRecord:
        try:
            return ClassificationRecord.model_validate_json(json.dumps(payload))
        except (TypeError, ValueError, ValidationError) as error:
            raise ClassificationPersistenceError(key, operation) from error

    @staticmethod
    def _key_from_record_payload(
        payload: object,
        fallback: ClassificationKey,
        operation: str,
    ) -> ClassificationKey:
        if not isinstance(payload, dict):
            raise ClassificationPersistenceError(fallback, operation)
        try:
            return ClassificationKey.model_validate_json(
                json.dumps(
                    {
                        "article_id": payload.get("article_id"),
                        "classifier_version": payload.get("classifier_version"),
                    }
                )
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise ClassificationPersistenceError(fallback, operation) from error

    @staticmethod
    def _enum_value(
        enum_type: type[_OutcomeT],
        value: object,
        key: ClassificationKey,
        operation: str,
    ) -> _OutcomeT:
        try:
            if not isinstance(value, str):
                raise TypeError
            return enum_type(value)
        except (TypeError, ValueError) as error:
            raise ClassificationPersistenceError(key, operation) from error

    @staticmethod
    def _claim_params(claim: ClassificationClaim) -> dict[str, object]:
        return {
            "p_article_id": claim.key.article_id,
            "p_classifier_version": claim.key.classifier_version,
            "p_claim_token": str(claim.claim_token),
        }

    @staticmethod
    def _usage_params(usage: ClassificationUsage) -> dict[str, object]:
        return {
            "p_prompt_tokens": usage.prompt_tokens,
            "p_prompt_cache_hit_tokens": usage.prompt_cache_hit_tokens,
            "p_prompt_cache_miss_tokens": usage.prompt_cache_miss_tokens,
            "p_completion_tokens": usage.completion_tokens,
            "p_total_tokens": usage.total_tokens,
        }

    @staticmethod
    def _validate_classifier_version(classifier_version: str) -> None:
        if not _CLASSIFIER_VERSION_PATTERN.fullmatch(classifier_version):
            raise ValueError("invalid classifier_version")

    @staticmethod
    def _validate_lease_seconds(lease_seconds: int) -> None:
        if isinstance(lease_seconds, bool) or not 30 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 30 and 900")

    @staticmethod
    def _validate_result_matches_claim(
        claim: ClassificationClaim,
        result: ClassificationResult,
    ) -> None:
        article = result.classified_article
        if (
            article.article_id != claim.key.article_id
            or article.classifier_version != claim.key.classifier_version
            or result.requested_model != claim.lineage.requested_model
            or result.prompt_version != claim.lineage.prompt_version
            or result.taxonomy_version != claim.lineage.taxonomy_version
        ):
            raise ClassificationLineageMismatchError(
                claim.key,
                (
                    "article_id",
                    "classifier_version",
                    "requested_model",
                    "prompt_version",
                    "taxonomy_version",
                ),
            )


def create_classification_repository_from_environment(
    environment: Mapping[str, str] | None = None,
) -> SupabaseClassificationRepository:
    values = os.environ if environment is None else environment
    url = _required_environment_value(values, _SUPABASE_URL)
    service_key = _required_environment_value(values, _SUPABASE_SERVICE_KEY)
    return SupabaseClassificationRepository(create_client(url, service_key))


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value.strip():
        raise PersistenceConfigurationError(f"missing required environment variable: {name}")
    return value.strip()
