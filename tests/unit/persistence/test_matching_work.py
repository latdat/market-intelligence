"""Offline contract tests for the matching work read boundary."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import ClassifiedArticle, Topic
from market_intelligence.persistence import (
    MatchingWorkItem,
    MatchingWorkPage,
    MatchingWorkReadError,
    normalize_cutoff,
)
from market_intelligence.source_registry import Domain, Market

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def article(article_id: str = "article-1") -> CanonicalArticle:
    return CanonicalArticle(
        article_id=article_id,
        source_id="us_test_source",
        source_item_id=f"item-{article_id}",
        url=f"https://example.test/{article_id}",
        canonical_url=f"https://example.test/{article_id}",
        title="Test article",
        description="Metadata-only description",
        language="en",
        market=Market.US,
        published_at=NOW - timedelta(hours=1),
        discovered_at=NOW - timedelta(minutes=30),
        content_hash=f"hash-{article_id}",
    )


def classification(article_id: str = "article-1") -> ClassifiedArticle:
    return ClassifiedArticle(
        article_id=article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.US,),
        category=Domain.TECHNOLOGY,
        topics=(Topic.AI,),
        confidence=0.9,
        classified_at=NOW - timedelta(minutes=10),
    )


def work_item(article_id: str = "article-1") -> MatchingWorkItem:
    return MatchingWorkItem(
        article=article(article_id),
        classification=classification(article_id),
    )


def test_work_item_exposes_the_shared_article_identity() -> None:
    item = work_item("article-7")

    assert item.article_id == "article-7"


def test_work_item_rejects_article_classification_identity_mismatch() -> None:
    with pytest.raises(ValidationError, match="article_id"):
        MatchingWorkItem(article=article("article-a"), classification=classification("article-b"))


def test_valid_page_preserves_article_id_order_and_cursor() -> None:
    page = MatchingWorkPage(
        items=(work_item("article-1"), work_item("article-2")),
        next_cursor="article-2",
    )

    assert tuple(item.article_id for item in page.items) == ("article-1", "article-2")
    assert page.next_cursor == "article-2"


def test_terminal_non_empty_page_may_have_no_cursor() -> None:
    page = MatchingWorkPage(items=(work_item("article-1"),), next_cursor=None)

    assert page.next_cursor is None


def test_duplicate_article_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate article_id"):
        MatchingWorkPage(items=(work_item("article-1"), work_item("article-1")))


def test_unsorted_page_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ordered by article_id ascending"):
        MatchingWorkPage(items=(work_item("article-2"), work_item("article-1")))


def test_empty_page_cannot_have_cursor() -> None:
    with pytest.raises(ValidationError, match="empty pages cannot have next_cursor"):
        MatchingWorkPage(items=(), next_cursor="article-1")


def test_cursor_must_equal_last_article_id() -> None:
    with pytest.raises(ValidationError, match="last article_id"):
        MatchingWorkPage(
            items=(work_item("article-1"), work_item("article-2")),
            next_cursor="article-1",
        )


@pytest.mark.parametrize("cursor", ["", " ", "\t"])
def test_blank_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(ValidationError, match="next_cursor must not be blank"):
        MatchingWorkPage(items=(work_item("article-1"),), next_cursor=cursor)


def test_read_error_is_sanitized_and_retains_operation_context() -> None:
    error = MatchingWorkReadError("list_page", "article-1")

    assert error.operation == "list_page"
    assert error.article_id == "article-1"
    assert str(error) == "matching work read list_page failed for article article-1"


def test_normalize_cutoff_converts_to_utc() -> None:
    value = NOW.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))

    assert normalize_cutoff("run_cutoff", value) == NOW


def test_normalize_cutoff_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone information"):
        normalize_cutoff("run_cutoff", datetime(2026, 8, 18, 12, 0))
