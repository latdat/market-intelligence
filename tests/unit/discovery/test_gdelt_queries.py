"""Offline tests for the reviewed GDELT query catalog contract and loader."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from market_intelligence.discovery import (
    DiscoveryProvider,
    DiscoveryQuery,
    GdeltQueryConfigError,
    GdeltQuerySpec,
    load_gdelt_query_specs,
    validate_gdelt_query_specs,
)
from market_intelligence.source_registry import Domain, Market

FIXTURE_QUERIES = Path(__file__).parents[2] / "fixtures" / "discovery" / "gdelt_queries.toml"
EXAMPLE_TEMPLATE = (
    Path(__file__).parents[3] / "config" / "discovery" / "gdelt_queries.toml.example"
)


def spec(
    query_id: str = "gdelt_v1_us_finance",
    *,
    provider: DiscoveryProvider = DiscoveryProvider.GDELT_DOC_2_0,
    market: Market = Market.US,
    domain: Domain = Domain.FINANCE,
    query_expression: str = "fixture-expression",
) -> GdeltQuerySpec:
    return GdeltQuerySpec(
        discovery_query=DiscoveryQuery(
            query_id=query_id,
            provider=provider,
            market=market,
            domain=domain,
        ),
        query_expression=query_expression,
    )


def write_catalog(tmp_path: Path, body: str, name: str = "gdelt_queries.toml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


VALID_ENTRY = """
[[queries]]
query_id = "gdelt_v1_us_finance"
provider = "GDELT_DOC_2_0"
market = "US"
domain = "FINANCE"
query_expression = "fixture-expression"
"""


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


def test_valid_spec_exposes_the_logical_cell() -> None:
    value = spec()

    assert value.query_id == "gdelt_v1_us_finance"
    assert value.market is Market.US
    assert value.domain is Domain.FINANCE
    assert value.query_expression == "fixture-expression"


def test_discovery_query_stays_provider_neutral() -> None:
    # The provider-neutral model must not carry GDELT syntax.
    assert "query_expression" not in DiscoveryQuery.model_fields


@pytest.mark.parametrize("expression", ["", " ", "\t"])
def test_blank_query_expression_is_rejected(expression: str) -> None:
    with pytest.raises(ValidationError):
        spec(query_expression=expression)


def test_control_characters_in_expression_are_rejected() -> None:
    with pytest.raises(ValidationError, match="control characters"):
        spec(query_expression="finance\nmarkets")


def test_overlong_query_expression_is_rejected() -> None:
    with pytest.raises(ValidationError):
        spec(query_expression="x" * 513)


def test_spec_rejects_a_non_gdelt_provider() -> None:
    # DiscoveryProvider currently models only GDELT, so the guard is exercised by patching a
    # constructed model rather than by inventing a second provider value.
    query = DiscoveryQuery(
        query_id="gdelt_v1_us_finance",
        provider=DiscoveryProvider.GDELT_DOC_2_0,
        market=Market.US,
        domain=Domain.FINANCE,
    )
    object.__setattr__(query, "provider", "SOME_OTHER_PROVIDER")

    with pytest.raises(ValidationError, match="GDELT_DOC_2_0"):
        GdeltQuerySpec(discovery_query=query, query_expression="fixture-expression")


# ---------------------------------------------------------------------------
# Catalog-level validation
# ---------------------------------------------------------------------------


def test_duplicate_query_id_is_rejected() -> None:
    with pytest.raises(GdeltQueryConfigError, match="duplicate query_id"):
        validate_gdelt_query_specs(
            (spec(domain=Domain.FINANCE), spec(domain=Domain.ENERGY)),
        )


def test_duplicate_logical_cell_is_rejected() -> None:
    with pytest.raises(GdeltQueryConfigError, match="duplicate active cell US/FINANCE"):
        validate_gdelt_query_specs(
            (spec("gdelt_v1_us_finance"), spec("gdelt_v2_us_finance")),
        )


def test_same_cell_across_different_markets_is_allowed() -> None:
    specs = validate_gdelt_query_specs(
        (spec("gdelt_v1_us_finance", market=Market.US), spec("gdelt_v1_eu_finance", market=Market.EU))
    )

    assert len(specs) == 2


def test_query_id_versioning_allows_a_new_id_to_replace_an_old_cell() -> None:
    # A material semantic change ships as a NEW query_id. The old ID keeps its historical
    # aggregate series; only one of them may be active for the cell at a time.
    with pytest.raises(GdeltQueryConfigError, match="duplicate active cell"):
        validate_gdelt_query_specs((spec("gdelt_v1_us_finance"), spec("gdelt_v2_us_finance")))

    upgraded = validate_gdelt_query_specs((spec("gdelt_v2_us_finance"),))
    assert upgraded[0].query_id == "gdelt_v2_us_finance"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_none_path_means_discovery_is_disabled() -> None:
    assert load_gdelt_query_specs(None) == ()


def test_explicit_missing_path_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(GdeltQueryConfigError, match="does not exist"):
        load_gdelt_query_specs(tmp_path / "absent.toml")


def test_explicitly_configured_empty_catalog_is_a_configuration_error(tmp_path: Path) -> None:
    # An emptied production catalog must never look like an ordinary zero-result run.
    with pytest.raises(GdeltQueryConfigError, match="declares no queries"):
        load_gdelt_query_specs(write_catalog(tmp_path, "queries = []\n"))


def test_file_with_no_queries_key_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(GdeltQueryConfigError, match="declares no queries"):
        load_gdelt_query_specs(write_catalog(tmp_path, "# nothing here\n"))


def test_valid_catalog_loads(tmp_path: Path) -> None:
    specs = load_gdelt_query_specs(write_catalog(tmp_path, VALID_ENTRY))

    assert len(specs) == 1
    assert specs[0].query_id == "gdelt_v1_us_finance"
    assert specs[0].discovery_query.provider is DiscoveryProvider.GDELT_DOC_2_0


def test_unsupported_top_level_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_ENTRY + '\n[settings]\nmax_records = 250\n'
    with pytest.raises(GdeltQueryConfigError, match="unsupported top-level keys"):
        load_gdelt_query_specs(write_catalog(tmp_path, body))


def test_unknown_entry_field_is_rejected(tmp_path: Path) -> None:
    body = VALID_ENTRY + 'extra_field = "nope"\n'
    with pytest.raises(GdeltQueryConfigError, match="is invalid"):
        load_gdelt_query_specs(write_catalog(tmp_path, body))


def test_malformed_toml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(GdeltQueryConfigError, match="cannot read"):
        load_gdelt_query_specs(write_catalog(tmp_path, "[[queries]\nbroken = "))


def test_non_gdelt_provider_in_config_is_rejected(tmp_path: Path) -> None:
    body = VALID_ENTRY.replace('provider = "GDELT_DOC_2_0"', 'provider = "SOMETHING_ELSE"')
    with pytest.raises(GdeltQueryConfigError, match="is invalid"):
        load_gdelt_query_specs(write_catalog(tmp_path, body))


def test_blank_expression_in_config_is_rejected(tmp_path: Path) -> None:
    body = VALID_ENTRY.replace('query_expression = "fixture-expression"', 'query_expression = " "')
    with pytest.raises(GdeltQueryConfigError, match="is invalid"):
        load_gdelt_query_specs(write_catalog(tmp_path, body))


def test_queries_must_be_an_array_of_tables(tmp_path: Path) -> None:
    with pytest.raises(GdeltQueryConfigError, match="array of tables"):
        load_gdelt_query_specs(write_catalog(tmp_path, 'queries = "not-a-list"\n'))


def test_duplicate_cell_in_config_is_rejected(tmp_path: Path) -> None:
    body = VALID_ENTRY + VALID_ENTRY.replace(
        'query_id = "gdelt_v1_us_finance"', 'query_id = "gdelt_v2_us_finance"'
    )
    with pytest.raises(GdeltQueryConfigError, match="duplicate active cell"):
        load_gdelt_query_specs(write_catalog(tmp_path, body))


# ---------------------------------------------------------------------------
# Shipped files
# ---------------------------------------------------------------------------


def test_synthetic_test_fixture_catalog_loads() -> None:
    specs = load_gdelt_query_specs(FIXTURE_QUERIES)

    assert [value.query_id for value in specs] == [
        "gdelt_v1_us_finance",
        "gdelt_v1_us_technology",
    ]


def test_non_production_example_template_is_parseable_and_labelled() -> None:
    # The template must stay loadable so authoring mistakes surface early, and must stay
    # obviously non-production so it is never promoted by accident.
    specs = load_gdelt_query_specs(EXAMPLE_TEMPLATE)

    assert specs
    assert all("PLACEHOLDER" in value.query_expression for value in specs)
    header = EXAMPLE_TEMPLATE.read_text(encoding="utf-8")
    assert "NOT PRODUCTION" in header


def test_no_active_production_catalog_is_committed() -> None:
    # Authoring the reviewed 20-cell catalog is an explicit activation gate.
    assert not (EXAMPLE_TEMPLATE.parent / "gdelt_queries.toml").exists()
