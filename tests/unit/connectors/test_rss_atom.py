import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from market_intelligence.connectors import (
    FeedConfigurationError,
    FeedFetchError,
    FeedParseError,
    RssAtomConnector,
)
from market_intelligence.source_registry import SourceConfig

FIXTURE_DIRECTORY = Path(__file__).parents[2] / "fixtures" / "rss"
FIXED_TIME = datetime(2026, 8, 14, 14, 5, tzinfo=UTC)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURE_DIRECTORY / name).read_bytes()


def source_config(
    *,
    method: str = "RSS",
    can_fetch: bool = True,
) -> SourceConfig:
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
                "method": method,
                "endpoint_url": "https://example.org/feed.xml",
                "poll_interval_minutes": 15,
                "rate_limit": None,
            },
            "rights": {
                "can_fetch": can_fetch,
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


def fetch_once(
    source: SourceConfig,
    handler: Callable[[httpx.Request], httpx.Response],
    **connector_options: object,
) -> list[object]:
    async def run() -> list[object]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            connector = RssAtomConnector(client, **connector_options)
            return list(await connector.fetch(source))

    return asyncio.run(run())


def successful_handler(content: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, request=request)

    return handler


def test_rss_200_maps_upstream_fields_to_raw_articles() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=fixture_bytes("valid_rss.xml"), request=request)

    articles = fetch_once(source_config(), handler, clock=lambda: FIXED_TIME)

    assert len(articles) == 1
    article = articles[0]
    assert article.source_id == "example_feed"
    assert article.source_item_id == "rss-item-1"
    assert article.url == "https://example.org/articles/1"
    assert article.title == "RSS title"
    assert article.description == "RSS description"
    assert article.published_at_raw == "Fri, 14 Aug 2026 14:00:00 GMT"
    assert article.language_hint == "en"
    assert article.retrieved_at == FIXED_TIME
    assert requests[0].headers["User-Agent"] == "market-intelligence/0.1 rss-atom-connector"


def test_atom_200_maps_id_link_summary_and_updated() -> None:
    articles = fetch_once(
        source_config(method="ATOM"),
        successful_handler(fixture_bytes("valid_atom.xml")),
        clock=lambda: FIXED_TIME,
    )

    assert len(articles) == 1
    article = articles[0]
    assert article.source_item_id == "atom-item-1"
    assert article.url == "https://example.org/articles/atom-1"
    assert article.description == "Atom summary"
    assert article.published_at_raw == "2026-08-14T14:01:00Z"


def test_valid_empty_feed_returns_empty_list() -> None:
    articles = fetch_once(
        source_config(),
        successful_handler(fixture_bytes("empty_rss.xml")),
        clock=lambda: FIXED_TIME,
    )

    assert articles == []


@pytest.mark.parametrize("content", [b"", fixture_bytes("malformed.xml")])
def test_empty_or_malformed_response_is_parse_error(content: bytes) -> None:
    with pytest.raises(FeedParseError):
        fetch_once(source_config(), successful_handler(content), clock=lambda: FIXED_TIME)


def test_mixed_feed_returns_valid_entries_and_warns_for_invalid_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        articles = fetch_once(
            source_config(),
            successful_handler(fixture_bytes("mixed_entries_rss.xml")),
            clock=lambda: FIXED_TIME,
        )

    assert [article.source_item_id for article in articles] == ["usable-item"]
    assert any(record.message == "rss_atom_entry_skipped" for record in caplog.records)


def test_feed_with_only_unusable_entries_is_parse_error() -> None:
    with pytest.raises(FeedParseError, match="no usable entries"):
        fetch_once(
            source_config(),
            successful_handler(fixture_bytes("all_unusable_rss.xml")),
            clock=lambda: FIXED_TIME,
        )


def test_missing_optional_entry_fields_are_preserved_as_none() -> None:
    content = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Minimal</title>
    <link>https://example.org/</link><description>Minimal feed</description>
    <item><link>https://example.org/minimal</link></item>
    </channel></rss>"""

    article = fetch_once(source_config(), successful_handler(content), clock=lambda: FIXED_TIME)[0]

    assert article.source_item_id is None
    assert article.title is None
    assert article.description is None
    assert article.published_at_raw is None


@pytest.mark.parametrize(
    "published_value",
    ["Fri, 14 Aug 2026 14:00:00 GMT", "2026-08-14T14:00:00+00:00"],
)
def test_publication_date_formats_remain_raw(published_value: str) -> None:
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Dates</title>
    <link>https://example.org/</link><description>Date feed</description>
    <item><link>https://example.org/date</link><pubDate>{published_value}</pubDate></item>
    </channel></rss>""".encode()

    article = fetch_once(source_config(), successful_handler(content), clock=lambda: FIXED_TIME)[0]

    assert article.published_at_raw == published_value


def test_feedparser_handles_declared_non_utf8_encoding() -> None:
    content = """<?xml version="1.0" encoding="ISO-8859-1"?>
    <rss version="2.0"><channel><title>Encoding</title>
    <link>https://example.org/</link><description>Encoding feed</description>
    <item><link>https://example.org/cafe</link><title>Café</title></item>
    </channel></rss>""".encode("iso-8859-1")

    article = fetch_once(source_config(), successful_handler(content), clock=lambda: FIXED_TIME)[0]

    assert article.title == "Café"


def test_duplicate_source_item_ids_are_preserved_for_downstream_deduplication() -> None:
    content = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Duplicates</title>
    <link>https://example.org/</link><description>Duplicate feed</description>
    <item><guid>same-id</guid><link>https://example.org/one</link></item>
    <item><guid>same-id</guid><link>https://example.org/two</link></item>
    </channel></rss>"""

    articles = fetch_once(source_config(), successful_handler(content), clock=lambda: FIXED_TIME)

    assert [article.source_item_id for article in articles] == ["same-id", "same-id"]


def test_repeated_fetch_has_no_connector_state_or_side_effect() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=fixture_bytes("valid_rss.xml"), request=request)

    async def run() -> tuple[list[object], list[object]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = RssAtomConnector(client, clock=lambda: FIXED_TIME)
            first = list(await connector.fetch(source_config()))
            second = list(await connector.fetch(source_config()))
            return first, second

    first, second = asyncio.run(run())

    assert first == second
    assert request_count == 2


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (source_config(method="REST_API"), "does not use RSS or ATOM"),
        (source_config(can_fetch=False), "not approved for fetching"),
    ],
)
def test_invalid_connector_configuration_fails_before_network(
    source: SourceConfig,
    message: str,
) -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    with pytest.raises(FeedConfigurationError, match=message):
        fetch_once(source, unexpected_request)


@pytest.mark.parametrize("failure_type", ["timeout", "connection"])
def test_network_failures_retry_without_real_sleep(failure_type: str) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if failure_type == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("connection failed", request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(FeedFetchError) as error_info:
        fetch_once(
            source_config(),
            handler,
            sleep=record_sleep,
            random_value=lambda: 1.0,
        )

    assert error_info.value.category == failure_type
    assert error_info.value.attempts == 3
    assert error_info.value.retryable is True
    assert request_count == 3
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize("status_code", [408, 500, 502, 503, 504])
def test_transient_http_status_retries_then_succeeds(status_code: int) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(status_code, request=request)
        return httpx.Response(200, content=fixture_bytes("valid_rss.xml"), request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    articles = fetch_once(
        source_config(),
        handler,
        sleep=record_sleep,
        random_value=lambda: 1.0,
        clock=lambda: FIXED_TIME,
    )

    assert len(articles) == 1
    assert request_count == 2
    assert delays == [0.5]


def test_429_retry_after_seconds_takes_priority_and_is_bounded() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "120"}, request=request)
        return httpx.Response(200, content=fixture_bytes("valid_rss.xml"), request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    fetch_once(
        source_config(),
        handler,
        sleep=record_sleep,
        max_retry_delay_seconds=30,
        clock=lambda: FIXED_TIME,
    )

    assert delays == [30.0]


def test_503_retry_after_http_date_takes_priority() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "Fri, 14 Aug 2026 14:05:12 GMT"},
                request=request,
            )
        return httpx.Response(200, content=fixture_bytes("valid_rss.xml"), request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    fetch_once(
        source_config(),
        handler,
        sleep=record_sleep,
        random_value=lambda: 0.0,
        clock=lambda: FIXED_TIME,
    )

    assert delays == [12.0]


def test_invalid_retry_after_falls_back_to_bounded_backoff() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "invalid"}, request=request)
        return httpx.Response(200, content=fixture_bytes("valid_rss.xml"), request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    fetch_once(
        source_config(),
        handler,
        sleep=record_sleep,
        random_value=lambda: 1.0,
        clock=lambda: FIXED_TIME,
    )

    assert delays == [0.5]


@pytest.mark.parametrize("status_code", [302, 404])
def test_permanent_http_error_or_redirect_is_not_retried(status_code: int) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            status_code,
            headers={"Location": "https://other.example.org/feed.xml"},
            request=request,
        )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(FeedFetchError) as error_info:
        fetch_once(source_config(), handler, sleep=record_sleep)

    assert error_info.value.status_code == status_code
    assert error_info.value.retryable is False
    assert error_info.value.attempts == 1
    assert request_count == 1
    assert delays == []


def test_retryable_http_error_reports_exhausted_attempts() -> None:
    request_count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(502, request=request)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(FeedFetchError) as error_info:
        fetch_once(
            source_config(),
            handler,
            sleep=record_sleep,
            random_value=lambda: 1.0,
        )

    assert error_info.value.status_code == 502
    assert error_info.value.attempts == 3
    assert request_count == 3
    assert delays == [0.5, 1.0]
