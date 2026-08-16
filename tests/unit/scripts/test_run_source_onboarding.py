from pathlib import Path

import pytest
from scripts.run_source_onboarding import has_failed_sources, select_sources

from market_intelligence.pipelines import SourcePreflightResult
from market_intelligence.source_registry import load_source_configs

CONFIG_DIR = Path(__file__).parents[3] / "config" / "sources"


def test_source_selection_allows_scheduler_scoped_runs() -> None:
    sources = load_source_configs(CONFIG_DIR)

    selected = select_sources(
        sources,
        ["us_govinfo_legal", "vn_sbv_regulatory_docs"],
    )

    assert {source.source_id for source in selected} == {
        "us_govinfo_legal",
        "vn_sbv_regulatory_docs",
    }


def test_source_selection_rejects_unknown_ids() -> None:
    sources = load_source_configs(CONFIG_DIR)

    with pytest.raises(ValueError, match="unknown_source"):
        select_sources(sources, ["unknown_source"])


def test_failed_source_makes_the_completed_cli_run_unsuccessful() -> None:
    results = (
        SourcePreflightResult("source-1", 1, 1, 1, 0),
        SourcePreflightResult(
            "source-2",
            0,
            0,
            0,
            0,
            status="FAILED",
            error_type="FeedFetchError",
        ),
    )

    assert has_failed_sources(results) is True
