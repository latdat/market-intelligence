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
        "cn_nbs_latest_releases",
        "eu_ec_policy_news",
        "eu_ecb_press",
        "us_fed_press_releases",
        "us_sec_regulatory",
        "vn_mst_news_events",
    ]
    assert all(source.rights.can_fetch for source in sources)

    editorial_news_sources = [s for s in sources if s.source_id != "us_sec_regulatory"]
    assert all(
        source.content_scope is ContentScope.EDITORIAL_NEWS for source in editorial_news_sources
    )

    assert all(source.rights.can_store_metadata for source in sources)
    assert all(source.rights.can_store_full_text is False for source in sources)
    assert all(source.rights.can_ai_process is False for source in sources)
    assert all(source.rights.can_show_snippet is False for source in sources)
    assert all(source.rights.can_redistribute_full_text is False for source in sources)
    assert all(
        source.rights.rights_review_status is RightsReviewStatus.PENDING for source in sources
    )

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
