"""Backend-neutral classification protocol, rights gate, and sanitized failures."""

from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification.models import (
    ClassificationInput,
    ClassificationResult,
    ClassificationUsage,
)
from market_intelligence.source_registry import RightsReviewStatus, SourceConfig


class ClassificationErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    RIGHTS_DENIED = "rights_denied"
    AI_FALLBACK_NOT_ALLOWED = "AI_FALLBACK_NOT_ALLOWED"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    PROVIDER_HTTP = "provider_http"
    MALFORMED_RESPONSE = "malformed_response"
    EMPTY_CONTENT = "empty_content"
    INVALID_OUTPUT = "invalid_output"
    INVALID_USAGE = "invalid_usage"
    INSUFFICIENT_SYSTEM_RESOURCE = "insufficient_system_resource"
    CONTENT_FILTER = "content_filter"
    OUTPUT_TRUNCATED = "output_truncated"
    UNEXPECTED_TOOL_CALL = "unexpected_tool_call"


class ClassificationConfigurationError(ValueError):
    """Raised before provider use when local configuration is invalid."""


class ClassificationError(RuntimeError):
    """Sanitized per-article terminal failure with observed cost metadata."""

    def __init__(
        self,
        article_id: str,
        *,
        category: ClassificationErrorCategory,
        retryable: bool,
        provider_attempts: int,
        last_http_status: int | None = None,
        usage: ClassificationUsage | None = None,
        estimated_cost_usd: Decimal | None = None,
        pricing_id: str | None = None,
    ) -> None:
        self.article_id = article_id
        self.category = category
        self.retryable = retryable
        self.provider_attempts = provider_attempts
        self.last_http_status = last_http_status
        self.usage = usage
        self.estimated_cost_usd = estimated_cost_usd
        self.pricing_id = pricing_id
        status_detail = (
            f", last_http_status={last_http_status}" if last_http_status is not None else ""
        )
        super().__init__(
            f"classification failed for {article_id}: category={category.value}, "
            f"retryable={retryable}, provider_attempts={provider_attempts}{status_detail}"
        )


class ArticleClassifier(Protocol):
    """Classification boundary that must enforce rights before provider access."""

    async def classify(
        self,
        article: CanonicalArticle,
        source: SourceConfig,
    ) -> ClassificationResult:
        """Classify one article without durable or cross-run state."""


def validate_article_source(
    article: CanonicalArticle,
    source: SourceConfig,
) -> None:
    """Validate local article/source identity without requiring AI-processing rights."""
    if article.source_id != source.source_id:
        raise ClassificationError(
            article.article_id,
            category=ClassificationErrorCategory.INVALID_INPUT,
            retryable=False,
            provider_attempts=0,
        )


def validate_classification_rights(
    article: CanonicalArticle,
    source: SourceConfig,
) -> None:
    """Fail before prompt construction unless explicit AI rights are approved."""
    validate_article_source(article, source)
    if (
        source.rights.rights_review_status is not RightsReviewStatus.APPROVED
        or source.rights.can_ai_process is not True
    ):
        raise ClassificationError(
            article.article_id,
            category=ClassificationErrorCategory.RIGHTS_DENIED,
            retryable=False,
            provider_attempts=0,
        )


def build_classification_input(
    article: CanonicalArticle,
    source: SourceConfig,
) -> ClassificationInput:
    """Build provider-bound semantic input after the caller has passed the rights gate."""
    return ClassificationInput(
        title=article.title,
        description=article.description,
        source_market=article.market,
        source_language=article.language,
        source_domains=tuple(source.domains),
        source_type=source.source_type,
        authority_level=source.authority_level,
    )
