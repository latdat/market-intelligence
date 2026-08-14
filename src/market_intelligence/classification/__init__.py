"""Public classification contracts and DeepSeek adapter."""

from market_intelligence.classification.classifier import (
    ArticleClassifier,
    ClassificationConfigurationError,
    ClassificationError,
    ClassificationErrorCategory,
    build_classification_input,
    validate_classification_rights,
)
from market_intelligence.classification.deepseek import (
    DEEPSEEK_MODEL,
    DeepSeekSettings,
    DeepSeekV4FlashClassifier,
    create_deepseek_classifier_from_environment,
)
from market_intelligence.classification.models import (
    CLASSIFIER_VERSION,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
    ClassificationInput,
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
    ProviderClassificationOutput,
    Topic,
)

__all__ = [
    "CLASSIFIER_VERSION",
    "DEEPSEEK_MODEL",
    "PROMPT_VERSION",
    "TAXONOMY_VERSION",
    "ArticleClassifier",
    "ClassificationConfigurationError",
    "ClassificationError",
    "ClassificationErrorCategory",
    "ClassificationInput",
    "ClassificationResult",
    "ClassificationUsage",
    "ClassifiedArticle",
    "DeepSeekSettings",
    "DeepSeekV4FlashClassifier",
    "ProviderClassificationOutput",
    "Topic",
    "build_classification_input",
    "create_deepseek_classifier_from_environment",
    "validate_classification_rights",
]
