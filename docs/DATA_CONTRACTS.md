# Data Contracts — v0.1

This document defines the proposed shared contracts for the current implementation phase.

These contracts are intentionally small. Do not add fields merely because they may be useful later.

---

# 1. Pipeline stages

```text
SourceDefinition
      ↓
RawArticle
      ↓
CanonicalArticle
      ↓
ClassifiedArticle
      ↓
AlertCandidate
```

The exact storage model may differ, but service boundaries should preserve these concepts.

---

# 2. SourceDefinition

Represents one configured source.

```json
{
  "source_id": "us_federal_register",
  "name": "Federal Register",
  "market": "US",
  "language": "en",
  "source_type": "GOVERNMENT",
  "authority_level": "PRIMARY",
  "domains": ["LAW_POLICY"],
  "acquisition_method": "REST_API",
  "poll_interval_minutes": 15,
  "rate_limit": null,
  "priority": 100,
  "health_status": "ACTIVE",
  "last_success_at": null,
  "last_failure_at": null,
  "rights": {
    "can_fetch": true,
    "can_store_metadata": true,
    "can_store_full_text": "REVIEWED",
    "can_ai_process": "REVIEWED",
    "can_show_snippet": "REVIEWED",
    "can_redistribute_full_text": false,
    "rights_review_status": "APPROVED"
  },
  "cost": {
    "type": "FREE",
    "monthly_fixed_usd": 0
  }
}
```

Minimum fields:

- `source_id`
- `name`
- `market`
- `language`
- `source_type`
- `authority_level`
- `domains`
- `acquisition_method`
- `poll_interval_minutes`
- `rate_limit`
- `rights`
- `cost`
- `priority`
- `health_status`
- `last_success_at`
- `last_failure_at`

---

# 3. RawArticle

`RawArticle` is connector output before canonical normalization.

Recommended shape:

```json
{
  "source_id": "us_federal_register",
  "source_item_id": "optional-upstream-id",
  "url": "https://example.org/item/123",
  "title": "Raw title",
  "description": "Raw source description or snippet",
  "published_at_raw": "2026-08-14T10:00:00-04:00",
  "language_hint": "en",
  "retrieved_at": "2026-08-14T14:05:00Z",
  "raw_metadata": {}
}
```

Rules:

- preserve source values without pretending they are normalized
- avoid storing full publisher body unless explicitly approved
- `retrieved_at` is not `published_at`
- connector-specific fields may live in `raw_metadata`

Field presence and defaults:

- required: `source_id`, `url`, `retrieved_at`
- nullable with a default of `null`: `source_item_id`, `title`, `description`, `published_at_raw`, `language_hint`
- `raw_metadata` defaults to a new empty object for each record

Timestamp policy:

- `retrieved_at` must include timezone information and is normalized to UTC
- `published_at_raw` remains an unparsed source value at this boundary

---

# 4. CanonicalArticle

Canonical article record for downstream processing.

```json
{
  "article_id": "deterministic-or-stable-id",
  "source_id": "us_federal_register",
  "source_item_id": "optional-upstream-id",
  "url": "https://example.org/item/123?utm_source=feed",
  "canonical_url": "https://example.org/item/123",
  "title": "Normalized title",
  "description": "Normalized source description/snippet",
  "language": "en",
  "market": "US",
  "published_at": "2026-08-14T14:00:00Z",
  "discovered_at": "2026-08-14T14:05:00Z",
  "content_hash": "sha256-or-other-approved-hash"
}
```

Required semantics:

- `published_at`: time attributed to source publication
- `discovered_at`: first time our system discovered the article
- `canonical_url`: normalized URL used for deduplication
- `article_id`: stable across repeated ingestion when possible
- `content_hash`: deterministic input for deduplication

Field presence and defaults:

- required: `article_id`, `source_id`, `url`, `canonical_url`, `title`, `language`, `market`, `discovered_at`, `content_hash`
- nullable with a default of `null`: `source_item_id`, `description`, `published_at`

Timestamp policy:

- parsed timestamps must include timezone information and are normalized to UTC
- when source publication time is unavailable, `published_at` remains `null`; it must not fall back to `discovered_at`
- the model does not enforce `published_at <= discovered_at`; timestamp anomaly handling belongs to a later pipeline or data-quality stage

Validation at this boundary does not perform URL normalization or generate `article_id` or `content_hash`. Those transformations belong to the normalization stage.

Do not overwrite `published_at` with retrieval time.

## 4.1 DE-005 normalization policy

The normalization boundary accepts one `RawArticle` and its validated static
`SourceConfig`. The two `source_id` values must match.

Field derivation:

- `url` preserves the original raw URL
- `canonical_url` uses the deterministic policy below
- `market` comes from source configuration
- `language` uses a non-empty normalized `language_hint`, otherwise source configuration
- `source_item_id` is normalized to NFC and trimmed; an empty result becomes `null`
- `discovered_at` is the raw `retrieved_at`
- missing source publication time never falls back to `retrieved_at`

Canonical URL policy:

- require an absolute HTTP(S) URL with a hostname and without userinfo
- lowercase scheme and hostname
- remove default HTTP/HTTPS ports
- remove the fragment
- normalize an empty path to `/`
- preserve a non-empty path and its trailing slash
- remove query names beginning with `utm_`, plus `gclid`, `dclid`, `fbclid`, `msclkid`, `mc_cid`, and `mc_eid`, matched case-insensitively
- preserve the raw representation, ordering, duplicates, blank values, and percent encoding of every retained query segment
- do not sort retained query parameters or remove generic names such as `id`, `source`, or `ref`

Text policy:

- decode HTML character references exactly once while extracting visible text
- discard markup and `script`/`style` content
- normalize Unicode to NFC
- collapse Unicode whitespace and trim
- reject a missing/empty normalized title
- represent an empty normalized description as `null`

Publication timestamp policy:

- parse supported timezone-aware ISO-8601 and RFC 822/2822 values
- normalize a parsed timestamp to UTC
- map a missing, invalid, or timezone-naive value to `null`
- surface invalid/naive timestamp input as a normalization warning
- do not enforce publication/discovery ordering at this boundary

`article_id` is the lowercase SHA-256 hexadecimal digest of compact UTF-8 JSON.
With `source_item_id`, the JSON array is
`["v1","source_item_id",source_id,normalized_source_item_id]`.
Without `source_item_id`, it is
`["v1","canonical_url",source_id,canonical_url]`.

The URL fallback includes `source_id` because `article_id` represents source-local
record identity. Cross-source duplicate detection belongs to the deduplication stage.

`content_hash` is the lowercase SHA-256 hexadecimal digest of compact UTF-8 JSON:
`["v1",normalized_title,normalized_description]`.

It intentionally excludes source, URL, language, and timestamps so exact normalized
content can be compared across sources in the later deduplication stage.

## 4.2 DE-007 persistence MVP

The `articles` table stores one first-seen snapshot of the fields in `CanonicalArticle`.
It does not add fields to the shared wire model.

Persistence semantics:

- `article_id` is the primary key and the only conflict target
- the first successful insert wins; a later write with the same `article_id` is ignored
- therefore `discovered_at` remains the first discovery timestamp from the persisted
  snapshot and is never overwritten by a retry
- all other `CanonicalArticle` fields are also immutable in this MVP because update
  semantics have not been defined
- `canonical_url`, `(source_id, source_item_id)`, and `content_hash` are not unique
  storage constraints; they remain deduplication signals
- persistence does not choose a canonical winner or store cross-source duplicate
  relationships

This policy makes retries of the same source-local record idempotent without changing
DE-006 duplicate semantics. A later task must explicitly define provenance and update
semantics before this insert-only snapshot policy is expanded.

---

# 5. ClassifiedArticle

`ClassifiedArticle` is the minimal shared DE/SWE output of DE-008. Provider lineage,
usage, cost, retry details, and prompts are deliberately excluded from this contract.

```json
{
  "article_id": "article-id",
  "classifier_version": "classification-v1",
  "is_relevant": true,
  "markets": ["US", "EU"],
  "category": "LAW_POLICY",
  "topics": ["AI", "REGULATION"],
  "confidence": 0.93,
  "classified_at": "2026-08-14T14:06:00Z"
}
```

Contract rules:

- `classifier_version` versions classification behavior/semantics: prompt semantics,
  taxonomy semantics, and output-contract behavior. The current value is
  `classification-v1`; do not call this field `classification_version`.
- `CanonicalArticle.market` is the source/provenance market. `ClassifiedArticle.markets`
  contains markets mentioned by the article content.
- allowed markets are `VN`, `US`, `EU`, `CN`, unique and ordered exactly in that order;
  at most four values are allowed.
- `category` is one primary category from `LAW_POLICY`, `ENERGY`, `TECHNOLOGY`,
  `REAL_ESTATE`, `FINANCE`.
- `topics` are controlled codes from `AI`, `BANKING`, `INTEREST_RATES`, `OIL_GAS`,
  `REAL_ESTATE`, `REGULATION`, `RENEWABLE_ENERGY`, `SEMICONDUCTORS`; values are unique,
  lexicographically ordered, and limited to five.
- relevant results require at least one market and a non-null category.
- irrelevant results require exactly `markets=[]`, `category=null`, and `topics=[]`.
- `confidence` is finite and in `[0,1]`. It is the model's self-assessment of the
  classification decision, including an irrelevance decision; it is not a calibrated
  probability or user-specific relevance.
- `classified_at` is generated by application code as a timezone-aware UTC timestamp.
- all unknown fields are rejected.

### 5.1 DE-internal classification contracts

`ClassificationInput` is the only semantic payload serialized into the provider prompt:

- title
- nullable description
- source market
- source language
- source domains
- source type
- authority level

It excludes `article_id`, `source_id`, URLs, raw metadata, full HTML/body, credentials,
and other lineage. Application code retains `article_id` outside the prompt to correlate
the result. Article fields are untrusted data, not instructions.

`ProviderClassificationOutput` permits only `is_relevant`, `markets`, `category`,
`topics`, and `confidence`. JSON is parsed and then validated by strict Pydantic models;
unknown fields, invalid types, duplicates, and unsupported taxonomy values fail.

`ClassificationResult` combines `ClassifiedArticle` with DE-internal metadata:

- requested/provider model identifiers when observed
- `classification-prompt-v1` and `classification-taxonomy-v1`
- observed aggregate token usage and estimated USD cost
- pricing version/window, request/response id, system fingerprint
- invocation duration and actual provider-call count

Provider output never controls application lineage, versions, timestamps, usage, or cost.

### 5.2 Rights, retries, and cost boundary

No provider prompt or HTTP request is created unless the article/source IDs match and
`rights_review_status == APPROVED` plus `can_ai_process is true`. The legacy value
`REVIEWED` is not AI-processing permission.

DE-008 has no durable state. It performs at most three provider calls per `classify()`
invocation. It retries transient transport failures, 429, generic transient
500/502/503/504 statuses, empty/malformed/semantically invalid output, and
`insufficient_system_resource`. `content_filter`, `length`, unexpected `tool_calls`,
other approved client errors, rights denial, and local configuration/input errors fail
without retry. No tool is ever executed by this classification request.

Usage and estimated cost aggregate every response in the invocation whose usage metadata
is valid. Token counts must be non-negative integers and satisfy the provider total/cache
invariants. Attempts without observable usage contribute no invented tokens. Therefore,
`estimated_cost_usd` is derived from observed provider usage and is not an authoritative
billing ledger when an attempt's usage cannot be observed. Pricing is versioned,
effective-dated, stored in configuration, and calculated with `Decimal`; no runtime
pricing fetch occurs.

### 5.3 Future DE-009 persistence boundary

DE-008 does not persist classifications and does not provide cross-run idempotency. DE-009
will own `public.article_classifications` with logical identity and equivalent database
primary-key semantics:

```text
(article_id, classifier_version)
```

DE-009 will own durable `PROCESSING`, `RETRYABLE`, `SUCCEEDED`, `QUARANTINED` lifecycle,
attempt count, claim/lease, cross-run retry budget, successful-result short circuit,
expired-work reclaim, scheduling, and quarantine. No taxonomy/history/telemetry tables
are implied until a concrete use case requires them.

Classification should not require storing full article body by default.

---

# 6. UserPreference — shared contract

Owned primarily by product/SWE, consumed by DE matching.

Recommended minimal shape:

```json
{
  "user_id": "user-id",
  "markets": ["VN", "US"],
  "categories": ["TECHNOLOGY", "FINANCE"],
  "topics": ["AI", "Banking"],
  "muted_source_ids": [],
  "muted_topics": [],
  "breaking_alert_enabled": true,
  "hourly_update_enabled": true,
  "daily_digest_enabled": true
}
```

Do not expand this contract without coordinating with SWE.

---

# 7. AlertCandidate — shared boundary

Produced by DE matching and consumed by the notification/product side.

Recommended minimal shape:

```json
{
  "candidate_id": "stable-id",
  "user_id": "user-id",
  "article_id": "article-id",
  "matched_at": "2026-08-14T14:07:00Z",
  "match_reasons": [
    "market:US",
    "category:TECHNOLOGY",
    "topic:AI"
  ],
  "importance": "NORMAL",
  "relevance_score": 0.82,
  "breaking_eligible": false
}
```

Important:

- matching should be explainable
- `match_reasons` should allow debugging
- a score must not hide deterministic reasons
- producing `AlertCandidate` must be idempotent

---

# 8. AlertBatch — shared boundary

Represents articles grouped for one user notification.

```json
{
  "batch_id": "batch-id",
  "user_id": "user-id",
  "notification_type": "HOURLY",
  "article_ids": ["a1", "a2"],
  "created_at": "2026-08-14T15:00:00Z",
  "email_queued_at": null,
  "email_sent_at": null
}
```

Normal hourly behavior:

- at most one normal batch/user/hour
- duplicates suppressed
- low-value content may be deferred to digest

---

# 9. TelemetryEvent

Recommended lightweight telemetry shape:

```json
{
  "event_name": "article_classified",
  "article_id": "article-id",
  "source_id": "source-id",
  "market": "US",
  "category": "TECHNOLOGY",
  "occurred_at": "2026-08-14T14:06:00Z",
  "pipeline_stage": "classification",
  "status": "SUCCESS",
  "duration_ms": 318,
  "estimated_ai_cost_usd": 0.00001
}
```

Do not put secrets or full article content into telemetry.

---

# 10. Contract change policy

Any change to:

- `CanonicalArticle`
- `ClassifiedArticle`
- `UserPreference`
- `AlertCandidate`
- `AlertBatch`

must include:

1. reason
2. affected services
3. migration impact
4. backward compatibility consideration
5. tests
6. documentation update
