"""Contract tests for the published SWE handoff pack.

These tests are the tripwire for DE/SWE drift. They assert that every record DE publishes
still validates against the live models, that the generated artifacts are not stale, and
that every foreign identifier in the pack resolves inside the pack.

They run offline: the pack is a set of files, no database and no network.

Note on validation: the shared models are declared ``strict=True``. Under strict mode
``model_validate`` rejects a JSON-decoded ``dict`` (a JSON array is a ``list``, not a
``tuple``; a timestamp is a ``str``, not a ``datetime``). ``model_validate_json`` applies
the JSON-specific coercion rules and is the correct entry point for file fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from market_intelligence.articles.models import CanonicalArticle
from market_intelligence.classification.models import ClassifiedArticle
from market_intelligence.models.alert_candidates import AlertCandidate
from market_intelligence.models.user_preferences import UserPreference
from market_intelligence.source_registry.models import SourceDefinition

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_DIRECTORY = REPOSITORY_ROOT / "swe_handoff"

ENTITY_FILES: tuple[tuple[str, type], ...] = (
    ("articles.sample.json", CanonicalArticle),
    ("articles.supplement.sample.json", CanonicalArticle),
    ("article_classifications.sample.json", ClassifiedArticle),
    ("article_classifications.supplement.sample.json", ClassifiedArticle),
    ("user_preferences.sample.json", UserPreference),
    ("user_preferences.supplement.sample.json", UserPreference),
    ("alert_candidates.sample.json", AlertCandidate),
    ("alert_candidates.supplement.sample.json", AlertCandidate),
    ("sources.sample.json", SourceDefinition),
    ("sources.supplement.sample.json", SourceDefinition),
)


def load_records(filename: str) -> list[dict[str, Any]]:
    """Return the record list, tolerating both the bare-array and {"records": []} shapes."""
    payload = json.loads((HANDOFF_DIRECTORY / filename).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "records" in payload:
        return list(payload["records"])
    return list(payload)


def load_all(*filenames: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for filename in filenames:
        records.extend(load_records(filename))
    return records


@pytest.mark.parametrize(("filename", "model"), ENTITY_FILES)
def test_every_published_record_validates(filename: str, model: type) -> None:
    records = load_records(filename)
    assert records, f"{filename} must not be empty"
    for index, record in enumerate(records):
        model.model_validate_json(json.dumps(record))  # raises on drift


def test_generated_artifacts_are_current() -> None:
    """Fail when a model or source config changed without regenerating the pack."""
    # ``scripts/`` is not an installed package; put the repository root on the path so
    # this test needs no pytest configuration change to run.
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    from scripts.export_contracts import collect_artifacts

    stale = [
        path.name
        for path, expected in collect_artifacts().items()
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
    assert not stale, (
        f"stale artifacts: {stale}. Run: python scripts/export_contracts.py"
    )


def test_classification_identity_is_article_plus_version() -> None:
    """DE's identity is (article_id, classifier_version); the pack must demonstrate it."""
    records = load_all(
        "article_classifications.sample.json",
        "article_classifications.supplement.sample.json",
    )
    identities = [(record["article_id"], record["classifier_version"]) for record in records]
    assert len(identities) == len(set(identities)), "composite identity must be unique"

    article_ids = [identity[0] for identity in identities]
    reclassified = {value for value in article_ids if article_ids.count(value) > 1}
    assert reclassified, (
        "the pack must contain at least one article classified under two versions, "
        "otherwise no consumer is forced to model reclassification"
    )


def test_every_foreign_identifier_resolves() -> None:
    articles = load_all("articles.sample.json", "articles.supplement.sample.json")
    classifications = load_all(
        "article_classifications.sample.json",
        "article_classifications.supplement.sample.json",
    )
    preferences = load_all(
        "user_preferences.sample.json", "user_preferences.supplement.sample.json"
    )
    candidates = load_all(
        "alert_candidates.sample.json", "alert_candidates.supplement.sample.json"
    )
    sources = load_all("sources.sample.json", "sources.supplement.sample.json")

    article_ids = {record["article_id"] for record in articles}
    user_ids = {record["user_id"] for record in preferences}
    source_ids = {record["source_id"] for record in sources}

    assert {record["source_id"] for record in articles} <= source_ids
    assert {record["article_id"] for record in classifications} <= article_ids
    assert {record["article_id"] for record in candidates} <= article_ids
    assert {record["user_id"] for record in candidates} <= user_ids


def test_irrelevant_classifications_carry_no_semantics() -> None:
    records = load_all(
        "article_classifications.sample.json",
        "article_classifications.supplement.sample.json",
    )
    irrelevant = [record for record in records if not record["is_relevant"]]
    assert irrelevant, "the pack must contain an irrelevant classification"
    for record in irrelevant:
        assert record["markets"] == []
        assert record["topics"] == []
        assert record["category"] is None


def test_match_reasons_are_never_empty() -> None:
    """DE guarantees at least one reason; a consumer defaulting to [] is wrong."""
    for record in load_all(
        "alert_candidates.sample.json", "alert_candidates.supplement.sample.json"
    ):
        assert record["match_reasons"], record["candidate_id"]


def test_snippet_rights_are_explicit_for_every_source() -> None:
    """SWE gates snippet display on this flag, so it must exist on every source."""
    for record in load_all("sources.sample.json", "sources.supplement.sample.json"):
        assert "can_show_snippet" in record["rights"], record["source_id"]
        assert "rights_review_status" in record["rights"], record["source_id"]
