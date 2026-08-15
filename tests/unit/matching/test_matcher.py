from datetime import UTC, datetime, timedelta

import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import ClassifiedArticle, Topic
from market_intelligence.matching import build_alert_candidate_id, match_article
from market_intelligence.models import AlertImportance, UserPreference
from market_intelligence.source_registry import Domain, Market

NOW = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)


def article(**overrides: object) -> CanonicalArticle:
    values: dict[str, object] = {
        "article_id": "article-1",
        "source_id": "us_test_source",
        "source_item_id": "item-1",
        "url": "https://example.test/article-1",
        "canonical_url": "https://example.test/article-1",
        "title": "Test article",
        "description": "Metadata-only description",
        "language": "en",
        "market": Market.US,
        "published_at": NOW - timedelta(hours=1),
        "discovered_at": NOW - timedelta(minutes=30),
        "content_hash": "hash-1",
    }
    values.update(overrides)
    return CanonicalArticle(**values)


def classification(**overrides: object) -> ClassifiedArticle:
    values: dict[str, object] = {
        "article_id": "article-1",
        "classifier_version": "classification-v2",
        "is_relevant": True,
        "markets": (Market.US,),
        "category": Domain.TECHNOLOGY,
        "topics": (Topic.AI,),
        "confidence": 0.9,
        "classified_at": NOW - timedelta(minutes=10),
    }
    values.update(overrides)
    return ClassifiedArticle(**values)


def preference(**overrides: object) -> UserPreference:
    values: dict[str, object] = {
        "user_id": "user-1",
        "markets": (Market.US,),
        "categories": (),
        "topics": (),
        "muted_source_ids": (),
        "muted_topics": (),
        "breaking_alert_enabled": True,
        "hourly_update_enabled": True,
        "daily_digest_enabled": True,
    }
    values.update(overrides)
    return UserPreference(**values)


def test_article_classification_identity_mismatch_fails_fast() -> None:
    with pytest.raises(ValueError, match="article_id"):
        match_article(
            article=article(article_id="article-a"),
            classification=classification(article_id="article-b"),
            preference=preference(),
            matched_at=NOW,
        )


def test_naive_matched_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        match_article(
            article=article(),
            classification=classification(),
            preference=preference(),
            matched_at=datetime(2026, 8, 15, 8, 0),
        )


def test_irrelevant_classification_does_not_match() -> None:
    result = match_article(
        article=article(),
        classification=classification(
            is_relevant=False,
            markets=(),
            category=None,
            topics=(),
        ),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is None


def test_market_only_match() -> None:
    result = match_article(
        article=article(),
        classification=classification(),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is not None
    assert result.match_reasons == ("market:US",)
    assert result.relevance_score == pytest.approx(0.9)


def test_category_only_match() -> None:
    result = match_article(
        article=article(),
        classification=classification(markets=(Market.EU,)),
        preference=preference(
            markets=(),
            categories=(Domain.TECHNOLOGY,),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.match_reasons == ("category:TECHNOLOGY",)


def test_topic_only_match() -> None:
    result = match_article(
        article=article(),
        classification=classification(markets=(Market.EU,)),
        preference=preference(
            markets=(),
            topics=(Topic.AI,),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.match_reasons == ("topic:AI",)


def test_multiple_reasons_have_deterministic_order() -> None:
    result = match_article(
        article=article(),
        classification=classification(
            markets=(Market.US, Market.EU),
            topics=(Topic.AI, Topic.SEMICONDUCTORS),
        ),
        preference=preference(
            markets=(Market.EU, Market.US),
            categories=(Domain.TECHNOLOGY,),
            topics=(Topic.SEMICONDUCTORS, Topic.AI),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.match_reasons == (
        "market:US",
        "market:EU",
        "category:TECHNOLOGY",
        "topic:AI",
        "topic:SEMICONDUCTORS",
    )


def test_no_positive_overlap_returns_none() -> None:
    result = match_article(
        article=article(),
        classification=classification(),
        preference=preference(markets=(Market.EU,)),
        matched_at=NOW,
    )

    assert result is None


def test_empty_positive_preferences_are_not_wildcards() -> None:
    result = match_article(
        article=article(),
        classification=classification(),
        preference=preference(
            markets=(),
            categories=(),
            topics=(),
        ),
        matched_at=NOW,
    )

    assert result is None


def test_empty_market_dimension_does_not_match_all_markets() -> None:
    result = match_article(
        article=article(),
        classification=classification(category=Domain.FINANCE),
        preference=preference(
            markets=(),
            categories=(Domain.TECHNOLOGY,),
            topics=(),
        ),
        matched_at=NOW,
    )

    assert result is None


def test_muted_source_vetoes_positive_match() -> None:
    result = match_article(
        article=article(),
        classification=classification(),
        preference=preference(muted_source_ids=("us_test_source",)),
        matched_at=NOW,
    )

    assert result is None


def test_muted_topic_vetoes_positive_match() -> None:
    result = match_article(
        article=article(),
        classification=classification(topics=(Topic.AI, Topic.REGULATION)),
        preference=preference(
            topics=(Topic.AI,),
            muted_topics=(Topic.REGULATION,),
        ),
        matched_at=NOW,
    )

    assert result is None


def test_unrelated_muted_topic_does_not_block() -> None:
    result = match_article(
        article=article(),
        classification=classification(topics=(Topic.AI,)),
        preference=preference(muted_topics=(Topic.REGULATION,)),
        matched_at=NOW,
    )

    assert result is not None


def test_exactly_24_hours_old_is_still_fresh() -> None:
    result = match_article(
        article=article(published_at=NOW - timedelta(hours=24)),
        classification=classification(),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is not None


def test_older_than_24_hours_is_stale() -> None:
    result = match_article(
        article=article(published_at=NOW - timedelta(hours=24, seconds=1)),
        classification=classification(),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is None


def test_missing_published_at_falls_back_to_discovered_at() -> None:
    result = match_article(
        article=article(
            published_at=None,
            discovered_at=NOW - timedelta(hours=25),
        ),
        classification=classification(),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is None


def test_future_published_at_falls_back_to_discovered_at() -> None:
    result = match_article(
        article=article(
            published_at=NOW + timedelta(hours=1),
            discovered_at=NOW - timedelta(hours=25),
        ),
        classification=classification(),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is None


def test_matched_at_before_discovered_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="discovered_at"):
        match_article(
            article=article(discovered_at=NOW + timedelta(hours=1)),
            classification=classification(),
            preference=preference(),
            matched_at=NOW,
        )


def test_matched_at_before_classified_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="classified_at"):
        match_article(
            article=article(),
            classification=classification(classified_at=NOW + timedelta(hours=1)),
            preference=preference(),
            matched_at=NOW,
        )


def test_score_uses_matched_dimensions_not_number_of_topics() -> None:
    result = match_article(
        article=article(),
        classification=classification(
            topics=(Topic.AI, Topic.BANKING, Topic.SEMICONDUCTORS),
            confidence=0.9,
        ),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.FINANCE,),
            topics=(Topic.AI, Topic.BANKING, Topic.SEMICONDUCTORS),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.relevance_score == pytest.approx(0.6)


def test_all_three_dimensions_can_produce_high_importance() -> None:
    result = match_article(
        article=article(),
        classification=classification(confidence=0.95),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(Topic.AI,),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.relevance_score == pytest.approx(0.95)
    assert result.importance is AlertImportance.HIGH


def test_one_dimension_remains_normal_even_with_high_confidence() -> None:
    result = match_article(
        article=article(),
        classification=classification(confidence=1.0),
        preference=preference(),
        matched_at=NOW,
    )

    assert result is not None
    assert result.importance is AlertImportance.NORMAL


def test_exactly_0_90_raw_score_is_high() -> None:
    result = match_article(
        article=article(),
        classification=classification(
            confidence=0.90,
            markets=(Market.US,),
            category=Domain.TECHNOLOGY,
        ),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.importance is AlertImportance.HIGH


def test_just_below_0_90_raw_score_is_normal() -> None:
    result = match_article(
        article=article(),
        classification=classification(
            confidence=0.899999,
            markets=(Market.US,),
            category=Domain.TECHNOLOGY,
        ),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.importance is AlertImportance.NORMAL


def test_rounding_cannot_promote_sub_0_90_raw_score() -> None:
    result = match_article(
        article=article(),
        classification=classification(
            confidence=0.8999996,
            markets=(Market.US,),
            category=Domain.TECHNOLOGY,
        ),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.relevance_score == 0.900000
    assert result.importance is AlertImportance.NORMAL


def test_hourly_and_daily_flags_do_not_suppress_semantic_candidate() -> None:
    result = match_article(
        article=article(),
        classification=classification(),
        preference=preference(
            hourly_update_enabled=False,
            daily_digest_enabled=False,
        ),
        matched_at=NOW,
    )

    assert result is not None


def test_breaking_disabled_keeps_candidate_but_not_breaking_eligible() -> None:
    result = match_article(
        article=article(discovered_at=NOW - timedelta(minutes=30)),
        classification=classification(confidence=0.95),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(Topic.AI,),
            breaking_alert_enabled=False,
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.importance is AlertImportance.HIGH
    assert result.breaking_eligible is False


def test_high_recent_candidate_can_be_breaking_eligible() -> None:
    result = match_article(
        article=article(discovered_at=NOW - timedelta(hours=2)),
        classification=classification(confidence=0.95),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(Topic.AI,),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.breaking_eligible is True


def test_high_candidate_discovered_over_two_hours_ago_is_not_breaking() -> None:
    result = match_article(
        article=article(discovered_at=NOW - timedelta(hours=2, seconds=1)),
        classification=classification(confidence=0.95),
        preference=preference(
            markets=(Market.US,),
            categories=(Domain.TECHNOLOGY,),
            topics=(Topic.AI,),
        ),
        matched_at=NOW,
    )

    assert result is not None
    assert result.breaking_eligible is False


def test_same_user_article_pair_has_stable_candidate_id() -> None:
    first = build_alert_candidate_id(user_id="user-1", article_id="article-1")
    second = build_alert_candidate_id(user_id="user-1", article_id="article-1")

    assert first == second
    assert len(first) == 64


def test_candidate_id_changes_with_user_or_article() -> None:
    base = build_alert_candidate_id(user_id="user-1", article_id="article-1")

    assert base != build_alert_candidate_id(user_id="user-2", article_id="article-1")
    assert base != build_alert_candidate_id(user_id="user-1", article_id="article-2")
