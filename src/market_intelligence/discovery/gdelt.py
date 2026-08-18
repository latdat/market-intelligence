"""GDELT DOC 2.0 ArticleList adapter with bounded adaptive time-window retrieval.

This module owns provider access only. It never resolves publisher identity, never touches
``SourceConfig``, and never produces anything beyond ephemeral ``DiscoveryCandidate`` values.

Why adaptive time windows instead of pagination
-----------------------------------------------
The DOC ArticleList response is bounded by ``maxrecords``. No offset/cursor pagination is
assumed to exist, so a window that returns exactly ``maxrecords`` records is treated as
*potentially saturated* and recursively split in time until each child window comes back under
the limit, the split depth bound is reached, or the window can no longer shrink at the
provider's one-second timestamp precision.

Saturation is decided from the *physical* record count the provider returned, before record
validation and before deduplication. A 250-entry payload of which only 249 map successfully is
still potentially saturated.

Records already retrieved from a saturated parent window are real sightings our system has
already made. They are kept and merged with the child results rather than discarded, so
recursive refinement can never push an article's first-seen time later than it actually was.
"""

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_intelligence.connectors.request_rate_limiter import RequestRateLimiter
from market_intelligence.discovery.gdelt_queries import GdeltQuerySpec
from market_intelligence.discovery.models import (
    DiscoveryCandidate,
    DiscoveryProvider,
)
from market_intelligence.normalization import ArticleNormalizationError, canonicalize_url
from market_intelligence.source_registry import RateLimitConfig

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]
type SleepFunction = Callable[[float], Awaitable[None]]
type RandomFunction = Callable[[], float]
type ProviderErrorCategory = Literal[
    "timeout",
    "connection",
    "http_status",
    "malformed_payload",
]

GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"

_ARTICLES_KEY = "articles"
_DEFAULT_USER_AGENT = "market-intelligence/0.1 gdelt-discovery-adapter"

# Reuse the repository's existing transient-status convention rather than inventing a
# GDELT-specific taxonomy.
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})
# Provisional, pending live verification: treated as "this authored expression is bad", which
# fails one cell without stopping the whole provider run.
_QUERY_REJECTED_STATUS_CODES = frozenset({400, 422})

_MAX_METADATA_VALUE_LENGTH = 512


def _utc_now() -> datetime:
    return datetime.now(UTC)


def floor_to_utc_second(value: datetime) -> datetime:
    """Normalize to timezone-aware UTC at whole-second precision.

    DOC request timestamps carry one-second resolution. Normalizing before formatting *and*
    before splitting is what lets the split algorithm guarantee strict progress at the
    precision the provider actually honours.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("GDELT window boundaries must include timezone information")
    return value.astimezone(UTC).replace(microsecond=0)


def format_gdelt_timestamp(value: datetime) -> str:
    """Render one normalized boundary in the provider's ``YYYYMMDDHHMMSS`` UTC form."""
    return floor_to_utc_second(value).strftime(GDELT_TIMESTAMP_FORMAT)


class GdeltWindowStatus(StrEnum):
    """Whether one retrieved window is believed to be complete."""

    COMPLETE = "COMPLETE"
    SATURATED_INCOMPLETE = "SATURATED_INCOMPLETE"


class GdeltProviderError(RuntimeError):
    """Systemic provider failure; never carries payload bodies or candidate URLs."""

    def __init__(
        self,
        query_id: str,
        *,
        category: ProviderErrorCategory,
        attempts: int,
        status_code: int | None = None,
    ) -> None:
        self.query_id = query_id
        self.category = category
        self.attempts = attempts
        self.status_code = status_code
        status_detail = f", status_code={status_code}" if status_code is not None else ""
        super().__init__(
            f"GDELT provider request failed for {query_id}: "
            f"category={category}, attempts={attempts}{status_detail}"
        )


class GdeltQueryRejectedError(RuntimeError):
    """The provider rejected this specific authored expression; other cells stay runnable."""

    def __init__(self, query_id: str, status_code: int) -> None:
        self.query_id = query_id
        self.status_code = status_code
        super().__init__(f"GDELT rejected query {query_id}: status_code={status_code}")


class GdeltClientConfig(BaseModel):
    """Validated bounded runtime settings for one GDELT adapter."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    max_records_per_request: int = Field(default=250, ge=1, le=250)
    request_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_attempts: int = Field(default=3, ge=1, le=6)
    base_retry_delay_seconds: float = Field(default=0.5, ge=0)
    max_retry_delay_seconds: float = Field(default=30.0, gt=0)
    max_split_depth: int = Field(default=6, ge=0, le=12)
    minimum_window_seconds: int = Field(default=60, ge=2, le=86_400)
    split_overlap_seconds: int = Field(default=1, ge=0, le=300)
    # Self-imposed politeness pacing. GDELT publishes no authoritative numeric quota, so this
    # is deliberately conservative and is not a claimed provider guarantee. ``None`` disables
    # pacing entirely and is intended for deterministic offline tests.
    rate_limit: RateLimitConfig | None = RateLimitConfig(max_requests=1, period_seconds=2)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.split_overlap_seconds >= self.minimum_window_seconds:
            raise ValueError("split_overlap_seconds must be smaller than minimum_window_seconds")
        return self


@dataclass(frozen=True, slots=True)
class GdeltResponseRecords:
    """One physical provider response after record mapping."""

    candidates: tuple[DiscoveryCandidate, ...]
    provider_record_count: int
    invalid_record_count: int

    @property
    def valid_record_count(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True, slots=True)
class GdeltWindowOutcome:
    """Merged result of one window and every child window it was split into."""

    candidates: tuple[DiscoveryCandidate, ...]
    status: GdeltWindowStatus
    request_count: int
    provider_record_count: int
    valid_record_count: int
    invalid_record_count: int

    @staticmethod
    def merge(
        status: GdeltWindowStatus,
        parts: Sequence["GdeltWindowOutcome"],
    ) -> "GdeltWindowOutcome":
        candidates: list[DiscoveryCandidate] = []
        for part in parts:
            candidates.extend(part.candidates)
        return GdeltWindowOutcome(
            candidates=tuple(candidates),
            status=status,
            request_count=sum(part.request_count for part in parts),
            provider_record_count=sum(part.provider_record_count for part in parts),
            valid_record_count=sum(part.valid_record_count for part in parts),
            invalid_record_count=sum(part.invalid_record_count for part in parts),
        )


def dedupe_candidates(candidates: Iterable[DiscoveryCandidate]) -> tuple[DiscoveryCandidate, ...]:
    """Collapse repeated sightings inside one query cell, keeping the earliest observation.

    Identity is the canonical URL where the URL can be canonicalized and the verbatim URL where
    it cannot, matching :func:`duplicate_candidate_rate`, so a malformed provider URL is never
    silently merged with a valid one. This is ephemeral cell-scoped deduplication and creates
    no global article identity.

    The first occurrence fixes output position; a later duplicate can only pull ``observed_at``
    *earlier*. Because a saturated parent window is fetched before its children, the parent's
    earlier sighting time survives refinement.
    """
    ordered: list[DiscoveryCandidate] = []
    index_by_key: dict[str, int] = {}

    for candidate in candidates:
        key = _dedupe_key(candidate.original_url)
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(ordered)
            ordered.append(candidate)
            continue
        existing = ordered[existing_index]
        if candidate.observed_at < existing.observed_at:
            ordered[existing_index] = existing.model_copy(
                update={"observed_at": candidate.observed_at}
            )

    return tuple(ordered)


def _dedupe_key(url: str) -> str:
    try:
        return canonicalize_url(url)
    except ArticleNormalizationError:
        return url


class GdeltDocClient:
    """Bounded GDELT DOC 2.0 ArticleList adapter with adaptive time-window retrieval."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        config: GdeltClientConfig | None = None,
        clock: Clock = _utc_now,
        sleep: SleepFunction = asyncio.sleep,
        random_value: RandomFunction = random.random,
    ) -> None:
        self._client = client
        self._config = config or GdeltClientConfig()
        self._clock = clock
        self._sleep = sleep
        self._random_value = random_value
        self._timeout = httpx.Timeout(self._config.request_timeout_seconds)

    @property
    def config(self) -> GdeltClientConfig:
        return self._config

    async def fetch_cell(
        self,
        spec: GdeltQuerySpec,
        window_start: datetime,
        window_end: datetime,
    ) -> GdeltWindowOutcome:
        """Retrieve one query cell over an explicit window, splitting when saturated."""
        start = floor_to_utc_second(window_start)
        end = floor_to_utc_second(window_end)
        if start >= end:
            raise ValueError("window_start must be earlier than window_end")

        limiter = RequestRateLimiter(self._config.rate_limit, sleep=self._sleep)
        started_at = time.perf_counter()

        if self._client is not None:
            outcome = await self._fetch_window(self._client, spec, start, end, 0, limiter)
        else:  # pragma: no cover - exercised only against the live provider
            async with httpx.AsyncClient() as client:
                outcome = await self._fetch_window(client, spec, start, end, 0, limiter)

        logger.info(
            "gdelt_cell_fetch_completed",
            extra={
                "query_id": spec.query_id,
                "pipeline_stage": "secondary_discovery",
                "status": outcome.status.value,
                "request_count": outcome.request_count,
                "provider_record_count": outcome.provider_record_count,
                "valid_record_count": outcome.valid_record_count,
                "invalid_record_count": outcome.invalid_record_count,
                "duration_ms": round((time.perf_counter() - started_at) * 1000),
            },
        )
        return outcome

    async def _fetch_window(
        self,
        client: httpx.AsyncClient,
        spec: GdeltQuerySpec,
        start: datetime,
        end: datetime,
        depth: int,
        limiter: RequestRateLimiter,
    ) -> GdeltWindowOutcome:
        records = await self._request_window(client, spec, start, end, limiter)
        parent = GdeltWindowOutcome(
            candidates=records.candidates,
            status=GdeltWindowStatus.COMPLETE,
            request_count=1,
            provider_record_count=records.provider_record_count,
            valid_record_count=records.valid_record_count,
            invalid_record_count=records.invalid_record_count,
        )

        # Saturation is judged on the physical provider count, before validation and dedupe.
        if records.provider_record_count < self._config.max_records_per_request:
            return parent

        saturated_parent = GdeltWindowOutcome.merge(
            GdeltWindowStatus.SATURATED_INCOMPLETE, (parent,)
        )
        if depth >= self._config.max_split_depth:
            return saturated_parent
        if (end - start) <= timedelta(seconds=self._config.minimum_window_seconds):
            return saturated_parent

        boundaries = self._split_boundaries(start, end)
        if boundaries is None:
            # Cannot shrink further at provider precision: keep the parent's real sightings and
            # report the window as incomplete rather than recursing without progress.
            return saturated_parent
        left_end, right_start = boundaries

        left = await self._fetch_window(client, spec, start, left_end, depth + 1, limiter)
        right = await self._fetch_window(client, spec, right_start, end, depth + 1, limiter)

        status = (
            GdeltWindowStatus.COMPLETE
            if left.status is GdeltWindowStatus.COMPLETE
            and right.status is GdeltWindowStatus.COMPLETE
            else GdeltWindowStatus.SATURATED_INCOMPLETE
        )
        # The parent's records are retained: they were genuinely observed already.
        return GdeltWindowOutcome.merge(status, (parent, left, right))

    def _split_boundaries(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime] | None:
        """Return ``(left_end, right_start)`` or ``None`` when no strict progress is possible.

        The child windows overlap by ``split_overlap_seconds`` so that a record sitting exactly
        on the midpoint cannot fall through the boundary whichever way the provider treats
        range inclusivity. The resulting duplicates are removed by :func:`dedupe_candidates`.
        """
        span = end - start
        mid = floor_to_utc_second(start + span / 2)
        if not start < mid < end:
            return None

        left_end = min(
            floor_to_utc_second(mid + timedelta(seconds=self._config.split_overlap_seconds)),
            end,
        )
        right_start = mid

        if (left_end - start) >= span or (end - right_start) >= span:
            return None

        parent_window = (format_gdelt_timestamp(start), format_gdelt_timestamp(end))
        left_window = (format_gdelt_timestamp(start), format_gdelt_timestamp(left_end))
        right_window = (format_gdelt_timestamp(right_start), format_gdelt_timestamp(end))
        if left_window == parent_window or right_window == parent_window:
            return None

        return left_end, right_start

    async def _request_window(
        self,
        client: httpx.AsyncClient,
        spec: GdeltQuerySpec,
        start: datetime,
        end: datetime,
        limiter: RequestRateLimiter,
    ) -> GdeltResponseRecords:
        params = {
            "query": spec.query_expression,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(self._config.max_records_per_request),
            "sort": "dateasc",
            "startdatetime": format_gdelt_timestamp(start),
            "enddatetime": format_gdelt_timestamp(end),
        }

        for attempt in range(1, self._config.max_attempts + 1):
            await limiter.wait()
            try:
                response = await client.get(
                    GDELT_DOC_ENDPOINT,
                    params=params,
                    headers={"User-Agent": _DEFAULT_USER_AGENT},
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as error:
                if attempt == self._config.max_attempts:
                    raise GdeltProviderError(
                        spec.query_id, category="timeout", attempts=attempt
                    ) from error
                await self._wait_before_retry(spec.query_id, attempt, "timeout")
                continue
            except httpx.RequestError as error:
                if attempt == self._config.max_attempts:
                    raise GdeltProviderError(
                        spec.query_id, category="connection", attempts=attempt
                    ) from error
                await self._wait_before_retry(spec.query_id, attempt, "connection")
                continue

            status_code = response.status_code
            if status_code in _QUERY_REJECTED_STATUS_CODES:
                # Cell-scoped: this authored expression is bad, the provider is not.
                raise GdeltQueryRejectedError(spec.query_id, status_code)

            if status_code in _RETRYABLE_STATUS_CODES:
                if attempt == self._config.max_attempts:
                    raise GdeltProviderError(
                        spec.query_id,
                        category="http_status",
                        attempts=attempt,
                        status_code=status_code,
                    )
                await self._wait_before_retry(
                    spec.query_id,
                    attempt,
                    "http_status",
                    status_code=status_code,
                    retry_after=self._retry_after_delay(response),
                )
                continue

            if status_code >= 400:
                # Unrecognized client/server refusal: classified conservatively as systemic so
                # the runner stops instead of hammering the remaining cells. No retry, because
                # nothing suggests this would succeed on a second identical request.
                raise GdeltProviderError(
                    spec.query_id,
                    category="http_status",
                    attempts=attempt,
                    status_code=status_code,
                )

            # One clock read per accepted physical response: parsing-loop position must never
            # manufacture discovery latency between articles of the same response.
            response_observed_at = self._current_utc_time()
            try:
                return self._parse_response(spec, response.content, response_observed_at)
            except _PayloadSchemaError:
                if attempt == self._config.max_attempts:
                    raise GdeltProviderError(
                        spec.query_id,
                        category="malformed_payload",
                        attempts=attempt,
                        status_code=status_code,
                    ) from None
                await self._wait_before_retry(spec.query_id, attempt, "malformed_payload")
                continue

        raise AssertionError("retry loop exhausted without returning or raising")

    def _parse_response(
        self,
        spec: GdeltQuerySpec,
        content: bytes,
        response_observed_at: datetime,
    ) -> GdeltResponseRecords:
        """Map one ArticleList payload, skipping malformed individual records.

        Only the explicitly modeled ``{"articles": [...]}`` shape is accepted. An object with no
        list-valued ``articles`` — including a bare ``{}`` — is a schema failure rather than an
        assumed empty result, so a malformed payload can never be reported as a successful
        zero-result window.
        """
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError) as error:
            raise _PayloadSchemaError("response is not valid JSON") from error

        if not isinstance(payload, dict):
            raise _PayloadSchemaError("expected a JSON object at the response root")

        raw_articles = payload.get(_ARTICLES_KEY)
        if not isinstance(raw_articles, list):
            raise _PayloadSchemaError(f"expected a list '{_ARTICLES_KEY}' in the response")

        candidates: list[DiscoveryCandidate] = []
        invalid_record_count = 0
        for index, record in enumerate(raw_articles):
            candidate = self._map_record(spec, record, response_observed_at)
            if candidate is None:
                invalid_record_count += 1
                self._log_skipped_record(spec.query_id, index)
                continue
            candidates.append(candidate)

        return GdeltResponseRecords(
            candidates=tuple(candidates),
            provider_record_count=len(raw_articles),
            invalid_record_count=invalid_record_count,
        )

    @staticmethod
    def _map_record(
        spec: GdeltQuerySpec,
        record: object,
        response_observed_at: datetime,
    ) -> DiscoveryCandidate | None:
        """Map one provider record, or return ``None`` when it is unusable.

        A non-blank URL that cannot be canonicalized is still a usable sighting: route
        resolution already maps such a candidate to ``UNKNOWN``. Only a missing, non-string, or
        blank URL makes a record invalid.

        ``seendate`` is GDELT's crawl/index time, not a publisher-attributed publication
        timestamp, so it is kept as bounded provider metadata and never mapped into
        ``published_at_raw``.
        """
        if not isinstance(record, dict):
            return None

        url = record.get("url")
        if not isinstance(url, str) or not url.strip():
            return None

        provider_metadata: dict[str, str] = {}
        for record_key, metadata_key in (
            ("sourcecountry", "sourcecountry"),
            ("seendate", "provider_seendate"),
        ):
            value = record.get(record_key)
            if isinstance(value, str) and value.strip():
                provider_metadata[metadata_key] = value.strip()[:_MAX_METADATA_VALUE_LENGTH]

        try:
            return DiscoveryCandidate(
                provider=DiscoveryProvider.GDELT_DOC_2_0,
                query_id=spec.query_id,
                observed_at=response_observed_at,
                original_url=url.strip(),
                title=_optional_text(record.get("title")),
                description=None,
                published_at_raw=None,
                language_hint=_optional_text(record.get("language")),
                provider_publisher_hint=_optional_text(record.get("domain")),
                provider_metadata=provider_metadata,
            )
        except ValueError:
            return None

    async def _wait_before_retry(
        self,
        query_id: str,
        attempt: int,
        category: ProviderErrorCategory,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
        logger.warning(
            "gdelt_request_retry",
            extra={
                "query_id": query_id,
                "pipeline_stage": "secondary_discovery",
                "status": "RETRYING",
                "attempt": attempt,
                "error_type": category,
                "status_code": status_code,
                "retry_delay_seconds": delay,
            },
        )
        await self._sleep(delay)

    def _backoff_delay(self, attempt: int) -> float:
        upper_bound = min(
            self._config.max_retry_delay_seconds,
            self._config.base_retry_delay_seconds * (2.0 ** (attempt - 1)),
        )
        random_fraction = min(1.0, max(0.0, self._random_value()))
        return upper_bound * random_fraction

    def _retry_after_delay(self, response: httpx.Response) -> float | None:
        if response.status_code not in _RETRY_AFTER_STATUS_CODES:
            return None

        value = response.headers.get("Retry-After")
        if value is None:
            return None

        stripped_value = value.strip()
        if stripped_value.isdigit():
            return min(float(stripped_value), self._config.max_retry_delay_seconds)

        try:
            retry_at = parsedate_to_datetime(stripped_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None or retry_at.utcoffset() is None:
            return None

        delay = max(0.0, (retry_at.astimezone(UTC) - self._current_utc_time()).total_seconds())
        return min(delay, self._config.max_retry_delay_seconds)

    def _current_utc_time(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("GDELT adapter clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)

    @staticmethod
    def _log_skipped_record(query_id: str, record_index: int) -> None:
        logger.warning(
            "gdelt_record_skipped",
            extra={
                "query_id": query_id,
                "pipeline_stage": "secondary_discovery",
                "status": "SKIPPED",
                "record_index": record_index,
            },
        )


class _PayloadSchemaError(ValueError):
    """Internal marker for an unusable provider payload shape."""


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
