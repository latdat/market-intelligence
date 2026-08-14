from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from market_intelligence.articles import CanonicalArticle, RawArticle
from market_intelligence.source_registry import Market


def valid_raw_article_data() -> dict[str, object]:
    return {
        "source_id": "us_federal_register",
        "source_item_id": "document-123",
        "url": "https://example.org/item/123?utm_source=feed",
        "title": " Raw upstream title ",
        "description": "Raw upstream description",
        "published_at_raw": "2026-08-14T10:00:00-04:00",
        "language_hint": "en",
        "retrieved_at": "2026-08-14T14:05:00Z",
        "raw_metadata": {"upstream_type": "NOTICE"},
    }


def valid_canonical_article_data() -> dict[str, object]:
    return {
        "article_id": "article-stable-id",
        "source_id": "us_federal_register",
        "source_item_id": "document-123",
        "url": "https://example.org/item/123?utm_source=feed",
        "canonical_url": "https://example.org/item/123",
        "title": "Normalized title",
        "description": "Normalized description",
        "language": "en",
        "market": "US",
        "published_at": "2026-08-14T10:00:00-04:00",
        "discovered_at": "2026-08-14T14:05:00Z",
        "content_hash": "sha256-or-other-approved-hash",
    }


def test_valid_raw_article_preserves_upstream_values() -> None:
    article = RawArticle.model_validate(valid_raw_article_data())

    assert article.title == " Raw upstream title "
    assert article.published_at_raw == "2026-08-14T10:00:00-04:00"
    assert article.url == "https://example.org/item/123?utm_source=feed"
    assert article.retrieved_at == datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def test_minimal_raw_article_uses_nullable_defaults() -> None:
    article = RawArticle(
        source_id="us_federal_register",
        url="https://example.org/item/123",
        retrieved_at="2026-08-14T14:05:00Z",
    )

    assert article.source_item_id is None
    assert article.title is None
    assert article.description is None
    assert article.published_at_raw is None
    assert article.language_hint is None
    assert article.raw_metadata == {}


def test_raw_metadata_uses_independent_default_dicts() -> None:
    first = RawArticle(
        source_id="us_federal_register",
        url="https://example.org/item/1",
        retrieved_at="2026-08-14T14:05:00Z",
    )
    second = RawArticle(
        source_id="us_federal_register",
        url="https://example.org/item/2",
        retrieved_at="2026-08-14T14:05:00Z",
    )

    first.raw_metadata["attempt"] = 1

    assert second.raw_metadata == {}


def test_published_at_raw_remains_unparsed_source_data() -> None:
    payload = valid_raw_article_data()
    payload["published_at_raw"] = "source-specific date value"

    article = RawArticle.model_validate(payload)

    assert article.published_at_raw == "source-specific date value"


@pytest.mark.parametrize("field", ["source_id", "url", "retrieved_at"])
def test_raw_article_requires_core_fields(field: str) -> None:
    payload = valid_raw_article_data()
    del payload[field]

    with pytest.raises(ValidationError):
        RawArticle.model_validate(payload)


def test_raw_article_rejects_naive_retrieved_at() -> None:
    payload = valid_raw_article_data()
    payload["retrieved_at"] = datetime(2026, 8, 14, 14, 5)

    with pytest.raises(ValidationError):
        RawArticle.model_validate(payload)


def test_raw_article_rejects_unknown_fields() -> None:
    payload = valid_raw_article_data()
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError):
        RawArticle.model_validate(payload)


def test_valid_canonical_article_normalizes_timestamps_to_utc() -> None:
    article = CanonicalArticle.model_validate(valid_canonical_article_data())

    assert article.market is Market.US
    assert article.published_at == datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
    assert article.discovered_at == datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def test_canonical_article_preserves_nullable_fields_without_timestamp_fallback() -> None:
    payload = valid_canonical_article_data()
    payload.pop("source_item_id")
    payload.pop("description")
    payload.pop("published_at")

    article = CanonicalArticle.model_validate(payload)

    assert article.source_item_id is None
    assert article.description is None
    assert article.published_at is None
    assert article.discovered_at == datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def test_model_does_not_enforce_published_before_discovered() -> None:
    payload = valid_canonical_article_data()
    payload["published_at"] = "2026-08-14T15:00:00Z"
    payload["discovered_at"] = "2026-08-14T14:05:00Z"

    article = CanonicalArticle.model_validate(payload)

    assert article.published_at is not None
    assert article.published_at > article.discovered_at


@pytest.mark.parametrize("field", ["published_at", "discovered_at"])
def test_canonical_article_rejects_naive_timestamps(field: str) -> None:
    payload = valid_canonical_article_data()
    payload[field] = datetime(2026, 8, 14, 14, 0)

    with pytest.raises(ValidationError):
        CanonicalArticle.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "article_id",
        "source_id",
        "url",
        "canonical_url",
        "title",
        "language",
        "market",
        "discovered_at",
        "content_hash",
    ],
)
def test_canonical_article_requires_contract_fields(field: str) -> None:
    payload = valid_canonical_article_data()
    del payload[field]

    with pytest.raises(ValidationError):
        CanonicalArticle.model_validate(payload)


def test_canonical_article_rejects_invalid_market() -> None:
    payload = valid_canonical_article_data()
    payload["market"] = "UK"

    with pytest.raises(ValidationError):
        CanonicalArticle.model_validate(payload)


def test_canonical_article_rejects_unknown_fields() -> None:
    payload = valid_canonical_article_data()
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError):
        CanonicalArticle.model_validate(payload)


def test_canonical_model_does_not_normalize_or_generate_identifiers() -> None:
    article = CanonicalArticle.model_validate(valid_canonical_article_data())

    assert article.url == "https://example.org/item/123?utm_source=feed"
    assert article.canonical_url == "https://example.org/item/123"
    assert article.article_id == "article-stable-id"
    assert article.content_hash == "sha256-or-other-approved-hash"


def test_canonical_article_flat_json_round_trip_is_stable() -> None:
    payload = valid_canonical_article_data()
    payload["published_at"] = None
    article = CanonicalArticle.model_validate(payload)

    serialized = article.model_dump(mode="json")
    restored = CanonicalArticle.model_validate_json(article.model_dump_json())

    assert serialized["published_at"] is None
    assert serialized["source_item_id"] == "document-123"
    assert restored == article


def test_timestamp_with_offset_is_normalized_without_changing_instant() -> None:
    source_time = datetime(2026, 8, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4)))
    payload = valid_canonical_article_data()
    payload["published_at"] = source_time

    article = CanonicalArticle.model_validate(payload)

    assert article.published_at == datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
