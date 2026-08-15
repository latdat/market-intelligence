"""Read-only article discovery boundary for classification orchestration."""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from market_intelligence.articles import CanonicalArticle
from market_intelligence.persistence.classifications import ClassificationKey, ClassificationLineage


class DiscoveryScope(BaseModel):
    """Optional bounded selectors used by controlled discovery/backfill callers."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_ids: tuple[str, ...] | None = None
    article_ids: tuple[str, ...] | None = None
    discovered_from: datetime | None = None
    discovered_to: datetime | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "DiscoveryScope":
        for name, values in (("source_ids", self.source_ids), ("article_ids", self.article_ids)):
            if values is not None:
                if not values or any(not value for value in values):
                    raise ValueError(f"{name} must be non-empty when supplied")
                if len(values) != len(set(values)):
                    raise ValueError(f"{name} must not contain duplicates")

        for name, value in (
            ("discovered_from", self.discovered_from),
            ("discovered_to", self.discovered_to),
        ):
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{name} must include timezone information")
                object.__setattr__(self, name, value.astimezone(UTC))

        if (
            self.discovered_from is not None
            and self.discovered_to is not None
            and self.discovered_from >= self.discovered_to
        ):
            raise ValueError("discovered_from must be earlier than discovered_to")
        return self


class ClassificationWorkReadError(RuntimeError):
    """Sanitized failure for classification discovery/article reads."""

    def __init__(self, operation: str, article_id: str | None = None) -> None:
        self.operation = operation
        self.article_id = article_id
        suffix = f" for article {article_id}" if article_id is not None else ""
        super().__init__(f"classification work read {operation} failed{suffix}")


class ClassificationWorkReader(Protocol):
    """Read only the article state needed by DE-009B."""

    def list_unclassified_articles(
        self,
        *,
        classifier_version: str,
        eligible_source_ids: tuple[str, ...],
        limit: int,
        scope: DiscoveryScope | None = None,
    ) -> tuple[CanonicalArticle, ...]: ...

    def get_article(self, article_id: str) -> CanonicalArticle | None: ...

    def find_lineage_mismatch(
        self,
        *,
        classifier_version: str,
        lineage: ClassificationLineage,
    ) -> ClassificationKey | None: ...
