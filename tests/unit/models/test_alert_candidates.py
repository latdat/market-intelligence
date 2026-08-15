from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.models import AlertCandidate, AlertImportance

NOW = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)


def candidate(**overrides: object) -> AlertCandidate:
    values: dict[str, object] = {
        "candidate_id": "candidate-1",
        "user_id": "user-1",
        "article_id": "article-1",
        "matched_at": NOW,
        "match_reasons": ("market:US",),
        "importance": AlertImportance.NORMAL,
        "relevance_score": 0.8,
        "breaking_eligible": False,
    }
    values.update(overrides)
    return AlertCandidate(**values)


def test_valid_candidate_is_immutable_and_utc_normalized() -> None:
    result = candidate(matched_at=NOW + timedelta(hours=7))

    assert result.matched_at.tzinfo is UTC
    assert result.match_reasons == ("market:US",)


def test_naive_matched_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        candidate(matched_at=datetime(2026, 8, 15, 8, 0))


def test_duplicate_match_reasons_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        candidate(match_reasons=("market:US", "market:US"))


def test_empty_match_reasons_are_rejected() -> None:
    with pytest.raises(ValidationError):
        candidate(match_reasons=())


@pytest.mark.parametrize("score", [-0.1, 1.1, float("inf"), float("nan")])
def test_invalid_relevance_score_is_rejected(score: float) -> None:
    with pytest.raises(ValidationError):
        candidate(relevance_score=score)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AlertCandidate(
            candidate_id="candidate-1",
            user_id="user-1",
            article_id="article-1",
            matched_at=NOW,
            match_reasons=("market:US",),
            importance=AlertImportance.NORMAL,
            relevance_score=0.8,
            breaking_eligible=False,
            source_id="source-1",
        )
