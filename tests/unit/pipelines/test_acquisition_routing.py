"""Unit tests for acquisition routing in the ingestion pipeline (SO-005)."""

import asyncio

import pytest

from market_intelligence.articles import RawArticle
from market_intelligence.connectors import OfficialListingConnector
from market_intelligence.pipelines import preflight_sources
from market_intelligence.pipelines.rss_to_supabase import _create_connector_for_source
from market_intelligence.source_registry import SourceConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RETRIEVED_AT_STR = "2026-08-15T12:00:00+00:00"


def _raw_article(source_id: str) -> RawArticle:
    from datetime import UTC, datetime

    return RawArticle(
        source_id=source_id,
        source_item_id="item-1",
        url="https://example.gov/doc/1",
        title="Test article",
        description=None,
        published_at_raw="2026-08-15",
        language_hint="en",
        retrieved_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
    )


class _StaticFetcher:
    """Test double that returns pre-set articles without network."""

    def __init__(self, articles: list[RawArticle]) -> None:
        self._articles = articles
        self.call_count = 0

    async def fetch(self, source: SourceConfig) -> list[RawArticle]:
        self.call_count += 1
        return self._articles


def _make_source(source_id: str, method: str, endpoint: str) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": source_id,
            "market": "US",
            "language": "en",
            "source_type": "GOVERNMENT",
            "authority_level": "PRIMARY",
            "domains": ["LAW_POLICY"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "acquisition": {
                "method": method,
                "endpoint_url": endpoint,
                "poll_interval_minutes": 15,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": False,
                "can_show_snippet": False,
                "can_redistribute_full_text": False,
                "rights_review_status": "PENDING",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def _rss_source() -> SourceConfig:
    return _make_source(
        "us_fed_press_releases",
        "RSS",
        "https://www.federalreserve.gov/feeds/press_all.xml",
    )


def _atom_source() -> SourceConfig:
    return _make_source(
        "eu_ecb_press",
        "ATOM",
        "https://www.ecb.europa.eu/rss/press.html",
    )


def _rest_api_source() -> SourceConfig:
    return _make_source(
        "us_federal_register",
        "REST_API",
        "https://www.federalregister.gov/api/v1/documents.json",
    )


# ---------------------------------------------------------------------------
# Routing tests (via preflight_sources with injected connector override)
# ---------------------------------------------------------------------------


def test_rss_source_uses_injected_fetcher() -> None:
    """When a connector override is provided, RSS sources use it."""
    fetcher = _StaticFetcher([_raw_article("us_fed_press_releases")])
    source = _rss_source()

    result = asyncio.run(preflight_sources([source], max_items=10, connector=fetcher))

    assert len(result) == 1
    assert result[0].source_id == "us_fed_press_releases"
    assert result[0].fetched_count == 1
    assert fetcher.call_count == 1


def test_atom_source_uses_injected_fetcher() -> None:
    """When a connector override is provided, ATOM sources use it."""
    fetcher = _StaticFetcher([_raw_article("eu_ecb_press")])
    source = _atom_source()

    result = asyncio.run(preflight_sources([source], max_items=10, connector=fetcher))

    assert result[0].source_id == "eu_ecb_press"
    assert result[0].fetched_count == 1


def test_rest_api_source_uses_injected_fetcher() -> None:
    """When a connector override is provided, REST_API sources use it."""
    fetcher = _StaticFetcher([_raw_article("us_federal_register")])
    source = _rest_api_source()

    result = asyncio.run(preflight_sources([source], max_items=10, connector=fetcher))

    assert result[0].source_id == "us_federal_register"
    assert result[0].fetched_count == 1


def test_unsupported_acquisition_method_fails_closed_before_network() -> None:
    """An unsupported source is a failed outcome without stopping the run."""
    # HTML is a valid SourceConfig method but has no connector registered.
    source = _make_source(
        "some_html_source",
        "HTML",
        "https://example.gov/listing",
    )

    result = asyncio.run(preflight_sources([source], max_items=10))

    assert result[0].status == "FAILED"
    assert result[0].error_type == "UnsupportedAcquisitionMethod"


# ---------------------------------------------------------------------------
# Dispatcher-level regression: OfficialListingConnector HTML routing (P1 fix)
# ---------------------------------------------------------------------------
#
# The China batch source (SO-007 U2) tests in
# tests/unit/connectors/test_official_listing_china_batch.py exercise
# OfficialListingConnector directly and therefore never caught that
# _create_connector_for_source() had not been updated to route these four
# source_ids. These tests call the real dispatcher (no connector override)
# to prove the routing itself, not just the connector's own behavior.

_CHINA_OFFICIAL_LISTING_SOURCE_IDS = [
    "cn_state_council_policy_docs",
    "cn_pboc_regulatory_docs",
    "cn_csrc_regulatory_docs",
    "cn_nea_regulatory_docs",
]


@pytest.mark.parametrize("source_id", _CHINA_OFFICIAL_LISTING_SOURCE_IDS)
def test_china_html_sources_route_to_official_listing_connector(source_id: str) -> None:
    """Regression (P1 fix): the real dispatcher must route these four HTML
    source_ids to OfficialListingConnector instead of raising
    UnsupportedAcquisitionMethod. No network call is made; only the
    connector selection is verified.
    """
    source = _make_source(source_id, "HTML", "https://example.gov.cn/listing")

    connector = _create_connector_for_source(source, max_items=10)

    assert isinstance(connector, OfficialListingConnector)


def test_mixed_rss_and_rest_sources_with_injected_fetcher() -> None:
    """preflight_sources handles a mixed list with a single injected fetcher override."""
    rss = _rss_source()
    rest = _rest_api_source()
    fetcher = _StaticFetcher([_raw_article("any")])

    result = asyncio.run(preflight_sources([rss, rest], max_items=10, connector=fetcher))

    assert len(result) == 2
    assert fetcher.call_count == 2


def test_preflight_sources_backward_alias_identical_behavior() -> None:
    """preflight_rss_sources must produce the same result as preflight_sources."""
    from market_intelligence.pipelines import preflight_rss_sources

    fetcher = _StaticFetcher([_raw_article("us_fed_press_releases")])
    source = _rss_source()

    via_new = asyncio.run(preflight_sources([source], max_items=10, connector=fetcher))
    # Reset call count
    fetcher.call_count = 0
    via_old = asyncio.run(preflight_rss_sources([source], max_items=10, connector=fetcher))

    assert via_new == via_old


def test_max_items_is_passed_to_government_api_connector_not_just_sliced_downstream() -> None:
    """Regression (SO-005 fix): when no connector override is provided, preflight_sources
    must construct GovernmentApiConnector(max_items=N), so fetch() itself returns at most N
    items.  The pre-fix bug constructed GovernmentApiConnector() with default max_items=100,
    causing fetched_count=100 regardless of the --max-items flag.

    This test uses a real GovernmentApiConnector with a mock HTTP transport that serves a
    response with 5 items plus a next_page_url. With max_items=2, the connector must return
    exactly 2 items and must not request the next page.
    """
    import json

    import httpx

    from market_intelligence.pipelines import preflight_sources

    # Build a response with 5 items and a next_page_url so pagination would normally continue.
    next_page_url = "https://www.federalregister.gov/api/v1/documents.json?page=2"
    payload = {
        "count": 5,
        "next_page_url": next_page_url,
        "results": [
            {
                "document_number": f"2026-4000{i}",
                "html_url": (
                    f"https://www.federalregister.gov/documents/2026/08/15/2026-4000{i}/rule"
                ),
                "title": f"Rule {i}",
                "publication_date": "2026-08-15",
            }
            for i in range(1, 6)
        ],
    }
    page1_content = json.dumps(payload).encode()
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert "page=2" not in str(request.url), (
            "connector must not request next page when max_items already reached"
        )
        return httpx.Response(200, content=page1_content, request=request)

    async def run() -> object:
        from market_intelligence.connectors import GovernmentApiConnector

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            connector = GovernmentApiConnector(client, max_items=2)
            source = _rest_api_source()
            return await preflight_sources([source], max_items=2, connector=connector)

    result = asyncio.run(run())

    assert isinstance(result, tuple)
    assert len(result) == 1
    fr_result = result[0]
    # fetched_count == selected_count == 2: the connector itself returned 2.
    assert fr_result.fetched_count == 2, (
        f"fetched_count should be 2 (connector-bounded), got {fr_result.fetched_count}"
    )
    assert fr_result.selected_count == 2
    assert fr_result.normalized_count == 2
    # Only one HTTP request was made (first page only).
    assert request_count == 1, f"expected 1 HTTP request, got {request_count}"
