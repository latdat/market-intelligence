from datetime import UTC, datetime

import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.source_registry import SourceConfig


@pytest.fixture
def approved_source() -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "us_test_source",
            "name": "US Test Source",
            "market": "US",
            "language": "en",
            "source_type": "REGULATOR",
            "authority_level": "PRIMARY",
            "domains": ["FINANCE", "LAW_POLICY"],
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://source.example/feed.xml",
                "poll_interval_minutes": 15,
                "rate_limit": None,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": True,
                "can_show_snippet": True,
                "can_redistribute_full_text": False,
                "rights_review_status": "APPROVED",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


@pytest.fixture
def canonical_article() -> CanonicalArticle:
    return CanonicalArticle(
        article_id="article-secret-id",
        source_id="us_test_source",
        source_item_id="source-secret-item-id",
        url="https://source.example/secret-url?token=not-for-provider",
        canonical_url="https://source.example/secret-canonical-url",
        title="Federal Reserve announces a rate decision",
        description="The central bank changed its benchmark rate.",
        language="en",
        market="US",
        published_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        discovered_at=datetime(2026, 8, 15, 12, 5, tzinfo=UTC),
        content_hash="secret-content-hash",
    )
