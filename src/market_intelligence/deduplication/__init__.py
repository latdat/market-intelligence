"""Public deterministic article deduplication API."""

from market_intelligence.deduplication.articles import (
    DedupDecision,
    DedupReason,
    evaluate_duplicate,
)

__all__ = ["DedupDecision", "DedupReason", "evaluate_duplicate"]
