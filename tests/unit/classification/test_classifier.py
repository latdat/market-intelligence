import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import (
    ClassificationError,
    ClassificationErrorCategory,
    DeepSeekSettings,
    DeepSeekV4FlashClassifier,
    build_classification_input,
    validate_classification_rights,
)
from market_intelligence.classification.pricing import load_pricing_catalog
from market_intelligence.source_registry import SourceConfig

PRICING_PATH = Path("config/classification/deepseek_pricing.toml")


def change_rights(source: SourceConfig, **changes: object) -> SourceConfig:
    rights_payload = source.rights.model_dump(mode="python")
    rights_payload.update(changes)
    rights = source.rights.__class__.model_validate(rights_payload)
    return source.model_copy(update={"rights": rights})


def test_approved_true_rights_and_matching_source_are_accepted(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    validate_classification_rights(canonical_article, approved_source)


@pytest.mark.parametrize(
    ("status", "can_ai_process"),
    [
        ("PENDING", True),
        ("REJECTED", True),
        ("APPROVED", False),
        ("APPROVED", "REVIEWED"),
    ],
)
def test_rights_gate_denies_every_non_approved_true_combination(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    status: str,
    can_ai_process: bool | str,
) -> None:
    source = change_rights(
        approved_source,
        rights_review_status=status,
        can_ai_process=can_ai_process,
    )

    with pytest.raises(ClassificationError) as captured:
        validate_classification_rights(canonical_article, source)

    assert captured.value.category is ClassificationErrorCategory.RIGHTS_DENIED
    assert captured.value.retryable is False
    assert captured.value.provider_attempts == 0


def test_source_identity_mismatch_is_rejected_as_invalid_input(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    article = canonical_article.model_copy(update={"source_id": "different_source"})

    with pytest.raises(ClassificationError) as captured:
        validate_classification_rights(article, approved_source)

    assert captured.value.category is ClassificationErrorCategory.INVALID_INPUT
    assert captured.value.provider_attempts == 0


def test_classification_input_contains_only_approved_semantic_fields(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    classification_input = build_classification_input(canonical_article, approved_source)

    assert set(classification_input.__class__.model_fields) == {
        "title",
        "description",
        "source_market",
        "source_language",
        "source_domains",
        "source_type",
        "authority_level",
    }
    assert classification_input.description == canonical_article.description
    assert [value.value for value in classification_input.source_domains] == [
        "FINANCE",
        "LAW_POLICY",
    ]


def test_classification_input_allows_null_description(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    article = canonical_article.model_copy(update={"description": None})

    assert build_classification_input(article, approved_source).description is None


@pytest.mark.parametrize(
    "source_factory",
    [
        lambda source: change_rights(source, rights_review_status="PENDING"),
        lambda source: change_rights(source, can_ai_process="REVIEWED"),
    ],
)
def test_denied_request_builds_no_prompt_and_makes_zero_http_calls(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    source_factory: Callable[[SourceConfig], SourceConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(500, request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            classifier = DeepSeekV4FlashClassifier(
                DeepSeekSettings(api_key="unit-test-key"),
                load_pricing_catalog(PRICING_PATH),
                client,
            )
            prompt_builder = Mock(side_effect=AssertionError("prompt must not be built"))
            monkeypatch.setattr(classifier, "_build_request_body", prompt_builder)
            with pytest.raises(ClassificationError) as captured:
                await classifier.classify(
                    canonical_article,
                    source_factory(approved_source),
                )
            assert captured.value.provider_attempts == 0
            prompt_builder.assert_not_called()

    asyncio.run(run())
    assert http_calls == 0
