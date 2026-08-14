from pathlib import Path

import pytest

from market_intelligence.source_registry import (
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
        "eu_ecb_press",
        "us_fed_press_releases",
        "vn_mst_news_events",
    ]
    assert all(source.rights.can_fetch for source in sources)
    assert all(source.rights.can_store_metadata for source in sources)
    assert all(source.rights.can_store_full_text is False for source in sources)
    assert all(source.rights.can_ai_process is False for source in sources)
    assert all(source.rights.can_show_snippet is False for source in sources)
    assert all(source.rights.can_redistribute_full_text is False for source in sources)
    assert all(
        source.rights.rights_review_status is RightsReviewStatus.PENDING for source in sources
    )


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


def test_empty_source_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceConfigLoadError, match="no source TOML files found"):
        load_source_configs(tmp_path)
