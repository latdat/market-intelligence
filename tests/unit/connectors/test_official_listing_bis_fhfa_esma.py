from datetime import UTC, datetime

import pytest

from market_intelligence.connectors.official_listing import (
    ListingParseError,
    OfficialListingConnector,
)
from market_intelligence.source_registry import SourceConfig


def _create_source(source_id, url="https://example.com"):
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": "Test",
            "market": "US",
            "language": "en",
            "source_type": "REGULATOR",
            "authority_level": "PRIMARY",
            "domains": ["LAW_POLICY"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "priority": 100,
            "acquisition": {"method": "HTML", "endpoint_url": url, "poll_interval_minutes": 15},
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
        }
    )


def test_bis_parse():
    source = _create_source(
        "us_bis_regulatory", "https://www.bis.gov/regulations/federal-register-notices"
    )
    html = """<html><body>
    <script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"frns":[{"frnTitle":"Test 1","frnPublicationDate":{"time":"2026-08-14T12:00:00+00:00"},"frnCitation":"91FR123","frnUrl":{"url":"https://www.federalregister.gov/documents/2026/08/14/2026-16628/test"}}]}}}
    </script>
    </body></html>"""
    articles, next_url = OfficialListingConnector._parse_bis_html(
        source, html, "https://www.bis.gov", datetime.now(UTC), 10
    )
    assert len(articles) == 1
    assert articles[0].title == "Test 1"
    assert articles[0].source_item_id == "2026-16628"
    assert articles[0].url == "https://www.federalregister.gov/documents/2026/08/14/2026-16628/test"
    assert articles[0].published_at_raw == "2026-08-14T12:00:00+00:00"
    assert articles[0].raw_metadata["fr_citation"] == "91FR123"
    assert next_url is None


def test_bis_malformed():
    source = _create_source("us_bis_regulatory")
    with pytest.raises(ListingParseError):
        OfficialListingConnector._parse_bis_html(
            source, "<html></html>", "https://www.bis.gov", datetime.now(UTC), 10
        )


def test_fhfa_parse():
    source = _create_source(
        "us_fhfa_regulatory", "https://www.fhfa.gov/regulation/federal-register"
    )
    html = """<html><body>
    <table>
      <tr><td>DateSort ascending</td></tr>
      <tr>
        <td>07/13/2026</td>
        <td>Test Title</td>
        <td>RIN-2590-AB52</td>
        <td>Notice</td>
        <td>91 FR 123</td>
        <td><a href="/regulation/federal-register/test">Detail</a> <a href="https://www.federalregister.gov/documents/2026/07/13/2026-14035/test">FR</a></td>
      </tr>
    </table>
    <a href="?page=1" class="next">Next</a>
    </body></html>"""
    articles, next_url = OfficialListingConnector._parse_fhfa_html(
        source, html, "https://www.fhfa.gov/regulation/federal-register", datetime.now(UTC), 10
    )
    assert len(articles) == 1
    assert articles[0].title == "Test Title"
    assert articles[0].source_item_id == "2026-14035"
    assert articles[0].url == "https://www.fhfa.gov/regulation/federal-register/test"
    assert articles[0].published_at_raw == "07/13/2026"
    assert articles[0].raw_metadata["fhfa_number"] == "RIN-2590-AB52"
    assert next_url == "https://www.fhfa.gov/regulation/federal-register?page=1"


def test_esma_parse():
    source = _create_source("eu_esma_regulatory", "https://www.esma.europa.eu/")
    html = """<html><body>
    <table>
      <tr><td>DateSort ascending</td></tr>
      <tr>
        <td>11/08/2026</td>
        <td>ESMA75-113276571-1525</td>
        <td>Test Title</td>
        <td>Digital Finance and Innovation, Guidelines and Technical standards</td>
        <td>Compliance table</td>
        <td><a href="/document/test">Detail</a></td>
      </tr>
    </table>
    <a href="?f%5B0%5D=basic_section%3A35&page=1" class="next">Next</a>
    </body></html>"""
    articles, next_url = OfficialListingConnector._parse_esma_html(
        source,
        html,
        "https://www.esma.europa.eu/databases-library/esma-library?f%5B0%5D=basic_section%3A35",
        datetime.now(UTC),
        10,
    )
    assert len(articles) == 1
    assert articles[0].title == "Test Title"
    assert articles[0].source_item_id == "ESMA75-113276571-1525"
    assert articles[0].url == "https://www.esma.europa.eu/document/test"
    assert articles[0].published_at_raw == "11/08/2026"
    assert (
        next_url
        == "https://www.esma.europa.eu/databases-library/esma-library?f%5B0%5D=basic_section%3A35&page=1"
    )


def test_esma_reject_wrong_section():
    source = _create_source("eu_esma_regulatory")
    html = """<html><body>
    <table>
      <tr>
        <td>11/08/2026</td>
        <td>ESMA75-123</td>
        <td>Test Title</td>
        <td>Wrong Section</td>
        <td>Notice</td>
        <td><a href="/document/test">Detail</a></td>
      </tr>
    </table>
    </body></html>"""
    with pytest.raises(ListingParseError):
        OfficialListingConnector._parse_esma_html(
            source, html, "https://www.esma.europa.eu", datetime.now(UTC), 10
        )
