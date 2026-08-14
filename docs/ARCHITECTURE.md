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
              DeepSeek V4 Flash
                classification
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

## 8. AI usage

Model:

```text
DeepSeek V4 Flash
```

Purpose:

- market verification
- category
- topics
- confidence
- basic relevance metadata

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
