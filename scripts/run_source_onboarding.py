"""Manual bounded runner for source onboarding — supports RSS/Atom and REST API sources."""

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from market_intelligence.persistence import create_article_repository_from_environment
from market_intelligence.pipelines import preflight_sources, run_rss_ingestion
from market_intelligence.source_registry import SourceConfig, load_source_configs

DEFAULT_SOURCE_DIRECTORY = Path("config/sources")


def positive_integer(value: str) -> int:
    """Parse a strictly positive command-line integer."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    """Parse explicit safety bounds and preflight mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIRECTORY,
        help="directory containing one TOML file per source",
    )
    parser.add_argument(
        "--max-items",
        type=positive_integer,
        required=True,
        help="maximum entries processed per source",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="fetch and normalize without writing to Supabase",
    )
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        help="source_id to run; repeat to select multiple sources (default: all)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> Any:
    """Run preflight or persistence with the same validated source registry."""
    sources = load_source_configs(args.config_dir)
    sources = select_sources(sources, args.source_ids)
    if args.preflight:
        return await preflight_sources(sources, max_items=args.max_items)

    repository = create_article_repository_from_environment()
    return await run_rss_ingestion(
        sources,
        repository,
        max_items=args.max_items,
    )


def select_sources(
    sources: tuple[SourceConfig, ...],
    source_ids: list[str] | None,
) -> tuple[SourceConfig, ...]:
    """Select an explicit scheduler/run group and reject configuration typos."""
    selected_ids = set(source_ids or ())
    if selected_ids:
        known_ids = {source.source_id for source in sources}
        unknown_ids = sorted(selected_ids - known_ids)
        if unknown_ids:
            raise ValueError(f"unknown source_id values: {', '.join(unknown_ids)}")
        sources = tuple(source for source in sources if source.source_id in selected_ids)
    return tuple(sorted(sources, key=lambda source: (-source.priority, source.source_id)))


def main() -> None:
    """Execute the manual runner without logging environment credentials."""
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(_to_jsonable(result), default=str))
    if has_failed_sources(result):
        raise SystemExit(1)


def has_failed_sources(result: Any) -> bool:
    """Return true when a completed multi-source run contains source failures."""
    source_results = result.sources if hasattr(result, "sources") else result
    return any(item.status == "FAILED" for item in source_results)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
