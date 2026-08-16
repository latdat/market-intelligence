# Source Registry Guide

## 1. Purpose

The Source Registry is the control plane for external information sources.

This document is the canonical source of truth for:

- the currently configured/onboarded sources;
- the 25-source Official Source Architecture v1 MUST matrix;
- source/channel and content-scope rules used by future Source Onboarding work.

Other documents should reference this registry instead of copying the full target matrix.

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

content_scope: FORMAL_REGULATORY_LEGAL

acquisition:
  method: REST_API
  endpoint_url: https://www.federalregister.gov/api/v1/documents.json
  poll_interval_minutes: 15
  rate_limit: null

rights:
  can_fetch: true
  can_store_metadata: true
  can_store_full_text: REVIEWED
  can_ai_process: false
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

DE-002 defined the validated models and a test fixture. SO-001 adds the first
production registry plus a deterministic TOML loader. Each filename must equal its
source_id, and unknown or invalid fields fail before any network request.

Static authoring data is represented by `SourceConfig`. It groups acquisition fields
under `acquisition`, requires one validated `content_scope`, and does not contain
runtime health fields. Dynamic values are represented separately by
`SourceOperationalState` and must not be written back to the TOML source configuration.
`content_scope` is internal authoring/audit metadata and is not added to the flat
`SourceDefinition` shared wire contract.

`AcquisitionConfig.endpoint_url` is the reviewed HTTP(S) endpoint used by the
configured acquisition method. It is internal/static configuration so connectors do
not accept arbitrary runtime target URLs. It is intentionally not added to the flat
`SourceDefinition` shared wire contract.

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

### 2.2 Current implementation — production source registry

`CURRENT IMPLEMENTATION` contains exactly 16 static `SourceConfig` files under
`config/sources/`: six RSS/Atom sources (SO-001/SO-004), two REST API sources (SO-004, SO-005), and eight HTML sources (SO-006, SO-007).

| Source ID | Market | Acquisition | Current role | Official v1 MUST? |
|---|---|---|---|---|
| `vn_mst_news_events` | VN | RSS | Domain news/discovery; editorial AI OFF/PENDING | No |
| `us_fed_press_releases` | US | RSS | Official Federal Reserve press releases | Yes |
| `eu_ecb_press` | EU | RSS | Official ECB press/news | Yes |
| `cn_nbs_latest_releases` | CN | RSS | Market/statistical context; narrative AI OFF/PENDING | No |
| `us_sec_regulatory` | US | RSS | Official SEC Administrative Proceedings | Yes |
| `eu_ec_policy_news` | EU | RSS | Official European Commission policy news | Yes |
| `us_federal_register` | US | REST_API | Federal Register regulatory event spine (SO-004) | Yes |
| `us_govinfo_legal` | US | REST_API | GovInfo canonical legal corpus (SO-005 v1: PLAW package-level) | Yes |
| `vn_sbv_regulatory_docs` | VN | HTML | State Bank of Vietnam regulatory documents (SO-006) | Yes |
| `vn_moit_regulatory_docs` | VN | HTML | Ministry of Industry and Trade regulatory documents (SO-007) | Yes |
| `vn_mst_regulatory_docs` | VN | HTML | Ministry of Science and Technology regulatory documents (SO-007) | Yes |
| `us_bis_regulatory` | US | HTML | Industry/security/trade-control regulation (SO-007) | Yes |
| `us_fhfa_regulatory` | US | HTML | Housing-finance regulation (SO-007) | Yes |
| `eu_esma_regulatory` | EU | HTML | Securities/markets regulation (SO-007) | Yes |
| `cn_samr_market_regulation_bulletins` | CN | HTML | Market regulation bulletins (SO-007C1) | Yes |
| `cn_miit_policy_listing` | CN | HTML | Industry/technology policy documents (SO-007C1) | Yes |

Of the 25 official MUST sources, **14** are currently implemented: `us_fed_press_releases`,
`eu_ecb_press`, `us_sec_regulatory`, `eu_ec_policy_news`, `us_federal_register`, `us_govinfo_legal`, `vn_sbv_regulatory_docs`, `vn_moit_regulatory_docs`, `vn_mst_regulatory_docs`, `us_bis_regulatory`, `us_fhfa_regulatory`, `eu_esma_regulatory`, `cn_samr_market_regulation_bulletins`, and `cn_miit_policy_listing`. The
remaining **11** MUST sources are `Planned / documented only`.

All 11 records use conservative metadata-only rights with
`rights_review_status = "PENDING"` and `can_ai_process = false`. This permits fetch and
metadata persistence but does not approve full-text storage, AI processing, snippet
display, or redistribution.

Content scope:
- `EDITORIAL_NEWS`: `vn_mst_news_events`, `us_fed_press_releases`, `eu_ecb_press`,
  `cn_nbs_latest_releases`, `eu_ec_policy_news`
- `FORMAL_REGULATORY_LEGAL`: `us_sec_regulatory`, `us_federal_register`, `us_govinfo_legal`, `vn_sbv_regulatory_docs`, `vn_moit_regulatory_docs`, `vn_mst_regulatory_docs`, `us_bis_regulatory`, `us_fhfa_regulatory`, `eu_esma_regulatory`, `cn_samr_market_regulation_bulletins`, `cn_miit_policy_listing`

**us_federal_register event-spine vs. GovInfo corpus boundary:**
The `us_federal_register` source is the Federal Register **regulatory event spine** — it
provides timely notice of proposed and final rules. It is NOT the canonical legal corpus.
The `us_govinfo_legal` source is implemented in SO-005 v1 for PLAW package-level coverage;
CFR and U.S. Code remain future expansion. These two sources have separate
identities and must not be merged into a single `SourceConfig`.

This implemented list is not the 25-source target. In particular,
`vn_mst_news_events` remains separate from the future formal-document config
`vn_mst_regulatory_docs`, and `cn_nbs_latest_releases` remains context rather than a
canonical legal/regulatory spine.

vn_moc_direction is intentionally not in the pilot registry. Its upstream HTTP URL
redirects to an HTTPS endpoint whose certificate chain could not be verified by the
Python connector during SO-001 inspection. TLS verification must not be disabled to
work around that source.

The NBS feed publishes timezone-naive timestamp strings. Existing normalization keeps
published_at = null and never substitutes discovered_at.

### 2.3 Target Official Architecture v1 — canonical matrix

`TARGET OFFICIAL ARCHITECTURE v1` contains 25 MUST official core sources. `MUST` is a
target priority, not an implementation claim. Only the four rows explicitly marked
`Implemented (SO-001 SourceConfig)` or `Implemented (SO-004 SourceConfig)` currently have production source config records;
the remaining 21 rows are `Planned / documented only`.

| Market | Source ID | Target role | Implementation status |
|---|---|---|---|
| VN | `vn_vbpl_legal` | Canonical legal spine (VBPL) | `Planned / documented only` |
| VN | `vn_sbv_regulatory_docs` | State Bank regulatory documents | `Implemented (SO-006 SourceConfig)` |
| VN | `vn_ssc_regulatory_docs` | Securities regulatory documents | `Planned / documented only` |
| VN | `vn_moit_regulatory_docs` | Industry/trade/energy regulatory documents | `Implemented (SO-007 SourceConfig)` |
| VN | `vn_mst_regulatory_docs` | Technology regulatory documents | `Implemented (SO-007 SourceConfig)` |
| VN | `vn_moc_regulatory_docs` | Construction/real-estate regulatory documents | `Planned / documented only` |
| US | `us_federal_register` | Federal Register event spine | `Implemented (SO-004 SourceConfig)` |
| US | `us_govinfo_legal` | GovInfo canonical legal corpus | `Implemented (SO-005 v1: PLAW package-level)` |
| US | `us_fed_press_releases` | Federal Reserve press releases | `Implemented (SO-001 SourceConfig)` |
| US | `us_sec_regulatory` | Securities regulation | `Implemented (SO-004 SourceConfig)` |
| US | `us_ferc_regulatory` | Energy regulation | `Planned / documented only` |
| US | `us_bis_regulatory` | Industry/security/trade-control regulation | `Implemented (SO-007 SourceConfig)` |
| US | `us_fhfa_regulatory` | Housing-finance regulation | `Implemented (SO-007 SourceConfig)` |
| EU | `eu_eurlex_cellar` | Canonical legal spine (EUR-Lex/CELLAR) | `Planned / documented only` |
| EU | `eu_ec_policy_news` | European Commission policy news | `Implemented (SO-004 SourceConfig)` |
| EU | `eu_ecb_press` | ECB press releases | `Implemented (SO-001 SourceConfig)` |
| EU | `eu_esma_regulatory` | Securities/markets regulation | `Implemented (SO-007 SourceConfig)` |
| CN | `cn_npc_law_db` | Canonical legal spine (NPC Laws DB) | `Planned / documented only` |
| CN | `cn_state_council_policy_docs` | State Council formal policy documents | `Planned / documented only` |
| CN | `cn_pboc_regulatory_docs` | Central-bank regulatory documents | `Planned / documented only` |
| CN | `cn_nfra_regulatory_docs` | Financial regulation | `Planned / documented only` |
| CN | `cn_csrc_regulatory_docs` | Securities regulation | `Planned / documented only` |
| CN | `cn_samr_market_regulation_bulletins` | Market regulation bulletins | `Implemented (SO-007C1 SourceConfig)` |
| CN | `cn_miit_policy_listing` | Industry/technology policy documents | `Implemented (SO-007C1 SourceConfig)` |
| CN | `cn_nea_regulatory_docs` | Energy regulation | `Planned / documented only` |
| CN | `cn_mohurd_regulatory_docs` | Housing/urban-rural development regulation | `Planned / documented only` |

Market totals are VN 6, US 7, EU 4, and CN 8: 25 MUST sources in total.

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

Content scopes:

```text
EDITORIAL_NEWS
FORMAL_REGULATORY_LEGAL
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

Canonical rule:
`poll_interval_minutes` is scheduler-owned scheduling metadata.
The ingestion runner performs bounded one-shot execution and does not sleep to enforce polling cadence.

Default target is approximately 15 minutes where practical.

A source may use 30/60 minute polling when:

- rate limits require it
- content changes slowly
- source-specific reliability suggests a slower cadence

Do not poll every source identically without reason.

## 6. Rights

Rights fields are operational constraints, not documentation-only notes.

Code that stores or displays content must respect them.

Official Source Architecture v1 scopes a rights decision by all of:

- source/channel;
- content class;
- third-party exclusions within that channel.

A hostname is not a sufficient rights boundary. One host may publish formal documents,
editorial news, and third-party material under different terms.

Each `SourceConfig` must therefore be content-homogeneous. If one website contains both
editorial news and formal regulatory documents, create separate configs rather than a
single mixed config. For example, `vn_mst_news_events` and
`vn_mst_regulatory_docs` remain separate source identities even if their channels share
an organization or hostname.

`SourceConfig.content_scope` is required and accepts exactly `EDITORIAL_NEWS` or
`FORMAL_REGULATORY_LEGAL`. It describes the content class covered by the config and
rights review; it does not grant or deny AI permission. Third-party exclusions remain
part of the reviewed channel/connector boundary rather than a generic policy field.

`can_ai_process` is strict boolean-only (`true` or `false`). Do not add or coerce
pseudo-booleans such as `REVIEWED`, `restricted`, `"true"`, or `1`.
`rights_review_status` remains separate. AI processing is allowed only when the status
is `APPROVED` and `can_ai_process is true`.

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
