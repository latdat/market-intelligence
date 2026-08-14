# Source Registry Guide

## 1. Purpose

The Source Registry is the control plane for external information sources.

It answers:

- what source is this?
- what market/language/domain does it cover?
- how should it be acquired?
- how frequently may it be polled?
- what rights have been reviewed?
- what does it cost?
- is it healthy?
- when did it last succeed/fail?

## 2. Minimum source record

```yaml
source_id: us_federal_register
name: Federal Register
market: US
language: en
source_type: GOVERNMENT
authority_level: PRIMARY

domains:
  - LAW_POLICY

acquisition:
  method: REST_API
  poll_interval_minutes: 15
  rate_limit: null

rights:
  can_fetch: true
  can_store_metadata: true
  can_store_full_text: REVIEWED
  can_ai_process: REVIEWED
  can_show_snippet: REVIEWED
  can_redistribute_full_text: false
  rights_review_status: APPROVED

cost:
  type: FREE
  monthly_fixed_usd: 0

priority: 100
status: ACTIVE
health_status: UNKNOWN
last_success_at: null
last_failure_at: null
```

### 2.1 DE-002 implementation boundary

The YAML above is a conceptual registry record, not the selected production file
format. Static source configuration is intended to be authored as one TOML file per
source under:

```text
config/sources/<source_id>.toml
```

DE-002 defines the validated models and a test fixture only. It does not create a
production registry or loader.

Static authoring data is represented by `SourceConfig`. It groups acquisition fields
under `acquisition` and does not contain runtime health fields. Dynamic values are
represented separately by `SourceOperationalState` and must not be written back to
the TOML source configuration.

`SourceDefinition` remains the shared compatibility boundary from
`DATA_CONTRACTS.md`. Its serialized shape keeps these fields flat:

```text
acquisition_method
poll_interval_minutes
rate_limit
health_status
last_success_at
last_failure_at
```

The internal non-null `rate_limit` representation is provisional because the shared
contract currently defines the field but not its non-null shape:

```text
max_requests: positive integer
period_seconds: positive integer
```

No burst, concurrency, quota, or rate-limit framework is part of DE-002.

`ACTIVE` remains an accepted health value only for backward compatibility with the
current contract example. New operational state defaults to `UNKNOWN`. Business
enablement `status` remains conceptually separate from health but is not implemented
in DE-002.

## 3. Suggested enums

These are implementation proposals and may be adjusted with tests/migrations.

Markets:

```text
VN
US
EU
CN
```

Initial domains/categories:

```text
LAW_POLICY
ENERGY
TECHNOLOGY
REAL_ESTATE
FINANCE
```

Acquisition methods:

```text
REST_API
RSS
ATOM
HTML
SITEMAP
```

Source types may include:

```text
GOVERNMENT
REGULATOR
OFFICIAL_ORGANIZATION
NEWS
PUBLIC_DATA
```

Authority level may include:

```text
PRIMARY
SECONDARY
DISCOVERY
```

## 4. Health semantics

Keep business status and health status separate.

Example:

```text
status = ACTIVE
health_status = DEGRADED
```

An active source can be temporarily unhealthy.

Suggested health inputs:

- last successful fetch
- consecutive failures
- parse failure rate
- HTTP status patterns
- latency
- items returned

## 5. Polling

Default target is approximately 15 minutes where practical.

A source may use 30/60 minute polling when:

- rate limits require it
- content changes slowly
- source-specific reliability suggests a slower cadence

Do not poll every source identically without reason.

## 6. Rights

Rights fields are operational constraints, not documentation-only notes.

Code that stores or displays content must respect them.

Important:

```text
can_fetch = true
```

does not imply:

```text
can_redistribute_full_text = true
```

## 7. Cost

Paid source/API activation should require measured benefit.

By default, initial source acquisition should prioritize free official/public sources.

## 8. Source onboarding checklist

Before marking a source `ACTIVE`:

- source identity confirmed
- market/language/domain mapped
- acquisition method known
- polling interval selected
- rate-limit behavior understood
- rights reviewed
- connector tests added
- source produces valid RawArticle
- health telemetry visible
