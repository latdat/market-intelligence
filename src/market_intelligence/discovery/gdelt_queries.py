"""Reviewed, versioned GDELT DOC 2.0 query catalog (GDELT-002B).

``DiscoveryQuery`` stays provider-neutral: it names one logical ``(market, domain)`` discovery
cell and never carries provider syntax. ``GdeltQuerySpec`` is the provider-specific pairing of
that logical cell with one reviewed GDELT query expression.

Query-ID versioning invariant
-----------------------------
``query_id`` is part of the durable ``discovery_observation_daily`` aggregate key. A material
change to what a query *means* must therefore be published under a **new** versioned
``query_id`` (``gdelt_v1_us_finance`` -> ``gdelt_v2_us_finance``), never by editing an
already-used ID in place. Doing otherwise would silently blend two different populations into
one historical aggregate series. No ``query_version`` column is introduced for this; the
version lives in the authored ID.

Market semantics are never derived
----------------------------------
The logical cell ``US x FINANCE`` does not mean "publisher country is US". No code here maps a
``Market`` to provider syntax such as ``sourcecountry:US``. Every expression is reviewed
authored configuration.

Expected file shape::

    [[queries]]
    query_id = "gdelt_v1_us_finance"
    provider = "GDELT_DOC_2_0"
    market = "US"
    domain = "FINANCE"
    query_expression = "<reviewed expression>"
"""

import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from market_intelligence.discovery.models import (
    DiscoveryModel,
    DiscoveryProvider,
    DiscoveryQuery,
    NonBlankString,
)
from market_intelligence.source_registry import Domain, Market

MAX_QUERY_EXPRESSION_LENGTH = 512

_QUERIES_KEY = "queries"


def _reject_unsafe_expression(value: str) -> str:
    if not value.strip():
        raise ValueError("query_expression must not be blank")
    if any(character < " " or character == "\x7f" for character in value):
        raise ValueError("query_expression must not contain control characters")
    return value


type QueryExpression = Annotated[
    str,
    Field(min_length=1, max_length=MAX_QUERY_EXPRESSION_LENGTH),
    AfterValidator(_reject_unsafe_expression),
]


class GdeltQueryConfigError(ValueError):
    """Raised when an authored GDELT query catalog is not a valid configuration."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"invalid GDELT query configuration: {detail}")


class GdeltQuerySpec(DiscoveryModel):
    """One reviewed GDELT expression bound to one provider-neutral discovery cell."""

    discovery_query: DiscoveryQuery
    query_expression: QueryExpression

    @model_validator(mode="after")
    def validate_provider(self) -> Self:
        if self.discovery_query.provider is not DiscoveryProvider.GDELT_DOC_2_0:
            raise ValueError("GdeltQuerySpec requires the GDELT_DOC_2_0 provider")
        return self

    @property
    def query_id(self) -> str:
        return self.discovery_query.query_id

    @property
    def market(self) -> Market:
        return self.discovery_query.market

    @property
    def domain(self) -> Domain:
        return self.discovery_query.domain


class _GdeltQueryEntry(BaseModel):
    """Flat authoring shape; the loader assembles the nested runtime model from it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    query_id: NonBlankString
    provider: DiscoveryProvider
    market: Market
    domain: Domain
    query_expression: QueryExpression

    def to_spec(self) -> GdeltQuerySpec:
        return GdeltQuerySpec(
            discovery_query=DiscoveryQuery(
                query_id=self.query_id,
                provider=self.provider,
                market=self.market,
                domain=self.domain,
            ),
            query_expression=self.query_expression,
        )


def validate_gdelt_query_specs(
    specs: Sequence[GdeltQuerySpec],
) -> tuple[GdeltQuerySpec, ...]:
    """Reject duplicate query IDs and duplicate logical cells, or return the catalog.

    A duplicate is a configuration error rather than a silently dropped entry: two active
    definitions of one logical cell would make discovery coverage non-deterministic, and a
    reused ``query_id`` would corrupt the durable aggregate series.
    """
    seen_ids: set[str] = set()
    seen_cells: set[tuple[Market, Domain]] = set()

    for spec in specs:
        if spec.query_id in seen_ids:
            raise GdeltQueryConfigError(f"duplicate query_id {spec.query_id}")
        seen_ids.add(spec.query_id)

        cell = (spec.market, spec.domain)
        if cell in seen_cells:
            raise GdeltQueryConfigError(
                f"duplicate active cell {spec.market.value}/{spec.domain.value}"
            )
        seen_cells.add(cell)

    return tuple(specs)


def load_gdelt_query_specs(path: Path | None) -> tuple[GdeltQuerySpec, ...]:
    """Load and fully validate an authored GDELT query catalog.

    ``path is None`` means GDELT discovery is intentionally disabled and yields an empty
    catalog. Any explicitly supplied path must resolve to a readable catalog declaring at
    least one query: a missing file and a successfully parsed but empty query list are both
    configuration errors, so a mis-pointed or emptied production catalog can never masquerade
    as an ordinary zero-result discovery run.
    """
    if path is None:
        return ()

    if not path.is_file():
        raise GdeltQueryConfigError(f"{path} does not exist")

    try:
        with path.open("rb") as query_file:
            payload = tomllib.load(query_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise GdeltQueryConfigError(f"cannot read {path}: {error}") from error

    unexpected_keys = sorted(key for key in payload if key != _QUERIES_KEY)
    if unexpected_keys:
        raise GdeltQueryConfigError(
            f"{path} contains unsupported top-level keys: {', '.join(unexpected_keys)}"
        )

    entries = payload.get(_QUERIES_KEY, [])
    if not isinstance(entries, list):
        raise GdeltQueryConfigError(f"{path} must define '{_QUERIES_KEY}' as an array of tables")
    if not entries:
        raise GdeltQueryConfigError(f"{path} declares no queries")

    specs = tuple(_parse_entry(entry, path, index) for index, entry in enumerate(entries))
    return validate_gdelt_query_specs(specs)


def _parse_entry(entry: object, path: Path, index: int) -> GdeltQuerySpec:
    if not isinstance(entry, dict):
        raise GdeltQueryConfigError(f"{path} query {index} must be a table")
    try:
        return _GdeltQueryEntry.model_validate_json(json.dumps(entry)).to_spec()
    except (TypeError, ValueError, ValidationError) as error:
        raise GdeltQueryConfigError(f"{path} query {index} is invalid: {error}") from error
