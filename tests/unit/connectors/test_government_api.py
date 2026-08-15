"""Unit tests for the Government API connector (SO-005, us_federal_register)."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from market_intelligence.connectors import (
    ApiConfigurationError,
    ApiFetchError,
    ApiParseError,
    GovernmentApiConnector,
)
from market_intelligence.source_registry import SourceConfig

FIXTURE_DIRECTORY = Path(__file__).parents[2] / "fixtures" / "federal_register"
FIXED_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fixture_bytes(name: str) -> bytes:
    return (FIXTURE_DIRECTORY / name).read_bytes()


def federal_register_source(
    *,
    can_fetch: bool = True,
    method: str = "REST_API",
) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "us_federal_register",
            "name": "Federal Register",
            "market": "US",
            "language": "en",
            "source_type": "GOVERNMENT",
            "authority_level": "PRIMARY",
            "domains": ["LAW_POLICY", "ENERGY", "TECHNOLOGY", "REAL_ESTATE", "FINANCE"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "acquisition": {
                "method": method,
                "endpoint_url": "https://www.federalregister.gov/api/v1/documents.json",
                "poll_interval_minutes": 15,
            },
            "rights": {
                "can_fetch": can_fetch,
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


def unsupported_rest_source() -> SourceConfig:
    """A REST_API source that is not us_federal_register."""
    return SourceConfig.model_validate(
        {
            "source_id": "some_other_api",
            "name": "Other API",
            "market": "US",
            "language": "en",
            "source_type": "GOVERNMENT",
            "authority_level": "PRIMARY",
            "domains": ["FINANCE"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "acquisition": {
                "method": "REST_API",
                "endpoint_url": "https://api.example.gov/v1/docs.json",
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


def fetch_once(
    source: SourceConfig,
    handler: Callable[[httpx.Request], httpx.Response],
    **connector_options: object,
) -> list[object]:
    async def run() -> list[object]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            connector = GovernmentApiConnector(client, **connector_options)
            return list(await connector.fetch(source))

    return asyncio.run(run())


def single_page_handler(content: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, request=request)

    return handler


# ---------------------------------------------------------------------------
# Mapping tests
# ---------------------------------------------------------------------------


def test_successful_mapping_from_page1_fixture() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        # Return page2 (no next_page_url) so pagination stops
        if "page=2" in str(request.url):
            return httpx.Response(200, content=fixture_bytes("page2.json"), request=request)
        return httpx.Response(200, content=fixture_bytes("page1.json"), request=request)

    articles = fetch_once(
        federal_register_source(), handler, clock=lambda: FIXED_TIME, max_items=10
    )

    # page1 has 2 documents, page2 has 1; total 3
    assert len(articles) == 3

    first = articles[0]
    assert first.source_id == "us_federal_register"
    assert first.source_item_id == "2026-18001"
    assert first.url == (
        "https://www.federalregister.gov/documents/2026/08/14/2026-18001/example-rule"
    )
    assert first.title == "Example Final Rule on Energy Standards"
    assert first.description == (
        "This rule establishes updated energy efficiency standards for residential appliances."
    )
    assert first.published_at_raw == "2026-08-14"
    assert first.language_hint == "en"
    assert first.retrieved_at == FIXED_TIME

    # Verify User-Agent header
    assert requests[0].headers["User-Agent"] == "market-intelligence/0.1 government-api-connector"


def test_exact_date_only_publication_preserved() -> None:
    """publication_date must be preserved exactly as a date string; no midnight UTC synthesis."""
    content = b"""{
        "count": 1,
        "results": [{
            "document_number": "2026-99999",
            "html_url": "https://www.federalregister.gov/documents/2026/08/15/2026-99999/test",
            "title": "Date preservation test",
            "publication_date": "2026-08-15"
        }]
    }"""
    articles = fetch_once(
        federal_register_source(),
        single_page_handler(content),
        clock=lambda: FIXED_TIME,
    )

    assert len(articles) == 1
    # Must be exactly the date string from the API, NOT a datetime or constructed timestamp
    assert articles[0].published_at_raw == "2026-08-15"


def test_missing_optional_fields_are_none() -> None:
    """Items without abstract, publication_date retain None for optional fields."""
    articles = fetch_once(
        federal_register_source(),
        single_page_handler(fixture_bytes("missing_optional_fields.json")),
        clock=lambda: FIXED_TIME,
    )

    assert len(articles) == 2
    minimal = articles[0]
    assert minimal.source_item_id == "2026-19001"
    assert minimal.title == "Minimal Document With No Optional Fields"
    assert minimal.description is None
    # publication_date present in fixture
    assert minimal.published_at_raw == "2026-08-14"


def test_language_hint_comes_from_source_language() -> None:
    content = b"""{
        "count": 1,
        "results": [{
            "document_number": "2026-77001",
            "html_url": "https://www.federalregister.gov/documents/2026/08/14/2026-77001/lang",
            "title": "Language test",
            "publication_date": "2026-08-14"
        }]
    }"""
    articles = fetch_once(
        federal_register_source(),
        single_page_handler(content),
        clock=lambda: FIXED_TIME,
    )

    assert articles[0].language_hint == "en"


# ---------------------------------------------------------------------------
# Empty / malformed response tests
# ---------------------------------------------------------------------------


def test_empty_results_list_returns_empty_articles() -> None:
    articles = fetch_once(
        federal_register_source(),
        single_page_handler(fixture_bytes("empty_results.json")),
        clock=lambda: FIXED_TIME,
    )
    assert articles == []


def test_malformed_json_raises_parse_error() -> None:
    with pytest.raises(ApiParseError, match="not valid JSON"):
        fetch_once(
            federal_register_source(),
            single_page_handler(b"not-json{{{"),
            clock=lambda: FIXED_TIME,
        )


def test_json_array_root_raises_parse_error() -> None:
    with pytest.raises(ApiParseError, match="expected JSON object at root"):
        fetch_once(
            federal_register_source(),
            single_page_handler(b"[1, 2, 3]"),
            clock=lambda: FIXED_TIME,
        )


def test_missing_results_key_raises_parse_error() -> None:
    with pytest.raises(ApiParseError, match="expected 'results' list"):
        fetch_once(
            federal_register_source(),
            single_page_handler(b'{"count": 0}'),
            clock=lambda: FIXED_TIME,
        )


def test_partial_invalid_records_skipped_valid_returned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        articles = fetch_once(
            federal_register_source(),
            single_page_handler(fixture_bytes("partial_invalid.json")),
            clock=lambda: FIXED_TIME,
        )

    # One item is missing html_url (unusable), one is valid
    assert len(articles) == 1
    assert articles[0].source_item_id == "2026-20002"
    assert any(record.message == "government_api_item_skipped" for record in caplog.records)


def test_all_records_unusable_raises_parse_error() -> None:
    with pytest.raises(ApiParseError, match="no usable items"):
        fetch_once(
            federal_register_source(),
            single_page_handler(fixture_bytes("all_unusable.json")),
            clock=lambda: FIXED_TIME,
        )


# ---------------------------------------------------------------------------
# Configuration / rights gate tests (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected_error", "match"),
    [
        (
            federal_register_source(method="RSS"),
            ApiConfigurationError,
            "does not use REST_API",
        ),
        (
            federal_register_source(can_fetch=False),
            ApiConfigurationError,
            "not approved for fetching",
        ),
        (
            unsupported_rest_source(),
            ApiConfigurationError,
            "not supported by GovernmentApiConnector",
        ),
    ],
)
def test_invalid_configuration_fails_before_network(
    source: SourceConfig,
    expected_error: type[Exception],
    match: str,
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected HTTP request to {request.url}")

    with pytest.raises(expected_error, match=match):
        fetch_once(source, unexpected)


# ---------------------------------------------------------------------------
# HTTP retry tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("failure_type", ["timeout", "connection"])
def test_network_failure_retries_without_real_sleep(failure_type: str) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if failure_type == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("refused", request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(ApiFetchError) as err:
        fetch_once(
            federal_register_source(),
            handler,
            sleep=record_sleep,
            random_value=lambda: 1.0,
        )

    assert err.value.category == failure_type
    assert err.value.attempts == 3
    assert err.value.retryable is True
    assert request_count == 3
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize("status_code", [408, 500, 502, 503, 504])
def test_transient_http_retries_then_succeeds(status_code: int) -> None:
    request_count = 0
    delays: list[float] = []
    content = fixture_bytes("empty_results.json")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(status_code, request=request)
        return httpx.Response(200, content=content, request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    articles = fetch_once(
        federal_register_source(),
        handler,
        sleep=record_sleep,
        random_value=lambda: 1.0,
        clock=lambda: FIXED_TIME,
    )

    assert articles == []
    assert request_count == 2
    assert delays == [0.5]


def test_429_retry_after_seconds_bounded() -> None:
    request_count = 0
    delays: list[float] = []
    content = fixture_bytes("empty_results.json")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "120"}, request=request)
        return httpx.Response(200, content=content, request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    fetch_once(
        federal_register_source(),
        handler,
        sleep=record_sleep,
        max_retry_delay_seconds=30,
        clock=lambda: FIXED_TIME,
    )

    assert delays == [30.0]


def test_503_retry_after_http_date() -> None:
    request_count = 0
    delays: list[float] = []
    content = fixture_bytes("empty_results.json")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "Fri, 15 Aug 2026 12:00:12 GMT"},
                request=request,
            )
        return httpx.Response(200, content=content, request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    fetch_once(
        federal_register_source(),
        handler,
        sleep=record_sleep,
        random_value=lambda: 0.0,
        clock=lambda: FIXED_TIME,
    )

    assert delays == [12.0]


def test_redirect_is_refused() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            302,
            headers={"Location": "https://other.example.gov/docs.json"},
            request=request,
        )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(ApiFetchError) as err:
        fetch_once(federal_register_source(), handler, sleep=record_sleep)

    assert err.value.status_code == 302
    assert err.value.retryable is False
    assert request_count == 1
    assert delays == []


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


def test_pagination_follows_next_page_url() -> None:
    """Connector should follow next_page_url from page1 and fetch page2."""
    request_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_urls.append(str(request.url))
        if "page=2" in str(request.url):
            return httpx.Response(200, content=fixture_bytes("page2.json"), request=request)
        return httpx.Response(200, content=fixture_bytes("page1.json"), request=request)

    articles = fetch_once(
        federal_register_source(),
        handler,
        clock=lambda: FIXED_TIME,
        max_items=100,
    )

    # page1 has 2 items, page2 has 1 item
    assert len(articles) == 3
    assert len(request_urls) == 2
    assert "page=2" in request_urls[1]


def test_max_items_bounds_pagination() -> None:
    """Connector must not exceed max_items even if more pages are available."""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        # Always return page1 (with next_page_url) to test bounding
        return httpx.Response(200, content=fixture_bytes("page1.json"), request=request)

    articles = fetch_once(
        federal_register_source(),
        handler,
        clock=lambda: FIXED_TIME,
        max_items=1,
    )

    assert len(articles) == 1
    # Only one HTTP request should have been made (page1 had 2 items, limit is 1)
    assert request_count == 1


def test_cross_origin_next_page_url_raises_parse_error() -> None:
    """next_page_url pointing to a different origin must raise an error."""
    content = b"""{
        "count": 1,
        "next_page_url": "https://evil.example.com/steal?page=2",
        "results": [{
            "document_number": "2026-55001",
            "html_url": "https://www.federalregister.gov/documents/2026/08/14/2026-55001/rule",
            "title": "Cross-origin test",
            "publication_date": "2026-08-14"
        }]
    }"""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=content, request=request)

    with pytest.raises(ApiParseError, match=r"invalid pagination URL \(cross-origin\)"):
        fetch_once(
            federal_register_source(),
            handler,
            clock=lambda: FIXED_TIME,
            max_items=100,
        )

    # Should only have fetched page 1 before failing
    assert request_count == 1


def test_http_next_page_url_raises_parse_error() -> None:
    """next_page_url using plain HTTP (not HTTPS) must raise an error."""
    content = b"""{
        "count": 1,
        "next_page_url": "http://www.federalregister.gov/api/v1/documents.json?page=2",
        "results": [{
            "document_number": "2026-55002",
            "html_url": "https://www.federalregister.gov/documents/2026/08/14/2026-55002/rule",
            "title": "HTTP test",
            "publication_date": "2026-08-14"
        }]
    }"""

    with pytest.raises(ApiParseError, match=r"invalid pagination URL \(not https\)"):
        fetch_once(
            federal_register_source(),
            single_page_handler(content),
            clock=lambda: FIXED_TIME,
            max_items=100,
        )


def test_wrong_path_next_page_url_raises_parse_error() -> None:
    """next_page_url with a non-documents-API path must raise an error."""
    content = b"""{
        "count": 1,
        "next_page_url": "https://www.federalregister.gov/admin/steal?page=2",
        "results": [{
            "document_number": "2026-55003",
            "html_url": "https://www.federalregister.gov/documents/2026/08/14/2026-55003/rule",
            "title": "Wrong path test",
            "publication_date": "2026-08-14"
        }]
    }"""

    with pytest.raises(ApiParseError, match=r"invalid pagination URL \(wrong path\)"):
        fetch_once(
            federal_register_source(),
            single_page_handler(content),
            clock=lambda: FIXED_TIME,
            max_items=100,
        )


def test_pagination_loop_detection_raises_parse_error() -> None:
    """Connector must detect and fail on a pagination loop."""
    loop_url = "https://www.federalregister.gov/api/v1/documents.json?page=2"
    # page2_loop returns itself as next_page_url — a loop
    content_page2_loop = (
        b'{"count": 1, "next_page_url": "'
        + loop_url.encode()
        + b'", "results": [{"document_number": "2026-99001",'
        b'"html_url": "https://www.federalregister.gov/documents/2026/08/14/2026-99001/loop",'
        b'"title": "Loop page 2", "publication_date": "2026-08-14"}]}'
    )

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if "page=2" in str(request.url):
            return httpx.Response(200, content=content_page2_loop, request=request)
        return httpx.Response(200, content=fixture_bytes("page1.json"), request=request)

    with pytest.raises(ApiParseError, match="pagination loop detected"):
        fetch_once(
            federal_register_source(),
            handler,
            clock=lambda: FIXED_TIME,
            max_items=100,
        )

    # Should have stopped after detecting the loop on page 2 before fetching a 3rd time
    assert request_count == 2


def test_repeated_stateless_fetch_produces_same_results() -> None:
    """Connector must be stateless — repeated calls produce identical output."""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=fixture_bytes("empty_results.json"), request=request)

    async def run() -> tuple[list[object], list[object]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = GovernmentApiConnector(client, clock=lambda: FIXED_TIME, max_items=10)
            source = federal_register_source()
            first = list(await connector.fetch(source))
            second = list(await connector.fetch(source))
            return first, second

    first, second = asyncio.run(run())

    assert first == second
    assert request_count == 2


# ---------------------------------------------------------------------------
# Regression: max_items must be enforced at the connector level (SO-005 fix)
# ---------------------------------------------------------------------------


def test_connector_max_items_bounds_within_first_page_no_next_page_request() -> None:
    """Regression: when first-page records >= max_items, connector returns exactly
    max_items articles and does NOT request the next page.

    Root cause that was fixed: _create_connector_for_source() constructed
    GovernmentApiConnector() with the default max_items=100 instead of the
    pipeline-supplied max_items. The pipeline's downstream slice had no effect
    on fetched_count, so fetched_count equalled the connector's internal limit
    (100) instead of the requested max_items.

    Proof: fixture 'five_items_with_next_page.json' has 5 records and a
    next_page_url.  With max_items=3, the connector must:
    - make exactly 1 HTTP request (first page only);
    - return exactly 3 RawArticle records;
    - never contact the next-page URL.
    """
    request_count = 0
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        requested_urls.append(str(request.url))
        # The test must never reach page 2; if it does, raise so the bug is obvious.
        if "page=2" in str(request.url):
            raise AssertionError(
                "connector requested next page despite first page having >= max_items records"
            )
        return httpx.Response(
            200,
            content=fixture_bytes("five_items_with_next_page.json"),
            request=request,
        )

    articles = fetch_once(
        federal_register_source(),
        handler,
        clock=lambda: FIXED_TIME,
        max_items=3,
    )

    # Must return exactly max_items=3, not all 5 from the page.
    assert len(articles) == 3
    # Must have made exactly 1 HTTP request — next page must NOT be fetched.
    assert request_count == 1, (
        f"expected exactly 1 HTTP request, got {request_count}. Requested URLs: {requested_urls}"
    )
    # Verify correct article identities (first 3 in order)
    assert articles[0].source_item_id == "2026-30001"
    assert articles[1].source_item_id == "2026-30002"
    assert articles[2].source_item_id == "2026-30003"
