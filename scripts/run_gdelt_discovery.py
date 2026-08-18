"""Run one bounded GDELT DOC 2.0 discovery invocation after explicit live confirmation.

This is an operator preflight path, not a scheduled job. It defaults to ``--dry-run`` because
the discovery observation migrations have not been verified on the linked remote Supabase
project; persisting requires opting in explicitly.

It never promotes a candidate into ``articles``, never classifies, never matches, and never
sends anything.
"""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from market_intelligence.discovery import (
    GdeltClientConfig,
    GdeltDiscoveryRunner,
    GdeltDocClient,
    GdeltRunnerConfig,
    load_gdelt_query_specs,
    load_publisher_routes,
)
from market_intelligence.persistence import (
    DiscoveryObservation,
    DiscoveryObservationRecordResult,
    DiscoveryRecordOutcome,
    create_discovery_repository_from_environment,
)
from market_intelligence.source_registry import load_source_configs

DEFAULT_SOURCE_DIRECTORY = Path("config/sources")
DEFAULT_QUERY_CATALOG = Path("config/discovery/gdelt_queries.toml")
DEFAULT_ROUTE_CATALOG = Path("config/discovery/publisher_routes.toml")


class DryRunObservationRepository:
    """Accept observations without writing anything, for pre-migration preflight runs."""

    def __init__(self) -> None:
        self.observations: list[DiscoveryObservation] = []

    def record_observation(
        self,
        observation: DiscoveryObservation,
    ) -> DiscoveryObservationRecordResult:
        self.observations.append(observation)
        return DiscoveryObservationRecordResult(
            outcome=DiscoveryRecordOutcome.CREATED,
            observation_count=1,
            first_seen_at=observation.observed_at,
            last_seen_at=observation.observed_at,
            sample_count=0 if observation.sample is None else 1,
        )


def bounded_integer(minimum: int, maximum: int) -> object:
    def parse(value: str) -> int:
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"value must be between {minimum} and {maximum}")
        return parsed

    return parse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIRECTORY)
    parser.add_argument("--query-catalog", type=Path, default=DEFAULT_QUERY_CATALOG)
    parser.add_argument("--route-catalog", type=Path, default=DEFAULT_ROUTE_CATALOG)
    parser.add_argument("--query-id", action="append", default=None)
    parser.add_argument("--lookback-minutes", type=bounded_integer(1, 1_440), default=15)
    parser.add_argument("--max-records", type=bounded_integer(1, 250), default=250)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="write observations; requires verified remote discovery migrations",
    )
    parser.add_argument(
        "--confirm-live-provider",
        action="store_true",
        help="required acknowledgement that this command calls the live GDELT API",
    )
    args = parser.parse_args()
    if not args.confirm_live_provider:
        parser.error("--confirm-live-provider is required for a live GDELT request")
    return args


async def main() -> int:
    args = parse_args()

    # An absent catalog means GDELT discovery is intentionally disabled. Report that
    # explicitly rather than emitting a run result that looks like a successful empty run.
    catalog_path = args.query_catalog if args.query_catalog.is_file() else None
    specs = load_gdelt_query_specs(catalog_path)
    if not specs:
        print(
            "GDELT discovery DISABLED (no query catalog configured at "
            f"{args.query_catalog}). Authoring and reviewing the production query catalog "
            "is an explicit activation gate; no request was made."
        )
        return 0

    if args.query_id:
        selected = set(args.query_id)
        specs = tuple(value for value in specs if value.query_id in selected)
        if not specs:
            print(f"No configured query matched --query-id {sorted(selected)}")
            return 1

    sources = load_source_configs(args.source_dir)
    routes = load_publisher_routes(
        args.route_catalog if args.route_catalog.is_file() else None,
        sources=sources,
    )
    if not routes:
        print(
            "WARNING: no reviewed publisher routes are configured. Every candidate will "
            "resolve to UNKNOWN and stay discovery-only."
        )

    repository = (
        create_discovery_repository_from_environment()
        if args.persist
        else DryRunObservationRepository()
    )
    if not args.persist:
        print("DRY RUN: observations will not be persisted (pass --persist to write).")

    client = GdeltDocClient(config=GdeltClientConfig(max_records_per_request=args.max_records))
    runner = GdeltDiscoveryRunner(
        client,
        specs,
        routes,
        sources,
        repository,
        config=GdeltRunnerConfig(lookback_minutes=args.lookback_minutes),
    )

    result = await runner.run_once()
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0 if result.stop_reason is None else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
