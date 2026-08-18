"""Offline tests for the GDELT DOC 2.0 adapter: request contract, payloads, retries, mapping."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from market_intelligence.discovery import (
    GDELT_DOC_ENDPOINT,
    DiscoveryProvider,
    DiscoveryQuery,
    GdeltClientConfig,
    GdeltDocClient,
    GdeltProviderError,
    GdeltQueryRejectedError,
    GdeltQuerySpec,
    GdeltWindowStatus,
    format_gdelt_timestamp,
)
from market_intelligence.source_registry import Domain, Market, RateLimitConfig

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def query_spec(query_id: str = "gdelt_v1_us_finance") -> GdeltQuerySpec:
    return GdeltQuerySpec(
        discovery_query=DiscoveryQuery(
            query_id=query_id,
            provider=DiscoveryProvider.GDELT_DOC_2_0,
            market=Market.US,
            domain=Domain.FINANCE,
        ),
        query_expression="fixture-expression",
    )


def article(index: int, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "url": f"https://editorial.example.test/news/business/story-{index}",
        "title": f"Story {index}",
        "domain": "editorial.example.test",
        "language": "English",
        "sourcecountry": "United States",
        "seendate": "20260818T103000Z",
    }
    record.update(overrides)
    return record


def payload(records: list[dict[str, object]]) -> bytes:
    return json.dumps({"articles": records}).encode("utf-8")


class StepClock:
    """Injected clock that advances one second per read and records every read."""

    def __init__(self, start: datetime = NOW, step: timedelta = timedelta(seconds=1)) -> None:
        self._current = start
        self._step = step
        self.reads: list[datetime] = []

    def __call__(self) -> datetime:
        value = self._current
        self._current += self._step
        self.reads.append(value)
        return value


def client_config(**overrides: object) -> GdeltClientConfig:
    """Client config with pacing disabled, so every observed sleep is a retry sleep."""
    values: dict[str, object] = {"rate_limit": None}
    values.update(overrides)
    return GdeltClientConfig(**values)  # type: ignore[arg-type]


def build_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: StepClock | None = None,
    config: GdeltClientConfig | None = None,
    sleeps: list[float] | None = None,
) -> tuple[GdeltDocClient, httpx.AsyncClient, StepClock]:
    resolved_clock = clock or StepClock()
    recorded = sleeps if sleeps is not None else []

    async def sleep(delay: float) -> None:
        recorded.append(delay)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = GdeltDocClient(
        http_client,
        config=config or client_config(),
        clock=resolved_clock,
        sleep=sleep,
        random_value=lambda: 1.0,
    )
    return client, http_client, resolved_clock


def fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    spec: GdeltQuerySpec | None = None,
    clock: StepClock | None = None,
    config: GdeltClientConfig | None = None,
    sleeps: list[float] | None = None,
    window_start: datetime = WINDOW_START,
    window_end: datetime = WINDOW_END,
) -> object:
    async def run() -> object:
        client, http_client, _ = build_client(
            handler, clock=clock, config=config, sleeps=sleeps
        )
        async with http_client:
            return await client.fetch_cell(spec or query_spec(), window_start, window_end)

    return asyncio.run(run())


def ok(records: list[dict[str, object]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload(records), request=request)

    return handler


def status_handler(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers or {}, request=request)

    return handler


# ---------------------------------------------------------------------------
# Request contract
# ---------------------------------------------------------------------------


def test_request_uses_the_exact_https_endpoint_and_bounded_parameters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload([article(1)]), request=request)

    fetch(handler)

    assert len(requests) == 1
    url = requests[0].url
    assert str(url).startswith(GDELT_DOC_ENDPOINT)
    assert urlsplit(str(url)).scheme == "https"

    params = parse_qs(url.query.decode())
    assert params["mode"] == ["artlist"]
    assert params["format"] == ["json"]
    assert params["sort"] == ["dateasc"]
    assert params["maxrecords"] == ["250"]
    assert params["query"] == ["fixture-expression"]
    assert params["startdatetime"] == ["20260818100000"]
    assert params["enddatetime"] == ["20260818110000"]


def test_window_boundaries_are_normalized_to_whole_utc_seconds() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload([]), request=request)

    fetch(
        handler,
        window_start=datetime(2026, 8, 18, 10, 0, 0, 750_000, tzinfo=UTC),
        window_end=datetime(2026, 8, 18, 11, 0, 0, 250_000, tzinfo=UTC),
    )

    params = parse_qs(requests[0].url.query.decode())
    assert params["startdatetime"] == ["20260818100000"]
    assert params["enddatetime"] == ["20260818110000"]


def test_naive_window_boundaries_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone information"):
        fetch(ok([]), window_start=datetime(2026, 8, 18, 10, 0))  # noqa: DTZ001


def test_inverted_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="earlier than"):
        fetch(ok([]), window_start=WINDOW_END, window_end=WINDOW_START)


def test_configured_timeout_is_applied() -> None:
    timeouts: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions.get("timeout"))
        return httpx.Response(200, content=payload([]), request=request)

    fetch(handler, config=client_config(request_timeout_seconds=7.0))

    assert timeouts[0] == {"connect": 7.0, "pool": 7.0, "read": 7.0, "write": 7.0}


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_successful_mapping_populates_only_safe_fields() -> None:
    outcome = fetch(ok([article(1)]))

    assert outcome.status is GdeltWindowStatus.COMPLETE
    assert outcome.provider_record_count == 1
    assert outcome.valid_record_count == 1
    assert outcome.invalid_record_count == 0

    candidate = outcome.candidates[0]
    assert candidate.provider is DiscoveryProvider.GDELT_DOC_2_0
    assert candidate.query_id == "gdelt_v1_us_finance"
    assert candidate.original_url == "https://editorial.example.test/news/business/story-1"
    assert candidate.title == "Story 1"
    assert candidate.language_hint == "English"
    assert candidate.provider_publisher_hint == "editorial.example.test"
    assert candidate.description is None


def test_provider_sighting_time_never_becomes_published_at_raw() -> None:
    outcome = fetch(ok([article(1, seendate="20260818T103000Z")]))
    candidate = outcome.candidates[0]

    # seendate is GDELT crawl/index time, not a publisher-attributed publication timestamp.
    assert candidate.published_at_raw is None
    assert candidate.provider_metadata["provider_seendate"] == "20260818T103000Z"


def test_source_country_is_bounded_metadata_and_never_market() -> None:
    outcome = fetch(ok([article(1, sourcecountry="United States")]))
    candidate = outcome.candidates[0]

    assert candidate.provider_metadata["sourcecountry"] == "United States"
    assert not hasattr(candidate, "market")


def test_observed_at_uses_the_injected_clock_not_the_provider_timestamp() -> None:
    clock = StepClock(start=NOW)
    outcome = fetch(ok([article(1)]), clock=clock)

    assert outcome.candidates[0].observed_at == NOW


def test_clock_is_read_once_per_physical_response_not_once_per_article() -> None:
    # Parsing-loop position must never manufacture discovery latency between articles that
    # arrived in the same response.
    clock = StepClock(start=NOW)
    outcome = fetch(ok([article(1), article(2), article(3)]), clock=clock)

    assert len(clock.reads) == 1
    assert {candidate.observed_at for candidate in outcome.candidates} == {NOW}


def test_overlong_metadata_values_are_truncated() -> None:
    outcome = fetch(ok([article(1, sourcecountry="x" * 900)]))

    assert len(outcome.candidates[0].provider_metadata["sourcecountry"]) == 512


# ---------------------------------------------------------------------------
# Malformed individual records (R2 / R8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_record",
    [
        {"title": "no url"},
        {"url": "", "title": "blank url"},
        {"url": "   ", "title": "whitespace url"},
        {"url": 42, "title": "non-string url"},
        "not-a-mapping",
    ],
)
def test_one_malformed_record_is_skipped_without_failing_the_response(
    bad_record: object,
) -> None:
    outcome = fetch(ok([article(1), bad_record, article(2)]))  # type: ignore[list-item]

    assert outcome.provider_record_count == 3
    assert outcome.valid_record_count == 2
    assert outcome.invalid_record_count == 1
    assert len(outcome.candidates) == 2


def test_non_canonicalizable_but_non_blank_url_is_a_valid_candidate() -> None:
    # Route resolution already maps such a candidate to UNKNOWN; it is a real sighting, not a
    # malformed record.
    outcome = fetch(ok([article(1, url="ftp://not-http.example.test/story")]))

    assert outcome.invalid_record_count == 0
    assert outcome.valid_record_count == 1
    assert outcome.candidates[0].original_url == "ftp://not-http.example.test/story"


def test_a_response_of_only_malformed_records_is_still_a_successful_window() -> None:
    outcome = fetch(ok([{"title": "no url"}, {"title": "also no url"}]))

    assert outcome.status is GdeltWindowStatus.COMPLETE
    assert outcome.provider_record_count == 2
    assert outcome.invalid_record_count == 2
    assert outcome.candidates == ()


# ---------------------------------------------------------------------------
# Payload schema, fail-closed (R6)
# ---------------------------------------------------------------------------


def test_empty_articles_list_is_a_successful_zero_result_window() -> None:
    outcome = fetch(ok([]))

    assert outcome.status is GdeltWindowStatus.COMPLETE
    assert outcome.provider_record_count == 0
    assert outcome.candidates == ()


def test_bare_empty_object_is_a_schema_failure_not_an_assumed_empty_result() -> None:
    # Not assumed to be a legitimate zero-result response without provider evidence.
    with pytest.raises(GdeltProviderError) as error:
        fetch(lambda request: httpx.Response(200, content=b"{}", request=request))

    assert error.value.category == "malformed_payload"


@pytest.mark.parametrize(
    "body",
    [b"not-json{{{", b"[1, 2, 3]", b'{"articles": "not-a-list"}', b'{"count": 0}', b""],
)
def test_malformed_payloads_are_provider_failures(body: bytes) -> None:
    with pytest.raises(GdeltProviderError) as error:
        fetch(lambda request: httpx.Response(200, content=body, request=request))

    assert error.value.category == "malformed_payload"


def test_malformed_payload_is_retried_before_failing() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"{}", request=request)

    with pytest.raises(GdeltProviderError):
        fetch(handler, config=client_config(max_attempts=3))

    assert attempts == 3


def test_transient_malformed_payload_recovers_on_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, content=b"{}", request=request)
        return httpx.Response(200, content=payload([article(1)]), request=request)

    outcome = fetch(handler)

    assert attempts == 2
    assert outcome.valid_record_count == 1


# ---------------------------------------------------------------------------
# Retry taxonomy (R7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried_then_reported_as_provider_failures(
    status_code: int,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, request=request)

    with pytest.raises(GdeltProviderError) as error:
        fetch(handler, config=client_config(max_attempts=3))

    assert attempts == 3
    assert error.value.category == "http_status"
    assert error.value.status_code == status_code
    assert error.value.attempts == 3


def test_transient_status_recovers_within_the_retry_budget() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=payload([article(1)]), request=request)

    outcome = fetch(handler)

    assert attempts == 3
    assert outcome.valid_record_count == 1


def test_retry_after_seconds_header_is_honored() -> None:
    sleeps: list[float] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(200, content=payload([]), request=request)

    fetch(handler, sleeps=sleeps)

    assert sleeps == [7.0]


def test_retry_after_is_capped_by_the_configured_maximum() -> None:
    sleeps: list[float] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "99999"}, request=request)
        return httpx.Response(200, content=payload([]), request=request)

    fetch(handler, sleeps=sleeps, config=client_config(max_retry_delay_seconds=30.0))

    assert sleeps == [30.0]


def test_rate_limit_without_retry_after_uses_bounded_backoff() -> None:
    sleeps: list[float] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, request=request)
        return httpx.Response(200, content=payload([]), request=request)

    fetch(handler, sleeps=sleeps, config=client_config(base_retry_delay_seconds=0.5))

    assert sleeps == [0.5]


@pytest.mark.parametrize("status_code", [400, 422])
def test_query_rejection_statuses_are_cell_scoped_and_never_retried(status_code: int) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, request=request)

    with pytest.raises(GdeltQueryRejectedError) as error:
        fetch(handler)

    assert attempts == 1
    assert error.value.status_code == status_code
    assert error.value.query_id == "gdelt_v1_us_finance"


@pytest.mark.parametrize("status_code", [401, 403, 404, 418, 501])
def test_other_non_2xx_statuses_fail_conservatively_without_retrying(status_code: int) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, request=request)

    with pytest.raises(GdeltProviderError) as error:
        fetch(handler)

    assert attempts == 1
    assert error.value.category == "http_status"
    assert error.value.status_code == status_code


def test_timeouts_are_retried_then_reported() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(GdeltProviderError) as error:
        fetch(handler, config=client_config(max_attempts=2))

    assert attempts == 2
    assert error.value.category == "timeout"


def test_network_errors_are_retried_then_reported() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(GdeltProviderError) as error:
        fetch(handler, config=client_config(max_attempts=2))

    assert attempts == 2
    assert error.value.category == "connection"


def test_provider_errors_stay_sanitized() -> None:
    with pytest.raises(GdeltProviderError) as error:
        fetch(status_handler(403))

    message = str(error.value)
    assert "editorial.example.test" not in message
    assert "fixture-expression" not in message


# ---------------------------------------------------------------------------
# Config bounds
# ---------------------------------------------------------------------------


def test_split_overlap_must_be_smaller_than_the_minimum_window() -> None:
    with pytest.raises(ValueError, match="smaller than minimum_window_seconds"):
        GdeltClientConfig(minimum_window_seconds=60, split_overlap_seconds=60)


def test_max_records_is_bounded() -> None:
    with pytest.raises(ValueError):
        GdeltClientConfig(max_records_per_request=1_000)


def test_timestamp_formatting_is_second_precision_utc() -> None:
    value = datetime(2026, 8, 18, 11, 30, 45, 987_654, tzinfo=UTC)

    assert format_gdelt_timestamp(value) == "20260818113045"


def test_self_imposed_pacing_spaces_consecutive_requests() -> None:
    # Conservative politeness pacing, not a claimed GDELT quota.
    sleeps: list[float] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=payload([]), request=request)

    fetch(
        handler,
        sleeps=sleeps,
        config=GdeltClientConfig(
            base_retry_delay_seconds=0.0,
            rate_limit=RateLimitConfig(max_requests=1, period_seconds=2),
        ),
    )

    assert 2.0 in sleeps
