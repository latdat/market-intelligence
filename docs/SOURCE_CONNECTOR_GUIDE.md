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

### 3.1 Approved acquisition abstractions

Official Source Architecture v1 uses these connector-level abstractions:

| Abstraction | Status | Purpose |
|---|---|---|
| RSS/Atom Connector | `Implemented` | Parse configured RSS/Atom channels into `RawArticle` |
| Government API Connector | `Implemented (SO-004)` | Acquire bounded records from official government REST APIs |
| Legal Corpus Connector | `Implemented (SO-005 v1: PLAW package-level)` | Traverse canonical legal corpora while preserving official identity/provenance |
| Official Listing Connector | `Implemented (SO-006)` | Parse official publication/listing pages when no suitable feed/API exists |

The future abstractions may share `httpx`, pagination, HTML parsing, or sitemap helpers.
They are semantic connector boundaries, not separate queues or infrastructure systems.
SO-004 implements the Government API Connector boundary for `us_federal_register`.
SO-005 implements the Legal Corpus Connector boundary for `us_govinfo_legal` (v1 scope: PLAW collection package summaries).
SO-006 implements the Official Listing Connector boundary for `vn_sbv_regulatory_docs`.

Future GNews acquisition must also return `RawArticle` and then use the same normalize,
deduplication, article persistence, and classification path as official sources. It must
not introduce a parallel article contract or bypass official-evidence provenance. GNews
and Story/Event remain `Planned / documented only`.

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

### 9.1 GovernmentApiConnector v1 boundary (SO-004)

`GovernmentApiConnector` is implemented at
`src/market_intelligence/connectors/government_api.py`.

**v1 scope:**

- Accepts only `AcquisitionMethod.REST_API`.
- Supports only `source_id: us_federal_register`. Any other REST_API source raises
  `ApiConfigurationError` before network access.
- Refuses a source when `rights.can_fetch` is false.

**HTTP behavior (matches RssAtomConnector):**

- 10-second request timeout.
- At most 3 attempts.
- Retries timeout/connection failures and HTTP 408, 429, 500, 502, 503, and 504.
- Does not retry other HTTP 4xx responses.
- Does not automatically follow redirects (`follow_redirects=False`).
- Bounded exponential backoff with jitter.
- Honors a valid `Retry-After` header for HTTP 429/503, capped by max delay.
- `clock`, `sleep`, and `random_value` are injectable for deterministic tests.

**Pagination:**

- Uses `next_page_url` from the API response envelope.
- Bounded by `max_items` constructor parameter.
- Before following `next_page_url` validates: HTTPS only, no userinfo, same Federal
  Register origin (`https://www.federalregister.gov`), exact documents API path
  (`/api/v1/documents.json`), and URL not already visited.
- Rejects cross-origin pagination and loops with `ApiParseError` rather than warning/partial success.
- Does not persist pagination/cursor state between calls.

**Federal Register field mapping:**

| API field | RawArticle field |
|---|---|
| `document_number` | `source_item_id` |
| `html_url` | `url` |
| `title` | `title` |
| `abstract` | `description` |
| `publication_date` | `published_at_raw` (preserved exactly as date string) |
| source `language` | `language_hint` |
| connector clock | `retrieved_at` |
| source `source_id` | `source_id` |

`html_url` is required; items without it are skipped with a structured warning.
Missing `html_url` in all items in a non-empty response is a parse error.
Empty `results` list is valid (no new documents) and returns `[]`.

**Publication date policy:**

The Federal Register API returns `publication_date` as a date-only string (`YYYY-MM-DD`).
This value is preserved exactly in `published_at_raw`. The connector does NOT synthesize
midnight UTC or substitute `retrieved_at`. Normalization handles timestamp parsing
downstream using the existing policy.

**Connector does NOT:**

- Normalize, deduplicate, persist, classify, or emit telemetry records.
- Download Federal Register HTML bodies or GovInfo PDFs.
- Implement `us_govinfo_legal` (Legal Corpus — separate SO-005 connector).

### 9.2 LegalCorpusConnector v1 boundary (SO-005)

`LegalCorpusConnector` is implemented at
`src/market_intelligence/connectors/legal_corpus.py`.

**v1 scope:**

- Accepts only `AcquisitionMethod.REST_API`.
- Supports only `source_id: us_govinfo_legal`.
- Supports only the GovInfo `PLAW` collection at the package level.
- Refuses a source when `rights.can_fetch` is false.
- Fails closed on any parse anomaly, missing API key, or invalid pagination counts.

**Authentication & Environment:**

- Requires a valid `GOVINFO_API_KEY` provided via environment variable.
- Requires sending the API key securely via the `X-Api-Key` HTTP header.
- The key must NEVER be appended to request URLs or logged.

**Discovery & Pagination:**

- Uses the GovInfo Collections Service (`/collections/PLAW`).
- Discovers documents based on a rolling 7-day `lastModified` lookback derived from an injectable UTC clock.
- Enforces strict limits: `1 <= max_items <= 1000`.
- Raises `CorpusBoundsError` if the returned `count` exceeds `max_items`.
- Does not concurrently fetch packages (sequential package summary fetching only).

**GovInfo Field Mapping:**

| API summary field | RawArticle field |
|---|---|
| `packageId` | `source_item_id` |
| Constructed URL (`https://www.govinfo.gov/app/details/{packageId}`) | `url` |
| `title` | `title` |
| `None` | `description` |
| `dateIssued` | `published_at_raw` |
| source `language` | `language_hint` |
| connector clock | `retrieved_at` |
| source `source_id` | `source_id` |

**Metadata & Timestamp preservation:**
- The `dateIssued` field (e.g. `2026-08-10`) is mapped to `published_at_raw`.
- The `lastModified` timestamp from GovInfo is captured in `raw_metadata` but is NOT synthesized into `published_at_raw`.

**Connector does NOT:**
- Normalize, deduplicate, persist, classify, or emit telemetry records.
- Download full-text granule PDFs or XML files.
- Aggregate multiple GovInfo collections in v1.

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

Rights review must cover the configured source/channel, its content class, and explicit
third-party exclusions. Hostname-level approval alone is insufficient. A connector must
receive a content-homogeneous `SourceConfig`; it must not silently mix editorial news and
formal regulatory documents from the same website. The required `content_scope` value
describes that class but does not authorize AI; authorization remains the conjunction of
approved review status and boolean AI permission. See the canonical policy in
[`SOURCE_REGISTRY.md`](SOURCE_REGISTRY.md#6-rights).

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
