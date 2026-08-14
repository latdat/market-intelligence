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

Do not overwrite `published_at` with retrieval time.

---

# 5. ClassifiedArticle

Classification adds structured metadata to a canonical article.

```json
{
  "article_id": "article-id",
  "market": ["US"],
  "category": "TECHNOLOGY",
  "topics": ["AI", "Semiconductor"],
  "confidence": 0.93,
  "method": "deepseek-v4-flash",
  "classified_at": "2026-08-14T14:06:00Z"
}
```

Classification input should normally be limited to:

- title
- description/snippet
- source metadata

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
