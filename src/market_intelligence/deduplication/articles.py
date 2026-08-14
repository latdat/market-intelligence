"""Pure deterministic duplicate comparison for canonical articles."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from enum import StrEnum

from market_intelligence.articles import CanonicalArticle

_TITLE_SIMILARITY_THRESHOLD = 0.98
_TITLE_SIMILARITY_WINDOW = timedelta(hours=24)
_MINIMUM_TITLE_KEY_LENGTH = 30
_NUMERIC_TOKEN_PATTERN = re.compile(r"\d+")


class DedupReason(StrEnum):
    """The first matching layer in the approved deduplication order."""

    CANONICAL_URL = "CANONICAL_URL"
    SOURCE_ITEM_ID = "SOURCE_ITEM_ID"
    CONTENT_HASH = "CONTENT_HASH"
    TITLE_SIMILARITY = "TITLE_SIMILARITY"


@dataclass(frozen=True, slots=True)
class DedupDecision:
    """Explain whether a candidate matches one existing canonical article."""

    is_duplicate: bool
    reason: DedupReason | None = None
    matched_article_id: str | None = None
    title_similarity: float | None = None


def evaluate_duplicate(
    candidate: CanonicalArticle,
    existing: CanonicalArticle,
) -> DedupDecision:
    """Compare a candidate with one existing article using ordered short-circuit checks."""
    if candidate.canonical_url == existing.canonical_url:
        return _duplicate(existing, DedupReason.CANONICAL_URL)

    if (
        candidate.source_id == existing.source_id
        and candidate.source_item_id is not None
        and candidate.source_item_id == existing.source_item_id
    ):
        return _duplicate(existing, DedupReason.SOURCE_ITEM_ID)

    if candidate.content_hash == existing.content_hash:
        return _duplicate(existing, DedupReason.CONTENT_HASH)

    candidate_title = _title_comparison_key(candidate.title)
    existing_title = _title_comparison_key(existing.title)
    if not _title_similarity_is_eligible(
        candidate,
        existing,
        candidate_title,
        existing_title,
    ):
        return DedupDecision(is_duplicate=False)

    title_similarity = _calculate_title_similarity(candidate_title, existing_title)
    if title_similarity >= _TITLE_SIMILARITY_THRESHOLD:
        return _duplicate(
            existing,
            DedupReason.TITLE_SIMILARITY,
            title_similarity=title_similarity,
        )
    return DedupDecision(is_duplicate=False, title_similarity=title_similarity)


def _duplicate(
    existing: CanonicalArticle,
    reason: DedupReason,
    *,
    title_similarity: float | None = None,
) -> DedupDecision:
    return DedupDecision(
        is_duplicate=True,
        reason=reason,
        matched_article_id=existing.article_id,
        title_similarity=title_similarity,
    )


def _title_comparison_key(title: str) -> str:
    normalized = unicodedata.normalize("NFC", title).casefold()
    return " ".join(normalized.split())


def _title_similarity_is_eligible(
    candidate: CanonicalArticle,
    existing: CanonicalArticle,
    candidate_title: str,
    existing_title: str,
) -> bool:
    if candidate.market != existing.market:
        return False
    if min(len(candidate_title), len(existing_title)) < _MINIMUM_TITLE_KEY_LENGTH:
        return False

    candidate_time = candidate.published_at or candidate.discovered_at
    existing_time = existing.published_at or existing.discovered_at
    if abs(candidate_time - existing_time) > _TITLE_SIMILARITY_WINDOW:
        return False

    candidate_numeric_tokens = _numeric_tokens(candidate_title)
    existing_numeric_tokens = _numeric_tokens(existing_title)
    return not (
        candidate_numeric_tokens
        and existing_numeric_tokens
        and candidate_numeric_tokens != existing_numeric_tokens
    )


def _numeric_tokens(title: str) -> tuple[str, ...]:
    return tuple(_NUMERIC_TOKEN_PATTERN.findall(title))


def _calculate_title_similarity(candidate_title: str, existing_title: str) -> float:
    return SequenceMatcher(None, candidate_title, existing_title, autojunk=False).ratio()
