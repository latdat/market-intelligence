"""Source ingestion pipeline composition for RSS/Atom and REST API sources."""

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from market_intelligence.articles import CanonicalArticle, RawArticle
from market_intelligence.connectors import (
    GovernmentApiConnector,
    OfficialListingConnector,
    RssAtomConnector,
)
from market_intelligence.deduplication import DedupReason, evaluate_duplicate
from market_intelligence.normalization import ArticleNormalizationError, normalize_article
from market_intelligence.persistence import ArticlePersistenceError, ArticleRepository
from market_intelligence.source_registry import AcquisitionMethod, SourceConfig

logger = logging.getLogger(__name__)


class SourceFetcher(Protocol):
    """Fetch boundary used to keep pipeline tests offline."""

    async def fetch(self, source: SourceConfig) -> list[RawArticle]:
        """Fetch raw records for one validated source."""


# Backward-compatible alias; existing code and tests reference RssSourceFetcher.
RssSourceFetcher = SourceFetcher


class UnsupportedAcquisitionMethod(Exception):
    """Raised when a source uses an acquisition method with no registered connector."""


def _create_connector_for_source(source: SourceConfig, max_items: int) -> SourceFetcher:
    """Return the appropriate connector for a source's acquisition method.

    Routing:
      RSS / ATOM  -> RssAtomConnector
      REST_API + us_federal_register -> GovernmentApiConnector
      REST_API + us_govinfo_legal -> LegalCorpusConnector
      anything else -> UnsupportedAcquisitionMethod (before network access)
    """
    method = source.acquisition.method
    if method in (AcquisitionMethod.RSS, AcquisitionMethod.ATOM):
        return RssAtomConnector()
    if method is AcquisitionMethod.REST_API:
        if source.source_id == "us_govinfo_legal":
            from market_intelligence.connectors.legal_corpus import LegalCorpusConnector

            api_key = os.environ.get("GOVINFO_API_KEY", "")
            return LegalCorpusConnector(api_key=api_key, max_items=max_items)
        return GovernmentApiConnector(max_items=max_items)
    if method is AcquisitionMethod.HTML:
        if source.source_id == "vn_sbv_regulatory_docs":
            return OfficialListingConnector(max_items=max_items)
    raise UnsupportedAcquisitionMethod(
        f"source {source.source_id} uses acquisition method {method.value!r} "
        "which is not supported by any registered connector"
    )


@dataclass(frozen=True, slots=True)
class SourcePreflightResult:
    """Read-only fetch and normalization counts for one source."""

    source_id: str
    fetched_count: int
    selected_count: int
    normalized_count: int
    rejected_count: int
    status: Literal["SUCCESS", "FAILED"] = "SUCCESS"
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class BatchDuplicateMatch:
    """One batch-local DE-006 match that does not suppress persistence."""

    article_id: str
    matched_article_id: str
    reason: DedupReason
    title_similarity: float | None


@dataclass(frozen=True, slots=True)
class SourceIngestionResult:
    """Pipeline counts and stable IDs for one source."""

    source_id: str
    fetched_count: int
    selected_count: int
    normalized_count: int
    rejected_count: int
    duplicate_count: int
    persisted_count: int
    storage_skipped_count: int
    article_ids: tuple[str, ...]
    status: Literal["SUCCESS", "FAILED"] = "SUCCESS"
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionRunResult:
    """Aggregate result without adding cross-source canonical-winner semantics."""

    sources: tuple[SourceIngestionResult, ...]
    duplicate_matches: tuple[BatchDuplicateMatch, ...]


@dataclass(frozen=True, slots=True)
class _PreparedSource:
    source_id: str
    fetched_count: int
    selected_count: int
    articles: tuple[CanonicalArticle, ...]
    rejected_count: int


async def preflight_sources(
    sources: Sequence[SourceConfig],
    *,
    max_items: int,
    connector: SourceFetcher | None = None,
) -> tuple[SourcePreflightResult, ...]:
    """Fetch and normalize bounded live data without persistence.

    Routes each source to the appropriate connector by acquisition method unless
    a single override connector is provided (for testing).
    """
    _validate_max_items(max_items)
    results: list[SourcePreflightResult] = []
    for source in sources:
        try:
            fetcher = connector or _create_connector_for_source(source, max_items)
            prepared = await _prepare_source(source, fetcher, max_items)
        except Exception as error:
            logger.error(
                "source_preflight_failed",
                extra={
                    "source_id": source.source_id,
                    "stage": "fetch_normalize",
                    "status": "FAILED",
                    "error_type": type(error).__name__,
                },
            )
            results.append(
                SourcePreflightResult(
                    source_id=source.source_id,
                    fetched_count=0,
                    selected_count=0,
                    normalized_count=0,
                    rejected_count=0,
                    status="FAILED",
                    error_type=type(error).__name__,
                )
            )
            continue
        results.append(
            SourcePreflightResult(
                source_id=source.source_id,
                fetched_count=prepared.fetched_count,
                selected_count=prepared.selected_count,
                normalized_count=len(prepared.articles),
                rejected_count=prepared.rejected_count,
            )
        )
    return tuple(results)


async def preflight_rss_sources(
    sources: Sequence[SourceConfig],
    *,
    max_items: int,
    connector: SourceFetcher | None = None,
) -> tuple[SourcePreflightResult, ...]:
    """Backward-compatible alias for preflight_sources."""
    return await preflight_sources(sources, max_items=max_items, connector=connector)


async def run_rss_ingestion(
    sources: Sequence[SourceConfig],
    repository: ArticleRepository,
    *,
    max_items: int,
    connector: SourceFetcher | None = None,
) -> IngestionRunResult:
    """Run bounded ingestion for all acquisition methods and persist metadata-approved records."""
    _validate_max_items(max_items)
    batch_articles: list[CanonicalArticle] = []
    duplicate_matches: list[BatchDuplicateMatch] = []
    source_results: list[SourceIngestionResult] = []

    for source in sources:
        try:
            fetcher = connector or _create_connector_for_source(source, max_items)
            prepared = await _prepare_source(source, fetcher, max_items)
        except Exception as error:
            logger.error(
                "source_ingestion_failed",
                extra={
                    "source_id": source.source_id,
                    "stage": "fetch_normalize",
                    "status": "FAILED",
                    "error_type": type(error).__name__,
                },
            )
            source_results.append(
                SourceIngestionResult(
                    source_id=source.source_id,
                    fetched_count=0,
                    selected_count=0,
                    normalized_count=0,
                    rejected_count=0,
                    duplicate_count=0,
                    persisted_count=0,
                    storage_skipped_count=0,
                    article_ids=(),
                    status="FAILED",
                    error_type=type(error).__name__,
                )
            )
            continue
        duplicate_count = 0
        persisted_count = 0
        storage_skipped_count = 0
        persistence_error: ArticlePersistenceError | None = None

        for article in prepared.articles:
            match = _first_batch_duplicate(article, batch_articles)
            if match is not None:
                duplicate_matches.append(match)
                duplicate_count += 1

            batch_articles.append(article)
            if source.rights.can_store_metadata:
                try:
                    repository.save(article)
                except ArticlePersistenceError as error:
                    persistence_error = error
                    break
                persisted_count += 1
            else:
                storage_skipped_count += 1

        result = SourceIngestionResult(
            source_id=source.source_id,
            fetched_count=prepared.fetched_count,
            selected_count=prepared.selected_count,
            normalized_count=len(prepared.articles),
            rejected_count=prepared.rejected_count,
            duplicate_count=duplicate_count,
            persisted_count=persisted_count,
            storage_skipped_count=storage_skipped_count,
            article_ids=tuple(article.article_id for article in prepared.articles),
            status="FAILED" if persistence_error is not None else "SUCCESS",
            error_type=(
                type(persistence_error).__name__ if persistence_error is not None else None
            ),
        )
        source_results.append(result)
        log = logger.error if persistence_error is not None else logger.info
        log(
            (
                "source_ingestion_failed"
                if persistence_error is not None
                else "source_ingestion_completed"
            ),
            extra={
                "source_id": source.source_id,
                "stage": "source_to_persistence",
                "status": result.status,
                "error_type": result.error_type,
                "fetched_count": result.fetched_count,
                "selected_count": result.selected_count,
                "normalized_count": result.normalized_count,
                "rejected_count": result.rejected_count,
                "duplicate_count": result.duplicate_count,
                "persisted_count": result.persisted_count,
                "storage_skipped_count": result.storage_skipped_count,
            },
        )

    return IngestionRunResult(
        sources=tuple(source_results),
        duplicate_matches=tuple(duplicate_matches),
    )


async def _prepare_source(
    source: SourceConfig,
    fetcher: RssSourceFetcher,
    max_items: int,
) -> _PreparedSource:
    raw_articles = await fetcher.fetch(source)
    selected = raw_articles[:max_items]
    canonical_articles: list[CanonicalArticle] = []
    rejected_count = 0

    for raw_article in selected:
        try:
            canonical_articles.append(normalize_article(raw_article, source))
        except ArticleNormalizationError as error:
            rejected_count += 1
            logger.warning(
                "rss_article_normalization_rejected",
                extra={
                    "source_id": source.source_id,
                    "stage": "normalization",
                    "status": "REJECTED",
                    "field": error.field,
                    "error_type": type(error).__name__,
                },
            )

    return _PreparedSource(
        source_id=source.source_id,
        fetched_count=len(raw_articles),
        selected_count=len(selected),
        articles=tuple(canonical_articles),
        rejected_count=rejected_count,
    )


def _first_batch_duplicate(
    candidate: CanonicalArticle,
    existing_articles: Sequence[CanonicalArticle],
) -> BatchDuplicateMatch | None:
    for existing in existing_articles:
        decision = evaluate_duplicate(candidate, existing)
        if decision.is_duplicate:
            if decision.reason is None or decision.matched_article_id is None:
                raise AssertionError("duplicate decision is missing match details")
            return BatchDuplicateMatch(
                article_id=candidate.article_id,
                matched_article_id=decision.matched_article_id,
                reason=decision.reason,
                title_similarity=decision.title_similarity,
            )
    return None


def _validate_max_items(max_items: int) -> None:
    if isinstance(max_items, bool) or max_items <= 0:
        raise ValueError("max_items must be a positive integer")
