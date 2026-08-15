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
  "classifier_version": "classification-v2",
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
  taxonomy semantics, deterministic-rule semantics, and output-contract behavior.
  `classification-v1` is historical DeepSeek-first behavior and `classification-v2`
  is deterministic-first hybrid behavior; do not call this field
  `classification_version`.
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
- `confidence` is finite and in `[0,1]`. It is classifier-reported confidence,
  method-specific and not a calibrated probability. It is not user-specific relevance.
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

- `classification_method`: `DETERMINISTIC` or `DEEPSEEK`
- requested/provider model identifiers when observed
- `classification-prompt-v1` and `classification-taxonomy-v1`
- observed aggregate token usage and estimated USD cost
- pricing version/window, request/response id, system fingerprint
- invocation duration and actual provider-call count

Provider output never controls application lineage, versions, timestamps, usage, or cost.

### 5.2 Rights, retries, and cost boundary

Local deterministic classification may run when article metadata is legitimately stored;
it does not require `can_ai_process=true`. No provider prompt or HTTP request is created
unless the deterministic result is `AMBIGUOUS`, the article/source IDs match, and
`rights_review_status == APPROVED` plus `can_ai_process is true`. The legacy value
`REVIEWED` is not AI-processing permission.

An ambiguous result without AI permission makes zero provider calls and is terminal
`AI_FALLBACK_NOT_ALLOWED` / `QUARANTINED`.

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

### 5.3 DE-009 durable classification persistence

DE-009 implements the schema and repository contract for durable classification state.
It remains separate from the RSS/onboarding runner. DE-009B now owns enqueueing and
invoking DE-008 through the unchanged repository/classifier contracts.

The exact logical and database identity is:

```text
(article_id, classifier_version)
```

`classifier_version` identifies classification behavior. For an existing identity,
`requested_model`, `prompt_version`, and `taxonomy_version` are immutable enqueue
lineage. Enqueue verifies all three values. Any difference produces
`LINEAGE_MISMATCH`, returns the mismatched field names, does not overwrite lineage, and
is not treated as a normal idempotent enqueue.

The exact columns in `public.article_classifications` are:

```text
article_id text not null
classifier_version text not null
status text not null
classification_method text null

is_relevant boolean null
markets text[] null
category text null
topics text[] null
confidence double precision null
classified_at timestamptz null

requested_model text not null
provider_model text null
prompt_version text not null
taxonomy_version text not null
provider_request_id text null
system_fingerprint text null

prompt_tokens bigint not null
prompt_cache_hit_tokens bigint not null
prompt_cache_miss_tokens bigint not null
completion_tokens bigint not null
total_tokens bigint not null
estimated_cost_usd numeric(24,12) not null
last_pricing_id text null
last_pricing_window text null

attempt_count smallint not null
max_attempts smallint not null
last_provider_attempts smallint null

claim_token uuid null
claimed_at timestamptz null
lease_expires_at timestamptz null
next_attempt_at timestamptz null

last_error_category text null
last_http_status integer null
last_error_retryable boolean null
last_error_at timestamptz null
quarantined_at timestamptz null
created_at timestamptz not null
updated_at timestamptz not null

primary key (article_id, classifier_version)
foreign key (article_id) references public.articles(article_id) on delete restrict
```

`classification_method` is DE-internal and is not added to shared
`ClassifiedArticle`. A `SUCCEEDED` row requires `DETERMINISTIC` or `DEEPSEEK`;
every non-success row requires null. Deterministic success requires null provider
lineage/pricing metadata, zero provider attempts, zero token usage, and zero cost.
DeepSeek success requires one through three provider attempts and preserves the existing
usage/cost behavior.

`requested_model` remains immutable enqueue lineage. For `classification-v2` it names
the configured DeepSeek fallback model and does not prove that a provider call occurred.

Database constraints repeat the DE-008 taxonomy and cross-field invariants, require
canonical market/topic order with no duplicates, validate finite confidence and
non-negative consistent token totals, and enforce the lifecycle invariants below.
`max_attempts` is between 1 and 12 and defaults to 3.

| Current state | Operation | Next state | Required result |
| --- | --- | --- | --- |
| no row | enqueue | `RETRYABLE` | `attempt_count=0`, due immediately |
| any existing state | enqueue, matching lineage | unchanged | idempotent state-specific outcome |
| any existing state | enqueue, different lineage | unchanged | `LINEAGE_MISMATCH` |
| due `RETRYABLE` | atomic claim | `PROCESSING` | increment `attempt_count`, issue new token and lease |
| expired `PROCESSING`, budget remains | atomic reclaim | `PROCESSING` | increment `attempt_count`, replace token and lease |
| expired `PROCESSING`, budget exhausted | recovery | `QUARANTINED` | clear claim; mark lease-expiry exhaustion |
| live `PROCESSING`, matching token | renew | `PROCESSING` | extend lease and update `updated_at` |
| live `PROCESSING`, matching token | successful completion | `SUCCEEDED` | persist semantic result and cumulative observed usage |
| live `PROCESSING`, matching token | retryable failure, budget remains | `RETRYABLE` | clear claim; schedule 15 or 60 minutes |
| live `PROCESSING`, matching token | non-retryable/manual terminal failure | `QUARANTINED` | clear claim and retry schedule |
| live `PROCESSING`, matching token | any failure at exhausted budget | `QUARANTINED` | never leave an exhausted row `RETRYABLE` |
| stale/expired token | renew, success, or failure | unchanged | `LOST_CLAIM` |
| `SUCCEEDED` | completion replay | unchanged | `ALREADY_SUCCEEDED` |
| `SUCCEEDED` or `QUARANTINED` | update/delete | rejected | terminal rows are immutable |

`attempt_count` counts durable classifier invocations successfully
claimed/reclaimed. It increments atomically once per claim and is independent of
`provider_attempts`, which is zero for deterministic success and counts observed HTTP
calls inside a DeepSeek invocation (one through three) for DeepSeek success. DE-009
guarantees a durable invocation budget, not an exact
lifetime provider-call count. There is no provider-call slot reservation.

Every lifecycle mutation updates `updated_at`. Observed usage and estimated cost from
each persisted invocation are added cumulatively; attempts with unobservable usage do
not invent tokens. `last_provider_attempts` records only the most recently persisted
invocation, including zero for deterministic success. The cost remains an estimate from
observed provider usage, not an authoritative billing ledger.

Claims use a unique random `claim_token` as a fencing token plus `claimed_at` and
`lease_expires_at`. Mutation RPCs require the current token and an unexpired lease.
An expired lease can be reclaimed with a new token, after which the stale worker cannot
renew, fail, or overwrite the new worker result.

`claim_next_article_classification` locks candidates with
`FOR UPDATE SKIP LOCKED LIMIT 1`. One RPC examines and mutates at most one candidate.
If that candidate is an expired exhausted invocation, the call quarantines only that row
and returns `RECOVERED_QUARANTINED`; it does not sweep all exhausted work.

The backend-neutral repository API is:

```text
enqueue(key, lineage, max_attempts=3) -> EnqueueResult
get(key) -> ClassificationRecord | None
get_succeeded(key) -> ClassifiedArticle | None
claim_next(classifier_version, claim_token, lease_seconds=300)
    -> ClassificationClaim | None
renew_lease(claim, lease_seconds=300) -> LeaseRenewalResult
complete_success(claim, ClassificationResult) -> CompletionResult
record_failure(claim, ClassificationFailure, disposition) -> FailureResult
```

All lifecycle writes use transactional PostgreSQL RPCs. RLS is enabled with no
`anon`/`authenticated` policies. `service_role` receives table `SELECT` and execute
access to lifecycle RPCs, but no direct insert/update/delete grants. Terminal mutation
guards and constraints remain database-side defense in depth.

The original DE-009 migrations have been applied to and verified on the linked production
Supabase project. The additive DE-009C `classification_method` migration is locally
verified on PostgreSQL 17 and has not been applied remotely. No taxonomy, history, or
telemetry table is created.

Classification should not require storing full article body by default.

### 5.4 DE-009B internal orchestration contracts

`ClassificationWorkReader` is a DE-internal read boundary. It provides bounded discovery
of articles missing one `classifier_version`, article reload by ID, and a read-only
lineage audit. It does not change `CanonicalArticle`, `ClassifiedArticle`, or the DE-009
repository/lifecycle contract.

For `classification-v1`, the runner derives eligible source IDs only from static configs
satisfying `rights_review_status == APPROVED` and `can_ai_process is true`. For
`classification-v2`, discovery requires legitimate metadata storage rights and does not
require AI rights. Every claimed article is reloaded; the hybrid classifier applies the
AI rights gate only before an ambiguous DeepSeek fallback.

If rights were revoked after enqueue, v1 records `RIGHTS_DENIED` as `QUARANTINED`.
For v2, a confident deterministic result can still succeed; an ambiguous result records
`AI_FALLBACK_NOT_ALLOWED` as `QUARANTINED`. That
identity is terminal under the current lifecycle: DE-009B does not reset quarantine and
must not bump `classifier_version` merely because rights are later re-approved. Recovery
would require a separately designed and approved administrative lifecycle change.

`STOP_BATCH` is an in-process orchestration decision, not a database status. Systemic
configuration/provider failures reschedule the currently claimed row when the durable
budget permits, then stop further claims. Per-item terminal failures quarantine only that
identity. A lease heartbeat and `claim_token` fencing prevent stale success/failure
writes; provider work may still be repeated after a crash or lost claim.

---

# 6. UserPreference — shared contract

Owned primarily by product/SWE, consumed by DE matching. DE validates and reads this
contract but does not own the UI, preference-writing flow, or product-side persistence.

Recommended minimal shape:

```json
{
  "user_id": "user-id",
  "markets": ["VN", "US"],
  "categories": ["TECHNOLOGY", "FINANCE"],
  "topics": ["AI", "BANKING"],
  "muted_source_ids": [],
  "muted_topics": [],
  "breaking_alert_enabled": true,
  "hourly_update_enabled": true,
  "daily_digest_enabled": true
}
```

Contract rules:

- all nine fields are required; unknown fields are rejected;
- `user_id` and every `muted_source_ids` value must be non-blank strings;
- `markets` uses the same `VN`, `US`, `EU`, `CN` codes as classification;
- `categories` uses the same `LAW_POLICY`, `ENERGY`, `TECHNOLOGY`, `REAL_ESTATE`,
  `FINANCE` codes as classification;
- `topics` and `muted_topics` use the same controlled topic codes as `ClassifiedArticle`;
- collection values must be unique; input order is preserved and carries no DE matching
  precedence in DE-010;
- empty interest/mute collections are valid; the meaning of an empty preference is handled
  by later matching/product behavior rather than rejected by the shared contract;
- empty positive collections mean "not subscribed to that dimension", never "match all";
- mute rules override positive matches;
- notification flags are strict booleans;
- hourly/daily delivery flags are consumed downstream and do not change DE-011 semantic matching.

DE-010 defines a backend-neutral `UserPreferenceReader` with `get(user_id)` and bounded
`list_page(after_user_id, limit)` operations. The limit semantics are strictly defined:
a default of 100, and a valid range of 1 to 1000 inclusive. Boolean limits are invalid.
Concrete adapters must enforce this range. Pages are ordered by `user_id` ascending and
use the last `user_id` as a keyset cursor when another page is available. DE-010 does not
create a preference table, migration, write API, or Supabase adapter because no
authoritative product/SWE preference persistence contract exists yet.

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
- candidate identity is stable for `(user_id, article_id)`
- `match_reasons` are deterministic and ordered market -> category -> topic
- market reasons use `ClassifiedArticle.markets`, not provenance `CanonicalArticle.market`
- `importance` values currently: `NORMAL`, `HIGH`
- `relevance_score` is a deterministic ranking signal in [0,1], not a probability
- `breaking_eligible` is eligibility only, not proof an immediate notification will be sent
- persistence/idempotent storage remains DE-012

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
