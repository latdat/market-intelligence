"""Pure local, versioned deterministic article classification."""

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification.models import (
    DETERMINISTIC_RULES_VERSION,
    Topic,
)
from market_intelligence.source_registry import Domain, Market, SourceConfig

DEFAULT_DETERMINISTIC_RULES_PATH = Path("config/classification/deterministic_rules.toml")


def normalize_matching_text(value: str) -> str:
    """Normalize matching text without changing semantic token boundaries."""
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


class DeterministicDisposition(StrEnum):
    CONFIDENT = "CONFIDENT"
    AMBIGUOUS = "AMBIGUOUS"


class _RuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Thresholds(_RuleModel):
    min_category_score: int = Field(ge=4)
    min_category_margin: int = Field(ge=2)
    min_market_score: int = Field(ge=2)


class _CategoryRule(_RuleModel):
    category: Domain
    strong_title: tuple[str, ...]
    title_keywords: tuple[str, ...]
    description_keywords: tuple[str, ...]


class _MarketRule(_RuleModel):
    market: Market
    title_keywords: tuple[str, ...]
    description_keywords: tuple[str, ...]


class _TopicRule(_RuleModel):
    topic: Topic
    keywords: tuple[str, ...]


class DeterministicRules(_RuleModel):
    version: str
    thresholds: _Thresholds
    categories: tuple[_CategoryRule, ...]
    markets: tuple[_MarketRule, ...]
    topics: tuple[_TopicRule, ...]

    @model_validator(mode="after")
    def validate_rules(self) -> "DeterministicRules":
        if self.version != DETERMINISTIC_RULES_VERSION:
            raise ValueError(
                "deterministic rule semantics require an explicit code/config version bump"
            )
        for values, expected, name in (
            (self.categories, set(Domain), "categories"),
            (self.markets, set(Market), "markets"),
            (self.topics, set(Topic), "topics"),
        ):
            identities = {
                getattr(value, "category", getattr(value, "market", getattr(value, "topic", None)))
                for value in values
            }
            if identities != expected or len(values) != len(identities):
                raise ValueError(f"{name} must define each controlled value exactly once")
        for category_rule in self.categories:
            _validate_keywords(category_rule.strong_title)
            _validate_keywords(category_rule.title_keywords)
            _validate_keywords(category_rule.description_keywords)
        for market_rule in self.markets:
            _validate_keywords(market_rule.title_keywords)
            _validate_keywords(market_rule.description_keywords)
        for topic_rule in self.topics:
            _validate_keywords(topic_rule.keywords)
        return self


def _validate_keywords(keywords: tuple[str, ...]) -> None:
    if not keywords or any(not normalize_matching_text(value) for value in keywords):
        raise ValueError("deterministic keyword lists must contain non-empty values")
    normalized = tuple(normalize_matching_text(value) for value in keywords)
    if len(normalized) != len(set(normalized)):
        raise ValueError("deterministic keyword lists must not contain duplicates")


def load_deterministic_rules(
    path: Path = DEFAULT_DETERMINISTIC_RULES_PATH,
) -> DeterministicRules:
    """Load and strictly validate one local deterministic rule set."""
    try:
        with path.open("rb") as rules_file:
            payload = tomllib.load(rules_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load deterministic rules: {path}") from error
    return DeterministicRules.model_validate(payload)


@dataclass(frozen=True, slots=True)
class MatchedEvidence:
    target: str
    kind: str
    value: str
    score: int


@dataclass(frozen=True, slots=True)
class DeterministicResult:
    disposition: DeterministicDisposition
    markets: tuple[Market, ...] = ()
    category: Domain | None = None
    topics: tuple[Topic, ...] = ()
    confidence: float | None = None
    category_scores: tuple[tuple[Domain, int], ...] = ()
    evidence: tuple[MatchedEvidence, ...] = ()


_MARKET_ORDER = {Market.VN: 0, Market.US: 1, Market.EU: 2, Market.CN: 3}


class DeterministicClassifier:
    """Conservative metadata-only classifier with no network or database access."""

    def __init__(self, rules: DeterministicRules) -> None:
        self._rules = rules

    @property
    def rules_version(self) -> str:
        return self._rules.version

    def classify(
        self,
        article: CanonicalArticle,
        source: SourceConfig,
    ) -> DeterministicResult:
        if article.source_id != source.source_id or article.market is not source.market:
            raise ValueError("article and source identity/market must match")

        title = normalize_matching_text(article.title)
        description = normalize_matching_text(article.description or "")
        evidence: list[MatchedEvidence] = []
        scores: dict[Domain, int] = {}

        for rule in self._rules.categories:
            score = 0
            matched = _first_match(title, rule.strong_title)
            if matched is not None:
                score += 4
                evidence.append(MatchedEvidence(rule.category.value, "strong_title", matched, 4))
            matched = _first_match(title, rule.title_keywords)
            if matched is not None:
                score += 2
                evidence.append(MatchedEvidence(rule.category.value, "title_keyword", matched, 2))
            matched = _first_match(description, rule.description_keywords)
            if matched is not None:
                score += 1
                evidence.append(
                    MatchedEvidence(rule.category.value, "description_keyword", matched, 1)
                )
            if len(source.domains) == 1 and source.domains[0] is rule.category:
                score += 2
                evidence.append(
                    MatchedEvidence(rule.category.value, "single_source_domain_prior", "", 2)
                )
            scores[rule.category] = score

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
        top_category, top_score = ranked[0]
        second_score = ranked[1][1]
        score_snapshot = tuple(sorted(scores.items(), key=lambda item: item[0].value))
        if (
            top_score < self._rules.thresholds.min_category_score
            or top_score - second_score < self._rules.thresholds.min_category_margin
        ):
            return DeterministicResult(
                disposition=DeterministicDisposition.AMBIGUOUS,
                category_scores=score_snapshot,
                evidence=tuple(evidence),
            )

        markets = self._classify_markets(article.market, title, description, evidence)
        if not markets:
            return DeterministicResult(
                disposition=DeterministicDisposition.AMBIGUOUS,
                category_scores=score_snapshot,
                evidence=tuple(evidence),
            )
        topics = tuple(
            sorted(
                (
                    rule.topic
                    for rule in self._rules.topics
                    if _first_match(title, rule.keywords) is not None
                    or _first_match(description, rule.keywords) is not None
                ),
                key=lambda value: value.value,
            )
        )
        confidence = min(
            0.99,
            round(0.70 + min(top_score, 8) * 0.03 + min(top_score - second_score, 6) * 0.02, 2),
        )
        return DeterministicResult(
            disposition=DeterministicDisposition.CONFIDENT,
            markets=markets,
            category=top_category,
            topics=topics[:5],
            confidence=confidence,
            category_scores=score_snapshot,
            evidence=tuple(evidence),
        )

    def _classify_markets(
        self,
        source_market: Market,
        title: str,
        description: str,
        evidence: list[MatchedEvidence],
    ) -> tuple[Market, ...]:
        selected: list[Market] = []
        for rule in self._rules.markets:
            score = 1 if rule.market is source_market else 0
            content_score = 0
            if rule.market is source_market:
                evidence.append(MatchedEvidence(rule.market.value, "source_market_prior", "", 1))
            matched = _first_match(title, rule.title_keywords)
            if matched is not None:
                score += 2
                content_score += 2
                evidence.append(MatchedEvidence(rule.market.value, "title_keyword", matched, 2))
            matched = _first_match(description, rule.description_keywords)
            if matched is not None:
                score += 1
                content_score += 1
                evidence.append(
                    MatchedEvidence(rule.market.value, "description_keyword", matched, 1)
                )
            if content_score > 0 and score >= self._rules.thresholds.min_market_score:
                selected.append(rule.market)
        return tuple(sorted(selected, key=_MARKET_ORDER.__getitem__))


def _first_match(text: str, keywords: tuple[str, ...]) -> str | None:
    for keyword in sorted(normalize_matching_text(value) for value in keywords):
        if _has_token_boundary_match(text, keyword):
            return keyword
    return None


def _has_token_boundary_match(text: str, keyword: str) -> bool:
    prefix = r"(?<!\w)" if keyword[0].isalnum() or keyword[0] == "_" else ""
    suffix = r"(?!\w)" if keyword[-1].isalnum() or keyword[-1] == "_" else ""
    return re.search(f"{prefix}{re.escape(keyword)}{suffix}", text) is not None
