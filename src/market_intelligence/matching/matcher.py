"""Pure deterministic ClassifiedArticle + UserPreference matching."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import ClassifiedArticle, Topic
from market_intelligence.models import AlertCandidate, AlertImportance, UserPreference
from market_intelligence.source_registry import Market

NORMAL_FRESHNESS = timedelta(hours=24)
BREAKING_FRESHNESS = timedelta(hours=2)

_MARKET_ORDER = {
    Market.VN: 0,
    Market.US: 1,
    Market.EU: 2,
    Market.CN: 3,
}


def _normalize_matched_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("matched_at must include timezone information")
    return value.astimezone(UTC)


def build_alert_candidate_id(*, user_id: str, article_id: str) -> str:
    """Return the stable logical identity for one user/article candidate pair."""
    payload = json.dumps(
        ["alert-candidate-v1", user_id, article_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _is_stale(*, article: CanonicalArticle, matched_at: datetime) -> bool:
    if article.published_at is not None and article.published_at <= matched_at:
        effective_time = article.published_at
    else:
        effective_time = article.discovered_at
    return matched_at - effective_time > NORMAL_FRESHNESS


def _matching_markets(
    classification: ClassifiedArticle,
    preference: UserPreference,
) -> tuple[Market, ...]:
    preferred = set(preference.markets)
    return tuple(
        sorted(
            (market for market in classification.markets if market in preferred),
            key=_MARKET_ORDER.__getitem__,
        )
    )


def _matching_topics(
    classification: ClassifiedArticle,
    preference: UserPreference,
) -> tuple[Topic, ...]:
    preferred = set(preference.topics)
    return tuple(
        sorted(
            (topic for topic in classification.topics if topic in preferred),
            key=lambda value: value.value,
        )
    )


def match_article(
    *,
    article: CanonicalArticle,
    classification: ClassifiedArticle,
    preference: UserPreference,
    matched_at: datetime,
) -> AlertCandidate | None:
    """Return one deterministic candidate when the article matches the preference."""

    if article.article_id != classification.article_id:
        raise ValueError("article and classification article_id values must match")

    normalized_matched_at = _normalize_matched_at(matched_at)

    if normalized_matched_at < article.discovered_at:
        raise ValueError("matched_at cannot be before article.discovered_at")

    if normalized_matched_at < classification.classified_at:
        raise ValueError("matched_at cannot be before classification.classified_at")

    if not classification.is_relevant:
        return None

    if article.source_id in preference.muted_source_ids:
        return None

    if set(classification.topics).intersection(preference.muted_topics):
        return None

    if _is_stale(article=article, matched_at=normalized_matched_at):
        return None

    market_matches = _matching_markets(classification, preference)
    category_matches = (
        classification.category is not None and classification.category in preference.categories
    )
    topic_matches = _matching_topics(classification, preference)

    reasons: list[str] = [
        *(f"market:{market.value}" for market in market_matches),
    ]
    if category_matches and classification.category is not None:
        reasons.append(f"category:{classification.category.value}")
    reasons.extend(f"topic:{topic.value}" for topic in topic_matches)

    if not reasons:
        return None

    active_dimensions = sum(
        (
            bool(preference.markets),
            bool(preference.categories),
            bool(preference.topics),
        )
    )
    if active_dimensions == 0:
        return None

    matched_dimensions = sum(
        (
            bool(market_matches),
            category_matches,
            bool(topic_matches),
        )
    )
    coverage = matched_dimensions / active_dimensions
    raw_relevance_score = classification.confidence * coverage

    importance = (
        AlertImportance.HIGH
        if matched_dimensions >= 2 and raw_relevance_score >= 0.90
        else AlertImportance.NORMAL
    )

    relevance_score = round(raw_relevance_score, 6)

    discovery_age = normalized_matched_at - article.discovered_at
    breaking_eligible = (
        preference.breaking_alert_enabled
        and importance is AlertImportance.HIGH
        and timedelta(0) <= discovery_age <= BREAKING_FRESHNESS
    )

    return AlertCandidate(
        candidate_id=build_alert_candidate_id(
            user_id=preference.user_id,
            article_id=article.article_id,
        ),
        user_id=preference.user_id,
        article_id=article.article_id,
        matched_at=normalized_matched_at,
        match_reasons=tuple(reasons),
        importance=importance,
        relevance_score=relevance_score,
        breaking_eligible=breaking_eligible,
    )
