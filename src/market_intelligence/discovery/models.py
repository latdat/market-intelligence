"""Models for the secondary discovery admission boundary (GDELT-002A).

These models are admission-boundary-internal. They deliberately do not extend or modify
the shared ``RawArticle`` / ``CanonicalArticle`` / ``ClassifiedArticle`` / ``AlertCandidate``
contracts, and no field defined here is ever written into those contracts.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.source_registry import ContentScope, Domain, Market

_MAX_PROVIDER_METADATA_KEYS = 20
_MAX_PROVIDER_METADATA_KEY_LENGTH = 64
_MAX_PROVIDER_METADATA_VALUE_LENGTH = 512


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC)


type UtcDateTime = Annotated[datetime, AfterValidator(_normalize_utc)]
type NonBlankString = Annotated[str, Field(min_length=1)]
type SourceId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$"),
]


class DiscoveryModel(BaseModel):
    """Strict immutable base for every admission-boundary model."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DiscoveryProvider(StrEnum):
    """Secondary discovery providers modeled by this boundary."""

    GDELT_DOC_2_0 = "GDELT_DOC_2_0"


class IdentityMode(StrEnum):
    """How ``article_id`` is derived for the admitted path of a routed source.

    ``CANONICAL_URL_FALLBACK`` means the admitted path has never produced a native
    ``source_item_id``, so both discovery and direct acquisition hash identity via
    ``["v1", "canonical_url", source_id, canonical_url]``. Candidates on these routes are
    potentially promotable.

    ``NATIVE_SOURCE_ITEM_ID_REQUIRED`` means the admitted direct connector produces a stable
    native ``source_item_id`` and identity is hashed via
    ``["v1", "source_item_id", source_id, normalized_source_item_id]`` instead. A discovery
    candidate carries no native publisher ID, so it can never prove it is looking at the same
    identity, and is therefore always discovery-only.

    This value is reviewed and versioned authoring metadata. Changing it for an existing route
    is an identity-semantic change requiring dedicated migration/backfill review, not a routine
    configuration edit.
    """

    CANONICAL_URL_FALLBACK = "CANONICAL_URL_FALLBACK"
    NATIVE_SOURCE_ITEM_ID_REQUIRED = "NATIVE_SOURCE_ITEM_ID_REQUIRED"


class RouteResolutionStatus(StrEnum):
    """Outcome of runtime hostname + path_prefix route matching."""

    RESOLVED = "RESOLVED"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"


class ObservationStatus(StrEnum):
    """Umbrella outcome covering both admission stages.

    ``UNKNOWN`` and ``AMBIGUOUS_ROUTE`` are route-resolution outcomes. ``RIGHTS_METADATA_DENIED``
    and ``IDENTITY_INCOMPATIBLE`` are admission-gate outcomes that only apply once a route has
    resolved. ``ADMITTED`` means the candidate passed both stages. This is one aggregate key for
    all five outcomes; there is deliberately no separate resolution-status dimension.
    """

    UNKNOWN = "UNKNOWN"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    RIGHTS_METADATA_DENIED = "RIGHTS_METADATA_DENIED"
    IDENTITY_INCOMPATIBLE = "IDENTITY_INCOMPATIBLE"
    ADMITTED = "ADMITTED"


class BenchmarkTier(StrEnum):
    """Discovery-provider capture tier for one query cell (benchmark policy v1, axis 1)."""

    GREEN_A = "GREEN_A"
    GREEN_B = "GREEN_B"
    YELLOW = "YELLOW"
    RED = "RED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class GdeltDiscoveryRole(StrEnum):
    """Role the discovery provider may play, derived from the benchmark tier alone."""

    PRIMARY = "PRIMARY"
    DISCOVERY_ACCELERATOR = "DISCOVERY_ACCELERATOR"
    PARALLEL_DISCOVERY = "PARALLEL_DISCOVERY"
    SUPPLEMENTAL = "SUPPLEMENTAL"
    NO_CHANGE = "NO_CHANGE"


class DirectPathDisposition(StrEnum):
    """What may happen to the direct acquisition path for one route."""

    MAY_REDUCE_TO_SENTINEL = "MAY_REDUCE_TO_SENTINEL"
    REQUIRED = "REQUIRED"
    REQUIRED_PRIMARY = "REQUIRED_PRIMARY"
    UNCHANGED = "UNCHANGED"


class DiscoveryQuery(DiscoveryModel):
    """One logical discovery query cell (market x domain)."""

    query_id: NonBlankString
    provider: DiscoveryProvider
    market: Market
    domain: Domain


class DiscoveryCandidate(DiscoveryModel):
    """One ephemeral provider sighting, before any publisher identity is established.

    This model deliberately carries no ``source_id``, ``article_id``, ``market``,
    ``content_scope``, or native publisher ID field. Publisher identity comes only from route
    resolution; ``market`` comes only from the resolved ``SourceConfig``; ``content_scope`` is
    reviewed route/source authoring metadata and is never inferred from candidate text.
    """

    provider: DiscoveryProvider
    query_id: NonBlankString
    observed_at: UtcDateTime
    original_url: NonBlankString
    title: str | None = None
    description: str | None = None
    published_at_raw: str | None = None
    language_hint: str | None = None
    provider_publisher_hint: str | None = None
    provider_metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider_metadata_bounds(self) -> Self:
        if len(self.provider_metadata) > _MAX_PROVIDER_METADATA_KEYS:
            raise ValueError(
                f"provider_metadata must not exceed {_MAX_PROVIDER_METADATA_KEYS} keys"
            )
        for key, value in self.provider_metadata.items():
            if not key.strip():
                raise ValueError("provider_metadata keys must not be blank")
            if len(key) > _MAX_PROVIDER_METADATA_KEY_LENGTH:
                raise ValueError(
                    f"provider_metadata key exceeds {_MAX_PROVIDER_METADATA_KEY_LENGTH} characters"
                )
            if len(value) > _MAX_PROVIDER_METADATA_VALUE_LENGTH:
                raise ValueError(
                    "provider_metadata value exceeds "
                    f"{_MAX_PROVIDER_METADATA_VALUE_LENGTH} characters"
                )
        return self


class PublisherRoute(DiscoveryModel):
    """Reviewed mapping from a publisher URL prefix to an existing source identity.

    ``path_prefix`` is a literal path prefix, never a regex or glob. ``content_scope`` is
    validated against the target ``SourceConfig`` at load time and is never a runtime matching
    input.
    """

    hostname: NonBlankString
    path_prefix: NonBlankString
    target_source_id: SourceId
    content_scope: ContentScope
    identity_mode: IdentityMode

    @model_validator(mode="after")
    def validate_authored_shape(self) -> Self:
        if self.hostname != self.hostname.casefold():
            raise ValueError("hostname must be authored in lowercase")
        if self.hostname.strip() != self.hostname or " " in self.hostname:
            raise ValueError("hostname must not contain whitespace")
        if not self.path_prefix.startswith("/"):
            raise ValueError("path_prefix must start with '/'")
        if any(character.isspace() for character in self.path_prefix):
            raise ValueError("path_prefix must not contain whitespace")
        if "?" in self.path_prefix or "#" in self.path_prefix:
            raise ValueError("path_prefix must not contain a query or fragment")
        return self


class PublisherResolution(DiscoveryModel):
    """Result of runtime route matching for one candidate."""

    status: RouteResolutionStatus
    route: PublisherRoute | None = None
    matched_route_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_invariant(self) -> Self:
        if self.status is RouteResolutionStatus.RESOLVED:
            if self.route is None or self.matched_route_count != 1:
                raise ValueError("RESOLVED requires exactly one matched route")
        elif self.status is RouteResolutionStatus.UNKNOWN:
            if self.route is not None or self.matched_route_count != 0:
                raise ValueError("UNKNOWN requires zero matched routes")
        elif self.route is not None or self.matched_route_count < 2:
            raise ValueError("AMBIGUOUS_ROUTE requires more than one matched route")
        return self


class AdmissionDecision(DiscoveryModel):
    """Whether one candidate may enter the metadata pipeline, and why not when it may not."""

    observation_status: ObservationStatus
    admitted: bool
    target_source_id: SourceId | None = None

    @model_validator(mode="after")
    def validate_decision_invariant(self) -> Self:
        expected_admitted = self.observation_status is ObservationStatus.ADMITTED
        if self.admitted is not expected_admitted:
            raise ValueError("admitted must be true exactly when observation_status is ADMITTED")
        route_resolved = self.observation_status in {
            ObservationStatus.ADMITTED,
            ObservationStatus.RIGHTS_METADATA_DENIED,
            ObservationStatus.IDENTITY_INCOMPATIBLE,
        }
        if route_resolved and self.target_source_id is None:
            raise ValueError("resolved outcomes must carry the target source identity")
        if not route_resolved and self.target_source_id is not None:
            raise ValueError("unresolved outcomes must not carry a source identity")
        return self
