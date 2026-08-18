"""Backend-neutral read boundary for bounded matching work discovery.

This is a DE-internal read contract. It deliberately does not reuse the DE-009
claim/lease/fencing lifecycle: matching is a local deterministic computation with no
provider call, no token cost, and no external paid I/O, and DE-012 already enforces
durable idempotency at the alert-candidate persistence boundary.
"""

from datetime import UTC, datetime
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import ClassifiedArticle


class MatchingWorkModel(BaseModel):
    """Strict immutable models returned by the matching work read boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MatchingWorkItem(MatchingWorkModel):
    """One matchable article paired with its successful classification."""

    article: CanonicalArticle
    classification: ClassifiedArticle

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.article.article_id != self.classification.article_id:
            raise ValueError("article and classification article_id values must match")
        return self

    @property
    def article_id(self) -> str:
        return self.article.article_id


class MatchingWorkPage(MatchingWorkModel):
    """One deterministic keyset page of matchable work items."""

    items: tuple[MatchingWorkItem, ...]
    next_cursor: str | None = None

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        article_ids = tuple(item.article_id for item in self.items)

        if len(article_ids) != len(set(article_ids)):
            raise ValueError("items must not contain duplicate article_id values")
        if article_ids != tuple(sorted(article_ids)):
            raise ValueError("items must be ordered by article_id ascending")

        if self.next_cursor is not None:
            if not self.next_cursor.strip():
                raise ValueError("next_cursor must not be blank")
            if not self.items:
                raise ValueError("empty pages cannot have next_cursor")
            if self.next_cursor != article_ids[-1]:
                raise ValueError("next_cursor must equal the last article_id in items")

        return self


class MatchingWorkReadError(RuntimeError):
    """Sanitized failure for matching work reads."""

    def __init__(self, operation: str, article_id: str | None = None) -> None:
        self.operation = operation
        self.article_id = article_id
        suffix = f" for article {article_id}" if article_id is not None else ""
        super().__init__(f"matching work read {operation} failed{suffix}")


class MatchingWorkReader(Protocol):
    """Read only the durable article/classification state needed by the matching runner."""

    def list_page(
        self,
        *,
        classifier_version: str,
        run_cutoff: datetime,
        freshness_cutoff: datetime,
        after_article_id: str | None = None,
        limit: int = 100,
    ) -> MatchingWorkPage:
        """Return a bounded article_id-ascending keyset page of matchable work.

        Concrete adapters must select exactly the rows where:

        - the classification status is `SUCCEEDED`;
        - `classification.classifier_version == classifier_version` exactly, never a
          "latest version" heuristic and never more than one classifier lineage;
        - `classification.classified_at <= run_cutoff`, so one run sees a stable
          discovery snapshot;
        - the article passes a conservative freshness superset, meaning
          `article.discovered_at >= freshness_cutoff OR
          article.published_at >= freshness_cutoff` with a null `published_at`
          treated as non-matching for that disjunct only.

        The freshness filter is a discovery prefilter, not matching semantics.
        `CanonicalArticle` does not guarantee `published_at <= discovered_at`, so a
        `discovered_at`-only filter would under-select eligible work. Over-selection is
        acceptable and expected; `match_article()` remains the final authority on
        stale/fresh. Adapters must not reimplement DE-011 freshness rules in SQL.

        Adapters must enforce the limit range: 1 <= limit <= 1000 inclusive, with a
        default of 100. Boolean limits are invalid. Both cutoffs must be timezone-aware.
        Pages are ordered by `article_id` ascending and use the last `article_id` as a
        keyset cursor when another page is available.
        """
        ...


def normalize_cutoff(name: str, value: datetime) -> datetime:
    """Return a UTC-normalized cutoff, rejecting naive timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
    return value.astimezone(UTC)
