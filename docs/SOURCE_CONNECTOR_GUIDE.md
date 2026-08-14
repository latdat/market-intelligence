# Source Connector Guide

## 1. Goal

A source connector converts one configured external source into `RawArticle` records without owning downstream normalization, deduplication, classification, or matching.

Connector responsibility:

```text
configured source
      ↓
network request
      ↓
parse source format
      ↓
RawArticle[]
```

Not:

```text
connector
  ↓
write everything to DB
  ↓
call LLM
  ↓
send email
```

## 2. Preferred connector interface

The exact Python implementation may vary, but preserve a small boundary conceptually similar to:

```python
from typing import Protocol

class SourceConnector(Protocol):
    async def fetch(self, source) -> list["RawArticle"]:
        ...
```

Avoid over-engineering a plugin framework before multiple connectors prove the need.

## 3. Acquisition preference

Use the simplest reliable source interface:

1. official REST/API when available and appropriate
2. RSS/Atom
3. HTML
4. sitemap where appropriate
5. Playwright only when static HTTP approaches do not work

## 4. Connector inputs

A connector should receive source configuration instead of hardcoding operational policy where reasonable.

Potential configuration:

```text
source_id
base_url/feed_url/api_url
poll_interval
rate_limit
language
market
headers if non-secret/static
parser options
rights
```

Secrets must come from environment/secret management, not source registry files committed to Git.

## 5. Connector output

Return `RawArticle` records.

Do not perform canonical deduplication inside a source-specific parser unless the behavior is truly source-local.

Preserve upstream identifiers and timestamps.

## 6. HTTP behavior

Use:

- explicit timeout
- useful user agent when appropriate
- bounded retry policy
- response status handling
- rate-limit handling
- structured logging

Be conservative with concurrency.

A 40–60 source system does not require aggressive scraping.

## 7. 304 / conditional fetching

When the source supports caching validators such as ETag or Last-Modified, a connector may use them to reduce unnecessary transfer.

If implemented:

- persist validator state safely
- handle 304 as success/no-new-data
- test validator behavior

Do not add complexity before it benefits a real source.

## 8. RSS/Atom

Prefer `feedparser`.

Map commonly available fields into `RawArticle`:

```text
id/guid          → source_item_id
link             → url
title            → title
summary          → description
published/updated→ published_at_raw
```

Do not assume every feed supplies every field.

### 8.1 DE-004 implementation boundary

The generic `RssAtomConnector` receives validated `SourceConfig`. Its endpoint is
`SourceConfig.acquisition.endpoint_url`, which is static internal configuration and
is not part of the flat `SourceDefinition` shared wire contract.

The connector:

- accepts only `RSS` and `ATOM` acquisition methods
- refuses a source when `rights.can_fetch` is false
- does not automatically follow redirects away from the reviewed configured endpoint
- uses a 10-second request timeout and at most three attempts by default
- retries timeout/connection failures and HTTP 408, 429, 500, 502, 503, and 504
- does not retry other HTTP 4xx responses
- applies bounded exponential backoff with jitter
- honors a valid `Retry-After` header for HTTP 429/503, capped by the configured maximum delay
- returns valid entries while warning about unusable entries
- raises `FeedParseError` when a response is not a feed or all entries are unusable
- returns an empty list only for a valid feed that contains no entries

The retry sleep function is injectable so deterministic tests do not wait in real
time. The connector remains stateless and does not normalize, deduplicate, persist,
classify, or emit telemetry records.

## 9. REST/API

Use `httpx`.

Connector must define:

- endpoint
- pagination behavior
- source-specific field mapping
- rate-limit behavior
- publication timestamp mapping

Pagination must terminate deterministically.

## 10. HTML

Prefer `BeautifulSoup` or `lxml`.

HTML connector code should:

- keep selectors localized
- fail visibly when expected structure disappears
- resolve relative URLs
- avoid downloading full article pages when listing metadata is sufficient
- include representative HTML fixtures

Do not use Playwright just because HTML parsing is inconvenient.

## 11. Rights

Before implementing a connector, verify source registry metadata includes an acquisition/rights decision.

Important principle:

> Technical accessibility is not the same as permission to store or redistribute full text.

Default connector output should therefore be metadata-first.

## 12. Health metrics

Useful connector health signals:

- fetch success/failure
- status code
- duration
- items returned
- new items discovered
- parse errors
- last success time
- consecutive failures

## 13. Suggested source task template

```text
Goal:
Implement connector for <SOURCE>.

Read:
- AGENTS.md
- docs/ARCHITECTURE.md
- docs/DATA_CONTRACTS.md
- docs/SOURCE_CONNECTOR_GUIDE.md

Scope:
<connector path>

Requirements:
- implement configured acquisition method
- return RawArticle only
- preserve source_item_id when available
- preserve source publication timestamp
- handle rate limits
- emit structured logs

Do not:
- change CanonicalArticle
- call classification
- write notification state
- add Playwright unless explicitly justified

Tests:
- success
- missing optional fields
- timeout
- rate limit
- transient 5xx
- malformed response
- repeated run behavior

At end report:
- files changed
- tests run
- results
- remaining source-specific risks
```
