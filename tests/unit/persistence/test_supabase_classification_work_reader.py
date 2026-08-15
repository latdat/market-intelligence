"""Tests for bounded version-scoped Supabase classification discovery."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, call

import httpx
import pytest
from supabase import Client, ClientOptions, create_client

from market_intelligence.persistence import (
    ClassificationLineage,
    ClassificationWorkReadError,
    DiscoveryScope,
    SupabaseClassificationWorkReader,
)


def article_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "article_id": "article-1",
        "source_id": "us_test_source",
        "source_item_id": "item-1",
        "url": "https://example.test/article-1",
        "canonical_url": "https://example.test/article-1",
        "title": "Rate decision",
        "description": None,
        "language": "en",
        "market": "US",
        "published_at": "2026-08-16T01:00:00+00:00",
        "discovered_at": "2026-08-16T01:05:00+00:00",
        "content_hash": "hash-1",
        "article_classifications": [],
    }
    values.update(overrides)
    return values


def fluent_reader(data: object) -> tuple[SupabaseClassificationWorkReader, Mock, Mock]:
    client = Mock()
    query = Mock()
    client.table.return_value = query
    for method_name in ("select", "in_", "eq", "is_", "gte", "lt", "order", "limit", "neq"):
        getattr(query, method_name).return_value = query
    query.execute.return_value = SimpleNamespace(data=data)
    return SupabaseClassificationWorkReader(cast(Client, client)), client, query


def lineage() -> ClassificationLineage:
    return ClassificationLineage(
        requested_model="deepseek-v4-flash",
        prompt_version="classification-prompt-v1",
        taxonomy_version="classification-taxonomy-v1",
    )


def test_version_scoped_postgrest_antijoin_is_exact_and_bounded() -> None:
    reader, client, query = fluent_reader([article_row()])

    articles = reader.list_unclassified_articles(
        classifier_version="classification-v1",
        eligible_source_ids=("us_test_source", "eu_test_source"),
        limit=100,
    )

    assert [article.article_id for article in articles] == ["article-1"]
    client.table.assert_called_once_with("articles")
    query.select.assert_called_once_with(
        "article_id,source_id,source_item_id,url,canonical_url,title,description,language,"
        "market,published_at,discovered_at,content_hash,"
        "article_classifications!left(article_id)"
    )
    query.in_.assert_called_once_with(
        "source_id",
        ["eu_test_source", "us_test_source"],
    )
    query.eq.assert_called_once_with(
        "article_classifications.classifier_version",
        "classification-v1",
    )
    query.is_.assert_called_once_with("article_classifications", "null")
    query.order.assert_has_calls([call("discovered_at"), call("article_id")])
    query.limit.assert_called_once_with(100)
    query.execute.assert_called_once_with()


def test_real_postgrest_builder_encodes_version_scoped_antijoin() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[article_row()])

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        client = create_client(
            "https://project.supabase.co",
            "offline-fake-key",
            options=ClientOptions(httpx_client=http_client),
        )
        reader = SupabaseClassificationWorkReader(client)

        result = reader.list_unclassified_articles(
            classifier_version="classification-v1",
            eligible_source_ids=("us_test_source",),
            limit=100,
        )
    finally:
        http_client.close()

    assert [item.article_id for item in result] == ["article-1"]
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/rest/v1/articles"
    assert request.url.params["select"].endswith("article_classifications!left(article_id)")
    assert request.url.params["source_id"] == "in.(us_test_source)"
    assert request.url.params["article_classifications.classifier_version"] == (
        "eq.classification-v1"
    )
    assert request.url.params["article_classifications"] == "is.null"
    assert request.url.params["order"] == "discovered_at.asc,article_id.asc"
    assert request.url.params["limit"] == "100"


def test_empty_eligible_sources_short_circuits_without_postgrest_query() -> None:
    reader, client, _ = fluent_reader([])

    articles = reader.list_unclassified_articles(
        classifier_version="classification-v1",
        eligible_source_ids=(),
        limit=100,
    )

    assert articles == ()
    client.table.assert_not_called()


def test_scope_intersects_rights_and_applies_bounded_filters() -> None:
    reader, _, query = fluent_reader([])
    scope = DiscoveryScope(
        source_ids=("us_test_source", "denied_source"),
        article_ids=("article-1",),
        discovered_from=datetime(2026, 8, 1, tzinfo=UTC),
        discovered_to=datetime(2026, 9, 1, tzinfo=UTC),
    )

    reader.list_unclassified_articles(
        classifier_version="classification-v1",
        eligible_source_ids=("us_test_source",),
        limit=3,
        scope=scope,
    )

    query.in_.assert_has_calls(
        [
            call("source_id", ["us_test_source"]),
            call("article_id", ["article-1"]),
        ]
    )
    query.gte.assert_called_once_with("discovered_at", "2026-08-01T00:00:00+00:00")
    query.lt.assert_called_once_with("discovered_at", "2026-09-01T00:00:00+00:00")
    query.limit.assert_called_once_with(3)


def test_scope_with_no_rights_intersection_also_avoids_query() -> None:
    reader, client, _ = fluent_reader([])

    result = reader.list_unclassified_articles(
        classifier_version="classification-v1",
        eligible_source_ids=("approved_source",),
        limit=10,
        scope=DiscoveryScope(source_ids=("denied_source",)),
    )

    assert result == ()
    client.table.assert_not_called()


def test_get_article_strips_embedded_field_and_validates_contract() -> None:
    reader, client, query = fluent_reader([article_row()])

    article = reader.get_article("article-1")

    assert article is not None
    assert article.article_id == "article-1"
    client.table.assert_called_once_with("articles")
    query.eq.assert_called_once_with("article_id", "article-1")


def test_invalid_article_payload_is_sanitized() -> None:
    reader, _, _ = fluent_reader([article_row(market="UNSUPPORTED")])

    with pytest.raises(ClassificationWorkReadError) as captured:
        reader.get_article("article-1")

    assert "Rate decision" not in str(captured.value)
    assert captured.value.article_id == "article-1"


def test_lineage_audit_checks_each_field_and_returns_first_mismatch() -> None:
    reader, client, query = fluent_reader([])
    query.execute.side_effect = [
        SimpleNamespace(data=[]),
        SimpleNamespace(
            data=[
                {
                    "article_id": "article-9",
                    "classifier_version": "classification-v1",
                }
            ]
        ),
    ]

    mismatch = reader.find_lineage_mismatch(
        classifier_version="classification-v1",
        lineage=lineage(),
    )

    assert mismatch is not None
    assert mismatch.article_id == "article-9"
    assert client.table.call_count == 2
    query.neq.assert_has_calls(
        [
            call("requested_model", "deepseek-v4-flash"),
            call("prompt_version", "classification-prompt-v1"),
        ]
    )


def test_lineage_audit_returns_none_after_all_three_bounded_queries() -> None:
    reader, client, query = fluent_reader([])

    mismatch = reader.find_lineage_mismatch(
        classifier_version="classification-v1",
        lineage=lineage(),
    )

    assert mismatch is None
    assert client.table.call_count == 3
    assert query.execute.call_count == 3


@pytest.mark.parametrize("limit", [0, 1001, True])
def test_discovery_rejects_invalid_bounds_before_http(limit: int) -> None:
    reader, client, _ = fluent_reader([])

    with pytest.raises(ValueError, match="limit"):
        reader.list_unclassified_articles(
            classifier_version="classification-v1",
            eligible_source_ids=("source",),
            limit=limit,
        )

    client.table.assert_not_called()
