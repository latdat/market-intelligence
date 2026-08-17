from pathlib import Path

import pytest
from discovery_fixtures import (
    EDITORIAL_SOURCE_ID,
    FIXTURE_ROUTES,
    REGULATORY_SOURCE_ID,
    RIGHTS_DENIED_SOURCE_ID,
    build_route,
    build_source,
)

from market_intelligence.discovery import (
    IdentityMode,
    PublisherRouteConfigError,
    load_publisher_routes,
    validate_publisher_routes,
)
from market_intelligence.source_registry import ContentScope, SourceConfig


def test_fixture_routes_load_and_validate(fixture_sources: tuple[SourceConfig, ...]) -> None:
    routes = load_publisher_routes(FIXTURE_ROUTES, sources=fixture_sources)

    assert [route.target_source_id for route in routes] == [
        EDITORIAL_SOURCE_ID,
        REGULATORY_SOURCE_ID,
        RIGHTS_DENIED_SOURCE_ID,
    ]
    assert routes[1].identity_mode is IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED


def test_absent_route_file_loads_as_no_admitted_routes(
    tmp_path: Path,
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    assert load_publisher_routes(None, sources=fixture_sources) == ()
    assert load_publisher_routes(tmp_path / "missing.toml", sources=fixture_sources) == ()


def test_empty_route_file_loads_as_no_admitted_routes(
    tmp_path: Path,
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    empty = tmp_path / "publisher_routes.toml"
    empty.write_text("", encoding="utf-8")

    assert load_publisher_routes(empty, sources=fixture_sources) == ()


def test_unsupported_top_level_key_is_rejected(
    tmp_path: Path,
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    path = tmp_path / "publisher_routes.toml"
    path.write_text('provider = "GDELT_DOC_2_0"\n', encoding="utf-8")

    with pytest.raises(PublisherRouteConfigError, match="unsupported top-level keys"):
        load_publisher_routes(path, sources=fixture_sources)


def test_malformed_route_entry_is_rejected(
    tmp_path: Path,
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    path = tmp_path / "publisher_routes.toml"
    path.write_text(
        "[[routes]]\n"
        'hostname = "editorial.example.test"\n'
        'path_prefix = "news/business/"\n'
        f'target_source_id = "{EDITORIAL_SOURCE_ID}"\n'
        'content_scope = "EDITORIAL_NEWS"\n'
        'identity_mode = "CANONICAL_URL_FALLBACK"\n',
        encoding="utf-8",
    )

    with pytest.raises(PublisherRouteConfigError, match="route 0 is invalid"):
        load_publisher_routes(path, sources=fixture_sources)


def test_unknown_target_source_id_is_rejected(
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    routes = (build_route("editorial.example.test", "/news/", "xx_missing_source"),)

    with pytest.raises(PublisherRouteConfigError, match="unknown source"):
        validate_publisher_routes(routes, fixture_sources)


def test_content_scope_mismatch_is_rejected(fixture_sources: tuple[SourceConfig, ...]) -> None:
    routes = (
        build_route(
            "editorial.example.test",
            "/news/",
            EDITORIAL_SOURCE_ID,
            content_scope=ContentScope.FORMAL_REGULATORY_LEGAL,
        ),
    )

    with pytest.raises(PublisherRouteConfigError, match="declares content_scope"):
        validate_publisher_routes(routes, fixture_sources)


@pytest.mark.parametrize(
    ("first_prefix", "second_prefix"),
    [
        ("/news/", "/news/business/"),
        ("/news/business/", "/news/"),
        ("/news/", "/news/"),
        ("/news", "/news/"),
    ],
)
def test_overlapping_path_prefixes_on_one_hostname_are_rejected(
    first_prefix: str,
    second_prefix: str,
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    routes = (
        build_route("editorial.example.test", first_prefix, EDITORIAL_SOURCE_ID),
        build_route("editorial.example.test", second_prefix, EDITORIAL_SOURCE_ID),
    )

    with pytest.raises(PublisherRouteConfigError, match="overlapping path prefixes"):
        validate_publisher_routes(routes, fixture_sources)


def test_identical_path_prefixes_on_different_hostnames_are_allowed(
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    routes = (
        build_route("editorial.example.test", "/news/", EDITORIAL_SOURCE_ID),
        build_route("other.example.test", "/news/", EDITORIAL_SOURCE_ID),
    )

    assert validate_publisher_routes(routes, fixture_sources) == routes


def test_sibling_path_prefixes_on_one_hostname_are_allowed(
    fixture_sources: tuple[SourceConfig, ...],
) -> None:
    routes = (
        build_route("editorial.example.test", "/news/business/", EDITORIAL_SOURCE_ID),
        build_route("editorial.example.test", "/news/markets/", EDITORIAL_SOURCE_ID),
    )

    assert validate_publisher_routes(routes, fixture_sources) == routes


def test_duplicate_source_configurations_are_rejected() -> None:
    duplicated = (build_source(EDITORIAL_SOURCE_ID), build_source(EDITORIAL_SOURCE_ID))

    with pytest.raises(PublisherRouteConfigError, match="duplicate source_id"):
        validate_publisher_routes((), duplicated)


def test_path_prefix_must_be_authored_as_a_path() -> None:
    with pytest.raises(ValueError, match="path_prefix must start"):
        build_route("editorial.example.test", "news/", EDITORIAL_SOURCE_ID)


def test_hostname_must_be_authored_in_lowercase() -> None:
    with pytest.raises(ValueError, match="hostname must be authored in lowercase"):
        build_route("Editorial.Example.Test", "/news/", EDITORIAL_SOURCE_ID)
