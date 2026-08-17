from pathlib import Path

import pytest

from market_intelligence.source_registry import (
    ContentScope,
    RightsReviewStatus,
    SourceConfigLoadError,
    load_source_config,
    load_source_configs,
)

SOURCE_DIRECTORY = Path(__file__).parents[3] / "config" / "sources"


def test_loads_all_production_sources_in_deterministic_order() -> None:
    sources = load_source_configs(SOURCE_DIRECTORY)

    assert [source.source_id for source in sources] == [
        "cn_csrc_regulatory_docs",
        "cn_miit_policy_listing",
        "cn_nbs_latest_releases",
        "cn_nea_regulatory_docs",
        "cn_pboc_regulatory_docs",
        "cn_samr_market_regulation_bulletins",
        "cn_state_council_policy_docs",
        "eu_ec_policy_news",
        "eu_ecb_press",
        "eu_esma_regulatory",
        "eu_eurlex_cellar",
        "us_bis_regulatory",
        "us_fed_press_releases",
        "us_federal_register",
        "us_fhfa_regulatory",
        "us_govinfo_legal",
        "us_sec_regulatory",
        "vn_moit_regulatory_docs",
        "vn_mst_news_events",
        "vn_mst_regulatory_docs",
        "vn_sbv_regulatory_docs",
    ]
    assert len(sources) == 21
    assert all(source.rights.can_fetch for source in sources)

    # Rights baseline: all sources use conservative PENDING metadata-only rights
    assert all(source.rights.can_store_metadata for source in sources)
    assert all(source.rights.can_store_full_text is False for source in sources)
    assert all(source.rights.can_ai_process is False for source in sources)
    assert all(source.rights.can_show_snippet is False for source in sources)
    assert all(source.rights.can_redistribute_full_text is False for source in sources)
    assert all(
        source.rights.rights_review_status is RightsReviewStatus.PENDING for source in sources
    )

    # us_sec_regulatory, us_federal_register, and vn_sbv_regulatory_docs are FORMAL_REGULATORY_LEGAL
    formal_sources = {
        "us_sec_regulatory",
        "us_federal_register",
        "us_govinfo_legal",
        "vn_sbv_regulatory_docs",
        "vn_moit_regulatory_docs",
        "vn_mst_regulatory_docs",
        "us_bis_regulatory",
        "us_fhfa_regulatory",
        "eu_esma_regulatory",
        "cn_samr_market_regulation_bulletins",
        "cn_miit_policy_listing",
        "eu_eurlex_cellar",
        "cn_csrc_regulatory_docs",
        "cn_nea_regulatory_docs",
        "cn_pboc_regulatory_docs",
        "cn_state_council_policy_docs",
    }
    editorial_sources = [s for s in sources if s.source_id not in formal_sources]
    assert all(source.content_scope is ContentScope.EDITORIAL_NEWS for source in editorial_sources)

    sec_source = next(s for s in sources if s.source_id == "us_sec_regulatory")
    assert sec_source.content_scope is ContentScope.FORMAL_REGULATORY_LEGAL
    assert sec_source.acquisition.method.value == "RSS"
    assert (
        str(sec_source.acquisition.endpoint_url)
        == "https://www.sec.gov/enforcement-litigation/administrative-proceedings/rss"
    )
    assert set(sec_source.domains) == {"FINANCE", "LAW_POLICY"}

    ec_source = next(s for s in sources if s.source_id == "eu_ec_policy_news")
    assert ec_source.name == "European Commission Highlighted News"
    assert ec_source.content_scope is ContentScope.EDITORIAL_NEWS
    assert ec_source.acquisition.method.value == "RSS"
    assert str(ec_source.acquisition.endpoint_url) == "https://commission.europa.eu/node/2/rss_en"
    assert set(ec_source.domains) == {
        "LAW_POLICY",
        "ENERGY",
        "TECHNOLOGY",
        "REAL_ESTATE",
        "FINANCE",
    }


def test_us_federal_register_identity_and_contract() -> None:
    """Exact contract assertions for the us_federal_register SourceConfig (SO-005)."""
    from market_intelligence.source_registry import AcquisitionMethod, SourceType

    sources = load_source_configs(SOURCE_DIRECTORY)
    fr = next(s for s in sources if s.source_id == "us_federal_register")

    # Identity
    assert fr.source_id == "us_federal_register"
    assert fr.name == "Federal Register"
    assert fr.market.value == "US"
    assert fr.language == "en"
    assert fr.source_type is SourceType.GOVERNMENT
    assert fr.authority_level.value == "PRIMARY"

    # Cross-domain coverage
    assert set(str(d) for d in fr.domains) == {
        "LAW_POLICY",
        "ENERGY",
        "TECHNOLOGY",
        "REAL_ESTATE",
        "FINANCE",
    }

    # Content scope: formal regulatory, not editorial news
    assert fr.content_scope is ContentScope.FORMAL_REGULATORY_LEGAL

    # Acquisition: REST_API at the official endpoint with 15-minute polling
    assert fr.acquisition.method is AcquisitionMethod.REST_API
    assert (
        str(fr.acquisition.endpoint_url) == "https://www.federalregister.gov/api/v1/documents.json"
    )
    assert fr.acquisition.poll_interval_minutes == 15

    # Rights: conservative PENDING — AI processing off
    assert fr.rights.can_fetch is True
    assert fr.rights.can_store_metadata is True
    assert fr.rights.can_store_full_text is False
    assert fr.rights.can_ai_process is False
    assert fr.rights.can_show_snippet is False
    assert fr.rights.can_redistribute_full_text is False
    assert fr.rights.rights_review_status is RightsReviewStatus.PENDING

    # Cost: free
    assert fr.cost.type.value == "FREE"
    assert fr.cost.monthly_fixed_usd == 0


def test_vn_sbv_regulatory_docs_identity_and_contract() -> None:
    """Exact contract assertions for the vn_sbv_regulatory_docs SourceConfig (SO-006)."""
    from market_intelligence.source_registry import AcquisitionMethod, SourceType

    sources = load_source_configs(SOURCE_DIRECTORY)
    sbv = next(s for s in sources if s.source_id == "vn_sbv_regulatory_docs")

    # Identity
    assert sbv.source_id == "vn_sbv_regulatory_docs"
    assert sbv.name == "State Bank of Vietnam Regulatory Documents"
    assert sbv.market.value == "VN"
    assert sbv.language == "vi"
    assert sbv.source_type is SourceType.REGULATOR
    assert sbv.authority_level.value == "PRIMARY"

    # Cross-domain coverage
    assert set(str(d) for d in sbv.domains) == {
        "LAW_POLICY",
        "FINANCE",
    }

    # Content scope: formal regulatory, not editorial news
    assert sbv.content_scope is ContentScope.FORMAL_REGULATORY_LEGAL

    # Acquisition: HTML at the official endpoint with 15-minute polling
    assert sbv.acquisition.method is AcquisitionMethod.HTML
    assert (
        str(sbv.acquisition.endpoint_url)
        == "https://sbv.gov.vn/vi/v%C4%83n-b%E1%BA%A3n-quy-ph%E1%BA%A1m-ph%C3%A1p-lu%E1%BA%ADt"
    )
    assert sbv.acquisition.poll_interval_minutes == 15

    # Rights: conservative PENDING — AI processing off
    assert sbv.rights.can_fetch is True
    assert sbv.rights.can_store_metadata is True
    assert sbv.rights.can_store_full_text is False
    assert sbv.rights.can_ai_process is False
    assert sbv.rights.can_show_snippet is False
    assert sbv.rights.can_redistribute_full_text is False
    assert sbv.rights.rights_review_status is RightsReviewStatus.PENDING

    # Cost: free
    assert sbv.cost.type.value == "FREE"
    assert sbv.cost.monthly_fixed_usd == 0


def test_source_filename_must_match_source_id(tmp_path: Path) -> None:
    config_path = tmp_path / "wrong_name.toml"
    config_path.write_text(
        (SOURCE_DIRECTORY / "vn_mst_news_events.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(SourceConfigLoadError, match="filename must match source_id"):
        load_source_config(config_path)


def test_invalid_toml_has_file_context(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("source_id = [", encoding="utf-8")

    with pytest.raises(SourceConfigLoadError) as captured:
        load_source_config(config_path)

    assert captured.value.path == config_path


def test_legacy_reviewed_can_ai_process_is_rejected_with_file_context(tmp_path: Path) -> None:
    source_path = SOURCE_DIRECTORY / "vn_mst_news_events.toml"
    config_path = tmp_path / source_path.name
    config_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "can_ai_process = false",
            'can_ai_process = "REVIEWED"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourceConfigLoadError) as captured:
        load_source_config(config_path)

    assert captured.value.path == config_path
    assert "can_ai_process" in captured.value.detail


def test_empty_source_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceConfigLoadError, match="no source TOML files found"):
        load_source_configs(tmp_path)
