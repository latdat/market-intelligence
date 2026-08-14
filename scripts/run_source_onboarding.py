"""Manual bounded runner for SO-001 source onboarding."""

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from market_intelligence.persistence import create_article_repository_from_environment
from market_intelligence.pipelines import preflight_rss_sources, run_rss_ingestion
from market_intelligence.source_registry import load_source_configs

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
        help="maximum feed entries processed per source",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="fetch and normalize without writing to Supabase",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> Any:
    """Run preflight or persistence with the same validated source registry."""
    sources = load_source_configs(args.config_dir)
    if args.preflight:
        return await preflight_rss_sources(sources, max_items=args.max_items)

    repository = create_article_repository_from_environment()
    return await run_rss_ingestion(
        sources,
        repository,
        max_items=args.max_items,
    )


def main() -> None:
    """Execute the manual runner without logging environment credentials."""
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(_to_jsonable(result), default=str))


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
