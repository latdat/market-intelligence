from datetime import UTC, datetime

from market_intelligence.articles import RawArticle
from market_intelligence.deduplication import DedupReason, evaluate_duplicate
from market_intelligence.normalization import normalize_article
from market_intelligence.source_registry import SourceConfig

DISCOVERED_AT = datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def source_config(source_id: str) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": "Example Feed",
            "market": "US",
            "language": "en",
            "source_type": "OFFICIAL_ORGANIZATION",
            "authority_level": "PRIMARY",
            "domains": ["TECHNOLOGY"],
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
                "can_ai_process": "REVIEWED",
                "can_show_snippet": "REVIEWED",
                "can_redistribute_full_text": False,
                "rights_review_status": "APPROVED",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def raw_article(source_id: str, url: str) -> RawArticle:
    return RawArticle(
        source_id=source_id,
        source_item_id=None,
        url=url,
        title="Example normalized article title",
        description="Example description",
        published_at_raw="2026-08-14T14:00:00Z",
        retrieved_at=DISCOVERED_AT,
    )


def test_normalized_cross_source_urls_are_deduplicated_by_canonical_url() -> None:
    candidate = normalize_article(
        raw_article(
            "source_one",
            "https://EXAMPLE.org/article?id=7&utm_source=feed#fragment",
        ),
        source_config("source_one"),
    )
    existing = normalize_article(
        raw_article(
            "source_two",
            "https://example.org/article?id=7&utm_medium=email",
        ),
        source_config("source_two"),
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.reason is DedupReason.CANONICAL_URL
    assert decision.matched_article_id == existing.article_id
