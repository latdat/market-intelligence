"""Government API connector — v1 supports the Federal Register REST API."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urlparse

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
_DEFAULT_USER_AGENT = "market-intelligence/0.1 government-api-connector"

# Only the Federal Register is supported in v1.
_SUPPORTED_SOURCE_IDS = frozenset({"us_federal_register"})
# Allowed Federal Register API origin and exact path for pagination validation.
_FEDERAL_REGISTER_ORIGIN = "https://www.federalregister.gov"
_FEDERAL_REGISTER_API_PATH = "/api/v1/documents.json"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GovernmentApiConnectorError(Exception):
    """Base error for failures at the Government API connector boundary."""


class ApiConfigurationError(GovernmentApiConnectorError):
    """Raised when source configuration cannot be used by this connector."""


class ApiFetchError(GovernmentApiConnectorError):
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
            f"government API fetch failed for {source_id}: category={category}, "
            f"attempts={attempts}{status_detail}"
        )


class ApiParseError(GovernmentApiConnectorError):
    """Raised when a response cannot produce any trustworthy API records."""

    def __init__(self, source_id: str, detail: str) -> None:
        self.source_id = source_id
        self.detail = detail
        super().__init__(f"government API parse failed for {source_id}: {detail}")


class GovernmentApiConnector:
    """Fetch bounded records from official government REST APIs and return RawArticle.

    v1 supports:
    - AcquisitionMethod.REST_API
    - source_id: us_federal_register only

    The connector is stateless: it does not persist pagination cursors or any
    other runtime state between calls.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        base_retry_delay_seconds: float = 0.5,
        max_retry_delay_seconds: float = 30.0,
        max_items: int = 100,
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
        if isinstance(max_items, bool) or max_items < 1:
            raise ValueError("max_items must be a positive integer")

        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_attempts = max_attempts
        self._base_retry_delay_seconds = base_retry_delay_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._max_items = max_items
        self._sleep = sleep
        self._clock = clock
        self._random_value = random_value

    async def fetch(self, source: SourceConfig) -> list[RawArticle]:
        """Fetch one REST API source without owning downstream normalization."""
        started_at = time.perf_counter()

        try:
            self._validate_source(source)
            if self._client is not None:
                articles = await self._fetch_with_client(self._client, source)
            else:
                async with httpx.AsyncClient() as client:
                    articles = await self._fetch_with_client(client, source)
        except GovernmentApiConnectorError as error:
            logger.error(
                "government_api_fetch_failed",
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
            "government_api_fetch_succeeded",
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
        if source.acquisition.method is not AcquisitionMethod.REST_API:
            raise ApiConfigurationError(
                f"source {source.source_id} does not use REST_API acquisition"
            )
        if source.source_id not in _SUPPORTED_SOURCE_IDS:
            raise ApiConfigurationError(
                f"source {source.source_id} is not supported by GovernmentApiConnector v1"
            )
        if source.rights.can_fetch is not True:
            raise ApiConfigurationError(f"source {source.source_id} is not approved for fetching")

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
    ) -> list[RawArticle]:
        retrieved_at = self._current_utc_time()
        articles: list[RawArticle] = []
        visited_urls: set[str] = set()
        next_url: str | None = str(source.acquisition.endpoint_url)
        limiter = RequestRateLimiter(source.acquisition.rate_limit, sleep=self._sleep)

        while next_url is not None and len(articles) < self._max_items:
            if next_url in visited_urls:
                raise ApiParseError(
                    source.source_id,
                    f"pagination loop detected at {next_url}",
                )

            visited_urls.add(next_url)
            response = await self._request_with_retries(client, source, next_url, limiter)
            remaining = self._max_items - len(articles)
            page_articles, raw_next_url = self._parse_response(
                source, response.content, retrieved_at, remaining
            )
            articles.extend(page_articles)
            if len(articles) >= self._max_items:
                break
            next_url = self._validate_next_page_url(source, raw_next_url, visited_urls)

        return articles

    def _validate_next_page_url(
        self,
        source: SourceConfig,
        raw_next_url: str | None,
        visited_urls: set[str],
    ) -> str | None:
        """Validate pagination URL for security and loop prevention."""
        if raw_next_url is None:
            return None

        try:
            parsed = urlparse(raw_next_url)
        except ValueError as error:
            raise ApiParseError(
                source.source_id,
                f"invalid pagination URL (parse error): {raw_next_url}",
            ) from error

        # Must be HTTPS
        if parsed.scheme != "https":
            raise ApiParseError(
                source.source_id,
                f"invalid pagination URL (not https): {raw_next_url}",
            )

        # No userinfo (credentials in URL)
        if parsed.username or parsed.password:
            raise ApiParseError(
                source.source_id,
                f"invalid pagination URL (userinfo present): {raw_next_url}",
            )

        # Must be same Federal Register origin
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != _FEDERAL_REGISTER_ORIGIN:
            raise ApiParseError(
                source.source_id,
                f"invalid pagination URL (cross-origin): {raw_next_url}",
            )

        # Must be exact documents API path
        if parsed.path != _FEDERAL_REGISTER_API_PATH:
            raise ApiParseError(
                source.source_id,
                f"invalid pagination URL (wrong path): {raw_next_url}",
            )

        # Loop detection
        if raw_next_url in visited_urls:
            raise ApiParseError(
                source.source_id,
                f"pagination loop detected: {raw_next_url}",
            )

        return raw_next_url

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
        url: str,
        limiter: RequestRateLimiter,
    ) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            await limiter.wait()
            try:
                response = await client.get(
                    url,
                    headers={"User-Agent": _DEFAULT_USER_AGENT},
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as error:
                if attempt == self._max_attempts:
                    raise ApiFetchError(
                        source.source_id,
                        category="timeout",
                        attempts=attempt,
                        retryable=True,
                    ) from error
                await self._wait_before_retry(source.source_id, attempt, "timeout")
                continue
            except httpx.RequestError as error:
                if attempt == self._max_attempts:
                    raise ApiFetchError(
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
                    raise ApiFetchError(
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
                raise ApiFetchError(
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
            "government_api_fetch_retry",
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
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        """Parse one Federal Register API page. Returns (articles, next_page_url)."""
        import json

        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError) as error:
            raise ApiParseError(source.source_id, f"response is not valid JSON: {error}") from error

        if not isinstance(payload, dict):
            raise ApiParseError(
                source.source_id,
                "expected JSON object at root, got " + type(payload).__name__,
            )

        results_value = payload.get("results")
        if not isinstance(results_value, list):
            raise ApiParseError(
                source.source_id,
                "expected 'results' list in response envelope",
            )

        # Empty results list is valid — source has no new documents.
        if not results_value:
            return [], None

        next_page_url_raw = payload.get("next_page_url")
        next_page_url = (
            next_page_url_raw
            if isinstance(next_page_url_raw, str) and next_page_url_raw.strip()
            else None
        )

        articles: list[RawArticle] = []
        invalid_items = 0

        for item_index, item_value in enumerate(results_value):
            if len(articles) >= max_items:
                break

            if not isinstance(item_value, dict):
                invalid_items += 1
                GovernmentApiConnector._log_unusable_item(
                    source.source_id, item_index, "not_a_dict"
                )
                continue

            html_url = item_value.get("html_url")
            if not isinstance(html_url, str) or not html_url.strip():
                invalid_items += 1
                GovernmentApiConnector._log_unusable_item(
                    source.source_id, item_index, "missing_html_url"
                )
                continue

            document_number = item_value.get("document_number")
            source_item_id = (
                document_number
                if isinstance(document_number, str) and document_number.strip()
                else None
            )

            title_raw = item_value.get("title")
            title = title_raw if isinstance(title_raw, str) and title_raw.strip() else None

            abstract_raw = item_value.get("abstract")
            description = (
                abstract_raw if isinstance(abstract_raw, str) and abstract_raw.strip() else None
            )

            publication_date_raw = item_value.get("publication_date")
            published_at_raw = (
                publication_date_raw
                if isinstance(publication_date_raw, str) and publication_date_raw.strip()
                else None
            )

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=html_url.strip(),
                    title=title,
                    description=description,
                    published_at_raw=published_at_raw,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                )
            )

        if not articles and invalid_items:
            raise ApiParseError(
                source.source_id,
                "non-empty response contains no usable items",
            )

        return articles, next_page_url

    @staticmethod
    def _log_unusable_item(source_id: str, item_index: int, reason: str) -> None:
        logger.warning(
            "government_api_item_skipped",
            extra={
                "source_id": source_id,
                "stage": "parse",
                "status": "SKIPPED",
                "error_type": reason,
                "item_index": item_index,
            },
        )
