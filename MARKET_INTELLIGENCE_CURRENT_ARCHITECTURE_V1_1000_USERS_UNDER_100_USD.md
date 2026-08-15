# MARKET & REGULATORY INTELLIGENCE PLATFORM
## Current Architecture Baseline v1.0 — 1,000 users · 4 markets · < $100/month

> **Historical baseline notice (SO-002):** source-selection, source-role, GDELT, and
> acquisition-roadmap statements in this document are superseded. Use
> `docs/ARCHITECTURE.md` for the approved platform architecture and
> `docs/SOURCE_REGISTRY.md` for the canonical current-source inventory and 25-source
> Official Source Architecture v1 matrix. Future GNews is discovery/enrichment only and
> is not currently implemented. This file remains as historical design context.

**Status:** CHỐT cho giai đoạn hiện tại  
**Verified date:** 2026-08-14  
**Markets:** Vietnam · United States · European Union · China  
**Primary product goal:** Phát hiện thông tin mới liên quan tới lĩnh vực người dùng quan tâm và gửi email **trong ≤ 60 phút**, mục tiêu thực tế nhanh hơn khi nguồn cho phép.  
**User scale target:** ~1,000 active users  
**Target operating cost:** **~$30–60/month bình thường**  
**Warning threshold:** **$70/month**  
**Critical threshold:** **$85/month**  
**Hard ceiling:** **< $100/month**

---

# 1. Product objective hiện tại

Giai đoạn hiện tại **không cố xây toàn bộ full-product intelligence platform**.

Core value phải chứng minh trước:

```text
Có thông tin mới
        ↓
Phát hiện nhanh
        ↓
Biết thông tin thuộc thị trường/lĩnh vực nào
        ↓
Match với sở thích từng user
        ↓
Gửi email đúng người
        ↓
User biết tin trong <= 60 phút
```

## KPI quan trọng nhất

### Freshness SLO

> **≥ 95% nội dung relevant phải được phát hiện, phân loại và đưa vào notification trong ≤ 60 phút kể từ thời điểm source công bố, khi source có thể truy cập bình thường.**

Stretch target:

```text
P50 end-to-end latency < 20 phút
P95 end-to-end latency < 60 phút
```

Các metric bắt buộc lưu:

```text
published_at
discovered_at
classified_at
matched_at
email_queued_at
email_sent_at
```

---

# 2. Kiến trúc hiện tại — overview

```text
┌───────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                           │
│                                                               │
│   VN            US             EU              CN              │
│   ├ Official    ├ Official     ├ Official      ├ Official      │
│   ├ RSS/API     ├ RSS/API      ├ RSS/API       ├ RSS/HTML      │
│   └ News        └ News         └ News          └ News          │
│                                                               │
│                 + GDELT global signal                         │
│                                                               │
│   Paid Aggregator = OFF by default                            │
└───────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
                      ┌───────────────────┐
                      │  SOURCE REGISTRY  │
                      │ metadata/rights   │
                      │ market/cost       │
                      └─────────┬─────────┘
                                │
                                ▼
                 ┌────────────────────────────┐
                 │     INGESTION CONNECTORS   │
                 │ RSS │ REST/API │ HTML      │
                 │ Sitemap when appropriate   │
                 └──────────────┬─────────────┘
                                │
                                ▼
                      CLOUD SCHEDULER
                     polling mỗi 15 phút
                                │
                                ▼
                       CLOUD RUN JOB
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
              ▼                 ▼                 ▼
            Fetch           Normalize           Dedup
                                                 │
                                                 ▼
                                   DeepSeek V4 Flash
                                   classification only
                                                 │
                                  market/category/topic
                                                 │
                                                 ▼
                                         SUPABASE POSTGRES
                                                 │
                    ┌────────────────────────────┼─────────────┐
                    │                            │             │
                    ▼                            ▼             ▼
                 Article                 User Preference    Alert State
                    │                            │
                    └──────────────┬─────────────┘
                                   │
                                   ▼
                          PERSONALIZED MATCHING
                                   │
                         ┌─────────┴─────────┐
                         │                   │
                         ▼                   ▼
                    WEB FEED            ALERT BUFFER
                                             │
                                max 1 normal batch/user/hour
                                             │
                                 + Breaking Alert selective
                                             │
                                             ▼
                                   AMAZON SES À-LA-CARTE
                                             │
                                             ▼
                                           Gmail
```

Telemetry nhẹ:

```text
Pipeline → BigQuery
```

Frontend:

```text
Cloudflare Pages / Workers
```

---

# 3. Data Sources

## Strategy

> **Nhiều source, không phải nhiều paid API.**

Mục tiêu ban đầu:

```text
~40–60 carefully selected sources
```

Phân bổ tham khảo:

```text
Vietnam        ~10–15
United States  ~10–15
European Union ~10–15
China          ~10–15
```

Không bắt buộc chia đều.

## Vietnam

- VBPL
- Cổng/Báo Chính phủ
- Ngân hàng Nhà nước
- Bộ Công Thương
- Bộ Tài chính
- các Bộ/ngành phù hợp
- selected news/public feeds

## United States

- SEC EDGAR
- Federal Register
- Federal Reserve
- Regulations.gov
- official agencies/regulators
- selected news/public feeds

## European Union

- EUR-Lex
- European Commission
- European Parliament
- Eurostat
- relevant agencies/regulators
- selected news/public feeds

## China

- State Council / gov.cn
- People's Bank of China
- MOFCOM
- relevant ministries/regulators
- selected news/public feeds

### China rights warning

Không suy luận:

```text
fetch được HTML
=
được phép lưu/redistribute toàn văn
```

China phải review source-by-source.

## GDELT

Vai trò:

```text
global discovery
news signal
coverage benchmark
cross-market signal
```

GDELT dataset là nguồn open-data bổ sung.

**Không hiểu sai:** quyền sử dụng dataset GDELT không tự động chuyển thành quyền republish full text của publisher.

## Paid aggregator

Trạng thái:

```text
OFF
```

Không mua Mediastack / NewsAPI.ai / Perigon ở ngày đầu.

Chỉ bật khi coverage benchmark chứng minh gap thật.

---

# 4. Source Registry

Bắt buộc từ đầu.

Ví dụ:

```json
{
  "source_id": "us_federal_register",
  "name": "Federal Register",
  "market": "US",
  "source_type": "GOVERNMENT",
  "authority_level": "PRIMARY",
  "domains": ["LAW_POLICY"],

  "acquisition": {
    "method": "REST_API",
    "poll_interval_minutes": 15
  },

  "rights": {
    "can_fetch": true,
    "can_store_metadata": true,
    "can_store_full_text": "REVIEWED",
    "can_ai_process": false,
    "can_show_snippet": "REVIEWED",
    "can_redistribute_full_text": false,
    "rights_review_status": "APPROVED"
  },

  "cost": {
    "type": "FREE",
    "monthly_fixed_usd": 0
  },

  "status": "ACTIVE"
}
```

Minimum fields:

```text
source_id
name
market
language
source_type
authority_level
domains
acquisition_method
poll_interval
rate_limit
rights
cost
priority
health_status
last_success_at
last_failure_at
```

---

# 5. Ingestion

Generic connectors:

```text
RSS / Atom
REST / API
HTML
Sitemap
```

Python stack:

```text
Python 3.12+
httpx
feedparser
BeautifulSoup / lxml
Pydantic
```

Playwright chỉ là fallback.

## Polling

### Chốt

```text
15 phút/lần
```

Source nào rate-limit chặt thì dùng interval riêng:

```text
15m / 30m / 60m
```

---

# 6. Scheduling & Compute

## Cloud Scheduler

Trigger ingestion job.

Lý do:

- managed;
- cron đơn giản;
- first 3 jobs/billing account miễn phí;
- pricing theo job, không theo số lần chạy.

## Cloud Run Jobs

Dùng cho:

```text
fetch
parse
normalize
dedup
classification
matching
```

Lý do:

- scale-to-zero;
- trả tiền theo usage;
- free tier lớn;
- không cần VPS chạy 24/7.

## Không dùng

```text
Airflow
Cloud Composer
Kafka
Kubernetes
dedicated VM
```

---

# 7. Canonical Article v1

Record tối thiểu:

```json
{
  "article_id": "...",
  "source_id": "...",
  "url": "...",
  "canonical_url": "...",
  "title": "...",
  "description": "...",
  "language": "en",
  "market": "US",
  "published_at": "...",
  "discovered_at": "...",
  "category": "FINANCE",
  "topics": [],
  "content_hash": "...",
  "classification": {
    "method": "deepseek-v4-flash",
    "confidence": 0.91,
    "classified_at": "..."
  }
}
```

Không lưu mặc định:

```text
full publisher HTML
full article body
huge embeddings
multiple LLM outputs
```

Mục tiêu:

```text
metadata-first
```

---

# 8. Deduplication

Rẻ trước:

```text
canonical URL
↓
source item ID
↓
content/title hash
↓
normalized-title similarity
```

Không vector DB.

Không LLM cho mọi duplicate decision.

---

# 9. AI Classification

## Model

```text
DeepSeek V4 Flash
```

Chỉ làm:

```text
market verification
category
topics
basic relevance metadata
confidence
```

Không làm:

```text
full article rewriting
long summary cho mọi article
complex reasoning
chatbot
semantic search
```

Input:

```text
title
description/snippet
source metadata
```

Output:

```json
{
  "market": ["US"],
  "category": "TECHNOLOGY",
  "topics": ["AI", "Semiconductor"],
  "confidence": 0.93
}
```

## Escalation

```text
V4 Pro = OFF
```

Chỉ bật sau benchmark.

---

# 10. Database

## Chốt

```text
Supabase PostgreSQL
```

Dùng cho:

```text
users
preferences
sources
articles
notification preferences
alert batches
email delivery log
saved/read state
basic web feed
```

## Initial tier

```text
Supabase Free
```

Current public limits checked:

```text
50,000 MAU
500 MB DB
5 GB egress
```

1,000 users chưa gây áp lực MAU.

Upgrade sang Pro chỉ khi:

```text
DB/egress gần quota
hoặc
production reliability/backups cần cao hơn
```

---

# 11. BigQuery telemetry

## Trạng thái

```text
LIGHT / OPTIONAL BUT RECOMMENDED
```

Không serving web.

Lưu telemetry:

```text
article_id
source_id
market
category
published_at
discovered_at
classified_at
email_sent_at
processing_status
estimated_ai_cost
```

Mục tiêu:

```text
latency
source health
coverage
cost
```

BigQuery Free Tier hiện:

```text
10 GiB storage/month
1 TiB on-demand query processed/month
```

Expected initial cost:

```text
~$0
```

---

# 12. User Preference

Markets:

```text
VN
US
EU
CN
```

Domains:

```text
Law & Policy
Energy
Technology
Real Estate
Finance
```

Topics ví dụ:

```text
AI
Semiconductor
Banking
Interest Rate
Oil & Gas
Renewable Energy
Real Estate
Regulation
```

Notification:

```text
Breaking Alert ON/OFF
Hourly Update ON/OFF
Daily Digest ON/OFF
```

---

# 13. Personalized Matching

Không Recommendation ML.

Baseline:

```text
market match
+
category match
+
topic match
+
mute rules
+
freshness
+
basic importance
```

Rule-based matching đủ cho 1,000 users và dễ debug.

---

# 14. Email strategy

## Normal content

Không:

```text
mỗi article -> một email
```

Mà:

```text
relevant articles
↓
user-specific buffer
↓
batch trong <=1 giờ
↓
1 email
```

### Hard rule

```text
MAX 1 normal batch email/user/hour
```

## Breaking Alert

Chỉ khi:

```text
high importance
+
high relevance
```

Có:

```text
cooldown
rate limit
duplicate suppression
```

## Provider

### Chốt

```text
Amazon SES À-LA-CARTE
```

Không để mặc định Essentials nếu mục tiêu là minimum cost.

AWS hiện công bố:

```text
À-la-carte outbound:
$0.10 / 1,000 emails
```

New SES accounts từ 2026-07-21 mặc định vào Essentials:

```text
$0.16 / 1,000 emails
```

nhưng AWS cho phép chuyển sang à-la-carte.

Trước launch:

```text
production access
domain verification
SPF
DKIM
DMARC
bounce/complaint monitoring
```

---

# 15. Email cost model

## 4 emails/user/day

```text
1,000 × 4 × 30
= 120,000 emails
≈ $12/month
```

## 6 emails/user/day

```text
180,000
≈ $18/month
```

## 10 emails/user/day

```text
300,000
≈ $30/month
```

## Theoretical hourly ceiling

```text
1,000 × 24 × 30
= 720,000 emails
≈ $72/month
```

Normal behavior phải thấp hơn ceiling này.

---

# 16. Cost guardrail

```text
TARGET    $50
WARNING   $70
CRITICAL  $85
HARD CAP  $100
```

Nếu projected cost tăng:

1. giảm low-importance Breaking Alerts;
2. batch nhiều hơn;
3. ưu tiên high relevance;
4. đẩy low-value content sang Daily Digest;
5. không tự động bật paid API;
6. review trước mọi tier upgrade.

---

# 17. Website features

## Có ngay

```text
Register/Login
Onboarding
Market preference
Domain preference
Topic preference
Notification settings
Personalized Feed
Latest
Market filter
Category filter
Source provenance
```

## Nên thêm nếu đủ thời gian dev

```text
Save / Bookmark
Read / Unread
Alert history
Mute Source
Mute Topic
```

Recurring infra cost gần như không đáng kể.

## Chưa có

```text
AI chatbot
semantic search
vector search
complex Story intelligence
mobile app
Slack/Teams
recommendation ML
```

---

# 18. Frontend

## Chốt

```text
Cloudflare Pages / Workers
```

Current Free tier:

```text
100,000 Worker requests/day
static asset requests free/unlimited
```

Expected early cost:

```text
~$0
```

---

# 19. Monitoring

Không mua observability platform riêng.

Dùng:

```text
Cloud Run logs
BigQuery telemetry
simple admin/source-health page
billing alerts
```

Metrics:

```text
source_fetch_success_rate
source_fetch_latency
articles_discovered
duplicate_rate
classification_success_rate
classification_cost
email_send_success_rate
bounce_rate
complaint_rate
P50/P95 end-to-end latency
```

---

# 20. Cost model hiện tại

| Component | Expected/month |
|---|---:|
| Official/RSS/API sources | $0 |
| GDELT | $0 |
| Paid aggregator | $0 |
| Cloud Scheduler | ~$0 |
| Cloud Run | ~$0–5 |
| Supabase Free | $0 |
| BigQuery telemetry | ~$0 |
| Cloudflare frontend | ~$0 |
| DeepSeek V4 Flash | ~$5–10 |
| SES | ~$12–30 typical |
| Domain/misc/buffer | ~$2–5 |
| **Expected** | **~$19–50/month** |

Practical management target:

```text
~$30–60/month
```

Hard ceiling:

```text
< $100/month
```

---

# 21. DeepSeek pricing assumption

DeepSeek công bố bảng giá mới hiệu lực:

```text
2026-08-16 16:00 UTC
```

V4 Flash:

```text
OFF-PEAK
cache hit   $0.007 / 1M input
cache miss  $0.22  / 1M input
output      $0.66  / 1M output

PEAK
cache hit   $0.014 / 1M input
cache miss  $0.44  / 1M input
output      $1.32  / 1M output
```

AI policy:

```text
minimal input
structured JSON
no verbose output
no full summary for every article
```

---

# 22. Components deliberately removed

```text
❌ Paid News API
❌ R2 Raw/Bronze
❌ Pub/Sub
❌ Airflow
❌ Kafka
❌ Kubernetes
❌ Typesense/Elasticsearch/OpenSearch
❌ Vector DB
❌ Embedding pipeline
❌ Full Story Clustering
❌ V4 Pro
❌ Recommendation ML
```

Lý do chung:

> Chưa trực tiếp cải thiện KPI chính đủ để đáng chi phí/complexity.

---

# 23. Future activation triggers

## Paid aggregator

Bật khi:

```text
measured coverage gap
+
business value
>
subscription cost
```

## Supabase Pro

Bật khi:

```text
DB/egress quota
hoặc
reliability/backups requirement
```

## R2

Bật khi:

```text
cần raw evidence/history
+
rights cho phép
```

## Pub/Sub

Bật khi:

```text
single-job coupling tạo retry/failure bottleneck
```

## Story clustering

Bật khi:

```text
duplicate articles làm email UX xấu
hoặc
product chuyển sang Story-centric
```

## Dedicated search

Bật khi:

```text
Postgres search không đủ quality/latency
```

## V4 Pro

Bật khi:

```text
benchmark chứng minh Flash không đủ cho high-risk cases
```

---

# 24. Current architecture baseline — compact

```text
40–60 Sources
    │
    ↓
Source Registry
    │
    ↓
RSS / REST / HTML
    │
    ↓
Cloud Scheduler — 15 min
    │
    ↓
Cloud Run
    │
    ├─ Fetch
    ├─ Normalize
    ├─ Dedup
    └─ DeepSeek V4 Flash classification
    │
    ↓
Supabase PostgreSQL
    │
    ├─ Article
    ├─ User
    ├─ Preference
    ├─ Alert State
    └─ Delivery History
    │
    ↓
Rule-based Personalized Matching
    │
    ├─────────────────────┐
    ↓                     ↓
Web Feed             Alert Buffer
                           │
                    <= 1 hour batch
                           │
                    Breaking selective
                           │
                           ↓
                 Amazon SES à-la-carte
                           │
                           ↓
                         Gmail
```

Telemetry:

```text
Pipeline → BigQuery lightweight telemetry
```

Frontend:

```text
Cloudflare Pages / Workers
```

---

# 25. Product capabilities at ~1,000 users

```text
✓ 4 markets: VN / US / EU / CN
✓ 5 domains
✓ source polling ~15 min where practical
✓ AI-assisted classification
✓ personalized matching
✓ email delivery <= 1 hour target
✓ selective Breaking Alert
✓ Daily Digest
✓ personalized web feed
✓ market/category filters
✓ preference management
✓ mute source/topic
✓ save/bookmark
✓ read/unread
✓ alert history
✓ source provenance
```

---

# 26. Definition of success

Version này thành công nếu:

1. 4 markets đều có useful coverage.
2. ≥95% relevant ingested content đạt delivery ≤60 phút.
3. Email không spam quá mức.
4. User kiểm soát được market/domain/topic.
5. Classification đủ tốt để alert hữu ích.
6. Source failure được nhìn thấy.
7. Cost bình thường ~30–60 USD/month.
8. Không vượt hard ceiling $100/month.
9. Paid API chỉ thêm sau benchmark.
10. Có telemetry để quyết định upgrade tiếp theo.

---

# 27. Official references verified 2026-08-14

## Google Cloud

Cloud Run pricing  
https://cloud.google.com/run/pricing

Cloud Scheduler pricing  
https://cloud.google.com/scheduler/pricing

BigQuery pricing  
https://cloud.google.com/bigquery/pricing

## Supabase

https://supabase.com/pricing

## AWS SES

https://aws.amazon.com/ses/pricing/

Important current SES facts:

```text
Essentials:
$0.16 / 1,000 emails for first 10M/month

À-la-carte:
$0.10 / 1,000 outbound emails

New accounts/account-region combinations with no metered
SES activity since 2025-06-01 start on Essentials beginning
2026-07-21, but may switch to à-la-carte.
```

## DeepSeek

https://api-docs.deepseek.com/quick_start/pricing/

## GDELT

https://www.gdeltproject.org/data.html  
https://www.gdeltproject.org/about.html

## Cloudflare

https://developers.cloudflare.com/workers/platform/pricing/

---

# 28. Final one-line summary

> **Kiến trúc hiện tại ưu tiên Freshness + Relevance + Email Delivery: 4 thị trường, 40–60 nguồn, polling 15 phút, DeepSeek V4 Flash để phân loại, Supabase để lưu state, Cloud Run/Scheduler để chạy pipeline, SES à-la-carte để gửi Gmail, Cloudflare cho web; target ~$30–60/tháng và hard cap < $100 cho khoảng 1.000 users.**
