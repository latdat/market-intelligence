import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from market_intelligence.connectors.official_listing import (
    ListingConfigurationError,
    ListingFetchError,
    ListingParseError,
    OfficialListingConnector,
)
from market_intelligence.source_registry import AcquisitionMethod, load_source_config

CONFIG_DIR = Path(__file__).parents[3] / "config" / "sources"
FIXED_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def moit_config():
    return load_source_config(CONFIG_DIR / "vn_moit_regulatory_docs.toml")


@pytest.fixture
def mst_config():
    return load_source_config(CONFIG_DIR / "vn_mst_regulatory_docs.toml")


def fetch_once(source, handler, **connector_options):
    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            connector = OfficialListingConnector(
                client, clock=lambda: FIXED_TIME, **connector_options
            )
            return await connector.fetch(source)

    return asyncio.run(run())


def test_moit_listing_parse_success(moit_config):
    html = """
    <html><body>
      <table>
        <tr>
          <td>1</td>
          <td><a href="/van-ban-phap-luat/van-ban-phap-quy/test.html">Test MOIT Doc</a></td>
          <td>Số: 42/2026/TT-BCT</td>
          <td>05/06/2026</td>
        </tr>
        <tr>
          <td>2</td>
          <td><a href="/van-ban-phap-luat/van-ban-phap-quy/test2.html">Test MOIT Doc 2</a></td>
          <td>Số 123/QĐ-BCT</td>
          <td>05/06/2026</td>
        </tr>
      </table>
      <a rel="next" href="/van-ban-phap-luat/van-ban-phap-quy?page=2">Sau</a>
    </body></html>
    """.encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(
                200, content=b"<html><body><table></table></body></html>", request=request
            )
        return httpx.Response(200, content=html, request=request)

    articles = fetch_once(moit_config, handler, max_items=10)
    assert len(articles) == 2

    a1 = articles[0]
    assert a1.title == "Test MOIT Doc"
    assert a1.url == "https://moit.gov.vn/van-ban-phap-luat/van-ban-phap-quy/test.html"
    assert a1.source_item_id == "42/2026/TT-BCT"
    assert a1.published_at_raw == "05/06/2026"
    assert a1.raw_metadata["document_number"] == "Số: 42/2026/TT-BCT"

    a2 = articles[1]
    assert a2.source_item_id is None
    assert a2.url == "https://moit.gov.vn/van-ban-phap-luat/van-ban-phap-quy/test2.html"


def test_mst_listing_parse_success(mst_config):
    html = """
    <html><body>
      <table>
        <tr>
          <td>49/2026/TT-BKHCN</td>
          <td>Bộ Khoa học và Công nghệ</td>
          <td>Thông tư</td>
          <td></td>
          <td>Thông tư Quy định XYZXem chi tiết<a href="/van-ban-phap-luat/25489.htm"></a></td>
          <td>01/08/2026</td>
        </tr>
      </table>
      <a rel="next" href="/van-ban-phap-luat.htm?page=2">Sau</a>
    </body></html>
    """.encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(
                200, content=b"<html><body><table></table></body></html>", request=request
            )
        return httpx.Response(200, content=html, request=request)

    articles = fetch_once(mst_config, handler, max_items=10)
    assert len(articles) == 1

    a1 = articles[0]
    assert a1.title == "Thông tư Quy định XYZ"
    assert a1.url == "https://mst.gov.vn/van-ban-phap-luat/25489.htm"
    assert a1.source_item_id == "25489"
    assert a1.published_at_raw == "01/08/2026"
    assert a1.raw_metadata["document_number"] == "49/2026/TT-BKHCN"
    assert a1.raw_metadata["issuer"] == "Bộ Khoa học và Công nghệ"
    assert a1.raw_metadata["document_type"] == "Thông tư"

def test_moit_idempotency_and_identity_regression(moit_config):
    html = """
    <html><body><table><tr>
      <td>1</td>
      <td><a href="/van-ban-phap-luat/van-ban-phap-quy/test.html">Test</a></td>
      <td>Số: 42/2026/TT-BCT</td>
      <td>05/06/2026</td>
    </tr></table></body></html>
    """.encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, request=request)

    articles_run1 = fetch_once(moit_config, handler, max_items=10)
    articles_run2 = fetch_once(moit_config, handler, max_items=10)

    assert len(articles_run1) == len(articles_run2)
    for a1, a2 in zip(articles_run1, articles_run2, strict=True):
        assert a1.source_item_id == a2.source_item_id
        assert a1.url == a2.url
        assert a1.title == a2.title

def test_mst_idempotency_and_identity_regression(mst_config):
    html = """
    <html><body><table><tr>
      <td>49/2026/TT-BKHCN</td><td>Bộ KHCN</td><td>Thông tư</td><td></td>
      <td>Test Xem chi tiết<a href="/van-ban-phap-luat/25489.htm"></a></td><td>01/08/2026</td>
    </tr></table></body></html>
    """.encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, request=request)

    articles_run1 = fetch_once(mst_config, handler, max_items=10)
    articles_run2 = fetch_once(mst_config, handler, max_items=10)

    assert len(articles_run1) == len(articles_run2)
    for a1, a2 in zip(articles_run1, articles_run2, strict=True):
        assert a1.source_item_id == a2.source_item_id
        assert a1.url == a2.url
        assert a1.title == a2.title
