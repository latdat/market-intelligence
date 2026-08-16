"""Legal Corpus connector — v1 supports GovInfo PLAW."""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlparse

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
_DEFAULT_USER_AGENT = "market-intelligence/0.1 legal-corpus-connector"

_SUPPORTED_SOURCE_IDS = frozenset({"us_govinfo_legal"})
_GOVINFO_PLAW_ORIGIN = "https://api.govinfo.gov"
_GOVINFO_PLAW_COLLECTION_PATH = "/collections/PLAW"

_SAFE_PACKAGE_ID_PATTERN = re.compile(r"^PLAW-[A-Za-z0-9-]+$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LegalCorpusConnectorError(Exception):
    """Base error for failures at the Legal Corpus connector boundary."""


class CorpusConfigurationError(LegalCorpusConnectorError):
    """Raised when source configuration cannot be used by this connector."""


class CorpusFetchError(LegalCorpusConnectorError):
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
            f"legal corpus fetch failed for {source_id}: category={category}, "
            f"attempts={attempts}{status_detail}"
        )


class CorpusParseError(LegalCorpusConnectorError):
    """Raised when a response cannot produce any trustworthy API records."""

    def __init__(self, source_id: str, detail: str) -> None:
        self.source_id = source_id
        self.detail = detail
        super().__init__(f"legal corpus parse failed for {source_id}: {detail}")


class CorpusBoundsError(LegalCorpusConnectorError):
    """Raised when the collection response exceeds the max_items bound."""

    def __init__(self, source_id: str, detail: str) -> None:
        self.source_id = source_id
        self.detail = detail
        super().__init__(f"legal corpus bounds exceeded for {source_id}: {detail}")


class LegalCorpusConnector:
    """Fetch bounded records from canonical legal corpora and return RawArticle.

    v1 supports:
    - AcquisitionMethod.REST_API
    - source_id: us_govinfo_legal only
    - package-level PLAW only
    """

    def __init__(
        self,
        api_key: str,
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
        if not api_key or not api_key.strip():
            raise CorpusConfigurationError("GOVINFO_API_KEY must be provided")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if base_retry_delay_seconds < 0:
            raise ValueError("base_retry_delay_seconds must not be negative")
        if max_retry_delay_seconds <= 0:
            raise ValueError("max_retry_delay_seconds must be positive")
        if isinstance(max_items, bool) or max_items < 1 or max_items > 1000:
            raise ValueError("max_items must be between 1 and 1000")
        if isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")

        self._api_key = api_key.strip()
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
        started_at = time.perf_counter()

        try:
            self._validate_source(source)
            if self._client is not None:
                articles = await self._fetch_with_client(self._client, source)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False
                ) as client:
                    articles = await self._fetch_with_client(client, source)
        except LegalCorpusConnectorError as error:
            logger.error(
                "legal_corpus_fetch_failed",
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
            "legal_corpus_fetch_succeeded",
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
            raise CorpusConfigurationError(
                f"source {source.source_id} does not use REST_API acquisition"
            )
        if source.source_id not in _SUPPORTED_SOURCE_IDS:
            raise CorpusConfigurationError(
                f"source {source.source_id} is not supported by LegalCorpusConnector v1"
            )
        if source.rights.can_fetch is not True:
            raise CorpusConfigurationError(
                f"source {source.source_id} is not approved for fetching"
            )

        parsed = urlparse(str(source.acquisition.endpoint_url))
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != _GOVINFO_PLAW_ORIGIN or parsed.path != _GOVINFO_PLAW_COLLECTION_PATH:
            raise CorpusConfigurationError("source endpoint must be exact PLAW collection endpoint")

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
    ) -> list[RawArticle]:
        retrieved_at = self._current_utc_time()
        start_date = retrieved_at - timedelta(days=7)

        collection_url = (
            f"{source.acquisition.endpoint_url}/{start_date.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
        url = collection_url
        params: dict[str, str] = {
            "offsetMark": "*",
            "pageSize": str(self._max_items),
        }
        package_ids: list[str] = []
        seen_package_ids: set[str] = set()
        visited_urls: set[str] = set()
        pages_fetched = 0
        limiter = RequestRateLimiter(source.acquisition.rate_limit, sleep=self._sleep)

        while len(package_ids) < self._max_items:
            if pages_fetched >= self._max_pages:
                raise CorpusBoundsError(
                    source.source_id,
                    f"pagination exceeded max_pages={self._max_pages}",
                )
            request_identity = str(httpx.URL(url, params=params))
            if request_identity in visited_urls:
                raise CorpusParseError(source.source_id, "pagination loop detected")
            visited_urls.add(request_identity)
            pages_fetched += 1

            response = await self._request_with_retries(client, source, url, params, limiter)
            page_package_ids, raw_next_page = self._parse_collection_response(
                source, response.content
            )
            new_package_ids = [
                package_id for package_id in page_package_ids if package_id not in seen_package_ids
            ]
            remaining = self._max_items - len(package_ids)
            package_ids.extend(new_package_ids[:remaining])
            seen_package_ids.update(new_package_ids)

            if len(package_ids) >= self._max_items or raw_next_page is None:
                break
            if not new_package_ids:
                raise CorpusParseError(
                    source.source_id,
                    "pagination made no package progress",
                )
            url = self._validate_next_page_url(
                source,
                raw_next_page,
                collection_url,
            )
            params = {}

        articles: list[RawArticle] = []
        for package_id in package_ids:
            summary_url = f"{_GOVINFO_PLAW_ORIGIN}/packages/{package_id}/summary"
            summary_response = await self._request_with_retries(
                client, source, summary_url, {}, limiter
            )
            article = self._parse_summary_response(
                source, package_id, summary_response.content, retrieved_at
            )
            articles.append(article)

        return articles

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
        url: str,
        params: dict[str, str],
        limiter: RequestRateLimiter,
    ) -> httpx.Response:
        headers = {
            "User-Agent": _DEFAULT_USER_AGENT,
            "X-Api-Key": self._api_key,
        }
        for attempt in range(1, self._max_attempts + 1):
            await limiter.wait()
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as error:
                if attempt == self._max_attempts:
                    raise CorpusFetchError(
                        source.source_id,
                        category="timeout",
                        attempts=attempt,
                        retryable=True,
                    ) from error
                await self._wait_before_retry(source.source_id, attempt, "timeout")
                continue
            except httpx.RequestError as error:
                if attempt == self._max_attempts:
                    raise CorpusFetchError(
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
                    raise CorpusFetchError(
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

            if not (200 <= status_code < 300):
                raise CorpusFetchError(
                    source.source_id,
                    category="http_status",
                    attempts=attempt,
                    retryable=False,
                    status_code=status_code,
                )

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
            "legal_corpus_fetch_retry",
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
        from email.utils import parsedate_to_datetime

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

    def _parse_collection_response(
        self,
        source: SourceConfig,
        content: bytes,
    ) -> tuple[list[str], str | None]:
        import json

        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError) as error:
            raise CorpusParseError(
                source.source_id, f"response is not valid JSON: {error}"
            ) from error

        if not isinstance(payload, dict):
            raise CorpusParseError(source.source_id, "expected JSON object at root")

        count = payload.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise CorpusParseError(source.source_id, "missing or invalid count")

        packages = payload.get("packages")
        if not isinstance(packages, list):
            raise CorpusParseError(source.source_id, "missing or invalid packages list")

        if len(packages) > count:
            raise CorpusParseError(
                source.source_id,
                f"inconsistent packages/count: count={count} but {len(packages)} packages",
            )

        next_page = payload.get("nextPage")
        if next_page is not None and not isinstance(next_page, str):
            raise CorpusParseError(source.source_id, "invalid nextPage")
        normalized_next_page = next_page.strip() if isinstance(next_page, str) else None
        if normalized_next_page == "":
            normalized_next_page = None

        if count == 0 and len(packages) == 0:
            if normalized_next_page is not None:
                raise CorpusParseError(source.source_id, "empty collection has nextPage")
            return [], None

        package_ids: list[str] = []
        for pkg in packages:
            if not isinstance(pkg, dict):
                raise CorpusParseError(source.source_id, "malformed package entry")
            pkg_id = pkg.get("packageId")
            if not isinstance(pkg_id, str) or not _SAFE_PACKAGE_ID_PATTERN.match(pkg_id):
                raise CorpusParseError(source.source_id, f"unsafe packageId: {pkg_id}")
            package_ids.append(pkg_id)

        return package_ids, normalized_next_page

    @staticmethod
    def _validate_next_page_url(
        source: SourceConfig,
        raw_next_page: str,
        collection_url: str,
    ) -> str:
        try:
            parsed = urlparse(raw_next_page)
        except ValueError as error:
            raise CorpusParseError(source.source_id, "invalid nextPage URL") from error

        expected = urlparse(collection_url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.netloc != expected.netloc
            or parsed.path != expected.path
            or parsed.fragment
        ):
            raise CorpusParseError(source.source_id, "unsafe nextPage URL")

        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        query_names = [name for name, _ in query_items]
        if (
            any(name not in {"offsetMark", "pageSize"} for name in query_names)
            or query_names.count("offsetMark") != 1
        ):
            raise CorpusParseError(source.source_id, "unsafe nextPage query")
        offset_mark = next(value for name, value in query_items if name == "offsetMark")
        if not offset_mark:
            raise CorpusParseError(source.source_id, "empty nextPage offsetMark")

        page_sizes = [value for name, value in query_items if name == "pageSize"]
        if len(page_sizes) > 1:
            raise CorpusParseError(source.source_id, "duplicate nextPage pageSize")
        if page_sizes:
            try:
                page_size = int(page_sizes[0])
            except ValueError as error:
                raise CorpusParseError(source.source_id, "invalid nextPage pageSize") from error
            if not 1 <= page_size <= 1000:
                raise CorpusParseError(source.source_id, "invalid nextPage pageSize")

        return raw_next_page

    def _parse_summary_response(
        self,
        source: SourceConfig,
        expected_package_id: str,
        content: bytes,
        retrieved_at: datetime,
    ) -> RawArticle:
        import json

        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError) as error:
            raise CorpusParseError(
                source.source_id, f"summary response is not valid JSON: {error}"
            ) from error

        if not isinstance(payload, dict):
            raise CorpusParseError(source.source_id, "expected JSON object at root of summary")

        pkg_id = payload.get("packageId")
        if pkg_id != expected_package_id:
            raise CorpusParseError(
                source.source_id,
                f"packageId mismatch: expected {expected_package_id}, got {pkg_id}",
            )

        collection_code = payload.get("collectionCode")
        if collection_code != "PLAW":
            raise CorpusParseError(source.source_id, f"wrong collectionCode: {collection_code}")

        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise CorpusParseError(source.source_id, "missing or empty title")

        date_issued = payload.get("dateIssued")
        published_at_raw = (
            date_issued if isinstance(date_issued, str) and date_issued.strip() else None
        )

        last_modified = payload.get("lastModified")

        url = f"https://www.govinfo.gov/app/details/{pkg_id}"

        raw_metadata = {
            "collection_code": collection_code,
        }
        if isinstance(last_modified, str) and last_modified.strip():
            raw_metadata["last_modified"] = last_modified.strip()

        return RawArticle(
            source_id=source.source_id,
            source_item_id=pkg_id,
            url=url,
            title=title.strip(),
            description=None,
            published_at_raw=published_at_raw,
            language_hint=source.language,
            retrieved_at=retrieved_at,
            raw_metadata=raw_metadata,
        )
