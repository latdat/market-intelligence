import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import AnyHttpUrl

from market_intelligence.articles.models import RawArticle
from market_intelligence.connectors.official_listing import (
    ListingParseError,
    OfficialListingConnector,
)
from market_intelligence.source_registry import (
    AcquisitionConfig,
    CostConfig,
    RightsConfig,
    SourceConfig,
)
from market_intelligence.source_registry.models import (
    AcquisitionMethod,
    AuthorityLevel,
    ContentScope,
    CostType,
    Domain,
    Market,
    RightsReviewStatus,
    SourceType,
)


@pytest.fixture
def state_council_source() -> SourceConfig:
    return SourceConfig(
        source_id="cn_state_council_policy_docs",
        name="cn_state_council_policy_docs",
        market=Market.CN,
        language="zh",
        source_type=SourceType.GOVERNMENT,
        authority_level=AuthorityLevel.PRIMARY,
        domains=[Domain.LAW_POLICY],
        content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
        cost=CostConfig(type=CostType.FREE, monthly_fixed_usd=0),
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status=RightsReviewStatus.PENDING,
        ),
        priority=100,
        acquisition=AcquisitionConfig(
            method=AcquisitionMethod.HTML,
            endpoint_url=AnyHttpUrl("https://www.gov.cn/zhengce/zhengcewenjianku/"),
            poll_interval_minutes=15,
        ),
    )


@pytest.fixture
def pboc_source() -> SourceConfig:
    return SourceConfig(
        source_id="cn_pboc_regulatory_docs",
        name="cn_pboc_regulatory_docs",
        market=Market.CN,
        language="zh",
        source_type=SourceType.REGULATOR,
        authority_level=AuthorityLevel.PRIMARY,
        domains=[Domain.FINANCE],
        content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
        cost=CostConfig(type=CostType.FREE, monthly_fixed_usd=0),
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status=RightsReviewStatus.PENDING,
        ),
        priority=100,
        acquisition=AcquisitionConfig(
            method=AcquisitionMethod.HTML,
            endpoint_url=AnyHttpUrl("https://www.pbc.gov.cn/tiaofasi/144941/144957/index.html"),
            poll_interval_minutes=15,
        ),
    )


@pytest.fixture
def csrc_source() -> SourceConfig:
    return SourceConfig(
        source_id="cn_csrc_regulatory_docs",
        name="cn_csrc_regulatory_docs",
        market=Market.CN,
        language="zh",
        source_type=SourceType.REGULATOR,
        authority_level=AuthorityLevel.PRIMARY,
        domains=[Domain.FINANCE],
        content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
        cost=CostConfig(type=CostType.FREE, monthly_fixed_usd=0),
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status=RightsReviewStatus.PENDING,
        ),
        priority=100,
        acquisition=AcquisitionConfig(
            method=AcquisitionMethod.HTML,
            endpoint_url=AnyHttpUrl("https://www.csrc.gov.cn/csrc/c101954/index.shtml"),
            poll_interval_minutes=15,
        ),
    )


@pytest.fixture
def nea_source() -> SourceConfig:
    return SourceConfig(
        source_id="cn_nea_regulatory_docs",
        name="cn_nea_regulatory_docs",
        market=Market.CN,
        language="zh",
        source_type=SourceType.REGULATOR,
        authority_level=AuthorityLevel.PRIMARY,
        domains=[Domain.ENERGY],
        content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
        cost=CostConfig(type=CostType.FREE, monthly_fixed_usd=0),
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status=RightsReviewStatus.PENDING,
        ),
        priority=100,
        acquisition=AcquisitionConfig(
            method=AcquisitionMethod.HTML,
            endpoint_url=AnyHttpUrl("https://www.nea.gov.cn/"),
            poll_interval_minutes=15,
        ),
    )


def fetch_once(
    source: SourceConfig, handler: Callable[[httpx.Request], httpx.Response]
) -> list[RawArticle]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = OfficialListingConnector(client=client, max_items=10)

    async def run() -> list[RawArticle]:
        return await connector.fetch(source)

    return asyncio.run(run())


def test_state_council_success(state_council_source: SourceConfig) -> None:
    listing_html = """<html>
        <body><a href="/zhengce/content/2026-08/13/content_123.htm">Policy Doc 1</a></body>
    </html>"""
    detail_html = """<html>
        <body>
            <div>成文日期：2026-08-13</div>
            <div>发文字号：国发〔2026〕26号</div>
        </body>
    </html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "content_123.htm" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    articles = fetch_once(state_council_source, handler)
    assert len(articles) == 1
    assert articles[0].title == "Policy Doc 1"
    assert articles[0].published_at_raw == "2026-08-13"
    assert articles[0].source_item_id == "国发〔2026〕26号"


def test_pboc_success(pboc_source: SourceConfig) -> None:
    listing_html = """<html>
        <div class="Position">当前位置：首页 > 条法司 > 部门规章</div>
        <a href="/tiaofasi/144941/144957/20260813/index.html">中国人民银行令〔2026〕第4号</a>
    </html>"""
    detail_html = """<html>
        <div class="Position">当前位置：首页 > 条法司 > 部门规章</div>
        <meta name="PubDate" content="2026-08-13">
        <body><td class="rh1">中国人民银行令〔2026〕第4号</td></body>
    </html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "index.html" in str(request.url) and "20260813" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    articles = fetch_once(pboc_source, handler)
    assert len(articles) == 1
    assert articles[0].published_at_raw == "2026-08-13"
    assert articles[0].source_item_id == "中国人民银行令〔2026〕第4号"


def test_csrc_success(csrc_source: SourceConfig) -> None:
    listing_html = """<html><title>证监会公告</title><body>
        <a href="/csrc/c101954/c123/content.shtml">测试公告长标题</a>
    </body></html>"""
    detail_html = """<html><body>
        <div class="Position">证监会公告</div>
        <div>索 &nbsp;引 &nbsp;号：bm56000001/2026-00005439</div>
        <div>文号：证监会公告〔2026〕10号</div>
        <div>发文日期：2026年05月15日</div>
        <div>2026年5月9日 (signature date ignored)</div>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "content.shtml" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    articles = fetch_once(csrc_source, handler)
    assert len(articles) == 1
    assert articles[0].source_item_id == "bm56000001/2026-00005439"
    assert articles[0].published_at_raw == "2026-05-15"
    if articles[0].raw_metadata:
        assert articles[0].raw_metadata.get("document_number") == "证监会公告〔2026〕10号"


def test_nea_success(nea_source: SourceConfig) -> None:
    listing_html = """<html><body>
        <div><h2>通知</h2><a href="/20260813/123/c.html">通知1</a></div>
    </body></html>"""
    detail_html = """<html><body>
        <div class="nav">通知</div>
        <div>索引号：123456789/2026-01</div>
        <div>制发日期：2026-07-06</div>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "c.html" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    articles = fetch_once(nea_source, handler)
    assert len(articles) == 1
    assert articles[0].source_item_id == "123456789/2026-01"
    assert articles[0].published_at_raw == "2026-07-06"


def test_state_council_regressions(state_council_source: SourceConfig) -> None:
    detail_html = "<html><body><div>No ID or Date</div></body></html>"
    listing_html = '<html><body><a href="https://www.gov.cn/zhengce/content/2026-08/13/content_123.htm">Link</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        if "content_123.htm" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    with pytest.raises(ListingParseError, match="Listing container found but no usable articles"):
        fetch_once(state_council_source, handler)


def test_pboc_regressions(pboc_source: SourceConfig) -> None:
    listing_html = '<html><body><div class="Position">当前位置：首页 > 条法司 > 其他</div><a href="https://www.pbc.gov.cn/tiaofasi/144941/144957/20260813/index.html">中国人民银行令〔2026〕第4号</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=listing_html, request=request)

    with pytest.raises(ListingParseError, match="layout drift or scope leak"):
        fetch_once(pboc_source, handler)


def test_pboc_hui12_layout(pboc_source: SourceConfig) -> None:
    listing_html = """<html>
        <td class="hui12">我的位置：条法司 > 部门规章</td>
        <a href="/tiaofasi/144941/144957/20260813/index.html">中国人民银行令〔2026〕第4号</a>
    </html>"""
    detail_html = """<html>
        <td class="hui12">我的位置：条法司 > 部门规章</td>
        <meta name="PubDate" content="2026-08-13">
        <body>中国人民银行令〔2026〕第4号</body>
    </html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "index.html" in str(request.url) and "20260813" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    articles = fetch_once(pboc_source, handler)
    assert len(articles) == 1
    assert articles[0].published_at_raw == "2026-08-13"


def test_csrc_regressions(csrc_source: SourceConfig) -> None:
    detail_html = """<html><body>
        <div class="Position">证监会公告</div>
        <div>发文日期：2026年05月15日</div>
        <div>2026年5月9日</div>
    </body></html>"""
    listing_html = '<html><title>证监会公告</title><body><a href="https://www.csrc.gov.cn/csrc/c101954/c123/content.shtml">测试公告长标题</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        if "content.shtml" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    with pytest.raises(ListingParseError, match="Listing container found but no usable articles"):
        fetch_once(csrc_source, handler)


def test_nea_regressions(nea_source: SourceConfig) -> None:
    listing_html = """<html><body>
        <div><h2>通知</h2><a href="https://www.nea.gov.cn/20260813/123/c.html">新闻1</a></div>
    </body></html>"""
    detail_html = '<html><body><div class="nav">新闻发布</div><div>索引号：0000/2026-01</div><div>制发日期：2026-07-06</div></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        if "c.html" in str(request.url):
            return httpx.Response(200, content=detail_html, request=request)
        return httpx.Response(200, content=listing_html, request=request)

    with pytest.raises(ListingParseError, match="Listing container found but no usable articles"):
        fetch_once(nea_source, handler)
