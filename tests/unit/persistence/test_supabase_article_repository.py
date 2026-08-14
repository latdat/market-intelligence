from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import httpx
import pytest
from supabase import Client

import market_intelligence.persistence.supabase_repository as supabase_repository
from market_intelligence.articles import CanonicalArticle
from market_intelligence.persistence import (
    ArticlePersistenceError,
    PersistenceConfigurationError,
    SupabaseArticleRepository,
    create_article_repository_from_environment,
)


def canonical_article() -> CanonicalArticle:
    return CanonicalArticle(
        article_id="article-stable-id",
        source_id="us_federal_register",
        source_item_id="document-123",
        url="https://example.org/item/123?utm_source=feed",
        canonical_url="https://example.org/item/123",
        title="Normalized title",
        description="Normalized description",
        language="en",
        market="US",
        published_at=datetime(2026, 8, 14, 14, 0, tzinfo=UTC),
        discovered_at=datetime(2026, 8, 14, 14, 5, tzinfo=UTC),
        content_hash="content-hash",
    )


def repository_with_mock_client() -> tuple[SupabaseArticleRepository, Mock, Mock]:
    client = Mock()
    request = Mock()
    client.table.return_value = request
    request.upsert.return_value = request
    return SupabaseArticleRepository(cast(Client, client)), client, request


def test_save_maps_complete_canonical_article_and_ignores_primary_key_conflict() -> None:
    repository, client, request = repository_with_mock_client()
    article = canonical_article()

    repository.save(article)

    client.table.assert_called_once_with("articles")
    request.upsert.assert_called_once_with(
        article.model_dump(mode="json"),
        on_conflict="article_id",
        ignore_duplicates=True,
    )
    request.execute.assert_called_once_with()


def test_repeated_save_uses_the_same_idempotent_write_policy() -> None:
    repository, _, request = repository_with_mock_client()
    article = canonical_article()

    repository.save(article)
    repository.save(article)

    assert request.upsert.call_count == 2
    assert all(
        call.kwargs == {"on_conflict": "article_id", "ignore_duplicates": True}
        for call in request.upsert.call_args_list
    )


def test_supabase_transport_error_is_hidden_behind_repository_boundary() -> None:
    repository, _, request = repository_with_mock_client()
    request.execute.side_effect = httpx.ConnectError("connection failed")

    with pytest.raises(ArticlePersistenceError) as captured:
        repository.save(canonical_article())

    assert captured.value.article_id == "article-stable-id"
    assert str(captured.value) == "failed to persist article article-stable-id"


@pytest.mark.parametrize("missing_name", ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"])
def test_factory_requires_supabase_environment_values(missing_name: str) -> None:
    environment = {
        "SUPABASE_URL": "https://project.example.supabase.co",
        "SUPABASE_SERVICE_KEY": "test-service-key",
    }
    del environment[missing_name]

    with pytest.raises(PersistenceConfigurationError, match=missing_name):
        create_article_repository_from_environment(environment)


def test_factory_passes_environment_values_to_supabase_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    create_client = Mock(return_value=client)
    monkeypatch.setattr(supabase_repository, "create_client", create_client)

    repository = create_article_repository_from_environment(
        {
            "SUPABASE_URL": " https://project.example.supabase.co ",
            "SUPABASE_SERVICE_KEY": " test-service-key ",
        }
    )

    assert isinstance(repository, SupabaseArticleRepository)
    create_client.assert_called_once_with(
        "https://project.example.supabase.co",
        "test-service-key",
    )
