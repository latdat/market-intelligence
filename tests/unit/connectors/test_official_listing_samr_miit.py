from datetime import UTC, datetime

import pytest

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


@pytest.fixture
def samr_source():
    return SourceConfig(
        source_id="cn_samr_market_regulation_bulletins",
        name="SAMR",
        market="CN",
        language="zh",
        source_type="GOVERNMENT",
        authority_level="PRIMARY",
        domains=["LAW_POLICY"],
        content_scope="FORMAL_REGULATORY_LEGAL",
        acquisition=AcquisitionConfig(
            method="HTML",
            endpoint_url="https://zwfw.samr.gov.cn/scjg/wyk/tbtg/",
            poll_interval_minutes=15,
        ),
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status="PENDING",
        ),
        cost=CostConfig(type="FREE", monthly_fixed_usd=0),
        priority=100,
    )


@pytest.fixture
def miit_source():
    return SourceConfig(
        source_id="cn_miit_policy_listing",
        name="MIIT",
        market="CN",
        language="zh",
        source_type="GOVERNMENT",
        authority_level="PRIMARY",
        domains=["LAW_POLICY", "TECHNOLOGY"],
        content_scope="FORMAL_REGULATORY_LEGAL",
        acquisition=AcquisitionConfig(
            method="HTML",
            endpoint_url="https://www.miit.gov.cn/",
            poll_interval_minutes=15,
        ),
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status="PENDING",
        ),
        cost=CostConfig(type="FREE", monthly_fixed_usd=0),
        priority=100,
    )


def test_samr_parser_success(samr_source):
    """SAMR live site returns http:// links; parser should normalize to https."""
    html = """
    <html>
        <body>
            <div class="list">
                <ul>
                    <li>
                        <a href="http://www.samr.gov.cn/art/2026/art_71f38343435e4a3e96d0910ccc16c085.html" title="Sample TBTG">Sample TBTG</a>
                        <span>2026-08-13</span>
                    </li>
                    <li>
                        <a href="http://www.samr.gov.cn/art/2026/art_invalid.html">No title</a>
                    </li>
                    <li>
                        <a href="http://www.samr.gov.cn/art/2026/art_440c2ded2ba54f0387b4cca50acf8147.html" title="Another one">Another one</a>
                        <span>2026-08-14</span>
                    </li>
                    <li>
                        <a href="https://other.gov.cn/art/2026/art_71f38343435e4a3e96d0910ccc16c085.html" title="Cross origin">Cross origin</a>
                        <span>2026-08-15</span>
                    </li>
                </ul>
            </div>
        </body>
    </html>
    """

    retrieved_at = datetime(2026, 8, 16, tzinfo=UTC)
    articles, next_url = OfficialListingConnector._parse_samr_html(
        samr_source, html, "https://zwfw.samr.gov.cn/scjg/wyk/tbtg/", retrieved_at, 10
    )

    assert len(articles) == 3
    assert next_url is None

    art1 = articles[0]
    assert art1.title == "Sample TBTG"
    # http:// links must be normalized to https://
    assert art1.url == "https://www.samr.gov.cn/art/2026/art_71f38343435e4a3e96d0910ccc16c085.html"
    assert art1.source_item_id == "art_71f38343435e4a3e96d0910ccc16c085"
    assert art1.published_at_raw == "2026-08-13"
    assert art1.raw_metadata["structural_category"] == "通知通告"

    art2 = articles[1]
    assert art2.title == "No title"
    assert art2.source_item_id is None

    art3 = articles[2]
    assert art3.title == "Another one"
    assert art3.source_item_id == "art_440c2ded2ba54f0387b4cca50acf8147"


def test_samr_parser_https_links_also_accepted(samr_source):
    """Verify that HTTPS links from SAMR are also accepted (future-proof)."""
    html = """
    <html><body><ul>
        <li>
            <a href="https://www.samr.gov.cn/art/2026/art_aabbccdd11223344aabbccdd11223344.html"
               title="HTTPS link">HTTPS link</a>
            <span>2026-08-10</span>
        </li>
    </ul></body></html>
    """
    retrieved_at = datetime(2026, 8, 16, tzinfo=UTC)
    articles, _ = OfficialListingConnector._parse_samr_html(
        samr_source, html, "https://zwfw.samr.gov.cn/scjg/wyk/tbtg/", retrieved_at, 10
    )
    assert len(articles) == 1
    assert articles[0].url.startswith("https://")


def test_samr_parser_max_items(samr_source):
    """Verify max_items boundary is respected."""
    html = """
    <html><body><ul>
        <li>
            <a href="http://www.samr.gov.cn/art/2026/art_11111111111111111111111111111111.html"
               title="A">A</a>
            <span>2026-08-10</span>
        </li>
        <li>
            <a href="http://www.samr.gov.cn/art/2026/art_22222222222222222222222222222222.html"
               title="B">B</a>
            <span>2026-08-11</span>
        </li>
        <li>
            <a href="http://www.samr.gov.cn/art/2026/art_33333333333333333333333333333333.html"
               title="C">C</a>
            <span>2026-08-12</span>
        </li>
    </ul></body></html>
    """
    retrieved_at = datetime(2026, 8, 16, tzinfo=UTC)
    articles, _ = OfficialListingConnector._parse_samr_html(
        samr_source, html, "https://zwfw.samr.gov.cn/scjg/wyk/tbtg/", retrieved_at, 2
    )
    assert len(articles) == 2


def test_miit_parser_success(miit_source):
    html = """
    <html>
        <body>
            <div class="tabbox-hd tabbox-hds2">
                <a>政策文件</a>
                <a>政策解读</a>
            </div>
            <div class="tabbox-bd">
                <div class="list" id="panel_0">
                    <ul>
                        <li>
                            <a href="/zwgk/zcwj/wjfb/tg/art/2026/art_440c2ded2ba54f0387b4cca50acf8147.html" title="Policy Doc">Policy Doc</a>
                            <span>2026-08-13</span>
                        </li>
                        <li>
                            <a href="/other/art/2026/art_11111111111111111111111111111111.html" title="Wrong path">Wrong path</a>
                            <span>2026-08-14</span>
                        </li>
                    </ul>
                </div>
                <div class="list" id="panel_1">
                    <ul>
                        <li>
                            <a href="/zwgk/zcjd/art/2026/art_22222222222222222222222222222222.html" title="Policy Interpretation">Policy Interpretation</a>
                            <span>2026-08-13</span>
                        </li>
                    </ul>
                </div>
            </div>
        </body>
    </html>
    """

    retrieved_at = datetime(2026, 8, 16, tzinfo=UTC)
    articles, next_url = OfficialListingConnector._parse_miit_html(
        miit_source, html, "https://www.miit.gov.cn/", retrieved_at, 10
    )

    assert len(articles) == 1
    assert next_url is None

    art1 = articles[0]
    assert art1.title == "Policy Doc"
    assert (
        art1.url
        == "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tg/art/2026/art_440c2ded2ba54f0387b4cca50acf8147.html"
    )
    assert art1.source_item_id == "art_440c2ded2ba54f0387b4cca50acf8147"
    assert art1.published_at_raw == "2026-08-13"


def test_miit_parser_layout_drift(miit_source):
    html = "<html><body><div>No tabbox</div></body></html>"
    retrieved_at = datetime(2026, 8, 16, tzinfo=UTC)

    with pytest.raises(ListingParseError, match="layout drift: could not find 政策文件 panel"):
        OfficialListingConnector._parse_miit_html(
            miit_source, html, "https://www.miit.gov.cn/", retrieved_at, 10
        )
