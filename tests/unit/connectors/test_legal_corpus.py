"""Unit tests for the Legal Corpus connector (SO-006, us_govinfo_legal)."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from market_intelligence.articles import RawArticle
from market_intelligence.connectors.legal_corpus import (
    CorpusBoundsError,
    CorpusConfigurationError,
    CorpusFetchError,
    CorpusParseError,
    LegalCorpusConnector,
)
from market_intelligence.source_registry import RateLimitConfig, SourceConfig

FIXED_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def govinfo_source(
    *,
    can_fetch: bool = True,
    method: str = "REST_API",
    endpoint: str = "https://api.govinfo.gov/collections/PLAW",
    source_id: str = "us_govinfo_legal",
) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": "GovInfo Public and Private Laws",
            "market": "US",
            "language": "en",
            "source_type": "GOVERNMENT",
            "authority_level": "PRIMARY",
            "domains": ["LAW_POLICY", "ENERGY", "TECHNOLOGY", "REAL_ESTATE", "FINANCE"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "acquisition": {
                "method": method,
                "endpoint_url": endpoint,
                "poll_interval_minutes": 15,
            },
            "rights": {
                "rights_review_status": "PENDING",
                "can_fetch": can_fetch,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": False,
                "can_show_snippet": False,
                "can_redistribute_full_text": False,
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def fetch_once(
    source: SourceConfig,
    handler: Callable[[httpx.Request], httpx.Response],
    **connector_options: Any,
) -> list[RawArticle]:
    async def run() -> list[RawArticle]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            api_key = connector_options.pop("api_key", "test-key")
            connector = LegalCorpusConnector(api_key=api_key, client=client, **connector_options)
            return list(await connector.fetch(source))

    return asyncio.run(run())


def single_page_handler(content: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, request=request)

    return handler


# ---------------------------------------------------------------------------
# Configuration / auth tests (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_key", ["", "   ", None])
def test_missing_blank_api_key_fails(bad_key: str | None) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected HTTP request")

    with pytest.raises(CorpusConfigurationError, match="GOVINFO_API_KEY must be provided"):
        fetch_once(govinfo_source(), unexpected, api_key=bad_key)  # type: ignore


@pytest.mark.parametrize(
    ("source", "expected_error", "match"),
    [
        (
            govinfo_source(method="RSS"),
            CorpusConfigurationError,
            "does not use REST_API",
        ),
        (
            govinfo_source(can_fetch=False),
            CorpusConfigurationError,
            "not approved for fetching",
        ),
        (
            govinfo_source(source_id="other_source"),
            CorpusConfigurationError,
            "not supported by LegalCorpusConnector",
        ),
        (
            govinfo_source(endpoint="https://api.govinfo.gov/collections/BILLS"),
            CorpusConfigurationError,
            "must be exact PLAW collection endpoint",
        ),
    ],
)
def test_invalid_configuration_fails_before_network(
    source: SourceConfig,
    expected_error: type[Exception],
    match: str,
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected HTTP request")

    with pytest.raises(expected_error, match=match):
        fetch_once(source, unexpected)


def test_bounds_rejected_if_too_large() -> None:
    with pytest.raises(ValueError, match="must be between 1 and 1000"):
        LegalCorpusConnector(api_key="key", max_items=1001)


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


def test_successful_package_summary_mapping() -> None:
    request_urls: list[str] = []
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_urls.append(str(request.url))
        headers.append(request.headers)
        if "/collections/PLAW" in str(request.url):
            content = b'{"count": 2, "packages": [{"packageId": "PLAW-118publ1"}, {"packageId": "PLAW-118publ2"}]}'
            return httpx.Response(200, content=content, request=request)
        if "/summary" in str(request.url):
            if "PLAW-118publ1" in str(request.url):
                content = b'{"packageId": "PLAW-118publ1", "collectionCode": "PLAW", "title": "First Law", "dateIssued": "2026-08-10", "lastModified": "2026-08-14T10:00:00Z"}'
            else:
                content = b'{"packageId": "PLAW-118publ2", "collectionCode": "PLAW", "title": "Second Law", "dateIssued": "2026-08-11"}'
            return httpx.Response(200, content=content, request=request)
        raise AssertionError("Unexpected URL")

    articles = fetch_once(
        govinfo_source(),
        handler,
        api_key="my-secret-key",
        clock=lambda: FIXED_TIME,
        max_items=10,
    )

    assert len(articles) == 2
    # Verify auth
    for header in headers:
        assert header["X-Api-Key"] == "my-secret-key"

    # Verify api_key absent from URL
    for url in request_urls:
        assert "my-secret-key" not in url

    # Verify correct 7-day collection request path and parameters
    assert "/collections/PLAW/2026-08-08T12:00:00Z" in request_urls[0]
    assert "offsetMark=%2A" in request_urls[0]
    assert "pageSize=10" in request_urls[0]
    assert "offset=" not in request_urls[0]
    assert "lastModifiedStartDate=" not in request_urls[0]

    # Verify sequential summary requests
    assert len(request_urls) == 3
    assert request_urls[1].endswith("/packages/PLAW-118publ1/summary")
    assert request_urls[2].endswith("/packages/PLAW-118publ2/summary")

    # Verify no granule requests
    assert not any("granules" in url or ".pdf" in url or ".xml" in url for url in request_urls)

    first = articles[0]
    assert first.source_item_id == "PLAW-118publ1"
    assert first.title == "First Law"
    assert first.description is None
    # stable details URL
    assert first.url == "https://www.govinfo.gov/app/details/PLAW-118publ1"
    # exact dateIssued preservation
    assert first.published_at_raw == "2026-08-10"
    # lastModified not used as publication time
    assert first.raw_metadata["last_modified"] == "2026-08-14T10:00:00Z"


def test_empty_collection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"count": 0, "packages": []}', request=request)

    articles = fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME)
    assert len(articles) == 0


def test_repeated_stateless_fetch_produces_same_results() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=b'{"count": 0, "packages": []}', request=request)

    async def run() -> tuple[list[RawArticle], list[RawArticle]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LegalCorpusConnector(
                api_key="key", client=client, clock=lambda: FIXED_TIME, max_items=10
            )
            source = govinfo_source()
            first = list(await connector.fetch(source))
            second = list(await connector.fetch(source))
            return first, second

    first, second = asyncio.run(run())
    assert first == second
    assert request_count == 2


# ---------------------------------------------------------------------------
# Bounds and parse errors
# ---------------------------------------------------------------------------


def test_collection_total_greater_than_max_items_is_bounded_without_outage() -> None:
    request_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_urls.append(str(request.url))
        if "/summary" in str(request.url):
            package_id = request.url.path.split("/")[-2]
            content = (
                f'{{"packageId": "{package_id}", "collectionCode": "PLAW", '
                f'"title": "{package_id}"}}'
            ).encode()
            return httpx.Response(200, content=content, request=request)
        content = b'{"count": 5, "nextPage": "https://api.govinfo.gov/collections/PLAW/2026-08-08T12:00:00Z?offsetMark=next&pageSize=2", "packages": [{"packageId": "PLAW-1"}, {"packageId": "PLAW-2"}]}'
        return httpx.Response(200, content=content, request=request)

    articles = fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME, max_items=2)

    assert [article.source_item_id for article in articles] == ["PLAW-1", "PLAW-2"]
    assert len([url for url in request_urls if "/collections/" in url]) == 1


def test_malformed_collection_envelope() -> None:
    with pytest.raises(CorpusParseError, match="not valid JSON"):
        fetch_once(govinfo_source(), single_page_handler(b"not json"))


def test_boolean_count_is_invalid() -> None:
    content = b'{"count": true, "packages": []}'
    with pytest.raises(CorpusParseError, match="missing or invalid count"):
        fetch_once(govinfo_source(), single_page_handler(content))


def test_non_dict_package_entry() -> None:
    content = b'{"count": 1, "packages": ["PLAW-118publ1"]}'
    with pytest.raises(CorpusParseError, match="malformed package entry"):
        fetch_once(govinfo_source(), single_page_handler(content))


def test_inconsistent_packages_count() -> None:
    content = b'{"count": 0, "packages": [{"packageId": "PLAW-1"}]}'
    with pytest.raises(CorpusParseError, match="inconsistent packages/count"):
        fetch_once(govinfo_source(), single_page_handler(content))


@pytest.mark.parametrize(
    "bad_id", ["../evil", "PLAW-1?2", "PLAW-1#2", "PLAW-1%2F2", "PLAW- 1", "BILLS-118"]
)
def test_unsafe_package_id(bad_id: str) -> None:
    content = f'{{"count": 1, "packages": [{{"packageId": "{bad_id}"}}]}}'.encode()
    with pytest.raises(CorpusParseError, match="unsafe packageId"):
        fetch_once(govinfo_source(), single_page_handler(content))


def test_safe_next_page_is_followed_with_a_bounded_result() -> None:
    collection_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal collection_requests
        if "/summary" in str(request.url):
            package_id = request.url.path.split("/")[-2]
            content = (
                f'{{"packageId": "{package_id}", "collectionCode": "PLAW", '
                f'"title": "{package_id}"}}'
            ).encode()
            return httpx.Response(200, content=content, request=request)
        collection_requests += 1
        if collection_requests == 1:
            content = b'{"count": 2, "nextPage": "https://api.govinfo.gov/collections/PLAW/2026-08-08T12:00:00Z?offsetMark=next&pageSize=1", "packages": [{"packageId": "PLAW-1"}]}'
        else:
            content = b'{"count": 2, "packages": [{"packageId": "PLAW-2"}]}'
        return httpx.Response(200, content=content, request=request)

    articles = fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME, max_items=2)

    assert [article.source_item_id for article in articles] == ["PLAW-1", "PLAW-2"]
    assert collection_requests == 2


def test_cross_origin_next_page_fails_before_following_it() -> None:
    content = b'{"count": 2, "nextPage": "https://evil.example/collections/PLAW/2026-08-08T12:00:00Z?offsetMark=next", "packages": [{"packageId": "PLAW-1"}]}'

    with pytest.raises(CorpusParseError, match="unsafe nextPage URL"):
        fetch_once(
            govinfo_source(),
            single_page_handler(content),
            clock=lambda: FIXED_TIME,
            max_items=2,
        )


def test_collection_pagination_has_a_hard_page_budget() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        content = (
            f'{{"count": 10, "nextPage": '
            f'"https://api.govinfo.gov/collections/PLAW/2026-08-08T12:00:00Z'
            f'?offsetMark=next-{request_count}&pageSize=1", '
            f'"packages": [{{"packageId": "PLAW-{request_count}"}}]}}'
        ).encode()
        return httpx.Response(200, content=content, request=request)

    with pytest.raises(CorpusBoundsError, match="max_pages=2"):
        fetch_once(
            govinfo_source(),
            handler,
            clock=lambda: FIXED_TIME,
            max_items=10,
            max_pages=2,
        )

    assert request_count == 2


def test_connector_applies_source_rate_limit_to_collection_and_summary_requests() -> None:
    delays: list[float] = []
    source = govinfo_source()
    source = source.model_copy(
        update={
            "acquisition": source.acquisition.model_copy(
                update={
                    "rate_limit": RateLimitConfig(
                        max_requests=1,
                        period_seconds=10,
                    )
                }
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "/summary" in str(request.url):
            content = b'{"packageId": "PLAW-1", "collectionCode": "PLAW", "title": "Law"}'
        else:
            content = b'{"count": 1, "packages": [{"packageId": "PLAW-1"}]}'
        return httpx.Response(200, content=content, request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    articles = fetch_once(
        source,
        handler,
        clock=lambda: FIXED_TIME,
        sleep=record_sleep,
    )

    assert len(articles) == 1
    assert delays == [10.0]


def test_malformed_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/summary" in str(request.url):
            return httpx.Response(200, content=b"not json", request=request)
        return httpx.Response(
            200, content=b'{"count": 1, "packages": [{"packageId": "PLAW-1"}]}', request=request
        )

    with pytest.raises(CorpusParseError, match="not valid JSON"):
        fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME)


def test_summary_package_id_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/summary" in str(request.url):
            return httpx.Response(
                200,
                content=b'{"packageId": "PLAW-2", "collectionCode": "PLAW", "title": "title"}',
                request=request,
            )
        return httpx.Response(
            200, content=b'{"count": 1, "packages": [{"packageId": "PLAW-1"}]}', request=request
        )

    with pytest.raises(CorpusParseError, match="packageId mismatch"):
        fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME)


def test_summary_wrong_collection_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/summary" in str(request.url):
            return httpx.Response(
                200,
                content=b'{"packageId": "PLAW-1", "collectionCode": "BILLS", "title": "title"}',
                request=request,
            )
        return httpx.Response(
            200, content=b'{"count": 1, "packages": [{"packageId": "PLAW-1"}]}', request=request
        )

    with pytest.raises(CorpusParseError, match="wrong collectionCode"):
        fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME)


def test_missing_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/summary" in str(request.url):
            return httpx.Response(
                200, content=b'{"packageId": "PLAW-1", "collectionCode": "PLAW"}', request=request
            )
        return httpx.Response(
            200, content=b'{"count": 1, "packages": [{"packageId": "PLAW-1"}]}', request=request
        )

    with pytest.raises(CorpusParseError, match="missing or empty title"):
        fetch_once(govinfo_source(), handler, clock=lambda: FIXED_TIME)


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

    with pytest.raises(CorpusFetchError) as err:
        fetch_once(
            govinfo_source(),
            handler,
            sleep=record_sleep,
            random_value=lambda: 1.0,
        )

    assert err.value.category == failure_type
    assert request_count == 3
    assert delays == [0.5, 1.0]


def test_transient_http_retries_then_succeeds() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(502, request=request)
        return httpx.Response(200, content=b'{"count": 0, "packages": []}', request=request)

    async def record_sleep(delay: float) -> None:
        pass

    articles = fetch_once(
        govinfo_source(),
        handler,
        sleep=record_sleep,
        clock=lambda: FIXED_TIME,
    )

    assert articles == []
    assert request_count == 2


def test_429_retry_after_seconds_bounded() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "120"}, request=request)
        return httpx.Response(200, content=b'{"count": 0, "packages": []}', request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    fetch_once(
        govinfo_source(),
        handler,
        sleep=record_sleep,
        max_retry_delay_seconds=30,
        clock=lambda: FIXED_TIME,
    )

    assert delays == [30.0]


def test_redirect_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "https://other.example.gov"}, request=request
        )

    with pytest.raises(CorpusFetchError) as err:
        fetch_once(govinfo_source(), handler)

    assert err.value.status_code == 302
    assert err.value.retryable is False


def test_429_retry_after_http_date() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, request=request
            )
        return httpx.Response(200, content=b'{"count": 0, "packages": []}', request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    def clock() -> datetime:
        # Mock time just before the retry date
        return datetime(2026, 10, 21, 7, 27, 45, tzinfo=UTC)

    fetch_once(
        govinfo_source(),
        handler,
        sleep=record_sleep,
        max_retry_delay_seconds=30,
        clock=clock,
    )

    assert delays == [15.0]


def test_naive_clock_fails() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_once(govinfo_source(), single_page_handler(b""), clock=lambda: datetime.now())
