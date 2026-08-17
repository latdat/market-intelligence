import pytest
from discovery_fixtures import (
    EDITORIAL_SOURCE_ID,
    REGULATORY_SOURCE_ID,
    RIGHTS_DENIED_SOURCE_ID,
    build_candidate,
    build_route,
    build_source,
)

from market_intelligence.discovery import (
    IdentityMode,
    ObservationStatus,
    PublisherResolution,
    PublisherRoute,
    PublisherRouteConfigError,
    RouteResolutionStatus,
    admit_candidate,
    build_source_index,
    evaluate_admission,
    normalize_path_for_matching,
    resolve_publisher_route,
)
from market_intelligence.source_registry import ContentScope, SourceConfig

EDITORIAL_ROUTE = build_route(
    "editorial.example.test",
    "/news/business/",
    EDITORIAL_SOURCE_ID,
)
NATIVE_ID_ROUTE = build_route(
    "regulator.example.test",
    "/documents/",
    REGULATORY_SOURCE_ID,
    content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
    identity_mode=IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
)
RIGHTS_DENIED_ROUTE = build_route(
    "blocked.example.test",
    "/press/",
    RIGHTS_DENIED_SOURCE_ID,
)


def source_index(sources: tuple[SourceConfig, ...]) -> dict[str, SourceConfig]:
    return build_source_index(sources)


def test_no_matching_route_resolves_to_unknown() -> None:
    resolution = resolve_publisher_route(
        build_candidate("https://unlisted.example.test/news/business/story"),
        (EDITORIAL_ROUTE,),
    )

    assert resolution.status is RouteResolutionStatus.UNKNOWN
    assert resolution.route is None
    assert resolution.matched_route_count == 0


def test_single_matching_route_resolves() -> None:
    resolution = resolve_publisher_route(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        (EDITORIAL_ROUTE, NATIVE_ID_ROUTE),
    )

    assert resolution.status is RouteResolutionStatus.RESOLVED
    assert resolution.route == EDITORIAL_ROUTE
    assert resolution.matched_route_count == 1


def test_multiple_matching_routes_are_rejected_even_for_one_target_source() -> None:
    overlapping = (
        build_route("editorial.example.test", "/news/", EDITORIAL_SOURCE_ID),
        build_route("editorial.example.test", "/news/business/", EDITORIAL_SOURCE_ID),
    )

    resolution = resolve_publisher_route(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        overlapping,
    )

    assert resolution.status is RouteResolutionStatus.AMBIGUOUS_ROUTE
    assert resolution.route is None
    assert resolution.matched_route_count == 2


def test_query_cell_never_participates_in_publisher_identity() -> None:
    url = "https://editorial.example.test/news/business/story-1"
    routes = (EDITORIAL_ROUTE,)

    us_cell = resolve_publisher_route(build_candidate(url, query_id="us_finance"), routes)
    eu_cell = resolve_publisher_route(build_candidate(url, query_id="eu_energy"), routes)

    assert us_cell == eu_cell
    assert us_cell.route is not None
    assert us_cell.route.target_source_id == EDITORIAL_SOURCE_ID


def test_route_matching_preserves_path_case() -> None:
    resolution = resolve_publisher_route(
        build_candidate("https://editorial.example.test/News/Business/story-1"),
        (EDITORIAL_ROUTE,),
    )

    assert resolution.status is RouteResolutionStatus.UNKNOWN


def test_route_matching_respects_path_segment_boundaries() -> None:
    markets_route = build_route("editorial.example.test", "/markets", EDITORIAL_SOURCE_ID)

    sibling = resolve_publisher_route(
        build_candidate("https://editorial.example.test/marketsummary/story"),
        (markets_route,),
    )
    inside = resolve_publisher_route(
        build_candidate("https://editorial.example.test/markets/asia/story"),
        (markets_route,),
    )
    exact = resolve_publisher_route(
        build_candidate("https://editorial.example.test/markets"),
        (markets_route,),
    )

    assert sibling.status is RouteResolutionStatus.UNKNOWN
    assert inside.status is RouteResolutionStatus.RESOLVED
    assert exact.status is RouteResolutionStatus.RESOLVED


def test_authored_trailing_slash_does_not_change_matching() -> None:
    with_slash = build_route("editorial.example.test", "/news/business/", EDITORIAL_SOURCE_ID)
    without_slash = build_route("editorial.example.test", "/news/business", EDITORIAL_SOURCE_ID)
    candidate = build_candidate("https://editorial.example.test/news/business/story-1")

    assert (
        resolve_publisher_route(candidate, (with_slash,)).status
        is resolve_publisher_route(candidate, (without_slash,)).status
        is RouteResolutionStatus.RESOLVED
    )


def test_hostname_case_and_default_port_are_canonicalized_before_matching() -> None:
    resolution = resolve_publisher_route(
        build_candidate("HTTPS://Editorial.Example.Test:443/news/business/story-1"),
        (EDITORIAL_ROUTE,),
    )

    assert resolution.status is RouteResolutionStatus.RESOLVED


def test_unusable_candidate_url_resolves_to_unknown_without_raising() -> None:
    resolution = resolve_publisher_route(
        build_candidate("ftp://editorial.example.test/news/business/story-1"),
        (EDITORIAL_ROUTE,),
    )

    assert resolution.status is RouteResolutionStatus.UNKNOWN


def test_normalize_path_for_matching_uses_one_trailing_slash() -> None:
    assert normalize_path_for_matching("/news") == "/news/"
    assert normalize_path_for_matching("/news/") == "/news/"
    assert normalize_path_for_matching("news/") == "/news/"


def test_unknown_publisher_is_not_admitted(fixture_sources: tuple[SourceConfig, ...]) -> None:
    decision = admit_candidate(
        build_candidate("https://unlisted.example.test/news/business/story"),
        (EDITORIAL_ROUTE,),
        source_index(fixture_sources),
    )

    assert decision.observation_status is ObservationStatus.UNKNOWN
    assert decision.admitted is False
    assert decision.target_source_id is None


def test_ambiguous_route_is_not_admitted(fixture_sources: tuple[SourceConfig, ...]) -> None:
    overlapping = (
        build_route("editorial.example.test", "/news/", EDITORIAL_SOURCE_ID),
        build_route("editorial.example.test", "/news/business/", EDITORIAL_SOURCE_ID),
    )

    decision = admit_candidate(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        overlapping,
        source_index(fixture_sources),
    )

    assert decision.observation_status is ObservationStatus.AMBIGUOUS_ROUTE
    assert decision.admitted is False
    assert decision.target_source_id is None


@pytest.mark.parametrize(
    ("can_fetch", "can_store_metadata"),
    [(False, True), (True, False), (False, False)],
)
def test_metadata_rights_denial_blocks_admission(
    can_fetch: bool,
    can_store_metadata: bool,
) -> None:
    sources = (
        build_source(
            EDITORIAL_SOURCE_ID,
            can_fetch=can_fetch,
            can_store_metadata=can_store_metadata,
        ),
    )

    decision = admit_candidate(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        (EDITORIAL_ROUTE,),
        source_index(sources),
    )

    assert decision.observation_status is ObservationStatus.RIGHTS_METADATA_DENIED
    assert decision.admitted is False
    assert decision.target_source_id == EDITORIAL_SOURCE_ID


def test_metadata_admission_succeeds_under_pending_review_without_ai_rights() -> None:
    """Regression test for the rights-gate correction.

    Metadata admission and AI authorization are separate gates. A source that may be fetched
    and whose metadata may be stored is admissible even while its rights review is PENDING and
    AI processing is denied. This asserts the general condition on a synthetic fixture and
    deliberately says nothing about the current state of the production registry.
    """
    sources = (
        build_source(
            EDITORIAL_SOURCE_ID,
            can_fetch=True,
            can_store_metadata=True,
            can_ai_process=False,
            rights_review_status="PENDING",
        ),
    )

    decision = admit_candidate(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        (EDITORIAL_ROUTE,),
        source_index(sources),
    )

    assert decision.observation_status is ObservationStatus.ADMITTED
    assert decision.admitted is True
    assert decision.target_source_id == EDITORIAL_SOURCE_ID


def test_approved_ai_rights_do_not_change_metadata_admission() -> None:
    sources = (
        build_source(
            EDITORIAL_SOURCE_ID,
            can_ai_process=True,
            rights_review_status="APPROVED",
        ),
    )

    decision = admit_candidate(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        (EDITORIAL_ROUTE,),
        source_index(sources),
    )

    assert decision.observation_status is ObservationStatus.ADMITTED


def test_native_source_item_id_routes_are_always_discovery_only(
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    urls = (
        "https://regulator.example.test/documents/2026/decision-1",
        "https://regulator.example.test/documents/2026/decision-1?id=12345",
        "https://regulator.example.test/documents/",
    )

    for url in urls:
        decision = admit_candidate(
            build_candidate(url),
            (NATIVE_ID_ROUTE,),
            source_index(fixture_sources),
        )

        assert decision.observation_status is ObservationStatus.IDENTITY_INCOMPATIBLE
        assert decision.admitted is False
        assert decision.target_source_id == REGULATORY_SOURCE_ID


def test_canonical_url_fallback_routes_are_promotable(
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    decision = admit_candidate(
        build_candidate("https://editorial.example.test/news/business/story-1"),
        (EDITORIAL_ROUTE,),
        source_index(fixture_sources),
    )

    assert decision.observation_status is ObservationStatus.ADMITTED
    assert decision.admitted is True


def test_rights_denial_takes_precedence_over_identity_incompatibility() -> None:
    native_id_route = build_route(
        "blocked.example.test",
        "/press/",
        RIGHTS_DENIED_SOURCE_ID,
        identity_mode=IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
    )
    sources = (build_source(RIGHTS_DENIED_SOURCE_ID, can_store_metadata=False),)

    decision = admit_candidate(
        build_candidate("https://blocked.example.test/press/release-1"),
        (native_id_route,),
        source_index(sources),
    )

    assert decision.observation_status is ObservationStatus.RIGHTS_METADATA_DENIED


def test_fixture_rights_denied_route_is_denied(
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    decision = admit_candidate(
        build_candidate("https://blocked.example.test/press/release-1"),
        (RIGHTS_DENIED_ROUTE,),
        source_index(fixture_sources),
    )

    assert decision.observation_status is ObservationStatus.RIGHTS_METADATA_DENIED


def test_resolved_route_targeting_an_unknown_source_fails_closed() -> None:
    resolution = PublisherResolution(
        status=RouteResolutionStatus.RESOLVED,
        route=EDITORIAL_ROUTE,
        matched_route_count=1,
    )

    with pytest.raises(PublisherRouteConfigError, match="unknown source"):
        evaluate_admission(resolution, {})


@pytest.mark.parametrize(
    ("status", "route", "count"),
    [
        (RouteResolutionStatus.RESOLVED, None, 1),
        (RouteResolutionStatus.RESOLVED, EDITORIAL_ROUTE, 2),
        (RouteResolutionStatus.UNKNOWN, EDITORIAL_ROUTE, 0),
        (RouteResolutionStatus.AMBIGUOUS_ROUTE, None, 1),
    ],
)
def test_publisher_resolution_rejects_inconsistent_states(
    status: RouteResolutionStatus,
    route: PublisherRoute | None,
    count: int,
) -> None:
    with pytest.raises(ValueError):
        PublisherResolution(status=status, route=route, matched_route_count=count)
