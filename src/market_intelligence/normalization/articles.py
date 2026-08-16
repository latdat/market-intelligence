"""Deterministic normalization from raw connector records to canonical articles."""

import hashlib
import json
import logging
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from market_intelligence.articles import CanonicalArticle, RawArticle
from market_intelligence.source_registry import SourceConfig

logger = logging.getLogger(__name__)

_TRACKING_PARAMETER_NAMES = frozenset(
    {
        "dclid",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
    }
)
_IGNORED_CONTENT_TAGS = frozenset({"script", "style"})
_TEXT_SEPARATOR_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


class ArticleNormalizationError(ValueError):
    """Raised when a raw record cannot satisfy the canonical article contract."""

    def __init__(self, source_id: str, field: str, reason: str) -> None:
        self.source_id = source_id
        self.field = field
        self.reason = reason
        super().__init__(f"cannot normalize {field} for {source_id}: {reason}")


class _SinglePassTextExtractor(HTMLParser):
    """Extract visible text while decoding character references exactly once."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.casefold()
        if normalized_tag in _IGNORED_CONTENT_TAGS:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and normalized_tag in _TEXT_SEPARATOR_TAGS:
            self._chunks.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._ignored_depth == 0 and tag.casefold() in _TEXT_SEPARATOR_TAGS:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _IGNORED_CONTENT_TAGS:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
        elif self._ignored_depth == 0 and normalized_tag in _TEXT_SEPARATOR_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def normalize_article(raw: RawArticle, source: SourceConfig) -> CanonicalArticle:
    """Convert one validated raw record into a deterministic canonical record."""
    if raw.source_id != source.source_id:
        raise ArticleNormalizationError(
            raw.source_id,
            "source_id",
            "raw article and source configuration do not match",
        )

    canonical_url = canonicalize_url(raw.url, source_id=raw.source_id)
    title = _normalize_html_text(raw.title)
    if not title:
        raise ArticleNormalizationError(raw.source_id, "title", "title is missing or empty")

    description = _normalize_html_text(raw.description) or None
    source_item_id = _normalize_identifier(raw.source_item_id)
    language = _normalize_plain_text(raw.language_hint) or source.language
    published_at = _parse_published_at(raw.published_at_raw, raw.source_id)

    if source_item_id is not None:
        identity_parts = ["v1", "source_item_id", raw.source_id, source_item_id]
    else:
        identity_parts = ["v1", "canonical_url", raw.source_id, canonical_url]

    return CanonicalArticle(
        article_id=_sha256_json(identity_parts),
        source_id=raw.source_id,
        source_item_id=source_item_id,
        url=raw.url,
        canonical_url=canonical_url,
        title=title,
        description=description,
        language=language,
        market=source.market,
        published_at=published_at,
        discovered_at=raw.retrieved_at,
        content_hash=_sha256_json(["v1", title, description]),
    )


def canonicalize_url(url: str, *, source_id: str = "unknown_source") -> str:
    """Canonicalize an article URL without rewriting retained query segments."""
    if any(character.isspace() for character in url):
        raise ArticleNormalizationError(source_id, "url", "URL must not contain whitespace")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ArticleNormalizationError(source_id, "url", "URL is malformed") from error

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ArticleNormalizationError(source_id, "url", "URL must use HTTP or HTTPS")
    if parsed.hostname is None:
        raise ArticleNormalizationError(source_id, "url", "URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ArticleNormalizationError(source_id, "url", "URL must not contain userinfo")

    hostname = parsed.hostname.casefold()
    formatted_hostname = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = formatted_hostname if port is None or default_port else f"{formatted_hostname}:{port}"
    path = parsed.path or "/"
    query = _filter_tracking_query(parsed.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def _filter_tracking_query(query: str) -> str:
    retained_segments = [
        segment for segment in query.split("&") if not _is_tracking_query_segment(segment)
    ]
    return "&".join(retained_segments)


def _is_tracking_query_segment(segment: str) -> bool:
    encoded_name = segment.partition("=")[0]
    try:
        decoded_name = unquote_plus(encoded_name, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    normalized_name = decoded_name.casefold()
    return normalized_name.startswith("utm_") or normalized_name in _TRACKING_PARAMETER_NAMES


def _normalize_html_text(value: str | None) -> str:
    if value is None:
        return ""
    parser = _SinglePassTextExtractor()
    parser.feed(value)
    parser.close()
    return _normalize_plain_text(parser.text())


def _normalize_plain_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    return " ".join(normalized.split())


def _normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value).strip()
    return normalized or None


def _parse_published_at(value: str | None, source_id: str) -> datetime | None:
    if value is None:
        return None

    stripped_value = value.strip()
    parsed: datetime | None = None
    if stripped_value:
        try:
            parsed = datetime.fromisoformat(stripped_value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(stripped_value)
            except (TypeError, ValueError, OverflowError):
                parsed = None

        if parsed is None:
            tz: timezone | ZoneInfo | None = None
            formats: tuple[str, ...] = ()
            if source_id == "us_fhfa_regulatory":
                tz = ZoneInfo("America/New_York")
                formats = ("%m/%d/%Y",)
            elif source_id == "eu_esma_regulatory":
                tz = ZoneInfo("Europe/Paris")
                formats = ("%d/%m/%Y",)
            else:
                tz = timezone(timedelta(hours=7))
                formats = ("%d/%m/%Y", "%d/%m/%Y | %H:%M:%S", "%d/%m/%Y %H:%M:%S")

            if tz is not None:
                for fmt in formats:
                    try:
                        parsed = datetime.strptime(stripped_value, fmt).replace(tzinfo=tz)
                        break
                    except ValueError:
                        continue

    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        logger.warning(
            "article_published_at_unusable",
            extra={
                "source_id": source_id,
                "stage": "normalization",
                "status": "PARTIAL",
                "error_type": "invalid_or_naive_published_at",
            },
        )
        return None
    return parsed.astimezone(UTC)


def _sha256_json(parts: Sequence[str | None]) -> str:
    serialized = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
