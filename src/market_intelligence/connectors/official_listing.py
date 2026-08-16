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
from market_intelligence.connectors.request_rate_limiter import RequestRateLimiter
from market_intelligence.source_registry import AcquisitionMethod, SourceConfig

logger = logging.getLogger(__name__)

type FetchErrorCategory = Literal["timeout", "connection", "http_status"]
type SleepFunction = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]
type RandomFunction = Callable[[], float]

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})
_DEFAULT_USER_AGENT = "market-intelligence/0.1 official-listing-connector"

# Supported sources
_SUPPORTED_SOURCE_IDS = frozenset(
    {
        "vn_sbv_regulatory_docs",
        "vn_moit_regulatory_docs",
        "vn_mst_regulatory_docs",
        "us_bis_regulatory",
        "us_fhfa_regulatory",
        "eu_esma_regulatory",
    }
)

_SBV_ORIGIN = "https://sbv.gov.vn"
_SBV_PAGINATION_PATH = "/vi/vbdh"

_MOIT_ORIGIN = "https://moit.gov.vn"
_MOIT_PAGINATION_PATH = "/van-ban-phap-luat/van-ban-phap-quy"

_MST_ORIGIN = "https://mst.gov.vn"
_MST_PAGINATION_PATH = "/van-ban-phap-luat.htm"

_BIS_ORIGIN = "https://www.bis.gov"
_BIS_PAGINATION_PATH = "/regulations/federal-register-notices"

_FHFA_ORIGIN = "https://www.fhfa.gov"
_FHFA_PAGINATION_PATH = "/regulation/federal-register"

_ESMA_ORIGIN = "https://www.esma.europa.eu"
_ESMA_PAGINATION_PATH = "/databases-library/esma-library"


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
        max_pages: int = 10,
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
        if isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")

        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_attempts = max_attempts
        self._base_retry_delay_seconds = base_retry_delay_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._max_items = max_items
        self._max_pages = max_pages
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
        listing_path = str(urlparse(next_url).path)
        pages_fetched = 0
        limiter = RequestRateLimiter(source.acquisition.rate_limit, sleep=self._sleep)

        while next_url is not None and len(articles) < self._max_items:
            if pages_fetched >= self._max_pages:
                raise ListingParseError(
                    source.source_id,
                    f"pagination exceeded max_pages={self._max_pages}",
                )
            if next_url in visited_urls:
                raise ListingParseError(
                    source.source_id,
                    f"pagination loop detected at {next_url}",
                )

            visited_urls.add(next_url)
            pages_fetched += 1
            response = await self._request_with_retries(client, source, next_url, limiter)
            remaining = self._max_items - len(articles)

            # Use strict fail-closed parsing based on source
            if source.source_id == "vn_sbv_regulatory_docs":
                page_articles, raw_next_url = self._parse_sbv_html(
                    source, response.text, next_url, retrieved_at, remaining
                )
            elif source.source_id == "vn_moit_regulatory_docs":
                page_articles, raw_next_url = self._parse_moit_html(
                    source, response.text, next_url, retrieved_at, remaining
                )
            elif source.source_id == "vn_mst_regulatory_docs":
                page_articles, raw_next_url = self._parse_mst_html(
                    source, response.text, next_url, retrieved_at, remaining
                )
            elif source.source_id == "us_bis_regulatory":
                page_articles, raw_next_url = self._parse_bis_html(
                    source, response.text, next_url, retrieved_at, remaining
                )
            elif source.source_id == "us_fhfa_regulatory":
                page_articles, raw_next_url = self._parse_fhfa_html(
                    source, response.text, next_url, retrieved_at, remaining
                )
            elif source.source_id == "eu_esma_regulatory":
                page_articles, raw_next_url = self._parse_esma_html(
                    source, response.text, next_url, retrieved_at, remaining
                )
            else:
                raise ListingParseError(source.source_id, "unsupported source_id")
            articles.extend(page_articles)
            if len(articles) >= self._max_items:
                break
            if raw_next_url is not None and not page_articles:
                raise ListingParseError(
                    source.source_id,
                    "pagination made no article progress",
                )

            next_url = self._validate_next_page_url(
                source,
                raw_next_url,
                visited_urls,
                listing_path,
            )

        return articles

    def _validate_next_page_url(
        self,
        source: SourceConfig,
        raw_next_url: str | None,
        visited_urls: set[str],
        listing_path: str,
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
        if source.source_id == "vn_sbv_regulatory_docs" and origin != _SBV_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "vn_moit_regulatory_docs" and origin != _MOIT_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "vn_mst_regulatory_docs" and origin != _MST_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "us_bis_regulatory" and origin != _BIS_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "us_fhfa_regulatory" and origin != _FHFA_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "eu_esma_regulatory" and origin != _ESMA_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "vn_moit_regulatory_docs" and origin != _MOIT_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )
        elif source.source_id == "vn_mst_regulatory_docs" and origin != _MST_ORIGIN:
            raise ListingParseError(
                source.source_id, f"invalid pagination URL (cross-origin): {raw_next_url}"
            )

        allowed_paths = {listing_path}
        if source.source_id == "vn_sbv_regulatory_docs":
            allowed_paths.add(_SBV_PAGINATION_PATH)
        elif source.source_id == "vn_moit_regulatory_docs":
            allowed_paths.add(_MOIT_PAGINATION_PATH)
        elif source.source_id == "vn_mst_regulatory_docs":
            allowed_paths.add(_MST_PAGINATION_PATH)
        elif source.source_id == "us_bis_regulatory":
            allowed_paths.add(_BIS_PAGINATION_PATH)
        elif source.source_id == "us_fhfa_regulatory":
            allowed_paths.add(_FHFA_PAGINATION_PATH)
        elif source.source_id == "eu_esma_regulatory":
            allowed_paths.add(_ESMA_PAGINATION_PATH)
            query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if query_params.get("f[0]") != "basic_section:35":
                raise ListingParseError(
                    source.source_id, "invalid pagination URL (missing/changed section filter)"
                )

        if parsed.path not in allowed_paths:
            raise ListingParseError(
                source.source_id,
                f"invalid pagination URL (unexpected path): {raw_next_url}",
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
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_article_link"
                )
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
        if next_link is None:
            next_link = soup.find("a", rel="next")
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

    @staticmethod
    def _parse_moit_html(
        source: SourceConfig,
        html_content: str,
        base_url: str,
        retrieved_at: datetime,
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        """Parse MOIT regulatory document listing page."""
        import re

        soup = BeautifulSoup(html_content, "html.parser")
        articles: list[RawArticle] = []
        invalid_items = 0

        rows = soup.find_all("tr")
        for item_index, row in enumerate(rows):
            if len(articles) >= max_items:
                break

            tds = row.find_all("td")
            if not tds or len(tds) < 4:
                continue

            link = row.find("a")
            if not link:
                continue

            href = link.get("href")
            if not href or not str(href).strip():
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_href"
                )
                continue

            absolute_url = urljoin(base_url, str(href).strip())
            parsed_url = urlparse(absolute_url)
            query_params = parse_qsl(parsed_url.query, keep_blank_values=True)
            filtered_query = [(k, v) for k, v in query_params if k != "redirect"]
            canonical_url = urlunparse(parsed_url._replace(query=urlencode(filtered_query)))

            title = tds[1].get_text(strip=True)
            if not title:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_title"
                )
                continue

            doc_num_raw = tds[2].get_text(strip=True)
            published_at_raw = tds[3].get_text(strip=True)

            # Clean and determine identity
            source_item_id = None
            if doc_num_raw:
                doc_num_clean = re.sub(r"^[Ss]ố[:\s]*", "", doc_num_raw).strip()
                if re.search(r"[0-9]+/[0-9]{4}/", doc_num_clean):
                    source_item_id = doc_num_clean

            raw_metadata: dict[str, object] = {}
            if doc_num_raw:
                raw_metadata["document_number"] = doc_num_raw

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=canonical_url,
                    title=title,
                    description=None,
                    published_at_raw=published_at_raw or None,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                    raw_metadata=raw_metadata,
                )
            )

        # Pagination
        next_page_url = None
        next_link = soup.find("a", class_="next") or soup.find("a", rel="next")
        if not next_link:
            next_link = soup.find("a", string=re.compile(r"Sau"))
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
    def _parse_mst_html(
        source: SourceConfig,
        html_content: str,
        base_url: str,
        retrieved_at: datetime,
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        """Parse MST regulatory document listing page."""
        import re

        soup = BeautifulSoup(html_content, "html.parser")
        articles: list[RawArticle] = []
        invalid_items = 0

        rows = soup.find_all("tr")
        for item_index, row in enumerate(rows):
            if len(articles) >= max_items:
                break

            tds = row.find_all("td")
            if not tds or len(tds) < 6:
                continue

            # Look for detail link in the 5th column or anywhere in the row
            link = row.find("a", href=re.compile(r"\.htm$"))
            if not link:
                continue

            href = link.get("href")
            if not href or not str(href).strip():
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_href"
                )
                continue

            absolute_url = urljoin(base_url, str(href).strip())
            parsed_url = urlparse(absolute_url)
            query_params = parse_qsl(parsed_url.query, keep_blank_values=True)
            filtered_query = [(k, v) for k, v in query_params if k != "redirect"]
            canonical_url = urlunparse(parsed_url._replace(query=urlencode(filtered_query)))

            # Extract numeric record ID from the detail href
            source_item_id = None
            match = re.search(r"/van-ban-phap-luat/(\d+)\.htm", canonical_url)
            if match:
                source_item_id = match.group(1)

            title_cell_text = tds[4].get_text(strip=True)
            title = re.sub(r"Xem chi tiết$", "", title_cell_text, flags=re.IGNORECASE).strip()
            if not title:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_title"
                )
                continue

            doc_num = tds[0].get_text(strip=True)
            issuer = tds[1].get_text(strip=True)
            doc_type = tds[2].get_text(strip=True)
            field = tds[3].get_text(strip=True)
            published_at_raw = tds[5].get_text(strip=True)

            raw_metadata: dict[str, object] = {}
            if doc_num:
                raw_metadata["document_number"] = doc_num
            if issuer:
                raw_metadata["issuer"] = issuer
            if doc_type:
                raw_metadata["document_type"] = doc_type
            if field:
                raw_metadata["field"] = field

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=canonical_url,
                    title=title,
                    description=None,
                    published_at_raw=published_at_raw or None,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                    raw_metadata=raw_metadata,
                )
            )

        # Pagination
        next_page_url = None
        next_link = soup.find("a", string=re.compile(r"Sau")) or soup.find("a", rel="next")
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
    def _parse_bis_html(
        source: SourceConfig,
        html_content: str,
        base_url: str,
        retrieved_at: datetime,
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        import json
        import re

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")
        articles: list[RawArticle] = []
        invalid_items = 0

        script_tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
        if not script_tag or not script_tag.string:
            raise ListingParseError(source.source_id, "missing __NEXT_DATA__ script block")

        try:
            data = json.loads(script_tag.string)
        except json.JSONDecodeError as error:
            raise ListingParseError(
                source.source_id, f"malformed JSON in __NEXT_DATA__: {error}"
            ) from error

        frns = None

        def find_frns(node: object) -> None:
            nonlocal frns
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "frns" and isinstance(v, list):
                        frns = v
                        return
                for v in node.values():
                    if frns is not None:
                        return
                    find_frns(v)
            elif isinstance(node, list):
                for item in node:
                    if frns is not None:
                        return
                    find_frns(item)

        find_frns(data)

        if frns is None:
            raise ListingParseError(source.source_id, "missing frns structure in JSON")

        if not isinstance(frns, list):
            raise ListingParseError(source.source_id, "frns structure is not a list")

        for item_index, item in enumerate(frns):
            if len(articles) >= max_items:
                break

            title = item.get("frnTitle")
            if not title:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_title"
                )
                continue

            frn_url = item.get("frnUrl", {}).get("url")
            if not frn_url:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_frn_url"
                )
                continue

            published_at_raw = item.get("frnPublicationDate", {}).get("time")
            fr_citation = item.get("frnCitation")

            # Extract Federal Register Document Number from frn_url
            source_item_id = None
            if "federalregister.gov" in frn_url:
                match = re.search(r"/documents/\d{4}/\d{2}/\d{2}/([^/]+)/", frn_url)
                if match:
                    source_item_id = match.group(1)

            raw_metadata: dict[str, object] = {}
            if fr_citation:
                raw_metadata["fr_citation"] = fr_citation
            if "frnEffectiveOnDate" in item and isinstance(item["frnEffectiveOnDate"], dict):
                if "time" in item["frnEffectiveOnDate"]:
                    raw_metadata["effective_date"] = item["frnEffectiveOnDate"]["time"]
            if item.get("frnDocumentType"):
                raw_metadata["document_category"] = item["frnDocumentType"]
            if item.get("frnRegulationType"):
                raw_metadata["regulation_set"] = item["frnRegulationType"]

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=frn_url,
                    title=title,
                    description=None,
                    published_at_raw=published_at_raw,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                    raw_metadata=raw_metadata,
                )
            )

        if not articles and invalid_items:
            raise ListingParseError(
                source.source_id, "Listing container found but no usable articles were extracted"
            )

        return articles, None

    @staticmethod
    def _parse_fhfa_html(
        source: SourceConfig,
        html_content: str,
        base_url: str,
        retrieved_at: datetime,
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        import re

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")
        articles: list[RawArticle] = []
        invalid_items = 0

        rows = soup.find_all("tr")
        for item_index, row in enumerate(rows):
            if len(articles) >= max_items:
                break

            tds = row.find_all(["td", "th"])
            if not tds or len(tds) < 5:
                continue

            # Skip header row
            if "DateSort ascending" in tds[0].get_text():
                continue

            date_raw = tds[0].get_text(strip=True)
            title = tds[1].get_text(strip=True)
            number = tds[2].get_text(strip=True)
            doc_type = tds[3].get_text(strip=True)
            volume_page = tds[4].get_text(strip=True)

            if not title:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_title"
                )
                continue

            fhfa_detail_link = None
            fr_link = None
            for link in row.find_all("a", href=True):
                href = str(link.get("href")).strip()
                if "federalregister.gov" in href:
                    fr_link = href
                elif href.startswith("/regulation/federal-register/"):
                    fhfa_detail_link = href

            if not fhfa_detail_link:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_detail_link"
                )
                continue

            absolute_url = urljoin(base_url, fhfa_detail_link)

            source_item_id = None
            if fr_link:
                match = re.search(r"/documents/\d{4}/\d{2}/\d{2}/([^/]+)/", fr_link)
                if match:
                    source_item_id = match.group(1)

            raw_metadata: dict[str, object] = {}
            if number:
                raw_metadata["fhfa_number"] = number
            if doc_type:
                raw_metadata["type"] = doc_type
            if volume_page:
                raw_metadata["fr_citation"] = volume_page
            if fr_link:
                raw_metadata["federal_register_url"] = fr_link

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=absolute_url,
                    title=title,
                    description=None,
                    published_at_raw=date_raw or None,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                    raw_metadata=raw_metadata,
                )
            )

        next_page_url = None
        next_link = soup.find("a", string=re.compile(r"Next", re.I))
        if not next_link:
            next_link = soup.find("a", class_=re.compile(r"next", re.I))
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
    def _parse_esma_html(
        source: SourceConfig,
        html_content: str,
        base_url: str,
        retrieved_at: datetime,
        max_items: int,
    ) -> tuple[list[RawArticle], str | None]:
        import re

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")
        articles: list[RawArticle] = []
        invalid_items = 0

        rows = soup.find_all("tr")
        for item_index, row in enumerate(rows):
            if len(articles) >= max_items:
                break

            tds = row.find_all(["td", "th"])
            if not tds or len(tds) < 6:
                continue

            if "DateSort ascending" in tds[0].get_text():
                continue

            date_raw = tds[0].get_text(strip=True)
            reference = tds[1].get_text(strip=True)
            title = tds[2].get_text(strip=True)
            sections = tds[3].get_text(strip=True)
            doc_type = tds[4].get_text(strip=True)

            if not title:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_title"
                )
                continue

            if "Guidelines and Technical standards" not in sections:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_required_section"
                )
                continue

            detail_link = None
            for link in row.find_all("a", href=True):
                href = str(link.get("href")).strip()
                if href.startswith("/document/"):
                    detail_link = href
                    break

            if not detail_link:
                invalid_items += 1
                OfficialListingConnector._log_unusable_item(
                    source.source_id, item_index, "missing_detail_link"
                )
                continue

            absolute_url = urljoin(base_url, detail_link)

            source_item_id = None
            # e.g., ESMA75-113276571-1525
            if reference and re.match(r"^ESMA\d+-[a-zA-Z0-9\-]+$", reference):
                source_item_id = reference

            raw_metadata: dict[str, object] = {}
            if reference:
                raw_metadata["reference"] = reference
            if sections:
                raw_metadata["sections"] = sections
            if doc_type:
                raw_metadata["document_type"] = doc_type

            articles.append(
                RawArticle(
                    source_id=source.source_id,
                    source_item_id=source_item_id,
                    url=absolute_url,
                    title=title,
                    description=None,
                    published_at_raw=date_raw or None,
                    language_hint=source.language,
                    retrieved_at=retrieved_at,
                    raw_metadata=raw_metadata,
                )
            )

        next_page_url = None
        next_link = soup.find("a", string=re.compile(r"Next", re.I))
        if not next_link:
            next_link = soup.find("a", class_=re.compile(r"next", re.I))
            if not next_link:
                li = soup.find("li", class_=re.compile(r"pager__item--next", re.I))
                if li:
                    next_link = li.find("a")

        if next_link and next_link.get("href"):
            raw_href = str(next_link.get("href")).strip()
            if not raw_href.startswith("javascript"):
                next_page_url = urljoin(base_url, raw_href)

        if not articles and invalid_items:
            raise ListingParseError(
                source.source_id, "Listing container found but no usable articles were extracted"
            )

        return articles, next_page_url
