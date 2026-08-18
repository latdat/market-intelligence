# Architecture Baseline — Current Phase

## 1. Product objective

The current phase is not a full intelligence platform.

The core value to prove is:

```text
New information appears
        ↓
Detect quickly
        ↓
Determine market/domain/topic
        ↓
Match user interests
        ↓
Notify the right user
        ↓
User learns about it within <= 60 minutes
```

Target:

- ~1,000 active users
- VN / US / EU / CN
- 5 initial domains
- broader portfolio target of 40–60 carefully selected sources, including the
  25-source Official Source Architecture v1 core
- polling typically every 15 minutes where practical
- normal operating cost ~$30–60/month
- hard ceiling < $100/month

## 2. Freshness SLO

Primary SLO:

> >=95% of relevant ingested content should be classified and made available for notification within <=60 minutes of source publication, when the source is normally accessible.

Stretch targets:

- P50 end-to-end latency < 20 minutes
- P95 end-to-end latency < 60 minutes

Required timestamps:

```text
published_at
discovered_at
classified_at
matched_at
email_queued_at
email_sent_at
```

## 3. Current implementation and approved platform flow

Current source-onboarding implementation covers 21 configured `SourceConfig` records:
6 RSS/Atom records, 3 REST API records, and 12 HTML records. `eu_eurlex_cellar` uses the
`REST_API` acquisition abstraction against a SPARQL endpoint; `SPARQL` is not a separate
`AcquisitionMethod` in the current code model.
The exact implemented list and its rights state are maintained in
[`SOURCE_REGISTRY.md`](SOURCE_REGISTRY.md#22-current-implementation--production-source-registry).
18 of those 21 records are members of the 25-source official-core target and the other 3 are
non-MUST/context/supplemental configs. The remaining 7 MUST sources, GNews, and Story/Event
are not implemented.

The diagram below is the approved platform flow, not evidence that every box is deployed
or operational. Section 7 records implementation boundaries for the downstream stages.

```text
┌───────────────────────────────────────────────────┐
│                   DATA SOURCES                    │
│ Official core: 25 MUST target; 18 current configs │
│ Non-MUST/context/supplemental: 3 current configs  │
│ Future GNews: discovery/enrichment only           │
└─────────────────────────┬─────────────────────────┘
                       ↓
                SOURCE REGISTRY
                       ↓
               INGESTION CONNECTORS
              RSS / REST / HTML
                       ↓
                CLOUD SCHEDULER
                 ~15 min polling
                       ↓
                 CLOUD RUN JOB
                       ↓
       Fetch → Parse → Normalize → Dedup
                       ↓
          DETERMINISTIC CLASSIFIER
             ↙ confident   ↘ ambiguous
       local success      AI rights gate
                              ↓ allowed
                     DeepSeek V4 Flash fallback
                       ↓
             CLASSIFICATION PERSISTENCE
                       ↓
              SUPABASE POSTGRES
                       ↓
            RULE-BASED MATCHING
               ↙              ↘
          Web Feed         Alert Candidate
                              ↓
                         Alert Buffer
                              ↓
                          Amazon SES
                              ↓
                            Email
```

Light telemetry:

```text
Pipeline → BigQuery
```

Frontend:

```text
Cloudflare Pages / Workers
```


## 4. Official Source Architecture v1

### 4.1 Status boundary

`CURRENT IMPLEMENTATION` and `TARGET OFFICIAL ARCHITECTURE v1` are different scopes:

- `CURRENT IMPLEMENTATION`: 21 `SourceConfig` records (6 RSS/Atom, 3 REST API, 12 HTML) and
  the RSS/Atom, Government API, Legal Corpus, and Official Listing connector paths. One of the
  REST API records, `eu_eurlex_cellar`, targets a SPARQL endpoint through the same `REST_API`
  acquisition abstraction;
- `TARGET OFFICIAL ARCHITECTURE v1`: 25 MUST official core sources across VN, US, EU,
  and CN, of which 18 are currently implemented and 7 remain `SOURCE-LEVEL BLOCKED` or
  `DEFERRED_PENDING_POST_PILOT` according to [`SOURCE_REGISTRY.md`](SOURCE_REGISTRY.md#22-current-implementation--production-source-registry);
- SO-002 adopts the target as documentation/design only. It does not create the missing
  `SourceConfig` records, implement connectors, fetch data, or prove production readiness.

The canonical 25-source matrix and current/target status are maintained only in
[`SOURCE_REGISTRY.md`](SOURCE_REGISTRY.md#23-target-official-architecture-v1--canonical-matrix).

### 4.2 Evidence and discovery roles

- Official sources provide authoritative/canonical evidence.
- Future GNews provides discovery, breadth, and reaction enrichment. It does not replace
  or become canonical evidence for an official legal or regulatory record.
- The broader 40–60 source portfolio remains a product-level direction; Official Source
  Architecture v1 fixes its 25-source MUST core rather than claiming all 40–60 sources
  are selected or onboarded.
- Paid aggregators remain OFF by default unless measured coverage benefit justifies them.

### 4.3 Canonical legal spines

- VN: VBPL.
- US: Federal Register as the event spine plus GovInfo as the canonical corpus.
- EU: EUR-Lex/CELLAR.
- CN: NPC Laws DB plus State Council formal policy documents.

### 4.4 Rights and content boundaries

Rights are scoped by source/channel, content class, and third-party exclusions—not by
hostname alone. A `SourceConfig` must be content-homogeneous: when one website publishes
both editorial news and formal regulatory documents, those channels require separate
configs. `SourceConfig.content_scope` is required and accepts only `EDITORIAL_NEWS` or
`FORMAL_REGULATORY_LEGAL`; it describes the reviewed content class and is not an AI
authorization input. `can_ai_process` is strict boolean-only. AI authorization continues
to require both `rights_review_status == APPROVED` and `can_ai_process is true`.

### 4.5 One pipeline for official and future GNews inputs

Future GNews must enter through the same acquisition and article contracts as official
sources:

```text
Official ─┐
          ├→ Acquisition → RawArticle → Normalize → Dedup → articles
GNews ────┘                                             ↓
                                                  Classification
                                                        ↓
                                                   Story/Event
```

GNews ingestion and Story/Event are `Planned / documented only`. There is no parallel
GNews persistence/classification path and SO-002 does not implement either component.

## 5. Ingestion

Approved acquisition abstractions and status:

| Abstraction | Status |
|---|---|
| RSS/Atom Connector | `Implemented` |
| Government API Connector | `Implemented (SO-004)` |
| Legal Corpus Connector | `Implemented (SO-005)` |
| Official Listing Connector | `Implemented (SO-006)` |

The future abstractions may reuse REST/API, HTML, or sitemap transport/parsing techniques;
they do not authorize a new infrastructure layer. Implementation guidance lives in
[`SOURCE_CONNECTOR_GUIDE.md`](SOURCE_CONNECTOR_GUIDE.md#31-approved-acquisition-abstractions).

Preferred Python stack:

- Python 3.12+
- `httpx`
- `feedparser`
- `BeautifulSoup` / `lxml`
- `pydantic`

Playwright is fallback only.

Default polling target:

```text
15 minutes
```

Use source-specific intervals such as 15m / 30m / 60m when rate limits require it.

## 6. Compute and scheduling

Approved:

- Cloud Scheduler
- Cloud Run Jobs

Cloud Run data work includes:

- fetch
- parse
- normalize
- dedup
- classification
- matching

Not used in the current phase:

- Airflow
- Cloud Composer
- Kafka
- Kubernetes
- dedicated always-on VM

## 7. Data persistence

Operational state: Supabase PostgreSQL.

Relevant data-side entities:

- sources
- articles
- article classification metadata
- alert candidates
- pipeline/source health state
- delivery history where shared with SWE

BigQuery is for lightweight telemetry, not serving the website.

### 7.1 DE-007 article persistence MVP

For the current pipeline-validation phase, Supabase PostgreSQL stores first-seen
`CanonicalArticle` snapshots. Pipeline code depends on the small `ArticleRepository`
boundary; only its adapter depends on the Supabase Python client. This keeps connector,
normalization, and deduplication code independent of the current backend.

The MVP has one `articles` table and does not add source state, classification, duplicate
relationships, matching, or notification persistence. It does not choose a canonical
winner for cross-source duplicates.

### 7.2 DE-008 classification boundary

DE-008 remains the existing standalone, rights-gated DeepSeek adapter:

```text
CanonicalArticle + SourceConfig
        -> RightsGate
        -> ClassificationInput
        -> DeepSeek V4 Flash
        -> strict semantic validation
        -> ClassifiedArticle + internal ClassificationResult metadata
```

It does not itself persist results. DE-009 implements the durable repository using
`(article_id, classifier_version)` identity; DE-009B/DE-009C orchestration invokes the
adapter only as the rights-approved fallback.

### 7.3 DE-009 classification persistence

`ClassificationRepository` separates durable lifecycle operations from both DE-008 and
the Supabase client. The Supabase adapter uses transactional PostgreSQL RPCs:

```text
enqueue
  -> RETRYABLE
  -> atomic claim/reclaim + new claim_token + lease
  -> PROCESSING
       -> SUCCEEDED
       -> RETRYABLE at +15/+60 minutes
       -> QUARANTINED
```

The composite primary key is `(article_id, classifier_version)`. Existing work can only
be re-enqueued when `requested_model`, `prompt_version`, and `taxonomy_version` match;
otherwise enqueue returns `LINEAGE_MISMATCH` without updating the row.

`attempt_count` counts successfully claimed durable classification invocations.
`provider_attempts` is zero for deterministic success and counts HTTP calls inside one
DeepSeek invocation. The two budgets are independent, and DE-009 does not reserve
provider-call slots or guarantee an exact lifetime HTTP-call count.

The claim RPC uses `FOR UPDATE SKIP LOCKED LIMIT 1`, increments `attempt_count` in the
same transaction, and assigns a unique `claim_token`. Completion, failure, and renewal
must match that token and a live lease. Reclaim replaces the token, fencing stale
workers. Successful and quarantined rows are immutable; replaying completion after
success returns the existing success without another write.

Expired exhausted recovery is deliberately bounded. One claim RPC locks and mutates at
most one candidate; it never performs a queue-wide sweep. All lifecycle mutations update
`updated_at`.

RLS is enabled. `service_role` may read the table and execute lifecycle RPCs, but receives
no direct mutation grant; `anon` and `authenticated` receive neither table access nor RPC
execution. Both DE-009 migrations have been applied to and catalog-verified on the linked
production Supabase project.

### 7.4 DE-009B classification orchestration

DE-009B connects the existing components without joining provider I/O to a database
transaction:

```text
eligible missing article -> enqueue -> claim -> reload article/source -> rights gate
                         -> DE-008 -> DE-009 success/failure RPC
```

Discovery uses a bounded, version-scoped PostgREST anti-join. Historical
`classification-v1` includes only source IDs with approved AI-processing rights.
`classification-v2` includes sources whose metadata may be stored, because local
deterministic code is not AI processing. AI rights are enforced only before fallback.

The shared classification runner processes sequentially with bounded enqueue/process/run
limits. It renews a
lease before provider access and maintains a heartbeat while DE-008 runs. A lost claim
cancels in-flight classification where possible and can never persist a stale result.
Systemic provider/configuration failures durably reschedule the current item when possible
and stop the batch so one dependency problem does not consume the whole queue.

### 7.5 DE-009C deterministic / hybrid classification

DE-009C reuses the DE-009B runner and the only DE-009 persistence path:

```text
articles
  → deterministic classifier
     ├─ CONFIDENT → fenced persistence
     └─ AMBIGUOUS → AI rights gate
                      ├─ allowed → existing DeepSeek classifier → fenced persistence
                      └─ denied → AI_FALLBACK_NOT_ALLOWED / QUARANTINED
  → matching
```

`classification-v1` is historical DeepSeek-first behavior. `classification-v2` is the
hybrid behavior and uses `deterministic-rules-v1`. Rule changes that can alter semantic
output require an explicit deterministic-rule version bump and, when classification
semantics change, a classifier version review.

The deterministic classifier is metadata-only, offline, and conservative. It uses NFC,
case folding, whitespace normalization, and token-boundary matching. A category is
confident only at score >=4 with a margin >=2; deterministic v1 never confidently marks
an article irrelevant. Every emitted market requires title/description evidence; the
source-market prior may support scoring but provenance alone never creates a content
market. Confident category evidence without a confident content market is `AMBIGUOUS`.
The 10–20% DeepSeek fallback rate is an unverified measurement target, not an achieved
result and not a reason to weaken thresholds.

Successful rows store DE-internal `classification_method` as `DETERMINISTIC` or
`DEEPSEEK`; this field is intentionally absent from shared `ClassifiedArticle`.
Deterministic success has zero provider calls, tokens, and cost. The additive DE-009C
migration `20260817000000_add_classification_method.sql` has been applied to and verified
on the linked remote Supabase project. The current remote migration drift is limited to
the two DE-012 alert-candidate migrations.

The arrow to matching shows the approved downstream architecture. DE-011 implemented the
pure matching component and the Matching Runner v1 core now orchestrates it offline
(section 7.9), but no production runner wires classification, Product/SWE-owned
preferences, matching, and candidate persistence end to end, because no production
`UserPreferenceReader` or `MatchingWorkReader` adapter exists.

### 7.6 DE-010 user preference read boundary

DE-010 introduces the backend-neutral read boundary consumed by DE-011:

```text
Product/SWE preference state
        -> UserPreferenceReader
        -> validated UserPreference
        -> DE-011 rule-based matching
```

`UserPreference` is a shared Product/SWE -> DE contract. DE-010 validates the shared
taxonomies and exposes `get` plus bounded `user_id`-ordered keyset pages, but does not own
preference writes, UI behavior, or persistence. No user-preference table, Supabase adapter,
RLS policy, RPC, or migration is introduced by DE. Product/SWE owns the authoritative
preference persistence contract; DE consumes that contract and must not create a fake or
parallel preference schema for matching.

### 7.7 DE-012 alert-candidate persistence

- DE-011 remains pure matching and creates in-memory `AlertCandidate`.
- DE-012 persists immutable first-seen candidate snapshots in Supabase PostgreSQL.
- Logical identity is `(user_id, article_id)`.
- `candidate_id` is the stable public/shared identifier produced by DE-011.
- Database defense-in-depth uses:
  - primary key `(candidate_id)`;
  - unique `(user_id, article_id)`;
  - FK `article_id -> articles(article_id) ON DELETE RESTRICT`.
- `save_alert_candidate` is a transactional SECURITY DEFINER RPC.
- repeated identical logical work returns `ALREADY_EXISTS` and the original first-seen snapshot;
  it never rewrites `matched_at`, reasons, importance, score, or breaking eligibility.
- concurrent saves for the same logical pair create exactly one row.
- same pair with a different candidate ID and same candidate ID used for a different pair are
  treated as persistence errors rather than silently merged.
- RLS is enabled.
- `service_role` has table SELECT and RPC EXECUTE only; no direct INSERT/UPDATE/DELETE.
- `anon` and `authenticated` have no table access and no RPC execution.
- No user/user-preference FK is introduced because DE does not own authoritative preference persistence.
- Delivery, batching, cooldowns, and email side effects remain outside DE-012.
- The repository migrations exist and were verified on isolated local PostgreSQL, but
  `20260818000000_create_alert_candidates.sql` and
  `20260818000001_grant_alert_candidates_service_role.sql` have not been applied to the
  linked remote Supabase project.

### 7.9 Matching Runner v1 core

Matching Runner v1 core is implemented and offline-tested only. It is independently
designed orchestration over the approved DE-010, DE-011, and DE-012 contracts; it does
**not** reuse the DE-009B claim/lease/fencing lifecycle, and it introduces no matching
queue, lease, heartbeat, `PROCESSING`/`RETRYABLE` state, retry table, or quarantine table.
`match_article()` is a local deterministic computation with no provider call and no paid
external I/O, and DE-012 already enforces durable first-write-wins idempotency.

```text
run_cutoff = clock()                       # discovery snapshot only, never matched_at
UserPreferenceReader pages -> one validated in-memory preference snapshot
freshness_cutoff = run_cutoff - NORMAL_FRESHNESS
MatchingWorkReader pages (exact classifier version, keyset by article_id)
        for each work item:
                for each preference:
                        matched_at = clock()       # per-item read
                        DE-011 match_article(...)
                        -> None      : normal NO_MATCH
                        -> candidate : DE-012 save() -> CREATED | ALREADY_EXISTS
```

`MatchingWorkReader` is a new minimal backend-neutral DE-internal read boundary. One page
carries the full `CanonicalArticle` + `ClassifiedArticle` pair so the runner never issues
ad-hoc persistence queries, and all persistence-specific query logic stays behind the
boundary. Adapters must select exactly `SUCCEEDED` rows whose `classifier_version` equals
the requested value, whose `classified_at <= run_cutoff`, and which pass a conservative
freshness superset:

```text
article.discovered_at >= freshness_cutoff
OR
article.published_at >= freshness_cutoff
```

`CanonicalArticle` does not guarantee `published_at <= discovered_at`, so a
`discovered_at`-only prefilter would under-select eligible work. Over-selection is
acceptable; under-selection is not. DE-011 remains the final semantic authority on
stale/fresh, and DE-011 freshness rules must not be reimplemented in SQL. `classified_at`
is a discovery snapshot bound only and is never treated as semantic freshness.

Other enforced invariants:

- the target classifier version is explicit configuration; the runner never auto-selects a
  "latest" version and never matches one article across more than one lineage in a run;
- preferences are paginated once per run into a snapshot reused by every work item, so
  work-item and preference cursors cannot pair page-by-page and silently drop combinations;
- pagination runs to exhaustion of the `run_cutoff` work-set; page size bounds each query
  only, and non-advancing keyset cursors stop the run instead of looping;
- candidate identity stays unversioned `(user_id, article_id)`, so matching-rule or
  candidate-namespace upgrades do not automatically regenerate historical candidates;
  changing that requires a separate `AlertCandidate` contract/persistence migration;
- current-state preference semantics only: the shared `UserPreference` contract has no
  version, revision, or effective timestamp, so a preference snapshot read at matching
  time applies to every still-fresh article;
- dependency failures and contract/identity violations `STOP_RUN` with a sanitized
  structured error (stage, error category, `article_id`, `classifier_version`, `user_id`)
  rather than silently skipping an item;
- a run is not one transaction. Candidates saved before a stop are durable and replay as
  `ALREADY_EXISTS`; there is no rollback and no run-wide DB transaction;
- no AI rights gate is re-checked in matching, and no display/redistribution right is
  inferred. Candidate creation is not delivery: no email, batching, or content send.

V1 has no durable cursor or watermark. That is safe only while one scheduled invocation
can exhaust the whole eligible work window; see section 7.10 for the production gates.

### 7.10 Matching production gates

Matching Runner v1 core completion is not production readiness. Production matching stays
blocked on:

Product/SWE-owned blockers:

1. an authoritative Product/SWE preference persistence contract;
2. a concrete production `UserPreferenceReader` adapter;
3. Product acceptance of current-state/backfill preference semantics, or a separately
   approved shared-contract change supporting prospective semantics.

DE/infrastructure blockers:

4. a concrete production `MatchingWorkReader` adapter (none exists; only offline
   in-memory fakes do);
5. an explicit production `target_classifier_version`;
6. the two DE-012 migrations applied and verified on remote Supabase;
7. a no-cursor benchmark proving one run exhausts the eligible recent work-set within
   scheduling/SLO limits. If it fails, design a durable cursor/watermark before
   production; do not silently cap the work-set and accept tail starvation.

### 7.8 SWE Data Ready v1 boundary

SWE Data Ready v1 uses synthetic, linked shared-contract examples from `swe_handoff/` so
SWE can build and validate read-side integration without waiting for production matching
or notification orchestration. The missing production matching runner does not block this
contract-data handoff; it blocks only real `alert_candidates` population.

Synthetic `UserPreference` examples demonstrate the shared Product/SWE -> DE contract.
They neither prove nor create a production preference table. Real candidate population
must wait for Product/SWE-owned preference persistence, concrete production read adapters,
a production-declared matching runner (the v1 core in section 7.9 is offline-tested only),
and the two DE-012 remote migrations.

DE-013 pipeline telemetry remains `PAUSED` and is not part of SWE Data Ready v1.

## 8. AI usage

Model:

```text
DeepSeek V4 Flash (hybrid fallback)
```

Purpose:

- content-mentioned markets (distinct from source provenance market)
- category
- controlled topics
- confidence
- basic article relevance decision

The adapter sends metadata only, enforces explicit source AI-processing rights before
prompt construction, requests JSON output, strictly validates it, and bounds one
invocation to at most three provider calls. It captures observed usage/cost internally;
these provider-specific details are not part of the shared `ClassifiedArticle` contract.

Avoid:

- full article rewriting
- long summary for every article
- complex reasoning by default
- chatbot
- semantic search

### 7.11 Secondary discovery boundary (GDELT-002A + GDELT-002B)

GDELT is a **secondary discovery provider**, not a publisher and not a source of truth. There
is deliberately no `SourceConfig(source_id="gdelt")`, and none is ever created. Publisher
identity, market authority, and content scope come only from reviewed `PublisherRoute` entries
and the target `SourceConfig` they point at.

```text
reviewed GdeltQuerySpec catalog          (GDELT-002B)
        -> GDELT DOC 2.0 ArticleList client
        -> bounded adaptive time windows
        -> DiscoveryCandidate                (ephemeral sighting)
        -> PublisherRoute resolution         (GDELT-002A)
        -> admission gate                    (GDELT-002A)
        -> bounded daily discovery observations
        -> STOP
```

The flow stops at the aggregates. GDELT-002B does **not** populate `articles`, produce
`RawArticle`/`CanonicalArticle`, classify, match, create alert candidates, or send anything.

**Admission (GDELT-002A, unchanged).** Route resolution matches hostname + literal path prefix
only; zero matches are `UNKNOWN`, more than one is `AMBIGUOUS_ROUTE`, and there is no
first-match-wins. The gate is exactly `can_fetch and can_store_metadata`; `rights_review_status`
and `can_ai_process` govern the separate downstream AI gate and are not consulted here. A
`NATIVE_SOURCE_ITEM_ID_REQUIRED` route is unconditionally `IDENTITY_INCOMPATIBLE` for discovery,
because a provider sighting carries no publisher-native ID. No native ID is ever synthesized
from a URL.

**Query catalog (GDELT-002B).** `DiscoveryQuery` stays provider-neutral; `GdeltQuerySpec` pairs
one logical `(market, domain)` cell with one reviewed GDELT expression. `query_id` is part of
the durable `discovery_observation_daily` key, so a material semantic change requires a **new**
versioned `query_id` (`gdelt_v1_...` -> `gdelt_v2_...`) rather than an in-place edit. Nothing
derives provider syntax from `Market`: the cell `US x FINANCE` does not mean "publisher country
is US".

**Bounded retrieval.** Requests are explicit `mode=artlist`, `format=json`, `sort=dateasc`,
`maxrecords`, `startdatetime`/`enddatetime` calls against a fixed HTTPS endpoint. The runner
owns the window; no implicit "last N minutes from provider now" is used, and no offset/cursor
pagination is assumed to exist. Because ArticleList is bounded, a window returning exactly
`maxrecords` is treated as **potentially saturated** and split in time. No provider result limit
is ever silently reported as a complete window.

Four properties make the split safe:

- saturation is judged on the **physical** provider record count, before record validation and
  before dedupe, so a 250-entry payload with malformed entries still splits;
- child windows **overlap** by a bounded margin so a record on the midpoint cannot fall through
  the boundary, and the resulting duplicates are removed;
- boundaries are normalized to whole UTC seconds, and a split that cannot produce two strictly
  smaller windows at that precision returns `SATURATED_INCOMPLETE` instead of recursing;
- a saturated parent's records are **kept and merged** with its children rather than discarded,
  because they are real sightings already made. Refinement can never push an article's
  first-seen time later.

**Deduplication has two scopes.** Within one cell, repeated sightings collapse on canonical URL
(verbatim fallback when a URL cannot be canonicalized), keeping the earliest `observed_at`.
Across cells nothing is suppressed: the same URL found by `gdelt_v1_us_finance` and
`gdelt_v1_us_technology` is two legitimate observations, and `query_id` never participates in
route resolution.

**Timestamps.** `observed_at` is *our* observation time, read once per accepted physical
response from an injected clock — never per article, so parsing order cannot manufacture
latency. GDELT's `seendate` is crawl/index time, not publisher publication time, so it is kept
only as bounded provider metadata and `published_at_raw` stays `None`. Adding it would require
verified evidence that a DOC field carries publisher-attributed publication semantics.

**Execution status is a separate axis.** `GdeltCellRunStatus`
(`COMPLETE`/`SATURATED_INCOMPLETE`/`QUERY_REJECTED`/`PROVIDER_FAILED`) is DE-internal and is
never merged into `ObservationStatus`, which gains no new members. One rejected query fails its
own cell and lets the others run; a systemic provider failure stops the run rather than
hammering the remaining cells; a persistence failure stops the run with earlier aggregates left
durable. Provider concurrency is 1 and cells run sequentially.

**Storage is unchanged.** GDELT-002B adds no table and no migration. It reuses
`discovery_observation_daily` and writes locator-only samples; there is no per-sighting GDELT
table, no parallel article store, no work queue, no claim/lease lifecycle, and no durable
cursor. Benchmark evidence is not written here and the benchmark is not graded:
`record_benchmark_evidence`, `evaluate_benchmark_tier`, and `evaluate_direct_path_disposition`
are GDELT-003 concerns. Direct RSS/REST/HTML acquisition is completely unchanged, and no source
is retired or reduced to sentinel mode by this work.

**Status: implemented and offline-tested only.** No live DOC 2.0 request has been made from this
repository. Two provisional behaviours are recorded as open questions for a bounded live
preflight: whether a bare `{}` body is a legitimate zero-result response (currently treated as a
wrong-schema provider failure, fail-closed), and whether HTTP 400/422 reliably indicates a
query-specific rejection (currently treated as cell-scoped `QUERY_REJECTED`).

### 7.12 Secondary discovery production gates

Discovery is not production-active and must not be described as such. Remaining gates:

Authoring gates (configuration, not code):

1. a reviewed production GDELT query catalog. `config/discovery/gdelt_queries.toml.example` is a
   clearly labelled non-production template; no active `gdelt_queries.toml` is committed, so the
   loader treats GDELT as intentionally disabled;
2. a reviewed production `PublisherRoute` catalog. Only synthetic test routes exist, so every
   real publisher would resolve to `UNKNOWN`. Routes are never learned automatically from GDELT
   results, and no `SourceConfig` is created because GDELT reported a hostname.

Verification gates:

3. the two discovery migrations applied and verified on the linked remote Supabase project;
4. a bounded live DOC 2.0 preflight (`scripts/run_gdelt_discovery.py`, dry-run by default);
5. a multi-cell live discovery run;
6. the GDELT-003 14-day benchmark campaign.

GDELT-002B core implementation does not satisfy GDELT-003, and nothing here justifies calling
GDELT a primary source or changing any direct acquisition path.

## 9. Matching

No recommendation ML in current phase.

DE-011 is pure deterministic logic over `CanonicalArticle + ClassifiedArticle + UserPreference`.

Baseline matching is rule-based:

- Mute rules veto positive matches.
- Positive dimensions use OR semantics: market OR category OR topic.
- Empty preference dimensions are not wildcards.
- Normal freshness is 24 hours, using `published_at` when present and not in the future, otherwise `discovered_at`.
- `hourly_update_enabled` and `daily_digest_enabled` do not suppress semantic candidates.
- `relevance_score = classification.confidence * matched_dimension_coverage`.
- `HIGH` importance requires at least two matched dimensions and score >= 0.90.
- `breaking_eligible` additionally requires user opt-in and `discovered_at` within 2 hours.

The output of DE matching is an `AlertCandidate`. DE-011 does not persist candidates. Durable idempotency/concurrency belongs to DE-012.

Matching Runner v1 core (section 7.9) wires DE-010, DE-011, and DE-012 behind injected
backend-neutral boundaries. It is implemented and offline-tested; it is not production
matching, and it is not live or remote-verified.

## 10. Email behavior

Normal content should not generate one email per article.

Expected flow:

```text
Relevant articles
     ↓
User-specific buffer
     ↓
Batch within <= 1 hour
     ↓
One normal email batch
```

Hard rule:

```text
MAX 1 normal batch email / user / hour
```

Breaking alerts are selective and require strong importance + relevance, with cooldown/rate limiting/duplicate suppression.

## 11. Monitoring

Use lightweight observability:

- Cloud Run logs
- BigQuery telemetry
- source-health/admin page
- billing alerts

Important metrics:

- source_fetch_success_rate
- source_fetch_latency
- articles_discovered
- duplicate_rate
- classification_success_rate
- classification_cost
- email_send_success_rate
- bounce_rate
- complaint_rate
- P50/P95 end-to-end latency

## 12. Upgrade philosophy

New infrastructure is activated only after measured evidence demonstrates a need.

Examples:

- paid aggregator only after a measured coverage gap
- Supabase Pro only for quota/reliability need
- R2 only when raw evidence/history is needed and rights permit
- Pub/Sub only if single-job coupling becomes a real retry/failure bottleneck
- Story clustering only if duplicate-story UX becomes materially bad
- dedicated search only when Postgres search is insufficient
- stronger LLM only if benchmark proves Flash is insufficient
