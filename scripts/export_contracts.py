"""Export the shared DE -> SWE contracts as machine-readable artifacts.

This script is the single authority for the shape of the DE/SWE boundary. It derives
everything from the live Pydantic models and the committed source registry, so the
published artifacts can never disagree with the code that produces the data.

Outputs (all under ``swe_handoff/``):

* ``<entity>.schema.json`` -- JSON Schema per shared entity, for TypeScript generation
* ``vocabularies.json``    -- the controlled vocabularies, one authoritative list each
* ``sources.sample.json``  -- every configured source as a flat ``SourceDefinition``

Usage::

    python scripts/export_contracts.py            # write artifacts
    python scripts/export_contracts.py --check    # CI: fail when artifacts are stale

``--check`` writes nothing. It exits non-zero when a regenerated artifact differs from
the committed one, which is the signal that a model or a source config changed without
the handoff pack being regenerated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from market_intelligence.articles.models import CanonicalArticle, RawArticle
from market_intelligence.classification.models import ClassifiedArticle, Topic
from market_intelligence.models.alert_candidates import AlertCandidate, AlertImportance
from market_intelligence.models.user_preferences import UserPreference
from market_intelligence.source_registry.loader import load_source_configs
from market_intelligence.source_registry.models import (
    Domain,
    Market,
    SourceDefinition,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CONFIG_DIRECTORY = REPOSITORY_ROOT / "config" / "sources"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "swe_handoff"

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

CONTRACTS: dict[str, type] = {
    "raw_article": RawArticle,
    "canonical_article": CanonicalArticle,
    "classified_article": ClassifiedArticle,
    "user_preference": UserPreference,
    "alert_candidate": AlertCandidate,
    "source_definition": SourceDefinition,
}


def build_schema(name: str, model: type) -> dict[str, Any]:
    """Return one entity schema with a stable identifier and dialect."""
    schema = model.model_json_schema(mode="serialization")
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": f"https://market-intelligence.local/contracts/{name}.schema.json",
        **schema,
    }


def build_vocabularies() -> dict[str, Any]:
    """Return every controlled vocabulary the two systems must agree on."""
    return {
        "$comment": (
            "Generated from the DE enums. Do not hand-edit; regenerate with "
            "scripts/export_contracts.py. Both repositories derive their validation "
            "from this file so a vocabulary change is a failing build, not a silent no-op."
        ),
        "markets": [member.value for member in Market],
        "categories": [member.value for member in Domain],
        "topics": [member.value for member in Topic],
        "alert_importance": [member.value for member in AlertImportance],
        "classifier_version_pattern": r"^classification-v[1-9][0-9]*$",
        "limits": {
            "markets_per_classification": 4,
            "topics_per_classification": 5,
            "match_reasons_minimum": 1,
        },
    }


def build_source_definitions() -> list[dict[str, Any]]:
    """Return every configured source flattened to the shared boundary shape."""
    configs = load_source_configs(SOURCE_CONFIG_DIRECTORY)
    definitions = [SourceDefinition.from_parts(config) for config in configs]
    return [definition.model_dump(mode="json") for definition in definitions]


def render(payload: Any) -> str:
    """Serialize deterministically so the artifacts diff cleanly in review."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def collect_artifacts() -> dict[Path, str]:
    """Return every artifact path mapped to its expected content."""
    artifacts: dict[Path, str] = {}
    for name, model in CONTRACTS.items():
        artifacts[OUTPUT_DIRECTORY / f"{name}.schema.json"] = render(build_schema(name, model))
    artifacts[OUTPUT_DIRECTORY / "vocabularies.json"] = render(build_vocabularies())
    artifacts[OUTPUT_DIRECTORY / "sources.sample.json"] = render(build_source_definitions())
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed artifacts are current; write nothing",
    )
    arguments = parser.parse_args(argv)

    artifacts = collect_artifacts()

    if arguments.check:
        stale = [
            path
            for path, expected in artifacts.items()
            if not path.exists() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            for path in stale:
                print(f"stale: {path.relative_to(REPOSITORY_ROOT)}", file=sys.stderr)
            print(
                "\nRegenerate with: python scripts/export_contracts.py",
                file=sys.stderr,
            )
            return 1
        print(f"contracts current ({len(artifacts)} artifacts)")
        return 0

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for path, content in artifacts.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
