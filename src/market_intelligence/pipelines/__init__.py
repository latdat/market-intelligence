"""Public pipeline composition helpers."""

from market_intelligence.pipelines.classification_runner import (
    BatchStopReason,
    ClassificationBatchResult,
    ClassificationRunner,
    ClassificationRunnerConfig,
    EnqueueBatchResult,
    ProcessingBatchResult,
)
from market_intelligence.pipelines.matching_runner import (
    MatchingErrorCategory,
    MatchingRunError,
    MatchingRunner,
    MatchingRunnerConfig,
    MatchingRunResult,
    MatchingStage,
)
from market_intelligence.pipelines.rss_to_supabase import (
    BatchDuplicateMatch,
    IngestionRunResult,
    SourceIngestionResult,
    SourcePreflightResult,
    UnsupportedAcquisitionMethod,
    preflight_rss_sources,
    preflight_sources,
    run_rss_ingestion,
)

__all__ = [
    "BatchStopReason",
    "ClassificationBatchResult",
    "ClassificationRunner",
    "ClassificationRunnerConfig",
    "EnqueueBatchResult",
    "ProcessingBatchResult",
    "MatchingErrorCategory",
    "MatchingRunError",
    "MatchingRunResult",
    "MatchingRunner",
    "MatchingRunnerConfig",
    "MatchingStage",
    "BatchDuplicateMatch",
    "IngestionRunResult",
    "SourceIngestionResult",
    "SourcePreflightResult",
    "UnsupportedAcquisitionMethod",
    "preflight_rss_sources",
    "preflight_sources",
    "run_rss_ingestion",
]
