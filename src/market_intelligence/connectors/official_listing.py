"""Official listing connector — v1 supports SBV Regulatory Documents."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from market_intelligence.articles import RawArticle
from market_intelligence.source_registry import AcquisitionMethod, SourceConfig

logger = logging.getLogger(__name__)

type FetchErrorCategory = Literal["timeout", "connection", "http_status"]
type SleepFunction = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]
type RandomFunction = Callable[[], float]

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})
_DEFAULT_USER_AGENT = "market-intelligence/0.1 official-listing-connector"

# Only the State Bank of Vietnam is supported in v1.
_SUPPORTED_SOURCE_IDS = frozenset({"vn_sbv_regulatory_docs"})
_SBV_ORIGIN = "https://sbv.gov.vn"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OfficialListingConnectorError(Exception):
    """Base error for failures at the Official Listing connector boundary."""


class ListingConfigurationError(OfficialListingConnectorError):
    """Raised when source configuration cannot be used by this connector."""


class ListingFetchError(OfficialListingConnectorError):
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
            f"official listing fetch failed for {source_id}: category={category}, "
            f"attempts={attempts}{status_detail}"
        )


class ListingParseError(OfficialListingConnectorError):
    """Raised when a response cannot produce any trustworthy API records."""

    def __init__(self, source_id: str, detail: str) -> None:
        self.source_id = source_id
        self.detail = detail
        super().__init__(f"official listing parse failed for {source_id}: {detail}")


class OfficialListingConnector:
    """Fetch bounded records from official HTML listings and return RawArticle.

    v1 supports:
    - AcquisitionMethod.HTML
    - source_id: vn_sbv_regulatory_docs only

    The connector is stateless.
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
        """Fetch one HTML source without owning downstream normalization."""
        started_at = time.perf_counter()

        try:
            self._validate_source(source)
            if self._client is not None:
                articles = await self._fetch_with_client(self._client, source)
            else:
                async with httpx.AsyncClient(verify=True) as client:
                    articles = await self._fetch_with_client(client, source)
        except OfficialListingConnectorError as error:
            logger.error(
                "official_listing_fetch_failed",
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
            "official_listing_fetch_succeeded",
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
        if source.acquisition.method is not AcquisitionMethod.HTML:
            raise ListingConfigurationError(
                f"source {source.source_id} does not use HTML acquisition"
            )
        if source.source_id not in _SUPPORTED_SOURCE_IDS:
            raise ListingConfigurationError(
                f"source {source.source_id} is not supported by OfficialListingConnector v1"
            )
        if source.rights.can_fetch is not True:
            raise ListingConfigurationError(
                f"source {source.source_id} is not approved for fetching"
            )
        if not source.acquisition.endpoint_url:
            raise ListingConfigurationError(f"source {source.source_id} missing endpoint_url")

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
    ) -> list[RawArticle]:
        retrieved_at = self._current_utc_time()
        articles: list[RawArticle] = []
        visited_urls: set[str] = set()
        next_url: str | None = str(source.acquisition.endpoint_url)

        while next_url is not None and len(articles) < self._max_items:
            if next_url in visited_urls:
                raise ListingParseError(
                    source.source_id,
                    f"pagination loop detected at {next_url}",
                )

            visited_urls.add(next_url)
            response = await self._request_with_retries(client, source, next_url)
            remaining = self._max_items - len(articles)

            # Use strict fail-closed parsing
            page_articles, raw_next_url = self._parse_sbv_html(
                source, response.text, next_url, retrieved_at, remaining
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
        if raw_next_url is None:
            return None

        try:
            parsed = urlparse(raw_next_url)
        except ValueError as error:
            raise ListingParseError(
                source.source_id,
                f"invalid pagination URL (parse error): {raw_next_url}",
            ) from error

        if parsed.scheme != "https":
            raise ListingParseError(
                source.source_id,
                f"invalid pagination URL (not https): {raw_next_url}",
            )

        if parsed.username or parsed.password:
            raise ListingParseError(
                source.source_id,
                f"invalid pagination URL (userinfo present): {raw_next_url}",
            )

        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != _SBV_ORIGIN:
            raise ListingParseError(
                source.source_id,
                f"invalid pagination URL (cross-origin): {raw_next_url}",
            )

        if raw_next_url in visited_urls:
            raise ListingParseError(
                source.source_id,
                f"pagination loop detected: {raw_next_url}",
            )

        return raw_next_url

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
        url: str,
    ) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await client.get(
                    url,
                    headers={"User-Agent": _DEFAULT_USER_AGENT},
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as error:
                if attempt == self._max_attempts:
                    raise ListingFetchError(
                        source.source_id,
                        category="timeout",
                        attempts=attempt,
                        retryable=True,
                    ) from error
                await self._wait_before_retry(source.source_id, attempt, "timeout")
                continue
            except httpx.RequestError as error:
                if attempt == self._max_attempts:
                    raise ListingFetchError(
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
                    raise ListingFetchError(
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
                raise ListingFetchError(
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
            "official_listing_fetch_retry",
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
    def _parse_sbv_html(
        source: SourceConfig,
        html_content: str,
        base_url: str,
        retrieved_at: datetime,
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        """Parse SBV regulatory document listing page."""
        soup = BeautifulSoup(html_content, "html.parser")

        articles: list[RawArticle] = []
        invalid_items = 0

        # Target the specific container for regulatory documents to avoid unrelated navigation links
        list_container = soup.find("div", class_="danh-sach-tin-tuc-v32")
        if not list_container:
            raise ListingParseError(
                source.source_id, "Could not find the regulatory document listing container"
            )

        items = list_container.find_all("li")

        for item_index, item in enumerate(items):
            if len(articles) >= max_items:
                break

            link = item.find("a", class_="title-news-link")
            if not link:
                continue

            href = link.get("href")
            if not href or not str(href).strip():
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_href"
                )
                continue

            # Canonicalize URL: resolve relative and ONLY strip the 'redirect' query parameter
            absolute_url = urljoin(base_url, str(href).strip())
            parsed_url = urlparse(absolute_url)
            query_params = parse_qsl(parsed_url.query, keep_blank_values=True)
            filtered_query = [(k, v) for k, v in query_params if k != "redirect"]
            new_query = urlencode(filtered_query)
            canonical_url = urlunparse(parsed_url._replace(query=new_query))

            title = link.get_text(strip=True)
            if not title:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_title"
                )
                continue

            # Extract a stable source_item_id from the title ONLY if it's a complete citation
            # e.g. 38/2026/TT-NHNN or 16/2014/TT-NHNN
            import re

            source_item_id = None
            symbol_match = re.search(r"\b([0-9]+/[0-9]{4}/[A-Za-z0-đĐ\-]+)\b", title)
            if symbol_match:
                source_item_id = symbol_match.group(1).strip()

            # Target the specific date column
            published_at_raw = None
            date_col = item.find("span", class_="date-about")
            if date_col:
                date_text = date_col.get_text(strip=True)
                if date_text:
                    published_at_raw = date_text

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=canonical_url,
                    title=title,
                    description=None,
                    published_at_raw=published_at_raw,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                )
            )

        # Pagination: Look for next page link in pagination container
        next_page_url = None
        next_link = soup.find("a", title="Trang kế tiếp")
        if next_link and next_link.get("href"):
            raw_href = str(next_link.get("href")).strip()
            if not raw_href.startswith("javascript"):
                next_page_url = urljoin(base_url, raw_href)

        if not articles and invalid_items:
            raise ListingParseError(
                source.source_id, "Listing container found but no usable articles were extracted"
            )

        return articles, next_page_url

    @staticmethod
    def _log_unusable_item(source_id: str, item_index: int, reason: str) -> None:
        logger.warning(
            "official_listing_item_skipped",
            extra={
                "source_id": source_id,
                "stage": "parse",
                "status": "SKIPPED",
                "error_type": reason,
                "item_index": item_index,
            },
        )
