"""Unit tests for deterministic article deduplication."""

from datetime import UTC, datetime, timedelta

import pytest

import market_intelligence.deduplication.articles as deduplication_articles
from market_intelligence.articles import CanonicalArticle
from market_intelligence.deduplication import DedupReason, evaluate_duplicate

BASE_TIME = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)


def canonical_article(**overrides: object) -> CanonicalArticle:
    payload: dict[str, object] = {
        "article_id": "article-candidate",
        "source_id": "source_one",
        "source_item_id": "item-candidate",
        "url": "https://example.org/candidate",
        "canonical_url": "https://example.org/candidate",
        "title": "Federal agency publishes final renewable energy guidance",
        "description": "Candidate description",
        "language": "en",
        "market": "US",
        "published_at": BASE_TIME,
        "discovered_at": BASE_TIME,
        "content_hash": "hash-candidate",
    }
    payload.update(overrides)
    return CanonicalArticle.model_validate(payload)


def test_same_canonical_url_is_duplicate_and_first_reason_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = canonical_article()
    existing = canonical_article(article_id="article-existing")

    def fail_if_called(candidate_title: str, existing_title: str) -> float:
        del candidate_title, existing_title
        pytest.fail("title similarity must not run after an exact match")

    monkeypatch.setattr(deduplication_articles, "_calculate_title_similarity", fail_if_called)

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is True
    assert decision.reason is DedupReason.CANONICAL_URL
    assert decision.matched_article_id == "article-existing"
    assert decision.title_similarity is None


def test_same_source_item_id_is_source_local_duplicate() -> None:
    candidate = canonical_article(canonical_url="https://example.org/candidate")
    existing = canonical_article(
        article_id="article-existing",
        canonical_url="https://example.org/existing",
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.reason is DedupReason.SOURCE_ITEM_ID
    assert decision.matched_article_id == "article-existing"
    assert decision.title_similarity is None


def test_same_source_item_id_from_different_sources_is_not_an_exact_match() -> None:
    candidate = canonical_article(title="Candidate article with sufficiently long distinct title")
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        canonical_url="https://other.example.org/existing",
        title="Unrelated existing report with another sufficiently long title",
        content_hash="hash-existing",
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is False
    assert decision.reason is None
    assert decision.matched_article_id is None


def test_missing_source_item_ids_do_not_match() -> None:
    candidate = canonical_article(
        source_item_id=None,
        title="Candidate article with sufficiently long distinct title",
    )
    existing = canonical_article(
        article_id="article-existing",
        source_item_id=None,
        canonical_url="https://example.org/existing",
        title="Unrelated existing report with another sufficiently long title",
        content_hash="hash-existing",
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is False


def test_same_content_hash_is_cross_source_duplicate() -> None:
    candidate = canonical_article()
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.reason is DedupReason.CONTENT_HASH
    assert decision.matched_article_id == "article-existing"
    assert decision.title_similarity is None


def test_similar_title_at_threshold_is_hard_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = canonical_article()
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        content_hash="hash-existing",
    )
    monkeypatch.setattr(
        deduplication_articles,
        "_calculate_title_similarity",
        lambda candidate_title, existing_title: 0.98,
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is True
    assert decision.reason is DedupReason.TITLE_SIMILARITY
    assert decision.matched_article_id == "article-existing"
    assert decision.title_similarity == 0.98


def test_similarity_below_threshold_is_returned_without_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = canonical_article()
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        content_hash="hash-existing",
    )
    monkeypatch.setattr(
        deduplication_articles,
        "_calculate_title_similarity",
        lambda candidate_title, existing_title: 0.979,
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is False
    assert decision.reason is None
    assert decision.matched_article_id is None
    assert decision.title_similarity == 0.979


def test_title_key_normalization_preserves_punctuation_and_numbers() -> None:
    candidate = canonical_article(
        title="  CAFÉ   Rule 10-K: final guidance for institutions.  ",
    )
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        title="Cafe\u0301 Rule 10-K: final guidance for institutions.",
        content_hash="hash-existing",
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.reason is DedupReason.TITLE_SIMILARITY
    assert decision.title_similarity == 1.0


def test_numeric_token_guard_blocks_different_sequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = canonical_article(
        title="Agency publishes regulatory filing 10-K for fiscal year 2025"
    )
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        title="Agency publishes regulatory filing 10-K for fiscal year 2026",
        content_hash="hash-existing",
    )

    def fail_if_called(candidate_title: str, existing_title: str) -> float:
        del candidate_title, existing_title
        pytest.fail("numeric-token guard must run before title similarity")

    monkeypatch.setattr(deduplication_articles, "_calculate_title_similarity", fail_if_called)

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is False
    assert decision.title_similarity is None


@pytest.mark.parametrize(
    "existing_overrides",
    [
        {"market": "EU"},
        {"published_at": BASE_TIME + timedelta(hours=24, microseconds=1)},
        {"title": "a" * 29},
    ],
)
def test_ineligible_title_similarity_does_not_compute_score(
    existing_overrides: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_overrides: dict[str, object] = {}
    if "title" in existing_overrides:
        candidate_overrides["title"] = "a" * 29
    candidate = canonical_article(**candidate_overrides)
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        content_hash="hash-existing",
        **existing_overrides,
    )

    def fail_if_called(candidate_title: str, existing_title: str) -> float:
        del candidate_title, existing_title
        pytest.fail("ineligible title similarity must not compute a score")

    monkeypatch.setattr(deduplication_articles, "_calculate_title_similarity", fail_if_called)

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is False
    assert decision.title_similarity is None


def test_exactly_24_hours_is_eligible_for_title_similarity() -> None:
    candidate = canonical_article()
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        content_hash="hash-existing",
        published_at=BASE_TIME + timedelta(hours=24),
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.reason is DedupReason.TITLE_SIMILARITY
    assert decision.title_similarity == 1.0


def test_discovered_at_is_effective_time_when_publication_time_is_missing() -> None:
    candidate = canonical_article(published_at=None)
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        content_hash="hash-existing",
        published_at=None,
        discovered_at=BASE_TIME + timedelta(hours=24),
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.reason is DedupReason.TITLE_SIMILARITY


def test_different_real_article_is_not_duplicate() -> None:
    candidate = canonical_article(title="Federal agency publishes renewable energy market guidance")
    existing = canonical_article(
        article_id="article-existing",
        source_id="source_two",
        source_item_id="item-existing",
        canonical_url="https://other.example.org/existing",
        title="Central bank releases quarterly financial stability assessment",
        content_hash="hash-existing",
    )

    decision = evaluate_duplicate(candidate, existing)

    assert decision.is_duplicate is False
    assert decision.reason is None
    assert decision.matched_article_id is None
    assert decision.title_similarity is not None
    assert decision.title_similarity < 0.98
