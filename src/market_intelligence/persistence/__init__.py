"""Public article persistence API."""

from market_intelligence.persistence.articles import (
    ArticlePersistenceError,
    ArticleRepository,
    PersistenceConfigurationError,
)
from market_intelligence.persistence.classifications import (
    ClassificationClaim,
    ClassificationFailure,
    ClassificationKey,
    ClassificationLineage,
    ClassificationLineageMismatchError,
    ClassificationPersistenceError,
    ClassificationRecord,
    ClassificationRepository,
    ClassificationStatus,
    CompletionOutcome,
    CompletionResult,
    EnqueueOutcome,
    EnqueueResult,
    FailureDisposition,
    FailureOutcome,
    FailureResult,
    LeaseRenewalOutcome,
    LeaseRenewalResult,
)
from market_intelligence.persistence.supabase_classification_repository import (
    SupabaseClassificationRepository,
    create_classification_repository_from_environment,
)
from market_intelligence.persistence.supabase_repository import (
    SupabaseArticleRepository,
    create_article_repository_from_environment,
)

__all__ = [
    "ArticlePersistenceError",
    "ArticleRepository",
    "ClassificationClaim",
    "ClassificationFailure",
    "ClassificationKey",
    "ClassificationLineage",
    "ClassificationLineageMismatchError",
    "ClassificationPersistenceError",
    "ClassificationRecord",
    "ClassificationRepository",
    "ClassificationStatus",
    "CompletionOutcome",
    "CompletionResult",
    "EnqueueOutcome",
    "EnqueueResult",
    "FailureDisposition",
    "FailureOutcome",
    "FailureResult",
    "LeaseRenewalOutcome",
    "LeaseRenewalResult",
    "PersistenceConfigurationError",
    "SupabaseArticleRepository",
    "SupabaseClassificationRepository",
    "create_article_repository_from_environment",
    "create_classification_repository_from_environment",
]
