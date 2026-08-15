"""Supabase read adapter for bounded DE-009B work discovery."""

import json
import os
import re
from collections.abc import Mapping
from typing import Any, cast

import httpx
from postgrest.exceptions import APIError
from pydantic import ValidationError
from supabase import Client, create_client

from market_intelligence.articles import CanonicalArticle
from market_intelligence.persistence.articles import PersistenceConfigurationError
from market_intelligence.persistence.classification_work import (
    ClassificationWorkReadError,
    DiscoveryScope,
)
from market_intelligence.persistence.classifications import (
    ClassificationKey,
    ClassificationLineage,
)

_ARTICLES_TABLE = "articles"
_CLASSIFICATIONS_TABLE = "article_classifications"
_SUPABASE_URL = "SUPABASE_URL"
_SUPABASE_SERVICE_KEY = "SUPABASE_SERVICE_KEY"
_CLASSIFIER_VERSION_PATTERN = re.compile(r"^classification-v[1-9][0-9]*$")
_ARTICLE_COLUMNS = (
    "article_id,source_id,source_item_id,url,canonical_url,title,description,language,"
    "market,published_at,discovered_at,content_hash"
)
_DISCOVERY_SELECT = f"{_ARTICLE_COLUMNS},article_classifications!left(article_id)"


class SupabaseClassificationWorkReader:
    """Read eligible missing work without creating another persistence contract."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def list_unclassified_articles(
        self,
        *,
        classifier_version: str,
        eligible_source_ids: tuple[str, ...],
        limit: int,
        scope: DiscoveryScope | None = None,
    ) -> tuple[CanonicalArticle, ...]:
        self._validate_classifier_version(classifier_version)
        if isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")

        scoped_source_ids = self._scoped_source_ids(eligible_source_ids, scope)
        if not scoped_source_ids:
            # Avoid an invalid `in.()` filter and a pointless anti-join request.
            return ()

        try:
            query = (
                self._client.table(_ARTICLES_TABLE)
                .select(_DISCOVERY_SELECT)
                .in_("source_id", list(scoped_source_ids))
                .eq("article_classifications.classifier_version", classifier_version)
                .is_("article_classifications", "null")
            )
            if scope is not None:
                if scope.article_ids is not None:
                    query = query.in_("article_id", list(scope.article_ids))
                if scope.discovered_from is not None:
                    query = query.gte("discovered_at", scope.discovered_from.isoformat())
                if scope.discovered_to is not None:
                    query = query.lt("discovered_at", scope.discovered_to.isoformat())
            response = query.order("discovered_at").order("article_id").limit(limit).execute()
        except (APIError, httpx.HTTPError) as error:
            raise ClassificationWorkReadError("list_unclassified") from error

        data = response.data
        if not isinstance(data, list):
            raise ClassificationWorkReadError("list_unclassified")
        return tuple(self._article(item, "list_unclassified") for item in data)

    def get_article(self, article_id: str) -> CanonicalArticle | None:
        if not article_id:
            raise ValueError("article_id must be non-empty")
        try:
            response = (
                self._client.table(_ARTICLES_TABLE)
                .select(_ARTICLE_COLUMNS)
                .eq("article_id", article_id)
                .limit(1)
                .execute()
            )
        except (APIError, httpx.HTTPError) as error:
            raise ClassificationWorkReadError("get_article", article_id) from error

        data = response.data
        if not isinstance(data, list):
            raise ClassificationWorkReadError("get_article", article_id)
        if not data:
            return None
        return self._article(data[0], "get_article", article_id)

    def find_lineage_mismatch(
        self,
        *,
        classifier_version: str,
        lineage: ClassificationLineage,
    ) -> ClassificationKey | None:
        self._validate_classifier_version(classifier_version)
        for field_name, expected_value in (
            ("requested_model", lineage.requested_model),
            ("prompt_version", lineage.prompt_version),
            ("taxonomy_version", lineage.taxonomy_version),
        ):
            try:
                response = (
                    self._client.table(_CLASSIFICATIONS_TABLE)
                    .select("article_id,classifier_version")
                    .eq("classifier_version", classifier_version)
                    .neq(field_name, expected_value)
                    .limit(1)
                    .execute()
                )
            except (APIError, httpx.HTTPError) as error:
                raise ClassificationWorkReadError("find_lineage_mismatch") from error
            data = response.data
            if not isinstance(data, list):
                raise ClassificationWorkReadError("find_lineage_mismatch")
            if data:
                return self._classification_key(data[0])
        return None

    @staticmethod
    def _scoped_source_ids(
        eligible_source_ids: tuple[str, ...],
        scope: DiscoveryScope | None,
    ) -> tuple[str, ...]:
        eligible = set(eligible_source_ids)
        if scope is not None and scope.source_ids is not None:
            eligible.intersection_update(scope.source_ids)
        return tuple(sorted(eligible))

    @staticmethod
    def _article(
        payload: object,
        operation: str,
        article_id: str | None = None,
    ) -> CanonicalArticle:
        if not isinstance(payload, dict):
            raise ClassificationWorkReadError(operation, article_id)
        article_payload = dict(cast(dict[str, Any], payload))
        article_payload.pop("article_classifications", None)
        try:
            return CanonicalArticle.model_validate_json(json.dumps(article_payload))
        except (TypeError, ValueError, ValidationError) as error:
            raise ClassificationWorkReadError(operation, article_id) from error

    @staticmethod
    def _classification_key(payload: object) -> ClassificationKey:
        try:
            return ClassificationKey.model_validate_json(json.dumps(payload))
        except (TypeError, ValueError, ValidationError) as error:
            raise ClassificationWorkReadError("find_lineage_mismatch") from error

    @staticmethod
    def _validate_classifier_version(classifier_version: str) -> None:
        if not _CLASSIFIER_VERSION_PATTERN.fullmatch(classifier_version):
            raise ValueError("invalid classifier_version")


def create_classification_work_reader_from_environment(
    environment: Mapping[str, str] | None = None,
) -> SupabaseClassificationWorkReader:
    """Create the read adapter from backend-only Supabase credentials."""
    values = os.environ if environment is None else environment
    url = _required_environment_value(values, _SUPABASE_URL)
    service_key = _required_environment_value(values, _SUPABASE_SERVICE_KEY)
    return SupabaseClassificationWorkReader(create_client(url, service_key))


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value.strip():
        raise PersistenceConfigurationError(f"missing required environment variable: {name}")
    return value.strip()
