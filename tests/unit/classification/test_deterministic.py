"""Offline tests for deterministic-rules-v1 semantics."""

from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import (
    DeterministicClassifier,
    DeterministicDisposition,
    Topic,
    load_deterministic_rules,
    normalize_matching_text,
)
from market_intelligence.source_registry import Domain, Market, SourceConfig

RULES_PATH = Path("config/classification/deterministic_rules.toml")
NOW = datetime(2026, 8, 15, tzinfo=UTC)


def source(
    *,
    market: Market = Market.US,
    domains: tuple[Domain, ...] = (Domain.FINANCE, Domain.LAW_POLICY),
) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "test_source",
            "name": "Test Source",
            "market": market,
            "language": "en",
            "source_type": "REGULATOR",
            "authority_level": "PRIMARY",
            "domains": [value.value for value in domains],
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://example.test/feed",
                "poll_interval_minutes": 15,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": False,
                "can_show_snippet": True,
                "can_redistribute_full_text": False,
                "rights_review_status": "PENDING",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def article(
    title: str,
    *,
    description: str | None = None,
    market: Market = Market.US,
) -> CanonicalArticle:
    return CanonicalArticle(
        article_id="article-1",
        source_id="test_source",
        url="https://example.test/article",
        canonical_url="https://example.test/article",
        title=title,
        description=description,
        language="en",
        market=market,
        discovered_at=NOW,
        content_hash="hash",
    )


def classifier() -> DeterministicClassifier:
    return DeterministicClassifier(load_deterministic_rules(RULES_PATH))


def test_unicode_case_and_whitespace_normalization() -> None:
    decomposed = "  QUYẾT\tĐỊNH \n LÃI SUẤT  "

    assert normalize_matching_text(decomposed) == "quyết định lãi suất"
    result = classifier().classify(article(decomposed + " Federal Reserve"), source())
    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.category is Domain.FINANCE


def test_matching_uses_token_boundaries_not_raw_substrings() -> None:
    result = classifier().classify(
        article("Federal Reserve interest rate decision for painting exports"),
        source(),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert Topic.AI not in result.topics


def test_strong_title_rule_is_confident() -> None:
    result = classifier().classify(
        article("Federal Reserve interest rate decision"),
        source(),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.category is Domain.FINANCE
    assert dict(result.category_scores)[Domain.FINANCE] >= 4


def test_single_source_domain_prior_plus_keyword_is_confident() -> None:
    result = classifier().classify(
        article("Federal Reserve banking outlook"),
        source(domains=(Domain.FINANCE,)),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.category is Domain.FINANCE


def test_conflicting_categories_are_ambiguous() -> None:
    result = classifier().classify(article("Policy and banking update"), source())

    assert result.disposition is DeterministicDisposition.AMBIGUOUS
    assert result.category is None


def test_insufficient_evidence_is_ambiguous() -> None:
    result = classifier().classify(article("General announcement"), source())

    assert result.disposition is DeterministicDisposition.AMBIGUOUS
    assert result.category is None


def test_source_market_without_content_market_evidence_is_ambiguous() -> None:
    result = classifier().classify(article("Interest rate decision"), source())

    assert dict(result.category_scores)[Domain.FINANCE] >= 4
    assert result.disposition is DeterministicDisposition.AMBIGUOUS
    assert result.markets == ()


def test_us_source_content_only_about_eu_does_not_emit_us() -> None:
    result = classifier().classify(
        article("ECB interest rate decision"),
        source(market=Market.US),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.markets == (Market.EU,)


def test_eu_source_content_only_about_us_does_not_emit_eu() -> None:
    result = classifier().classify(
        article("Federal Reserve interest rate decision", market=Market.EU),
        source(market=Market.EU),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.markets == (Market.US,)


def test_federal_reserve_alias_is_valid_us_content_evidence() -> None:
    result = classifier().classify(
        article("Federal Reserve interest rate decision"),
        source(market=Market.US),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.markets == (Market.US,)


def test_source_market_prior_can_support_but_not_replace_description_evidence() -> None:
    result = classifier().classify(
        article(
            "Interest rate decision",
            description="Federal Reserve officials announced the decision.",
        ),
        source(market=Market.US),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.markets == (Market.US,)


def test_controlled_multi_market_output() -> None:
    result = classifier().classify(
        article("Federal Reserve interest rate decision affects the European Commission and PBOC"),
        source(),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.markets == (Market.US, Market.EU, Market.CN)


def test_controlled_topics_are_canonical_and_bounded() -> None:
    result = classifier().classify(
        article(
            "Federal Reserve interest rate decision on AI semiconductor banking regulation",
            description="The chip policy affects bank regulation.",
        ),
        source(),
    )

    assert result.disposition is DeterministicDisposition.CONFIDENT
    assert result.topics == (
        Topic.AI,
        Topic.BANKING,
        Topic.INTEREST_RATES,
        Topic.REGULATION,
        Topic.SEMICONDUCTORS,
    )


def test_v1_never_makes_a_deterministic_irrelevant_decision() -> None:
    result = classifier().classify(article("Celebrity sports result"), source())

    assert result.disposition is DeterministicDisposition.AMBIGUOUS
    assert not hasattr(result, "is_relevant")


def test_output_is_reproducible() -> None:
    candidate = article(
        "Interest rate decision for China",
        description="Banking regulation in the United States.",
    )
    configured_source = source()
    local = classifier()

    assert local.classify(candidate, configured_source) == local.classify(
        candidate, configured_source
    )
