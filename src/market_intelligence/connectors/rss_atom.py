"""Generic metadata-first RSS/Atom connector."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, cast

import feedparser  # type: ignore[import-untyped]
import httpx

from market_intelligence.articles import RawArticle
from market_intelligence.connectors.request_rate_limiter import RequestRateLimiter
from market_intelligence.source_registry import AcquisitionMethod, SourceConfig

logger = logging.getLogger(__name__)

type FetchErrorCategory = Literal["timeout", "connection", "http_status"]
type SleepFunction = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]
type RandomFunction = Callable[[], float]

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})
_DEFAULT_USER_AGENT = "market-intelligence/0.1 rss-atom-connector"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RssAtomConnectorError(Exception):
    """Base error for failures at the RSS/Atom connector boundary."""


class FeedConfigurationError(RssAtomConnectorError):
    """Raised when source configuration cannot be used by this connector."""


class FeedFetchError(RssAtomConnectorError):
    """Categorized HTTP/network failure after connector retry handling."""

    def __init__(
        self,
        source_id: str,
        *,
        category: FetchErrorCategory,
        attempts: int,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        self.source_id = source_id
        self.category = category
        self.attempts = attempts
        self.retryable = retryable
        self.status_code = status_code
        status_detail = f", status_code={status_code}" if status_code is not None else ""
        super().__init__(
            f"feed fetch failed for {source_id}: category={category}, "
            f"attempts={attempts}{status_detail}"
        )


class FeedParseError(RssAtomConnectorError):
    """Raised when a response cannot produce any trustworthy feed records."""

    def __init__(self, source_id: str, detail: str) -> None:
        self.source_id = source_id
        self.detail = detail
        super().__init__(f"feed parse failed for {source_id}: {detail}")


class RssAtomConnector:
    """Fetch configured RSS/Atom endpoints and return raw article records."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        base_retry_delay_seconds: float = 0.5,
        max_retry_delay_seconds: float = 30.0,
        sleep: SleepFunction = asyncio.sleep,
        clock: Clock = _utc_now,
        random_value: RandomFunction = random.random,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if base_retry_delay_seconds < 0:
            raise ValueError("base_retry_delay_seconds must not be negative")
        if max_retry_delay_seconds <= 0:
            raise ValueError("max_retry_delay_seconds must be positive")

        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_attempts = max_attempts
        self._base_retry_delay_seconds = base_retry_delay_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._sleep = sleep
        self._clock = clock
        self._random_value = random_value

    async def fetch(self, source: SourceConfig) -> list[RawArticle]:
        """Fetch one configured feed without owning downstream normalization."""
        started_at = time.perf_counter()

        try:
            self._validate_source(source)
            if self._client is not None:
                articles = await self._fetch_with_client(self._client, source)
            else:
                async with httpx.AsyncClient() as client:
                    articles = await self._fetch_with_client(client, source)
        except RssAtomConnectorError as error:
            logger.error(
                "rss_atom_fetch_failed",
                extra={
                    "source_id": source.source_id,
                    "stage": "fetch_parse",
                    "status": "FAILED",
                    "error_type": type(error).__name__,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000),
                },
            )
            raise

        logger.info(
            "rss_atom_fetch_succeeded",
            extra={
                "source_id": source.source_id,
                "stage": "fetch_parse",
                "status": "SUCCESS",
                "items_returned": len(articles),
                "duration_ms": round((time.perf_counter() - started_at) * 1000),
            },
        )
        return articles

    @staticmethod
    def _validate_source(source: SourceConfig) -> None:
        if source.acquisition.method not in {
            AcquisitionMethod.RSS,
            AcquisitionMethod.ATOM,
        }:
            raise FeedConfigurationError(
                f"source {source.source_id} does not use RSS or ATOM acquisition"
            )
        if source.rights.can_fetch is not True:
            raise FeedConfigurationError(f"source {source.source_id} is not approved for fetching")

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
    ) -> list[RawArticle]:
        limiter = RequestRateLimiter(source.acquisition.rate_limit, sleep=self._sleep)
        response = await self._request_with_retries(client, source, limiter)
        retrieved_at = self._current_utc_time()
        return self._parse_response(source, response.content, retrieved_at)

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
        limiter: RequestRateLimiter,
    ) -> httpx.Response:
        endpoint_url = str(source.acquisition.endpoint_url)

        for attempt in range(1, self._max_attempts + 1):
            await limiter.wait()
            try:
                response = await client.get(
                    endpoint_url,
                    headers={"User-Agent": _DEFAULT_USER_AGENT},
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as error:
                if attempt == self._max_attempts:
                    raise FeedFetchError(
                        source.source_id,
                        category="timeout",
                        attempts=attempt,
                        retryable=True,
                    ) from error
                await self._wait_before_retry(source.source_id, attempt, "timeout")
                continue
            except httpx.RequestError as error:
                if attempt == self._max_attempts:
                    raise FeedFetchError(
                        source.source_id,
                        category="connection",
                        attempts=attempt,
                        retryable=True,
                    ) from error
                await self._wait_before_retry(source.source_id, attempt, "connection")
                continue

            status_code = response.status_code
            if status_code in _RETRYABLE_STATUS_CODES:
                if attempt == self._max_attempts:
                    raise FeedFetchError(
                        source.source_id,
                        category="http_status",
                        attempts=attempt,
                        retryable=True,
                        status_code=status_code,
                    )
                retry_after = self._retry_after_delay(response)
                await self._wait_before_retry(
                    source.source_id,
                    attempt,
                    "http_status",
                    status_code=status_code,
                    retry_after=retry_after,
                )
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise FeedFetchError(
                    source.source_id,
                    category="http_status",
                    attempts=attempt,
                    retryable=False,
                    status_code=status_code,
                ) from error

            return response

        raise AssertionError("retry loop exhausted without returning or raising")

    async def _wait_before_retry(
        self,
        source_id: str,
        attempt: int,
        error_type: FetchErrorCategory,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
        logger.warning(
            "rss_atom_fetch_retry",
            extra={
                "source_id": source_id,
                "stage": "fetch",
                "status": "RETRYING",
                "attempt": attempt,
                "error_type": error_type,
                "status_code": status_code,
                "retry_delay_seconds": delay,
            },
        )
        await self._sleep(delay)

    def _backoff_delay(self, attempt: int) -> float:
        upper_bound = min(
            self._max_retry_delay_seconds,
            self._base_retry_delay_seconds * (2.0 ** (attempt - 1)),
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
            return min(float(stripped_value), self._max_retry_delay_seconds)

        try:
            retry_at = parsedate_to_datetime(stripped_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None or retry_at.utcoffset() is None:
            return None

        delay = max(0.0, (retry_at.astimezone(UTC) - self._current_utc_time()).total_seconds())
        return min(delay, self._max_retry_delay_seconds)

    def _current_utc_time(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("connector clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)

    @staticmethod
    def _parse_response(
        source: SourceConfig,
        content: bytes,
        retrieved_at: datetime,
    ) -> list[RawArticle]:
        parsed = cast(
            Mapping[str, object],
            feedparser.parse(
                content,
                resolve_relative_uris=False,
                sanitize_html=False,
            ),
        )
        entries_value = parsed.get("entries", [])
        entries = entries_value if isinstance(entries_value, list) else []
        version_value = parsed.get("version")
        version = version_value if isinstance(version_value, str) else ""
        malformed = bool(parsed.get("bozo", False))

        if not entries:
            if version and not malformed:
                return []
            raise FeedParseError(source.source_id, "response is not a valid feed")

        if malformed:
            logger.warning(
                "rss_atom_feed_recovered_with_parse_warning",
                extra={
                    "source_id": source.source_id,
                    "stage": "parse",
                    "status": "PARTIAL",
                    "error_type": "malformed_feed",
                },
            )

        articles: list[RawArticle] = []
        invalid_entries = 0
        for entry_index, entry_value in enumerate(entries):
            if not isinstance(entry_value, Mapping):
                invalid_entries += 1
                RssAtomConnector._log_unusable_entry(source.source_id, entry_index)
                continue

            entry = cast(Mapping[str, object], entry_value)
            url = RssAtomConnector._first_text(entry, "link")
            if url is None or not url.strip():
                invalid_entries += 1
                RssAtomConnector._log_unusable_entry(source.source_id, entry_index)
                continue

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=RssAtomConnector._first_text(entry, "id", "guid"),
                    url=url,
                    title=RssAtomConnector._first_text(entry, "title"),
                    description=RssAtomConnector._first_text(entry, "summary", "description"),
                    published_at_raw=RssAtomConnector._first_text(
                        entry,
                        "published",
                        "updated",
                    ),
                    language_hint=RssAtomConnector._first_text(entry, "language")
                    or source.language,
                    retrieved_at=retrieved_at,
                )
            )

        if not articles and invalid_entries:
            raise FeedParseError(source.source_id, "feed contains no usable entries")

        return articles

    @staticmethod
    def _first_text(entry: Mapping[str, object], *keys: str) -> str | None:
        for key in keys:
            value = entry.get(key)
            if isinstance(value, str):
                return value
        return None

    @staticmethod
    def _log_unusable_entry(source_id: str, entry_index: int) -> None:
        logger.warning(
            "rss_atom_entry_skipped",
            extra={
                "source_id": source_id,
                "stage": "parse",
                "status": "SKIPPED",
                "error_type": "missing_url",
                "entry_index": entry_index,
            },
        )
