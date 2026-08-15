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
- 40–60 carefully selected sources
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

## 3. Current system

```text
┌─────────────────────────────────────────────┐
│                 DATA SOURCES                │
│ VN           US           EU          CN    │
│ Official     Official     Official    Official
│ RSS/API      RSS/API      RSS/API     RSS/HTML
│ News         News         News        News   │
│                 + GDELT                     │
└──────────────────────┬──────────────────────┘
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

## 4. Source strategy

Initial target: 40–60 carefully selected sources across the four markets.

Prefer many useful primary/public sources over many paid APIs.

Source types include:

- official government/regulatory sources
- official APIs
- RSS / Atom feeds
- selected public/news feeds
- HTML sources when necessary
- GDELT as discovery / global signal / coverage benchmark

Paid aggregator is OFF by default.

## 5. Ingestion

Generic connector families:

- RSS / Atom
- REST / API
- HTML
- sitemap where appropriate

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

The v1 runner processes sequentially with bounded enqueue/process/run limits. It renews a
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
migration has been verified locally on PostgreSQL 17 but has not been applied to remote
Supabase.

The arrow to matching shows the approved downstream architecture. Matching remains
planned under DE-010/DE-011 and was not implemented by DE-009C.

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
RLS policy, RPC, or migration is introduced until an authoritative product/SWE persistence
contract exists.

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

## 9. Matching

No recommendation ML in current phase.

Baseline matching is rule-based:

```text
market match
+ category match
+ topic match
+ mute rules
+ freshness
+ basic importance
```

The output of DE matching should be an `AlertCandidate` or equivalent shared contract.

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
