"""Backend-neutral article persistence boundary."""

from typing import Protocol

from market_intelligence.articles import CanonicalArticle


class ArticlePersistenceError(RuntimeError):
    """Raised when a validated article cannot be persisted."""

    def __init__(self, article_id: str) -> None:
        self.article_id = article_id
        super().__init__(f"failed to persist article {article_id}")


class PersistenceConfigurationError(ValueError):
    """Raised when required persistence configuration is missing."""


class ArticleRepository(Protocol):
    """Small persistence boundary consumed by the business pipeline."""

    def save(self, article: CanonicalArticle) -> None:
        """Persist one article idempotently by its stable article ID."""
