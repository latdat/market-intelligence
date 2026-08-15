import hashlib
import logging
from datetime import UTC, datetime

import pytest

from market_intelligence.articles import RawArticle
from market_intelligence.normalization import (
    ArticleNormalizationError,
    canonicalize_url,
    normalize_article,
)
from market_intelligence.source_registry import Market, SourceConfig

RETRIEVED_AT = datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def source_config(*, source_id: str = "example_feed", language: str = "en") -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": "Example Feed",
            "market": "US",
            "language": language,
            "source_type": "OFFICIAL_ORGANIZATION",
            "authority_level": "PRIMARY",
            "domains": ["TECHNOLOGY"],
            "content_scope": "EDITORIAL_NEWS",
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://example.org/feed.xml",
                "poll_interval_minutes": 15,
                "rate_limit": None,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": False,
                "can_show_snippet": "REVIEWED",
                "can_redistribute_full_text": False,
                "rights_review_status": "APPROVED",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def raw_article(**overrides: object) -> RawArticle:
    payload: dict[str, object] = {
        "source_id": "example_feed",
        "source_item_id": "item-1",
        "url": "HTTPS://Example.COM:443/articles/1?utm_source=feed&view=full#section",
        "title": "  Example   title  ",
        "description": "<p>Example description</p>",
        "published_at_raw": "2026-08-14T10:00:00-04:00",
        "language_hint": "en",
        "retrieved_at": RETRIEVED_AT,
        "raw_metadata": {"connector": "rss"},
    }
    payload.update(overrides)
    return RawArticle.model_validate(payload)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_normalize_article_populates_canonical_contract() -> None:
    raw = raw_article()

    article = normalize_article(raw, source_config())

    assert article.source_id == "example_feed"
    assert article.source_item_id == "item-1"
    assert article.url == raw.url
    assert article.canonical_url == "https://example.com/articles/1?view=full"
    assert article.title == "Example title"
    assert article.description == "Example description"
    assert article.language == "en"
    assert article.market is Market.US
    assert article.published_at == datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
    assert article.discovered_at == RETRIEVED_AT


def test_query_filter_preserves_every_retained_raw_segment() -> None:
    url = (
        "HTTPS://Example.COM:443/path?"
        "b=%2Fvalue%20x&a=1&a=2&blank=&flag&&"
        "UTM_Source=NEWS&x=%252F&ref=keep#fragment"
    )

    canonical = canonicalize_url(url)

    assert canonical == (
        "https://example.com/path?b=%2Fvalue%20x&a=1&a=2&blank=&flag&&x=%252F&ref=keep"
    )


def test_percent_encoded_tracking_name_is_removed_without_rewriting_other_values() -> None:
    canonical = canonicalize_url(
        "https://example.org/item?%75tm_campaign=sale&encoded=%2f%2F&bad=%ZZ"
    )

    assert canonical == "https://example.org/item?encoded=%2f%2F&bad=%ZZ"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://EXAMPLE.org:80", "http://example.org/"),
        ("https://EXAMPLE.org:443/path/", "https://example.org/path/"),
        ("https://EXAMPLE.org:8443/path", "https://example.org:8443/path"),
    ],
)
def test_url_normalization_handles_hosts_ports_and_trailing_slashes(
    url: str,
    expected: str,
) -> None:
    assert canonicalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "/relative/path",
        "ftp://example.org/item",
        "https://user:secret@example.org/item",
        "https://example.org:invalid/item",
        "https://example.org/bad path",
    ],
)
def test_invalid_or_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(ArticleNormalizationError):
        canonicalize_url(url, source_id="example_feed")


def test_text_cleanup_uses_nfc_html_cleanup_and_single_entity_decode() -> None:
    raw = raw_article(
        title="  <strong>Café &amp; Energy</strong>  ",
        description=(
            "<p>Fish &amp;amp; Chips</p>"
            "<script>alert('ignored')</script>"
            "<style>.ignored { color: red; }</style>"
            "<p>A&nbsp;B</p>"
        ),
    )

    article = normalize_article(raw, source_config())

    assert article.title == "Café & Energy"
    assert article.description == "Fish &amp; Chips A B"


@pytest.mark.parametrize("title", [None, "   ", "<script>ignored</script>"])
def test_missing_or_empty_normalized_title_is_rejected(title: str | None) -> None:
    with pytest.raises(ArticleNormalizationError, match="title is missing or empty"):
        normalize_article(raw_article(title=title), source_config())


def test_empty_normalized_description_becomes_none() -> None:
    article = normalize_article(
        raw_article(description=" <style>ignored</style> "),
        source_config(),
    )

    assert article.description is None


def test_language_hint_is_normalized_and_falls_back_to_source_language() -> None:
    hinted = normalize_article(raw_article(language_hint="  zh-CN  "), source_config())
    fallback = normalize_article(raw_article(language_hint="   "), source_config(language="vi"))

    assert hinted.language == "zh-CN"
    assert fallback.language == "vi"


def test_source_id_mismatch_is_rejected() -> None:
    with pytest.raises(ArticleNormalizationError, match="do not match"):
        normalize_article(raw_article(), source_config(source_id="different_source"))


@pytest.mark.parametrize(
    ("raw_timestamp", "expected"),
    [
        ("2026-08-14T10:00:00-04:00", datetime(2026, 8, 14, 14, 0, tzinfo=UTC)),
        ("Fri, 14 Aug 2026 14:00:00 GMT", datetime(2026, 8, 14, 14, 0, tzinfo=UTC)),
        ("2026-08-14T14:00:00Z", datetime(2026, 8, 14, 14, 0, tzinfo=UTC)),
    ],
)
def test_supported_publication_timestamps_are_normalized_to_utc(
    raw_timestamp: str,
    expected: datetime,
) -> None:
    article = normalize_article(
        raw_article(published_at_raw=raw_timestamp),
        source_config(),
    )

    assert article.published_at == expected


def test_missing_publication_time_stays_none_without_fallback() -> None:
    article = normalize_article(raw_article(published_at_raw=None), source_config())

    assert article.published_at is None
    assert article.discovered_at == RETRIEVED_AT


@pytest.mark.parametrize("raw_timestamp", ["not-a-date", "2026-08-14T14:00:00", "   "])
def test_invalid_or_naive_publication_time_becomes_none_with_warning(
    raw_timestamp: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        article = normalize_article(
            raw_article(published_at_raw=raw_timestamp),
            source_config(),
        )

    assert article.published_at is None
    assert any(record.message == "article_published_at_unusable" for record in caplog.records)


def test_model_does_not_reject_publication_after_discovery() -> None:
    article = normalize_article(
        raw_article(published_at_raw="2026-08-14T15:00:00Z"),
        source_config(),
    )

    assert article.published_at is not None
    assert article.published_at > article.discovered_at


def test_source_item_identity_is_normalized_and_hashed_deterministically() -> None:
    article = normalize_article(
        raw_article(source_item_id="  item-é  "),
        source_config(),
    )

    expected = sha256_text('["v1","source_item_id","example_feed","item-é"]')
    assert article.source_item_id == "item-é"
    assert article.article_id == expected


def test_source_item_identity_takes_priority_over_url_changes() -> None:
    first = normalize_article(raw_article(url="https://example.org/first"), source_config())
    second = normalize_article(raw_article(url="https://example.org/second"), source_config())

    assert first.article_id == second.article_id


def test_canonical_url_fallback_is_source_local() -> None:
    first_raw = raw_article(
        source_item_id=None,
        url="https://example.org/item?utm_source=one&id=7",
    )
    first = normalize_article(first_raw, source_config())
    same_source = normalize_article(
        raw_article(
            source_item_id="   ",
            url="https://EXAMPLE.org:443/item?utm_medium=two&id=7#fragment",
        ),
        source_config(),
    )
    other_source = normalize_article(
        raw_article(
            source_id="other_feed",
            source_item_id=None,
            url="https://example.org/item?id=7",
        ),
        source_config(source_id="other_feed"),
    )

    expected = sha256_text('["v1","canonical_url","example_feed","https://example.org/item?id=7"]')
    assert first.article_id == expected
    assert same_source.source_item_id is None
    assert same_source.article_id == expected
    assert other_source.article_id != expected


def test_content_hash_is_cross_source_and_uses_only_normalized_content() -> None:
    first = normalize_article(
        raw_article(title=" <b>Same</b> title ", description="Same   description"),
        source_config(),
    )
    second = normalize_article(
        raw_article(
            source_id="other_feed",
            source_item_id="other-id",
            url="https://other.example.org/story",
            title="Same title",
            description="<p>Same description</p>",
        ),
        source_config(source_id="other_feed"),
    )

    expected = sha256_text('["v1","Same title","Same description"]')
    assert first.content_hash == expected
    assert second.content_hash == expected
    assert first.article_id != second.article_id


def test_content_hash_changes_when_normalized_content_changes() -> None:
    first = normalize_article(raw_article(description="First"), source_config())
    second = normalize_article(raw_article(description="Second"), source_config())

    assert first.content_hash != second.content_hash


def test_repeated_normalization_is_stable() -> None:
    raw = raw_article()
    source = source_config()

    assert normalize_article(raw, source) == normalize_article(raw, source)
