"""Discovery admission boundary: route validation, route resolution, and the admission gate.

This module never performs network access, never creates or mutates ``SourceConfig`` records,
never approves rights, and never infers anything from candidate title/description text.

Resolution is split into two deliberately separate stages:

1. Load-time route validation (:func:`validate_publisher_routes`) runs once when routes are
   loaded. It checks that every ``target_source_id`` resolves to an existing ``SourceConfig``,
   that ``route.content_scope`` exactly equals that source's ``content_scope``, and that no two
   routes on one hostname have overlapping path prefixes.
2. Runtime candidate matching (:func:`resolve_publisher_route`) runs per candidate and matches
   on hostname + path prefix only. ``content_scope`` plays no part here: it was already
   enforced at load time. The query cell never participates either, because a differing
   ``source_id`` for the same URL would change ``article_id`` and break deduplication.
"""

from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from market_intelligence.discovery.models import (
    AdmissionDecision,
    DiscoveryCandidate,
    IdentityMode,
    ObservationStatus,
    PublisherResolution,
    PublisherRoute,
    RouteResolutionStatus,
)
from market_intelligence.normalization import ArticleNormalizationError, canonicalize_url
from market_intelligence.source_registry import SourceConfig


class PublisherRouteConfigError(ValueError):
    """Raised when authored publisher routes are not a valid configuration."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"invalid publisher route configuration: {detail}")


def normalize_path_for_matching(path: str) -> str:
    """Return a path in the single trailing-slash form used for prefix comparison.

    The convention is: always ensure exactly one trailing slash, on both authored
    ``path_prefix`` values and observed candidate paths. This is what makes a literal
    ``startswith`` comparison respect path segment boundaries, so ``/markets/`` matches
    ``/markets/asia/story`` but not ``/marketsummary/story``. Stripping trailing slashes
    instead would produce exactly that false match.

    Path case is preserved: URL paths may be case-sensitive on the origin server, so
    lowercasing risks both false-match and false-miss route resolution.
    """
    normalized = path if path.startswith("/") else f"/{path}"
    return normalized if normalized.endswith("/") else f"{normalized}/"


def build_source_index(sources: Sequence[SourceConfig]) -> dict[str, SourceConfig]:
    """Index sources by ``source_id``, rejecting duplicates."""
    source_by_id = {source.source_id: source for source in sources}
    if len(source_by_id) != len(sources):
        raise PublisherRouteConfigError("sources must not contain duplicate source_id values")
    return source_by_id


def validate_publisher_routes(
    routes: Sequence[PublisherRoute],
    sources: Sequence[SourceConfig],
) -> tuple[PublisherRoute, ...]:
    """Validate authored routes against the source registry, or fail.

    A route that fails any check is a configuration error: this raises rather than silently
    skipping the route, so a mis-authored route can never quietly disable admission.
    """
    source_by_id = build_source_index(sources)

    for route in routes:
        source = source_by_id.get(route.target_source_id)
        if source is None:
            raise PublisherRouteConfigError(
                f"route {route.hostname}{route.path_prefix} targets unknown source "
                f"{route.target_source_id}"
            )
        if route.content_scope is not source.content_scope:
            raise PublisherRouteConfigError(
                f"route {route.hostname}{route.path_prefix} declares content_scope "
                f"{route.content_scope} but source {route.target_source_id} is "
                f"{source.content_scope}"
            )

    _validate_no_overlapping_prefixes(routes)
    return tuple(routes)


def _validate_no_overlapping_prefixes(routes: Sequence[PublisherRoute]) -> None:
    """Reject any hostname where one route's path prefix is a prefix of another's.

    The comparison is a case-sensitive literal string check in both directions, so identical
    prefixes are rejected too. Automatic disambiguation is deliberately not attempted.
    """
    for index, route in enumerate(routes):
        normalized = normalize_path_for_matching(route.path_prefix)
        for other in routes[index + 1 :]:
            if other.hostname != route.hostname:
                continue
            other_normalized = normalize_path_for_matching(other.path_prefix)
            if normalized.startswith(other_normalized) or other_normalized.startswith(normalized):
                raise PublisherRouteConfigError(
                    f"overlapping path prefixes on {route.hostname}: "
                    f"{route.path_prefix!r} and {other.path_prefix!r}"
                )


def resolve_publisher_route(
    candidate: DiscoveryCandidate,
    routes: Sequence[PublisherRoute],
) -> PublisherResolution:
    """Match one candidate against validated routes on hostname + path prefix only.

    Zero matches resolve to ``UNKNOWN``, exactly one to ``RESOLVED``, and more than one to
    ``AMBIGUOUS_ROUTE`` — even when every match points at the same ``target_source_id``. There
    is no first-match-wins path. Load-time overlap validation should make a multi-match
    impossible; this check remains as a defensive fail-closed guard.

    A candidate URL that cannot be canonicalized resolves to ``UNKNOWN`` rather than raising,
    so one malformed provider record can never fail an entire discovery batch.
    """
    try:
        canonical_url = canonicalize_url(candidate.original_url)
    except ArticleNormalizationError:
        return PublisherResolution(status=RouteResolutionStatus.UNKNOWN, matched_route_count=0)

    parsed = urlsplit(canonical_url)
    hostname = parsed.hostname
    if hostname is None:
        return PublisherResolution(status=RouteResolutionStatus.UNKNOWN, matched_route_count=0)
    candidate_path = normalize_path_for_matching(parsed.path)

    matches = [
        route
        for route in routes
        if route.hostname == hostname
        and candidate_path.startswith(normalize_path_for_matching(route.path_prefix))
    ]

    if not matches:
        return PublisherResolution(status=RouteResolutionStatus.UNKNOWN, matched_route_count=0)
    if len(matches) > 1:
        return PublisherResolution(
            status=RouteResolutionStatus.AMBIGUOUS_ROUTE,
            matched_route_count=len(matches),
        )
    return PublisherResolution(
        status=RouteResolutionStatus.RESOLVED,
        route=matches[0],
        matched_route_count=1,
    )


def evaluate_admission(
    resolution: PublisherResolution,
    source_by_id: Mapping[str, SourceConfig],
) -> AdmissionDecision:
    """Decide whether a resolved candidate may enter the metadata pipeline.

    Gate order is route resolution, then metadata rights, then identity compatibility. Rights
    denial takes precedence: a route whose target source denies metadata storage reports
    ``RIGHTS_METADATA_DENIED`` regardless of identity mode or match quality.

    The metadata admission gate is exactly ``can_fetch and can_store_metadata``. It
    deliberately does not consult ``rights_review_status`` or ``can_ai_process``: those govern
    the separate, downstream AI authorization gate in
    ``market_intelligence.classification.classifier``, which this boundary does not touch.
    """
    if resolution.status is RouteResolutionStatus.UNKNOWN:
        return AdmissionDecision(observation_status=ObservationStatus.UNKNOWN, admitted=False)
    if resolution.status is RouteResolutionStatus.AMBIGUOUS_ROUTE:
        return AdmissionDecision(
            observation_status=ObservationStatus.AMBIGUOUS_ROUTE,
            admitted=False,
        )

    route = resolution.route
    if route is None:  # pragma: no cover - guarded by PublisherResolution invariants
        raise PublisherRouteConfigError("resolved resolution must carry a route")

    source = source_by_id.get(route.target_source_id)
    if source is None:
        raise PublisherRouteConfigError(
            f"route {route.hostname}{route.path_prefix} targets unknown source "
            f"{route.target_source_id}"
        )

    if source.rights.can_fetch is not True or source.rights.can_store_metadata is not True:
        return AdmissionDecision(
            observation_status=ObservationStatus.RIGHTS_METADATA_DENIED,
            admitted=False,
            target_source_id=route.target_source_id,
        )

    if route.identity_mode is IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED:
        # A discovery candidate carries no native publisher ID, so it cannot prove it is the
        # same identity a direct connector would hash. This is unconditional: there is no code
        # path that extracts, guesses, or synthesizes a native ID from a provider URL.
        return AdmissionDecision(
            observation_status=ObservationStatus.IDENTITY_INCOMPATIBLE,
            admitted=False,
            target_source_id=route.target_source_id,
        )

    return AdmissionDecision(
        observation_status=ObservationStatus.ADMITTED,
        admitted=True,
        target_source_id=route.target_source_id,
    )


def admit_candidate(
    candidate: DiscoveryCandidate,
    routes: Sequence[PublisherRoute],
    source_by_id: Mapping[str, SourceConfig],
) -> AdmissionDecision:
    """Resolve one candidate and evaluate the admission gate in one call."""
    return evaluate_admission(resolve_publisher_route(candidate, routes), source_by_id)
