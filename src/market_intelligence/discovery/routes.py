"""TOML loader for reviewed publisher routes.

Route files are authored configuration, exactly like ``config/sources/*.toml``. Unlike
``load_source_configs``, a missing or empty route file is not an error: the admission boundary
must be able to start with zero admitted routes, in which case every candidate resolves to
``UNKNOWN`` and stays discovery-only.

Expected file shape::

    [[routes]]
    hostname = "example.test"
    path_prefix = "/news/business/"
    target_source_id = "us_example_source"
    content_scope = "EDITORIAL_NEWS"
    identity_mode = "CANONICAL_URL_FALLBACK"
"""

import json
import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from market_intelligence.discovery.admission import (
    PublisherRouteConfigError,
    validate_publisher_routes,
)
from market_intelligence.discovery.models import PublisherRoute
from market_intelligence.source_registry import SourceConfig

_ROUTES_KEY = "routes"


def load_publisher_routes(
    path: Path | None,
    *,
    sources: Sequence[SourceConfig],
) -> tuple[PublisherRoute, ...]:
    """Load and fully validate authored routes, or return an empty tuple when absent."""
    if path is None or not path.is_file():
        return ()

    try:
        with path.open("rb") as route_file:
            payload = tomllib.load(route_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PublisherRouteConfigError(f"cannot read {path}: {error}") from error

    unexpected_keys = sorted(key for key in payload if key != _ROUTES_KEY)
    if unexpected_keys:
        raise PublisherRouteConfigError(
            f"{path} contains unsupported top-level keys: {', '.join(unexpected_keys)}"
        )

    entries = payload.get(_ROUTES_KEY, [])
    if not isinstance(entries, list):
        raise PublisherRouteConfigError(f"{path} must define '{_ROUTES_KEY}' as an array of tables")

    routes = tuple(_parse_route(entry, path, index) for index, entry in enumerate(entries))
    return validate_publisher_routes(routes, sources)


def _parse_route(entry: object, path: Path, index: int) -> PublisherRoute:
    if not isinstance(entry, dict):
        raise PublisherRouteConfigError(f"{path} route {index} must be a table")
    try:
        return PublisherRoute.model_validate_json(json.dumps(entry))
    except (TypeError, ValueError, ValidationError) as error:
        raise PublisherRouteConfigError(f"{path} route {index} is invalid: {error}") from error
