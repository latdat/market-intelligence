import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from market_intelligence.connectors.official_listing import (
    ListingConfigurationError,
    ListingFetchError,
    OfficialListingConnector,
)
from market_intelligence.source_registry import load_source_config

FIXTURES_DIR = Path(__file__).parents[2] / "fixtures" / "sbv_listing"
CONFIG_DIR = Path(__file__).parents[3] / "config" / "sources"
FIXED_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def sbv_config():
    return load_source_config(CONFIG_DIR / "vn_sbv_regulatory_docs.toml")


def fetch_once(source, handler, **connector_options):
    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            connector = OfficialListingConnector(
                client, clock=lambda: FIXED_TIME, **connector_options
            )
            return await connector.fetch(source)

    return asyncio.run(run())


def test_official_listing_configuration_validation(sbv_config, tmp_path):
    connector = OfficialListingConnector()

    # Valid config passes
    connector._validate_source(sbv_config)

    # Wrong acquisition method
    wrong_method_path = tmp_path / "wrong.toml"
    wrong_method_path.write_text(
        (CONFIG_DIR / "vn_sbv_regulatory_docs.toml")
        .read_text(encoding="utf-8")
        .replace('method = "HTML"', 'method = "REST_API"'),
        encoding="utf-8",
    )
    wrong_method = load_source_config(wrong_method_path)
    with pytest.raises(ListingConfigurationError, match="does not use HTML acquisition"):
        connector._validate_source(wrong_method)

    # Unsupported source
    unsupported_path = tmp_path / "unsupported.toml"
    unsupported_path.write_text(
        (CONFIG_DIR / "vn_sbv_regulatory_docs.toml")
        .read_text(encoding="utf-8")
        .replace('source_id = "vn_sbv_regulatory_docs"', 'source_id = "vn_fake_source"'),
        encoding="utf-8",
    )
    unsupported_source = load_source_config(unsupported_path)
    with pytest.raises(
        ListingConfigurationError, match="not supported by OfficialListingConnector v1"
    ):
        connector._validate_source(unsupported_source)

    # Missing rights
    no_rights_path = tmp_path / "no_rights.toml"
    no_rights_path.write_text(
        (CONFIG_DIR / "vn_sbv_regulatory_docs.toml")
        .read_text(encoding="utf-8")
        .replace("can_fetch = true", "can_fetch = false"),
        encoding="utf-8",
    )
    no_rights = load_source_config(no_rights_path)
    with pytest.raises(ListingConfigurationError, match="not approved for fetching"):
        connector._validate_source(no_rights)


def test_official_listing_parse_success(sbv_config):
    html_content = (FIXTURES_DIR / "page_1.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(
                200,
                content=b'<div class="danh-sach-tin-tuc-v32 radius-10px"><ul></ul></div>',
                request=request,
            )
        return httpx.Response(200, content=html_content, request=request)

    articles = fetch_once(sbv_config, handler, max_items=10)

    # 3 items in the list, all returned. Navigation links are ignored.
    assert len(articles) == 3

    # URL with redirect + another query parameter -> redirect removed, foo=bar preserved
    assert articles[0].title == "Thông tư 38/2026/TT-NHNN Quy định về quản lý ngoại hối"
    assert articles[0].url == "https://sbv.gov.vn/vi/w/tt38?foo=bar"
    assert articles[0].published_at_raw == "14/08/2026 | 17:01:00"
    assert articles[0].language_hint == "vi"
    assert articles[0].source_id == "vn_sbv_regulatory_docs"
    # Complete ID extracted
    assert articles[0].source_item_id == "38/2026/TT-NHNN"

    # URL with another query parameter but no redirect -> preserved
    assert articles[1].title == "Thông tư 39/2026/TT-NHNN ngày 05/8/2026 Sửa đổi"
    assert articles[1].published_at_raw == "07/08/2026 | 15:19:00"
    assert articles[1].source_item_id == "39/2026/TT-NHNN"
    assert articles[1].url == "https://sbv.gov.vn/vi/w/tt39?foo=bar"

    # URL with only redirect -> redirect removed, no query string left
    assert articles[2].title == "Quyết định 123/QĐ-NHNN không có năm đầy đủ"
    assert articles[2].url == "https://sbv.gov.vn/vi/w/qd123"
    # Incomplete document number -> rejected, fallback to None
    assert articles[2].source_item_id is None


def test_official_listing_idempotency_and_identity_regression(sbv_config):
    html_content = (FIXTURES_DIR / "page_1.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(
                200,
                content=b'<div class="danh-sach-tin-tuc-v32 radius-10px"><ul></ul></div>',
                request=request,
            )
        return httpx.Response(200, content=html_content, request=request)

    articles_run1 = fetch_once(sbv_config, handler, max_items=10)

    # Check that navigation noise like "/vi/tin-tuc" is NOT present
    urls = [a.url for a in articles_run1]
    assert not any("tin-tuc" in u for u in urls)

    # Run again, results should be exactly equivalent except for retrieved_at
    articles_run2 = fetch_once(sbv_config, handler, max_items=10)

    assert len(articles_run1) == len(articles_run2)
    for a1, a2 in zip(articles_run1, articles_run2, strict=True):
        assert a1.source_item_id == a2.source_item_id
        assert a1.url == a2.url
        assert a1.title == a2.title


def test_official_listing_pagination_and_max_items(sbv_config):
    html_content = (FIXTURES_DIR / "page_1.html").read_bytes()
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=html_content, request=request)

    articles = fetch_once(sbv_config, handler, max_items=2)

    assert len(articles) == 2
    assert request_count == 1


def test_official_listing_http_retry_and_timeout(sbv_config):
    request_count = 0
    html_content = (FIXTURES_DIR / "page_1.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            raise httpx.TimeoutException("Mock timeout", request=request)
        if request_count == 2:
            return httpx.Response(503, request=request)
        if "page=2" in str(request.url):
            return httpx.Response(
                200,
                content=b'<div class="danh-sach-tin-tuc-v32 radius-10px"><ul></ul></div>',
                request=request,
            )
        return httpx.Response(200, content=html_content, request=request)

    async def record_sleep(delay: float) -> None:
        pass

    articles = fetch_once(
        sbv_config,
        handler,
        max_attempts=3,
        base_retry_delay_seconds=0.0,
        max_items=10,
        sleep=record_sleep,
    )
    assert len(articles) == 3
    assert request_count == 4


def test_official_listing_http_failure(sbv_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    with pytest.raises(ListingFetchError) as exc_info:
        fetch_once(sbv_config, handler)

    assert exc_info.value.status_code == 404
    assert not exc_info.value.retryable
