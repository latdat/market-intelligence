"""Supabase implementation of the article persistence boundary."""

import os
from collections.abc import Mapping

import httpx
from postgrest.exceptions import APIError
from supabase import Client, create_client

from market_intelligence.articles import CanonicalArticle
from market_intelligence.persistence.articles import (
    ArticlePersistenceError,
    PersistenceConfigurationError,
)

_ARTICLES_TABLE = "articles"
_SUPABASE_URL = "SUPABASE_URL"
_SUPABASE_SERVICE_KEY = "SUPABASE_SERVICE_KEY"


class SupabaseArticleRepository:
    """Persist first-seen canonical article snapshots through the Supabase Data API."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def save(self, article: CanonicalArticle) -> None:
        """Insert once and ignore later writes with the same article ID."""
        payload = article.model_dump(mode="json")
        try:
            (
                self._client.table(_ARTICLES_TABLE)
                .upsert(
                    payload,
                    on_conflict="article_id",
                    ignore_duplicates=True,
                )
                .execute()
            )
        except (APIError, httpx.HTTPError) as error:
            raise ArticlePersistenceError(article.article_id) from error


def create_article_repository_from_environment(
    environment: Mapping[str, str] | None = None,
) -> SupabaseArticleRepository:
    """Create a repository from required environment variables without logging them."""
    values = os.environ if environment is None else environment
    url = _required_environment_value(values, _SUPABASE_URL)
    service_key = _required_environment_value(values, _SUPABASE_SERVICE_KEY)
    return SupabaseArticleRepository(create_client(url, service_key))


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value.strip():
        raise PersistenceConfigurationError(f"missing required environment variable: {name}")
    return value.strip()
