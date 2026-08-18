# Testing Strategy — Data Engineer Scope

## 1. Testing goals

Tests should protect the properties most likely to damage this product:

- missed information
- duplicate information
- wrong timestamps
- repeated side effects
- bad source parsing
- broken classification output
- incorrect matching
- silent pipeline failure

## 2. Test levels

### Unit tests

Use for:

- URL normalization
- timestamp parsing
- content hashing
- source-specific mapping
- classification output validation
- matching rules
- dedup helpers

### Connector tests

Use fixtures/mocked HTTP responses.

Do not require live public internet for normal CI.

### Integration tests

Use for:

- connector → normalization
- normalization → persistence
- persistence → classification
- classification → matching
- idempotent repeated runs

### End-to-end smoke tests

Use a small controlled source/fixture path to validate the critical pipeline.

Do not make production third-party services mandatory for every local test run.

## 3. Required connector cases

Every connector family should test representative cases:

- HTTP 200
- timeout
- connection failure
- 429 rate limit
- 500/502/503 transient error
- malformed response
- empty response/feed
- missing title
- missing description
- missing publication time
- unexpected encoding when relevant
- duplicate source item
- repeated run

RSS/Atom additionally:

- malformed XML
- feed with different date formats
- entries without GUID/source item ID

REST additionally:

- pagination
- empty page
- changed/missing optional field
- rate-limit response

HTML additionally:

- target element missing
- layout change fixture
- relative URL resolution
- duplicate links

## 4. Normalization cases

Test:

- tracking parameter removal
- canonical URL stability
- Unicode title normalization where used
- whitespace normalization
- timezone conversion
- missing timezone policy
- description cleanup
- deterministic content hash
- repeated normalization gives same result

## 5. Deduplication cases

Test each layer separately:

```text
same canonical URL → duplicate
same source item ID → duplicate
same deterministic hash → duplicate
similar normalized title → policy-dependent duplicate
different real article → not duplicate
```

Also test order of operations so expensive similarity checks are avoided when a cheap exact check succeeds.

## 6. Persistence/idempotency cases

Required:

- first insert succeeds
- repeated identical ingestion does not create a second logical article
- `discovered_at` preserves first discovery
- changed allowed metadata can update safely
- transaction rollback does not leave partial critical state
- duplicate classification side effect is prevented where applicable

DE-012 Alert Candidate persistence cases:

- first save => CREATED;
- repeated save => ALREADY_EXISTS;
- first-write-wins fields are preserved;
- concurrent same-pair saves create one row;
- same pair / different candidate ID is rejected;
- same candidate ID / different pair is rejected;
- article FK enforced;
- candidate schema constraints and RLS are verified;
- service_role SELECT=true and INSERT/UPDATE/DELETE=false;
- service_role RPC EXECUTE=true;
- anon/authenticated table/RPC access=false;
- malformed Supabase payload and transport failures remain sanitized;
- no remote Supabase or notification side effect in normal tests.

## 7. Classification cases

Mock the LLM API.

Test:

- valid structured output
- strict field/type validation and rejection of unknown fields
- valid unsorted markets/topics normalize without another provider call
- duplicate and unsupported controlled codes fail validation
- relevant/irrelevant cross-field invariants
- confidence finite/range boundaries and rejection of numeric strings/booleans
- rights denial and source mismatch produce zero prompt/HTTP calls
- provider prompt excludes IDs, URLs, raw/full content, and credentials
- invalid JSON
- missing required field
- unknown category
- confidence outside expected range
- connect/read/write timeout and connection failure
- 429 plus bounded `Retry-After`
- generic transient 500/502/503/504
- `insufficient_system_resource` retries
- `content_filter`, `length`, and unexpected `tool_calls` do not retry
- retry exhaustion
- provider attempt count includes every actual call
- observed usage and cost aggregate across failed and successful attempts
- invalid/negative/inconsistent provider usage is rejected
- timeout/lost-response attempts do not invent usage
- errors/logs do not leak keys, prompts, article content, or raw responses
- effective-date and UTC peak/off-peak pricing boundaries

DE-008 tests only bounded retries within one invocation and use mocked HTTP; normal CI
must not call the live provider.

DE-009 unit tests validate strict persistence models, repository-to-RPC mapping,
sanitized persistence errors, enqueue lineage mismatch, and the independence of durable
`attempt_count` from DE-008 `provider_attempts`.

DE-009 integration tests use an isolated local PostgreSQL cluster when PostgreSQL
`initdb`, `pg_ctl`, and `psql` are available. They apply every committed migration and
verify:

- exact composite primary key, constraints, indexes, RLS, grants, and RPC privileges;
- idempotent enqueue and `LINEAGE_MISMATCH` without overwriting lineage;
- concurrent `FOR UPDATE SKIP LOCKED` claims issue at most one claim per row;
- claim/reclaim increments once per durable invocation;
- lease renewal and `updated_at` mutation;
- stale token fencing after expiry/reclaim;
- immutable/idempotent `SUCCEEDED` replay;
- retry scheduling, cumulative observed usage/cost, and quarantine at budget exhaustion;
- non-retryable failures cannot remain `RETRYABLE`;
- invalid taxonomy arrays and inconsistent provider usage fail database validation;
- expired exhausted recovery mutates only one locked candidate per claim call.

The PostgreSQL fixture is offline, creates no provider calls, uses no remote Supabase,
and skips only when local PostgreSQL binaries are unavailable.

DE-009B tests use fake classifiers and fake persistence/read boundaries. They verify:

- a version-scoped PostgREST anti-join request and bounded deterministic ordering;
- v1 makes zero PostgREST discovery/enqueue calls when no source has approved AI rights;
- lineage preflight and idempotent enqueue outcomes;
- claim, article/source reload, rights recheck, success/failure persistence;
- terminal rights-revocation quarantine without provider access or automatic reset;
- durable retry/quarantine mapping and systemic `STOP_BATCH` behavior;
- immediate renewal, periodic heartbeat, cancellation and stale-claim rejection;
- sequential process/time limits and observed usage/cost propagation;
- no article content, provider payload, or credentials in errors/logs.

Normal DE-009B tests never construct the environment-based DeepSeek adapter and require
neither a DeepSeek account nor API key. A live smoke of one to three explicit articles is
a separate manually approved operational task.

DE-009C deterministic tests are offline and cover NFC/case/whitespace normalization,
token-boundary matching, strong-title and source-domain evidence, conflict/insufficient
fallback, provenance/content market separation, institution aliases, controlled
multi-market/topic output, no deterministic irrelevant decision, and reproducibility.

Hybrid/runner tests verify:

- confident deterministic success makes zero DeepSeek calls and has zero usage/cost;
- ambiguous approved work invokes the existing DE-008 boundary exactly once;
- ambiguous denied work makes zero provider calls and is terminal
  `AI_FALLBACK_NOT_ALLOWED`;
- `classification_method` persistence and separate v1/v2 identities;
- v2 discovery requires metadata-storage rights rather than AI rights;
- fallback still enforces AI rights and systemic provider failures keep `STOP_BATCH`;
- existing claim, lease, heartbeat, replay, and fencing tests continue to pass.

Migration tests apply all migrations to an isolated PostgreSQL cluster and verify method
constraints, deterministic zero-attempt success, rejection of zero-attempt DeepSeek
success, and null method on non-success states. PostgreSQL 17 verification remains a
required release gate for DE-009C.

DE-010 tests are offline and verify the shared `UserPreference` contract and read-page
invariants:

- all required fields and strict unknown-field rejection;
- shared market/category/topic taxonomy compatibility with classification;
- strict booleans, non-blank IDs, and duplicate collection rejection;
- empty interest/mute collections remain valid;
- input collection order is preserved;
- page-local unique `user_id` values and deterministic ascending order;
- empty-page/cursor consistency and cursor equality with the last returned `user_id`;
- sanitized read errors.

DE-010 does not fake a production persistence adapter merely to test pagination. Concrete
adapter request bounds, cursor continuity across pages, backend failure mapping, and
cross-page duplicate prevention must be tested when an authoritative preference
persistence adapter is introduced.

## 8. Matching cases

Test combinations:

- market match only
- category match
- topic match
- muted source
- muted topic
- stale article
- breaking disabled
- hourly disabled
- multiple matching reasons
- same article/user pair processed twice
- article/classification identity mismatch
- irrelevant classification
- market-only/category-only/topic-only
- empty dimension not wildcard
- all positive dimensions empty
- muted source/topic precedence
- exactly 24h and >24h freshness boundaries
- published_at fallback to discovered_at
- deterministic reason ordering
- score uses matched dimensions, not topic count
- NORMAL/HIGH threshold boundary
- hourly/daily disabled do not suppress candidate
- breaking disabled and 2h breaking freshness boundary
- deterministic candidate ID

Matching output should remain explainable.

### 8.1 Matching Runner v1 core cases

The runner is tested offline with in-memory fakes (`FakeClock`,
`FakeUserPreferenceReader`, `FakeMatchingWorkReader`, `FakeAlertCandidateRepository`).
No network access, no production database, and no provider call is involved.

Covered at minimum:

- matching item x matching preference -> `CREATED`; non-matching preference -> no save;
- replay of the same logical work -> `ALREADY_EXISTS` and exactly one durable
  `(user_id, article_id)` candidate, with the original snapshot never rewritten;
- exact classifier-version scoping: `classification-v1` alongside `classification-v2` with
  the runner targeting v2 processes only v2, and an out-of-lineage item stops the run;
- `run_cutoff` snapshot: `classified_at <= run_cutoff` is eligible, later work belongs to
  the next run, and `run_cutoff` is never reused as `matched_at`;
- conservative freshness superset: `discovered_at` older than the cutoff with a
  `published_at` inside the window stays selectable, guarding against a future
  `discovered_at`-only prefilter; an anomalous future `published_at` may be over-selected
  and is then rejected by `match_article()`, which stays the semantic authority;
- per-item `matched_at` from the injected clock, proven distinct per evaluation;
- full preference pagination and full work pagination, including tail pages;
- full Cartesian coverage: 2 paged work items x 3 paged preferences = 6 evaluations, which
  page-paired iteration could not produce;
- empty preference set completes with no saves;
- article/classification identity mismatch and dependency failures `STOP_RUN` rather than
  silently skipping, with sanitized structured error context;
- persistence failure mid-run keeps prior candidates durable, and a healthy replay yields
  `ALREADY_EXISTS` for prior work and completes the remainder.

The runner does not fake a production persistence adapter. Concrete adapter request
bounds, SQL-level freshness superset behavior, cursor continuity, and backend failure
mapping must be tested when production `UserPreferenceReader` and `MatchingWorkReader`
adapters are introduced.

## 8.2 Secondary discovery (GDELT) cases

All GDELT tests are offline and deterministic. The DOC 2.0 client is driven through
`httpx.MockTransport` with an injected clock, injected sleep, and injected jitter; no unit test
touches the real GDELT API and no live network access is required.

Query catalog and loader:

- valid spec loads; provider must be `GDELT_DOC_2_0`; blank/whitespace and control-character
  expressions rejected; over-length expression rejected;
- duplicate `query_id` and duplicate active `(market, domain)` cell rejected;
- unknown field, unsupported top-level key, and malformed TOML rejected;
- `catalog_path=None` yields an empty catalog (intentionally disabled), while an explicitly
  configured missing path or zero-query catalog is a hard configuration error;
- the shipped non-production `.example` template parses and stays labelled; no active production
  catalog is committed;
- constructing the runner with an empty catalog raises, so a disabled deployment cannot emit a
  result resembling a successful zero-cell run.

DOC client:

- exact HTTPS endpoint, `mode=artlist`, `format=json`, `sort=dateasc`, bounded `maxrecords`, and
  explicit second-precision `startdatetime`/`enddatetime`; configured timeout applied;
- retry taxonomy: `408/429/500/502/503/504` retried within budget and `Retry-After` honored and
  capped; `400`/`422` cell-scoped and never retried; other non-2xx classified conservatively as
  systemic with no pointless retries; timeouts and network errors retried then reported;
- payload handling is fail-closed: `{"articles": []}` is a successful zero-result window, while
  a bare `{}`, a non-object body, a missing or non-list `articles`, and unparseable JSON are all
  provider failures — a malformed payload is never reported as an empty success;
- a malformed individual record is skipped and counted while the rest of the response maps;
- a non-blank but non-canonicalizable URL is a valid candidate, not a malformed record;
- `published_at_raw` stays `None` and `seendate` appears only as bounded provider metadata;
- the clock is read exactly once per physical response and shared by every candidate from it.

Adaptive windowing (regression coverage):

- under-limit windows are complete and never split; exactly `maxrecords` triggers a split;
- split recursion is bounded by depth and by the minimum window; a still-saturated window is
  reported `SATURATED_INCOMPLETE` and never `COMPLETE`;
- child windows overlap so the midpoint cannot leak, and the duplicate produced by that overlap
  is emitted once;
- **saturated-parent preservation**: a URL returned only by the parent request survives the
  split, and a URL seen in both parent and child keeps the parent's earlier `observed_at`;
- **pre-validation saturation**: a payload with exactly `maxrecords` physical entries splits
  even when malformed entries reduce the valid count, and post-dedupe counts never drive
  splitting;
- **second-precision progress**: boundaries normalize to whole UTC seconds, and the split guard
  returns no boundaries rather than recursing when it cannot produce two strictly smaller
  formatted windows.

Admission and persistence integration:

- all five `ObservationStatus` outcomes exercised through synthetic fixture routes;
- query `market` never overrides source identity, `query_id` never participates in route
  resolution, and no native source item ID is synthesized;
- observations use the correct four-tuple aggregate key, one call per unique candidate, with
  locator-only samples;
- a candidate whose URL cannot be canonicalized still persists an `UNKNOWN` observation with
  `sample=None` rather than failing the run;
- persistence failure stops the run with earlier aggregates left durable;
- invalid configuration fails before any HTTP call; one rejected query fails only its cell; a
  systemic provider failure stops before the remaining cells are attempted.

An import-order regression test asserts the package graph stays acyclic, since
`persistence.discovery_observations` depends on `discovery.models` and the runner therefore
lives in `pipelines`.

Still requiring live verification: whether a bare `{}` is a legitimate zero-result DOC response,
whether `400`/`422` reliably signals query-specific rejection, the real ArticleList field
semantics, and end-to-end behaviour against the production query and route catalogs once those
are authored.

## 9. Freshness/telemetry cases

Ensure stage timestamps are not conflated.

At minimum verify:

```text
published_at <= discovered_at
discovered_at <= classified_at
classified_at <= matched_at
```

Do not force these inequalities when source data is known to be inconsistent without defining a policy; surface the anomaly instead.

## 10. Test data

Prefer small deterministic fixtures.

Recommended structure:

```text
tests/
├── fixtures/
│   ├── rss/
│   ├── rest/
│   └── html/
├── unit/
├── integration/
└── e2e/
```

Never put secrets or copyrighted full-text datasets into test fixtures without explicit approval.

## 11. Completion report

Codex should finish a task by reporting:

```text
Tests added:
Tests run:
Result:
Known gaps:
```
