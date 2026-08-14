import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx

from market_intelligence.connectors import RssAtomConnector
from market_intelligence.normalization import normalize_article
from market_intelligence.source_registry import SourceConfig

RSS_FIXTURE = Path(__file__).parents[1] / "fixtures" / "rss" / "valid_rss.xml"
DISCOVERED_AT = datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def source_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "example_feed",
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


def test_mocked_rss_connector_output_normalizes_to_canonical_article() -> None:
    source = source_config()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS_FIXTURE.read_bytes(), request=request)

    async def run() -> list[object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = RssAtomConnector(client, clock=lambda: DISCOVERED_AT)
            return list(await connector.fetch(source))

    raw_articles = asyncio.run(run())
    canonical = normalize_article(raw_articles[0], source)

    assert canonical.source_id == "example_feed"
    assert canonical.source_item_id == "rss-item-1"
    assert canonical.canonical_url == "https://example.org/articles/1"
    assert canonical.title == "RSS title"
    assert canonical.description == "RSS description"
    assert canonical.published_at == datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
    assert canonical.discovered_at == DISCOVERED_AT
