"""Run one bounded DE-009B classification batch after explicit live confirmation."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from market_intelligence.classification import (
    DeterministicClassifier,
    HybridArticleClassifier,
    create_deepseek_classifier_from_environment,
    load_deterministic_rules,
)
from market_intelligence.persistence import (
    create_classification_repository_from_environment,
    create_classification_work_reader_from_environment,
)
from market_intelligence.pipelines import ClassificationRunner, ClassificationRunnerConfig
from market_intelligence.source_registry import load_source_configs

DEFAULT_SOURCE_DIRECTORY = Path("config/sources")


def bounded_integer(minimum: int, maximum: int) -> object:
    def parse(value: str) -> int:
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"value must be between {minimum} and {maximum}")
        return parsed

    return parse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIRECTORY,
    )
    parser.add_argument("--enqueue-limit", type=bounded_integer(1, 1_000), default=100)
    parser.add_argument("--process-limit", type=bounded_integer(1, 1_000), default=20)
    parser.add_argument(
        "--confirm-live-provider",
        action="store_true",
        help="required acknowledgement that this command may call DeepSeek",
    )
    args = parser.parse_args()
    if not args.confirm_live_provider:
        parser.error("--confirm-live-provider is required")
    return args


async def run(args: argparse.Namespace) -> object:
    sources = load_source_configs(args.config_dir)
    # Construct every secret/config-bearing dependency before discovery or claim.
    deepseek = create_deepseek_classifier_from_environment()
    classifier = HybridArticleClassifier(
        DeterministicClassifier(load_deterministic_rules()),
        deepseek,
    )
    repository = create_classification_repository_from_environment()
    work_reader = create_classification_work_reader_from_environment()
    runner = ClassificationRunner(
        classifier,
        repository,
        work_reader,
        sources,
        config=ClassificationRunnerConfig(
            enqueue_limit=args.enqueue_limit,
            process_limit=args.process_limit,
        ),
    )
    return await runner.run_once()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(asdict(result), default=str))
    if result.stop_reason is not None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
