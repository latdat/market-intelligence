"""Offline tests for the bounded GDELT discovery runner and adaptive window algorithm."""

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from discovery_fixtures import (
    EDITORIAL_SOURCE_ID,
    REGULATORY_SOURCE_ID,
    RIGHTS_DENIED_SOURCE_ID,
    build_fixture_sources,
    build_route,
)

from market_intelligence.discovery import (
    DiscoveryProvider,
    DiscoveryQuery,
    GdeltClientConfig,
    GdeltDocClient,
    GdeltQueryConfigError,
    GdeltQuerySpec,
    IdentityMode,
    ObservationStatus,
    PublisherRoute,
    dedupe_candidates,
)
from market_intelligence.persistence import (
    DiscoveryObservation,
    DiscoveryObservationRecordResult,
    DiscoveryPersistenceError,
    DiscoveryRecordOutcome,
)
from market_intelligence.pipelines import (
    GdeltCellRunStatus,
    GdeltDiscoveryRunner,
    GdeltRunnerConfig,
    GdeltRunStopReason,
    build_observation_sample,
)
from market_intelligence.source_registry import ContentScope, Domain, Market, SourceConfig

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)

EDITORIAL_URL = "https://editorial.example.test/news/business/story"
REGULATORY_URL = "https://regulator.example.test/documents/doc-1"
BLOCKED_URL = "https://blocked.example.test/press/release-1"
UNROUTED_URL = "https://unknown.example.test/whatever/story"


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


class StepClock:
    def __init__(self, start: datetime = NOW, step: timedelta = timedelta(seconds=1)) -> None:
        self._current = start
        self._step = step
        self.reads: list[datetime] = []

    def __call__(self) -> datetime:
        value = self._current
        self._current += self._step
        self.reads.append(value)
        return value


class FakeObservationRepository:
    """In-memory stand-in for the bounded discovery aggregate boundary."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.observations: list[DiscoveryObservation] = []
        self.fail_after = fail_after
        self.attempts = 0

    def record_observation(
        self,
        observation: DiscoveryObservation,
    ) -> DiscoveryObservationRecordResult:
        self.attempts += 1
        if self.fail_after is not None and self.attempts > self.fail_after:
            raise DiscoveryPersistenceError("record_observation", observation.key.label())
        self.observations.append(observation)
        return DiscoveryObservationRecordResult(
            outcome=DiscoveryRecordOutcome.CREATED,
            observation_count=1,
            first_seen_at=observation.observed_at,
            last_seen_at=observation.observed_at,
            sample_count=0 if observation.sample is None else 1,
        )

    @property
    def statuses(self) -> list[ObservationStatus]:
        return [item.key.observation_status for item in self.observations]


def spec(
    query_id: str = "gdelt_v1_us_finance",
    *,
    domain: Domain = Domain.FINANCE,
    market: Market = Market.US,
) -> GdeltQuerySpec:
    return GdeltQuerySpec(
        discovery_query=DiscoveryQuery(
            query_id=query_id,
            provider=DiscoveryProvider.GDELT_DOC_2_0,
            market=market,
            domain=domain,
        ),
        query_expression=f"expression-for-{query_id}",
    )


def record(url: str, title: str = "Story") -> dict[str, object]:
    return {"url": url, "title": title, "domain": "editorial.example.test"}


def payload(records: Sequence[dict[str, object]]) -> bytes:
    return json.dumps({"articles": list(records)}).encode("utf-8")


def saturating_records(count: int, *, prefix: str = "sat") -> list[dict[str, object]]:
    return [record(f"{EDITORIAL_URL}-{prefix}-{index}") for index in range(count)]


def fixture_routes() -> tuple[PublisherRoute, ...]:
    return (
        build_route("editorial.example.test", "/news/business/", EDITORIAL_SOURCE_ID),
        build_route(
            "regulator.example.test",
            "/documents/",
            REGULATORY_SOURCE_ID,
            content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
            identity_mode=IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
        ),
        build_route("blocked.example.test", "/press/", RIGHTS_DENIED_SOURCE_ID),
    )


def run_discovery(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    specs: Sequence[GdeltQuerySpec] | None = None,
    routes: Sequence[PublisherRoute] | None = None,
    sources: Sequence[SourceConfig] | None = None,
    repository: FakeObservationRepository | None = None,
    client_config: GdeltClientConfig | None = None,
    runner_config: GdeltRunnerConfig | None = None,
    clock: StepClock | None = None,
    window_start: datetime | None = WINDOW_START,
    window_end: datetime | None = WINDOW_END,
) -> tuple[object, FakeObservationRepository]:
    resolved_repository = repository or FakeObservationRepository()

    async def sleep(delay: float) -> None:
        del delay

    async def run() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = GdeltDocClient(
                http_client,
                config=client_config or GdeltClientConfig(rate_limit=None),
                clock=clock or StepClock(),
                sleep=sleep,
                random_value=lambda: 0.0,
            )
            runner = GdeltDiscoveryRunner(
                client,
                specs or (spec(),),
                routes if routes is not None else fixture_routes(),
                sources or build_fixture_sources(),
                resolved_repository,
                config=runner_config,
                clock=clock or StepClock(),
            )
            return await runner.run_once(window_start=window_start, window_end=window_end)

    return asyncio.run(run()), resolved_repository


def ok(records: Sequence[dict[str, object]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload(records), request=request)

    return handler


def window_of(request: httpx.Request) -> tuple[str, str]:
    params = parse_qs(request.url.query.decode())
    return params["startdatetime"][0], params["enddatetime"][0]


# ---------------------------------------------------------------------------
# Disabled catalog (R5)
# ---------------------------------------------------------------------------


def test_runner_refuses_construction_with_an_empty_query_catalog() -> None:
    # A disabled catalog must never produce a run result that looks like an ordinary
    # successful zero-cell discovery run.
    async def build() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(ok([]))
        ) as http_client:
            GdeltDiscoveryRunner(
                GdeltDocClient(http_client),
                (),
                fixture_routes(),
                build_fixture_sources(),
                FakeObservationRepository(),
            )

    with pytest.raises(GdeltQueryConfigError, match="disabled"):
        asyncio.run(build())


# ---------------------------------------------------------------------------
# Basic cell execution
# ---------------------------------------------------------------------------


def test_admitted_candidate_is_recorded_as_an_admitted_observation() -> None:
    result, repository = run_discovery(ok([record(EDITORIAL_URL)]))

    cell = result.cell_results[0]
    assert cell.status is GdeltCellRunStatus.COMPLETE
    assert cell.request_count == 1
    assert cell.raw_result_count == 1
    assert cell.valid_record_count == 1
    assert cell.unique_candidate_count == 1
    assert cell.admitted_count == 1
    assert repository.statuses == [ObservationStatus.ADMITTED]


def test_observation_key_uses_the_four_tuple_aggregate_identity() -> None:
    clock = StepClock(start=NOW)
    _, repository = run_discovery(ok([record(EDITORIAL_URL)]), clock=clock)

    key = repository.observations[0].key
    assert key.observation_day == NOW.date()
    assert key.query_id == "gdelt_v1_us_finance"
    assert key.domain is Domain.FINANCE
    assert key.observation_status is ObservationStatus.ADMITTED


def test_empty_provider_response_records_nothing() -> None:
    result, repository = run_discovery(ok([]))

    assert result.cell_results[0].status is GdeltCellRunStatus.COMPLETE
    assert result.cell_results[0].unique_candidate_count == 0
    assert repository.observations == []


def test_runner_uses_the_lookback_window_when_none_is_supplied() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload([]), request=request)

    result, _ = run_discovery(
        handler,
        runner_config=GdeltRunnerConfig(lookback_minutes=30),
        clock=StepClock(start=NOW),
        window_start=None,
        window_end=None,
    )

    assert result.window_end == NOW
    assert result.window_start == NOW - timedelta(minutes=30)
    assert window_of(requests[0]) == ("20260818113000", "20260818120000")


# ---------------------------------------------------------------------------
# Admission integration (reused GDELT-002A boundary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (EDITORIAL_URL, ObservationStatus.ADMITTED),
        (UNROUTED_URL, ObservationStatus.UNKNOWN),
        (REGULATORY_URL, ObservationStatus.IDENTITY_INCOMPATIBLE),
        (BLOCKED_URL, ObservationStatus.RIGHTS_METADATA_DENIED),
    ],
)
def test_admission_outcomes_are_preserved_verbatim(
    url: str,
    expected: ObservationStatus,
) -> None:
    _, repository = run_discovery(ok([record(url)]))

    assert repository.statuses == [expected]


def test_ambiguous_route_is_preserved() -> None:
    # Two overlapping routes are rejected at load time; runtime multi-match remains a
    # fail-closed guard, so it is constructed directly here.
    routes = (
        build_route("editorial.example.test", "/news/", EDITORIAL_SOURCE_ID),
        build_route("editorial.example.test", "/news/business/", EDITORIAL_SOURCE_ID),
    )
    _, repository = run_discovery(ok([record(EDITORIAL_URL)]), routes=routes)

    assert repository.statuses == [ObservationStatus.AMBIGUOUS_ROUTE]


def test_query_market_never_overrides_source_identity() -> None:
    # The cell is authored as CN x ENERGY; the route still resolves to the US fixture source.
    _, repository = run_discovery(
        ok([record(EDITORIAL_URL)]),
        specs=(spec("gdelt_v1_cn_energy", market=Market.CN, domain=Domain.ENERGY),),
    )

    assert repository.statuses == [ObservationStatus.ADMITTED]
    assert repository.observations[0].key.domain is Domain.ENERGY


def test_query_id_does_not_participate_in_route_resolution() -> None:
    _, first = run_discovery(ok([record(EDITORIAL_URL)]))
    _, second = run_discovery(
        ok([record(EDITORIAL_URL)]),
        specs=(spec("gdelt_v1_us_technology", domain=Domain.TECHNOLOGY),),
    )

    assert first.statuses == second.statuses == [ObservationStatus.ADMITTED]


def test_no_native_source_item_id_is_synthesized() -> None:
    _, repository = run_discovery(ok([record(REGULATORY_URL)]))

    # A native-ID route can never be satisfied by a discovery sighting.
    assert repository.statuses == [ObservationStatus.IDENTITY_INCOMPATIBLE]


# ---------------------------------------------------------------------------
# Saturation and adaptive splitting
# ---------------------------------------------------------------------------


def test_under_limit_window_is_complete_and_never_splits() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload(saturating_records(4)), request=request)

    result, _ = run_discovery(handler, client_config=GdeltClientConfig(
        max_records_per_request=5, rate_limit=None
    ))

    assert len(requests) == 1
    assert result.cell_results[0].status is GdeltCellRunStatus.COMPLETE


def test_exactly_max_records_triggers_a_split() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            return httpx.Response(200, content=payload(saturating_records(5)), request=request)
        return httpx.Response(200, content=payload([record(EDITORIAL_URL)]), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(max_records_per_request=5, rate_limit=None),
    )

    assert len(requests) == 3  # parent + two children
    assert result.cell_results[0].status is GdeltCellRunStatus.COMPLETE


def test_split_children_cover_the_parent_range_with_an_overlap() -> None:
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        windows.append(window_of(request))
        if len(windows) == 1:
            return httpx.Response(200, content=payload(saturating_records(5)), request=request)
        return httpx.Response(200, content=payload([]), request=request)

    run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=5, split_overlap_seconds=1, rate_limit=None
        ),
    )

    parent, left, right = windows
    assert parent == ("20260818100000", "20260818110000")
    # Left ends one second AFTER the midpoint where the right child begins: overlapping, so a
    # record sitting exactly on the boundary cannot fall through it.
    assert left == ("20260818100000", "20260818103001")
    assert right == ("20260818103000", "20260818110000")
    assert left[1] > right[0]


def test_split_recursion_is_bounded_by_max_split_depth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload(saturating_records(2)), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=2,
            max_split_depth=2,
            minimum_window_seconds=2,
            rate_limit=None,
        ),
    )

    # Depth 0 -> 1 -> 2, a full binary tree of depth 2: 1 + 2 + 4 = 7 requests, then stop.
    assert len(requests) == 7
    assert result.cell_results[0].status is GdeltCellRunStatus.SATURATED_INCOMPLETE


def test_minimum_window_bound_stops_refinement() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload(saturating_records(2)), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=2,
            minimum_window_seconds=3_600,
            rate_limit=None,
        ),
    )

    assert len(requests) == 1
    assert result.cell_results[0].status is GdeltCellRunStatus.SATURATED_INCOMPLETE


def test_still_saturated_minimum_window_is_never_reported_complete() -> None:
    result, _ = run_discovery(
        ok(saturating_records(2)),
        client_config=GdeltClientConfig(
            max_records_per_request=2, minimum_window_seconds=7_200, rate_limit=None
        ),
    )

    cell = result.cell_results[0]
    assert cell.status is GdeltCellRunStatus.SATURATED_INCOMPLETE
    assert cell.status is not GdeltCellRunStatus.COMPLETE


def test_one_saturated_child_makes_the_whole_cell_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            return httpx.Response(200, content=payload(saturating_records(5)), request=request)
        if start == "20260818103000":
            # Right child stays saturated all the way down.
            return httpx.Response(200, content=payload(saturating_records(5)), request=request)
        return httpx.Response(200, content=payload([]), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=5, max_split_depth=1, rate_limit=None
        ),
    )

    assert result.cell_results[0].status is GdeltCellRunStatus.SATURATED_INCOMPLETE


# ---------------------------------------------------------------------------
# R1 — saturated-parent sightings are preserved
# ---------------------------------------------------------------------------


def test_saturated_parent_records_survive_the_split() -> None:
    # The parent's records are real sightings already made; refinement must not discard them.
    parent_only_url = f"{EDITORIAL_URL}-parent-only"

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            records = saturating_records(4)
            records.append(record(parent_only_url))
            return httpx.Response(200, content=payload(records), request=request)
        return httpx.Response(200, content=payload([]), request=request)

    result, repository = run_discovery(
        handler,
        client_config=GdeltClientConfig(max_records_per_request=5, rate_limit=None),
    )

    sampled_urls = {item.sample.url for item in repository.observations if item.sample}
    assert parent_only_url in sampled_urls
    assert result.cell_results[0].unique_candidate_count == 5


def test_duplicate_across_parent_and_child_keeps_the_earlier_parent_sighting() -> None:
    # Recursive refinement must never push an article's first-seen time later.
    shared_url = f"{EDITORIAL_URL}-shared"
    clock = StepClock(start=NOW, step=timedelta(seconds=10))

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            records = saturating_records(4)
            records.append(record(shared_url))
            return httpx.Response(200, content=payload(records), request=request)
        return httpx.Response(200, content=payload([record(shared_url)]), request=request)

    _, repository = run_discovery(
        handler,
        client_config=GdeltClientConfig(max_records_per_request=5, rate_limit=None),
        clock=clock,
    )

    shared = [
        item
        for item in repository.observations
        if item.sample is not None and item.sample.url == shared_url
    ]
    assert len(shared) == 1
    # The runner clock reads first, then the parent response, then each child response.
    assert shared[0].observed_at == NOW + timedelta(seconds=10)


def test_counters_separate_physical_records_from_unique_candidates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            return httpx.Response(200, content=payload(saturating_records(5)), request=request)
        return httpx.Response(200, content=payload(saturating_records(5)), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=5, max_split_depth=1, rate_limit=None
        ),
    )

    cell = result.cell_results[0]
    assert cell.request_count == 3
    # Physical provider records across every request, before dedupe.
    assert cell.raw_result_count == 15
    assert cell.valid_record_count == 15
    # The same five URLs were returned three times.
    assert cell.unique_candidate_count == 5


# ---------------------------------------------------------------------------
# R2 — saturation is judged before record validation
# ---------------------------------------------------------------------------


def test_saturation_uses_the_physical_record_count_not_the_valid_count() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            # Exactly max_records physical entries, but two of them are malformed.
            records: list[dict[str, object]] = saturating_records(3)
            records.extend([{"title": "no url"}, {"title": "also no url"}])
            return httpx.Response(200, content=payload(records), request=request)
        return httpx.Response(200, content=payload([]), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(max_records_per_request=5, rate_limit=None),
    )

    cell = result.cell_results[0]
    assert len(requests) == 3  # split still happened despite only 3 valid records
    assert cell.raw_result_count == 5
    assert cell.valid_record_count == 3
    assert cell.invalid_record_count == 2


def test_post_dedupe_count_never_drives_splitting() -> None:
    requests: list[httpx.Request] = []
    duplicate = record(f"{EDITORIAL_URL}-dup")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            # Five physical entries that all dedupe down to one candidate.
            return httpx.Response(200, content=payload([duplicate] * 5), request=request)
        return httpx.Response(200, content=payload([]), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(max_records_per_request=5, rate_limit=None),
    )

    assert len(requests) == 3
    assert result.cell_results[0].unique_candidate_count == 1


# ---------------------------------------------------------------------------
# R3 — second-precision split progress
# ---------------------------------------------------------------------------


def test_tiny_saturated_window_stops_without_recursing() -> None:
    # A window at the minimum bound must stop rather than spin, even at max split depth.
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload(saturating_records(2)), request=request)

    result, _ = run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=2,
            minimum_window_seconds=2,
            split_overlap_seconds=1,
            max_split_depth=8,
            rate_limit=None,
        ),
        window_start=datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 8, 18, 10, 0, 2, tzinfo=UTC),
    )

    assert len(requests) == 1
    assert result.cell_results[0].status is GdeltCellRunStatus.SATURATED_INCOMPLETE


def split_boundaries(
    start: datetime,
    end: datetime,
    *,
    minimum_window_seconds: int = 2,
    split_overlap_seconds: int = 1,
) -> tuple[datetime, datetime] | None:
    client = GdeltDocClient(
        config=GdeltClientConfig(
            minimum_window_seconds=minimum_window_seconds,
            split_overlap_seconds=split_overlap_seconds,
            rate_limit=None,
        )
    )
    return client._split_boundaries(start, end)


@pytest.mark.parametrize("span_seconds", [1, 2])
def test_split_boundaries_refuse_to_collapse_at_provider_precision(span_seconds: int) -> None:
    # White-box coverage of the fail-closed guard. At one-second provider precision these
    # windows cannot yield two strictly smaller formatted child windows, so the guard must
    # return None instead of recursing without progress.
    start = datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC)

    assert split_boundaries(start, start + timedelta(seconds=span_seconds)) is None


def test_split_boundaries_make_strict_progress_on_an_odd_span() -> None:
    start = datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=5)

    boundaries = split_boundaries(start, end)

    assert boundaries is not None
    left_end, right_start = boundaries
    # Both children are strictly shorter than the parent, and they overlap rather than abut.
    assert (left_end - start) < (end - start)
    assert (end - right_start) < (end - start)
    assert left_end > right_start


def test_split_boundaries_guard_is_defense_in_depth_below_the_minimum_window() -> None:
    # The config validator (overlap < minimum_window, minimum_window >= 2) means the runner's
    # minimum-window check normally stops refinement first. This guard exists so a future
    # bound change cannot reintroduce a non-progressing split.
    start = datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC)

    assert split_boundaries(start, start + timedelta(seconds=3)) is not None
    assert split_boundaries(start, start + timedelta(seconds=2)) is None


def test_odd_second_window_still_makes_strict_progress() -> None:
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        windows.append(window_of(request))
        if len(windows) == 1:
            return httpx.Response(200, content=payload(saturating_records(2)), request=request)
        return httpx.Response(200, content=payload([]), request=request)

    run_discovery(
        handler,
        client_config=GdeltClientConfig(
            max_records_per_request=2,
            minimum_window_seconds=2,
            split_overlap_seconds=0,
            max_split_depth=4,
            rate_limit=None,
        ),
        window_start=datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 8, 18, 10, 0, 5, tzinfo=UTC),
    )

    parent, left, right = windows
    assert parent == ("20260818100000", "20260818100005")
    assert left == ("20260818100000", "20260818100002")
    assert right == ("20260818100002", "20260818100005")
    # Both children are strictly smaller than the parent at provider precision.
    assert left != parent and right != parent


def test_sub_second_boundaries_are_normalized_before_splitting() -> None:
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        windows.append(window_of(request))
        return httpx.Response(200, content=payload([]), request=request)

    run_discovery(
        handler,
        window_start=datetime(2026, 8, 18, 10, 0, 0, 900_000, tzinfo=UTC),
        window_end=datetime(2026, 8, 18, 10, 30, 0, 100_000, tzinfo=UTC),
    )

    assert windows[0] == ("20260818100000", "20260818103000")


# ---------------------------------------------------------------------------
# R4 — per-response observation clock
# ---------------------------------------------------------------------------


def test_all_candidates_from_one_response_share_one_observation_time() -> None:
    clock = StepClock(start=NOW, step=timedelta(seconds=5))
    _, repository = run_discovery(
        ok([record(f"{EDITORIAL_URL}-{index}") for index in range(3)]),
        clock=clock,
    )

    observed = {item.observed_at for item in repository.observations}
    assert len(observed) == 1


def test_separate_responses_receive_separate_observation_times() -> None:
    clock = StepClock(start=NOW, step=timedelta(seconds=5))

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = window_of(request)
        if (start, end) == ("20260818100000", "20260818110000"):
            return httpx.Response(200, content=payload(saturating_records(5)), request=request)
        return httpx.Response(
            200, content=payload([record(f"{EDITORIAL_URL}-child-{start}")]), request=request
        )

    _, repository = run_discovery(
        handler,
        client_config=GdeltClientConfig(max_records_per_request=5, rate_limit=None),
        clock=clock,
    )

    observed = {item.observed_at for item in repository.observations}
    assert len(observed) == 3  # parent response plus two child responses


# ---------------------------------------------------------------------------
# Dedupe semantics
# ---------------------------------------------------------------------------


def test_repeated_canonical_url_in_one_cell_is_processed_once() -> None:
    result, repository = run_discovery(ok([record(EDITORIAL_URL), record(EDITORIAL_URL)]))

    assert result.cell_results[0].raw_result_count == 2
    assert result.cell_results[0].unique_candidate_count == 1
    assert len(repository.observations) == 1


def test_tracking_parameter_variants_collapse_per_existing_url_policy() -> None:
    result, _ = run_discovery(
        ok([record(EDITORIAL_URL), record(f"{EDITORIAL_URL}?utm_source=newsletter")])
    )

    assert result.cell_results[0].unique_candidate_count == 1


def test_uncanonicalizable_url_uses_a_verbatim_fallback_and_is_not_merged() -> None:
    result, _ = run_discovery(
        ok([record("ftp://bad.example.test/a"), record("ftp://bad.example.test/b")])
    )

    assert result.cell_results[0].unique_candidate_count == 2


def test_same_url_in_two_cells_is_not_globally_suppressed() -> None:
    result, repository = run_discovery(
        ok([record(EDITORIAL_URL)]),
        specs=(
            spec("gdelt_v1_us_finance", domain=Domain.FINANCE),
            spec("gdelt_v1_us_technology", domain=Domain.TECHNOLOGY),
        ),
    )

    # Two legitimate discovery-cell observations for the same publisher URL.
    assert len(result.cell_results) == 2
    assert len(repository.observations) == 2
    assert {item.key.query_id for item in repository.observations} == {
        "gdelt_v1_us_finance",
        "gdelt_v1_us_technology",
    }
    assert {item.key.domain for item in repository.observations} == {
        Domain.FINANCE,
        Domain.TECHNOLOGY,
    }


def test_dedupe_helper_keeps_first_position_and_earliest_observation() -> None:
    from market_intelligence.discovery import DiscoveryCandidate

    def candidate(url: str, observed_at: datetime) -> DiscoveryCandidate:
        return DiscoveryCandidate(
            provider=DiscoveryProvider.GDELT_DOC_2_0,
            query_id="gdelt_v1_us_finance",
            observed_at=observed_at,
            original_url=url,
        )

    later = candidate(EDITORIAL_URL, NOW + timedelta(minutes=5))
    other = candidate(f"{EDITORIAL_URL}-2", NOW + timedelta(minutes=6))
    earlier = candidate(EDITORIAL_URL, NOW)

    result = dedupe_candidates([later, other, earlier])

    assert [item.original_url for item in result] == [EDITORIAL_URL, f"{EDITORIAL_URL}-2"]
    assert result[0].observed_at == NOW


# ---------------------------------------------------------------------------
# R8 — best-effort locator sampling
# ---------------------------------------------------------------------------


def test_sample_is_locator_only() -> None:
    _, repository = run_discovery(ok([record(EDITORIAL_URL, title="Sensitive headline")]))

    sample = repository.observations[0].sample
    assert sample is not None
    assert sample.url == EDITORIAL_URL
    assert sample.hostname == "editorial.example.test"
    assert set(sample.model_dump()) == {"url", "hostname"}


def test_non_canonicalizable_url_persists_an_unknown_observation_without_a_sample() -> None:
    # Diagnostic sampling is best-effort and must never fail a run or drop a real sighting.
    result, repository = run_discovery(ok([record("ftp://bad.example.test/story")]))

    assert len(repository.observations) == 1
    assert repository.observations[0].key.observation_status is ObservationStatus.UNKNOWN
    assert repository.observations[0].sample is None
    assert result.cell_results[0].unknown_count == 1


def test_build_observation_sample_returns_none_for_unusable_locators() -> None:
    from market_intelligence.discovery import DiscoveryCandidate

    candidate = DiscoveryCandidate(
        provider=DiscoveryProvider.GDELT_DOC_2_0,
        query_id="gdelt_v1_us_finance",
        observed_at=NOW,
        original_url="not a url at all",
    )

    assert build_observation_sample(candidate) is None


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------


def test_invalid_route_configuration_fails_before_any_http_call() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload([]), request=request)

    with pytest.raises(Exception, match="duplicate source_id"):
        run_discovery(
            handler,
            sources=(*build_fixture_sources(), *build_fixture_sources()),
        )

    assert requests == []


def test_query_rejection_fails_one_cell_and_later_cells_still_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        if params["query"][0] == "expression-for-gdelt_v1_us_finance":
            return httpx.Response(400, request=request)
        return httpx.Response(200, content=payload([record(EDITORIAL_URL)]), request=request)

    result, repository = run_discovery(
        handler,
        specs=(
            spec("gdelt_v1_us_finance", domain=Domain.FINANCE),
            spec("gdelt_v1_us_technology", domain=Domain.TECHNOLOGY),
        ),
    )

    assert result.cell_results[0].status is GdeltCellRunStatus.QUERY_REJECTED
    assert result.cell_results[1].status is GdeltCellRunStatus.COMPLETE
    assert result.stop_reason is None
    assert repository.statuses == [ObservationStatus.ADMITTED]


def test_systemic_provider_failure_stops_before_the_remaining_cells() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, request=request)

    result, _ = run_discovery(
        handler,
        specs=(
            spec("gdelt_v1_us_finance", domain=Domain.FINANCE),
            spec("gdelt_v1_us_technology", domain=Domain.TECHNOLOGY),
            spec("gdelt_v1_us_energy", domain=Domain.ENERGY),
        ),
        client_config=GdeltClientConfig(max_attempts=2, rate_limit=None),
    )

    assert len(result.cell_results) == 1
    assert result.cell_results[0].status is GdeltCellRunStatus.PROVIDER_FAILED
    assert result.stop_reason is GdeltRunStopReason.PROVIDER_UNAVAILABLE
    # Bounded retries for the first cell only; the other two were never attempted.
    assert len(requests) == 2


def test_malformed_payload_becomes_a_provider_failure() -> None:
    result, _ = run_discovery(
        lambda request: httpx.Response(200, content=b"{}", request=request),
        client_config=GdeltClientConfig(max_attempts=1, rate_limit=None),
    )

    assert result.cell_results[0].status is GdeltCellRunStatus.PROVIDER_FAILED
    assert result.stop_reason is GdeltRunStopReason.PROVIDER_UNAVAILABLE


def test_persistence_failure_stops_the_run_and_earlier_writes_remain() -> None:
    repository = FakeObservationRepository(fail_after=2)

    with pytest.raises(DiscoveryPersistenceError):
        run_discovery(
            ok([record(f"{EDITORIAL_URL}-{index}") for index in range(4)]),
            repository=repository,
        )

    # No run-wide transaction: aggregates folded before the failure stay durable.
    assert len(repository.observations) == 2


def test_run_result_aggregates_are_deterministic() -> None:
    result, _ = run_discovery(
        ok([record(EDITORIAL_URL), record(UNROUTED_URL), record(BLOCKED_URL)]),
    )

    cell = result.cell_results[0]
    assert cell.admitted_count == 1
    assert cell.unknown_count == 1
    assert cell.rights_metadata_denied_count == 1
    assert cell.ambiguous_route_count == 0
    assert cell.identity_incompatible_count == 0
    assert result.admitted_count == 1
    assert result.unique_candidate_count == 3
    assert result.cells_attempted == 1


def test_provider_execution_status_is_separate_from_observation_status() -> None:
    assert {status.value for status in GdeltCellRunStatus} == {
        "COMPLETE",
        "SATURATED_INCOMPLETE",
        "QUERY_REJECTED",
        "PROVIDER_FAILED",
    }
    # Provider execution failures never leak into the admission-outcome vocabulary.
    assert {status.value for status in ObservationStatus} == {
        "UNKNOWN",
        "AMBIGUOUS_ROUTE",
        "RIGHTS_METADATA_DENIED",
        "IDENTITY_INCOMPATIBLE",
        "ADMITTED",
    }


def test_packages_import_cleanly_in_any_order() -> None:
    """Regression guard: the runner lives in ``pipelines`` to keep the dependency acyclic.

    ``persistence.discovery_observations`` imports ``discovery.models``, so a runner inside the
    ``discovery`` package would close a cycle that only shows up when ``persistence`` is
    imported first.
    """
    import subprocess
    import sys

    orders = (
        "import market_intelligence.persistence, market_intelligence.discovery",
        "import market_intelligence.discovery, market_intelligence.persistence",
        "import market_intelligence.pipelines",
    )
    for order in orders:
        completed = subprocess.run(
            [sys.executable, "-c", order],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"{order} failed: {completed.stderr}"
