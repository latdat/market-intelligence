"""Public deterministic matching API."""

from market_intelligence.matching.matcher import (
    BREAKING_FRESHNESS,
    NORMAL_FRESHNESS,
    build_alert_candidate_id,
    match_article,
)

__all__ = [
    "BREAKING_FRESHNESS",
    "NORMAL_FRESHNESS",
    "build_alert_candidate_id",
    "match_article",
]
