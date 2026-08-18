"""Generate the supplementary synthetic fixtures for the SWE handoff pack.

The original pack (``articles.sample.json`` and friends) covers the scenarios listed in
``swe_handoff/README.md``. Auditing it against the live models surfaced UI-relevant cases
it does not cover: every article is English, there is only one source identity, no article
is classified twice, every user has candidates, and no title is long enough to overflow.

This script emits ``*.supplement.sample.json`` files for exactly those gaps. Every record
is constructed through the real Pydantic models, so a fixture that stops validating is a
failing build rather than a stale file nobody notices.

The supplement joins to the base pack: identifiers are distinct, and every foreign
identifier used here resolves either inside the supplement or inside the base pack.

Usage::

    python scripts/generate_handoff_supplement.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_intelligence.articles.models import CanonicalArticle
from market_intelligence.classification.models import ClassifiedArticle, Topic
from market_intelligence.models.alert_candidates import AlertCandidate, AlertImportance
from market_intelligence.models.user_preferences import UserPreference
from market_intelligence.source_registry.models import (
    AcquisitionMethod,
    AuthorityLevel,
    CostConfig,
    CostType,
    Domain,
    Market,
    RightsConfig,
    RightsReviewStatus,
    SourceDefinition,
    SourceType,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "swe_handoff"

# Synthetic hashes. Deterministic so the fixtures are stable, but NOT produced by DE's
# real hashing pipeline. SWE must never recompute these; the value is DE-supplied.
_SYNTHETIC_NAMESPACE = "swe-handoff-supplement-v1"


def synthetic_id(*parts: str) -> str:
    payload = json.dumps([_SYNTHETIC_NAMESPACE, *parts], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def moment(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


LONG_TITLE = (
    "Synthetic: Joint interagency consultation on cross-border semiconductor export "
    "control alignment, renewable grid interconnection standards, and the supervisory "
    "treatment of algorithmic credit scoring under the revised prudential framework"
)

# --------------------------------------------------------------------------------------
# Articles
# --------------------------------------------------------------------------------------

ARTICLE_VI = CanonicalArticle(
    article_id=synthetic_id("article", "vi-sbv-lai-suat"),
    source_id="vn_sbv_regulatory_docs",
    source_item_id="sbv-2026-0842",
    url="https://example.invalid/vn/thong-tu-lai-suat?utm_source=feed",
    canonical_url="https://example.invalid/vn/thong-tu-lai-suat",
    title="Synthetic: Ngân hàng Nhà nước điều chỉnh trần lãi suất huy động ngắn hạn",
    description=(
        "Bản tin tổng hợp về việc điều chỉnh trần lãi suất huy động kỳ hạn dưới sáu tháng "
        "và lộ trình áp dụng đối với các tổ chức tín dụng."
    ),
    language="vi",
    market=Market.VN,
    published_at=moment(16, 2, 15),
    discovered_at=moment(16, 2, 20),
    content_hash=synthetic_id("hash", "vi-sbv-lai-suat"),
)

ARTICLE_ZH = CanonicalArticle(
    article_id=synthetic_id("article", "zh-pboc-policy"),
    source_id="cn_pboc_regulatory_docs",
    source_item_id=None,
    url="https://example.invalid/cn/pboc-policy-notice",
    canonical_url="https://example.invalid/cn/pboc-policy-notice",
    title="Synthetic: 中国人民银行发布宏观审慎评估参数调整通知",
    description="关于宏观审慎评估参数调整的合成测试摘要，用于前端渲染验证。",
    language="zh",
    market=Market.CN,
    published_at=moment(16, 3, 0),
    discovered_at=moment(16, 3, 5),
    content_hash=synthetic_id("hash", "zh-pboc-policy"),
)

ARTICLE_LONG_TITLE = CanonicalArticle(
    article_id=synthetic_id("article", "long-title"),
    source_id="us_federal_register",
    source_item_id="fr-2026-17734",
    url="https://example.invalid/us/interagency-consultation",
    canonical_url="https://example.invalid/us/interagency-consultation",
    title=LONG_TITLE,
    description=None,
    language="en",
    market=Market.US,
    published_at=moment(16, 13, 45),
    discovered_at=moment(16, 13, 50),
    content_hash=synthetic_id("hash", "long-title"),
)

ARTICLE_RECLASSIFIED = CanonicalArticle(
    article_id=synthetic_id("article", "reclassified"),
    source_id="eu_ecb_press",
    source_item_id="ecb-2026-0311",
    url="https://example.invalid/eu/ecb-statement",
    canonical_url="https://example.invalid/eu/ecb-statement",
    title="Synthetic: ECB statement on digital euro settlement pilot",
    description="A synthetic statement used to demonstrate classifier versioning.",
    language="en",
    market=Market.EU,
    published_at=moment(17, 9, 0),
    discovered_at=moment(17, 9, 4),
    content_hash=synthetic_id("hash", "reclassified"),
)

ARTICLE_MUTED_SOURCE = CanonicalArticle(
    article_id=synthetic_id("article", "muted-source"),
    source_id="us_sec_regulatory",
    source_item_id="sec-2026-5501",
    url="https://example.invalid/us/sec-enforcement-note",
    canonical_url="https://example.invalid/us/sec-enforcement-note",
    title="Synthetic: SEC note on disclosure obligations",
    description="Synthetic content used to demonstrate source muting.",
    language="en",
    market=Market.US,
    published_at=moment(17, 15, 30),
    discovered_at=moment(17, 15, 33),
    content_hash=synthetic_id("hash", "muted-source"),
)

ARTICLE_MAX_CARDINALITY = CanonicalArticle(
    article_id=synthetic_id("article", "max-cardinality"),
    source_id="eu_eurlex_cellar",
    source_item_id="cellar-2026-9004",
    url="https://example.invalid/eu/omnibus-regulation",
    canonical_url="https://example.invalid/eu/omnibus-regulation",
    title="Synthetic: Omnibus regulation touching every controlled topic",
    description="Synthetic article used to exercise maximum markets and topics per record.",
    language="en",
    market=Market.EU,
    published_at=None,
    discovered_at=moment(18, 1, 0),
    content_hash=synthetic_id("hash", "max-cardinality"),
)

ARTICLE_SNIPPET_ALLOWED = CanonicalArticle(
    article_id=synthetic_id("article", "snippet-allowed"),
    source_id="synthetic_snippet_approved_source",
    source_item_id="approved-0001",
    url="https://example.invalid/us/approved-snippet-item",
    canonical_url="https://example.invalid/us/approved-snippet-item",
    title="Synthetic: Item from a source whose snippet rights are APPROVED",
    description=(
        "This description MAY be rendered, because its source carries "
        "can_show_snippet=true and rights_review_status=APPROVED. Every other source in "
        "the pack denies snippet display."
    ),
    language="en",
    market=Market.US,
    published_at=moment(18, 4, 0),
    discovered_at=moment(18, 4, 3),
    content_hash=synthetic_id("hash", "snippet-allowed"),
)

ARTICLES = [
    ARTICLE_VI,
    ARTICLE_ZH,
    ARTICLE_LONG_TITLE,
    ARTICLE_RECLASSIFIED,
    ARTICLE_MUTED_SOURCE,
    ARTICLE_MAX_CARDINALITY,
    ARTICLE_SNIPPET_ALLOWED,
]

# --------------------------------------------------------------------------------------
# Classifications
# --------------------------------------------------------------------------------------

CLASSIFICATIONS = [
    ClassifiedArticle(
        article_id=ARTICLE_VI.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.VN,),
        category=Domain.FINANCE,
        topics=(Topic.BANKING, Topic.INTEREST_RATES),
        confidence=0.91,
        classified_at=moment(16, 2, 25),
    ),
    ClassifiedArticle(
        article_id=ARTICLE_ZH.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.CN,),
        category=Domain.FINANCE,
        topics=(Topic.BANKING, Topic.REGULATION),
        confidence=0.88,
        classified_at=moment(16, 3, 10),
    ),
    ClassifiedArticle(
        article_id=ARTICLE_LONG_TITLE.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.US, Market.EU),
        category=Domain.LAW_POLICY,
        topics=(Topic.REGULATION, Topic.SEMICONDUCTORS),
        confidence=0.74,
        classified_at=moment(16, 13, 55),
    ),
    # Reclassification pair: identical article_id, two classifier versions, both retained.
    # DE's primary key is (article_id, classifier_version) and terminal rows are immutable.
    ClassifiedArticle(
        article_id=ARTICLE_RECLASSIFIED.article_id,
        classifier_version="classification-v1",
        is_relevant=True,
        markets=(Market.EU,),
        category=Domain.TECHNOLOGY,
        topics=(Topic.REGULATION,),
        confidence=0.63,
        classified_at=moment(17, 9, 10),
    ),
    ClassifiedArticle(
        article_id=ARTICLE_RECLASSIFIED.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.EU,),
        category=Domain.FINANCE,
        topics=(Topic.BANKING, Topic.REGULATION),
        confidence=0.94,
        classified_at=moment(17, 11, 30),
    ),
    ClassifiedArticle(
        article_id=ARTICLE_MUTED_SOURCE.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.US,),
        category=Domain.FINANCE,
        topics=(Topic.REGULATION,),
        confidence=0.81,
        classified_at=moment(17, 15, 40),
    ),
    ClassifiedArticle(
        article_id=ARTICLE_SNIPPET_ALLOWED.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.US,),
        category=Domain.LAW_POLICY,
        topics=(Topic.REGULATION,),
        confidence=0.79,
        classified_at=moment(18, 4, 10),
    ),
    ClassifiedArticle(
        article_id=ARTICLE_MAX_CARDINALITY.article_id,
        classifier_version="classification-v2",
        is_relevant=True,
        markets=(Market.VN, Market.US, Market.EU, Market.CN),
        category=Domain.LAW_POLICY,
        topics=(
            Topic.AI,
            Topic.BANKING,
            Topic.REGULATION,
            Topic.RENEWABLE_ENERGY,
            Topic.SEMICONDUCTORS,
        ),
        confidence=0.87,
        classified_at=moment(18, 1, 10),
    ),
]

# --------------------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------------------

PREFERENCES = [
    # Follows US FINANCE/REGULATION but has muted the source the article came from.
    UserPreference(
        user_id="user-13-muted-source",
        markets=(Market.US,),
        categories=(Domain.FINANCE,),
        topics=(Topic.REGULATION,),
        muted_source_ids=("us_sec_regulatory",),
        muted_topics=(),
        breaking_alert_enabled=True,
        hourly_update_enabled=False,
        daily_digest_enabled=True,
    ),
    # Every list empty and every channel off: the empty state, and a user who must
    # never receive a candidate no matter what the corpus contains.
    UserPreference(
        user_id="user-14-empty",
        markets=(),
        categories=(),
        topics=(),
        muted_source_ids=(),
        muted_topics=(),
        breaking_alert_enabled=False,
        hourly_update_enabled=False,
        daily_digest_enabled=False,
    ),
    # Vietnamese reader: exercises non-ASCII rendering end to end.
    UserPreference(
        user_id="user-15-vn-banking",
        markets=(Market.VN,),
        categories=(Domain.FINANCE,),
        topics=(Topic.BANKING, Topic.INTEREST_RATES),
        muted_source_ids=(),
        muted_topics=(),
        breaking_alert_enabled=True,
        hourly_update_enabled=True,
        daily_digest_enabled=True,
    ),
]

# --------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------


def candidate_id(user_id: str, article_id: str) -> str:
    """Mirror DE's derivation: sha256 over a versioned, compact JSON array."""
    payload = json.dumps(
        ["alert-candidate-v1", user_id, article_id], separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


CANDIDATES = [
    AlertCandidate(
        candidate_id=candidate_id("user-15-vn-banking", ARTICLE_VI.article_id),
        user_id="user-15-vn-banking",
        article_id=ARTICLE_VI.article_id,
        matched_at=moment(16, 2, 30),
        match_reasons=("market:VN", "category:FINANCE", "topic:BANKING"),
        importance=AlertImportance.HIGH,
        relevance_score=0.91,
        breaking_eligible=True,
    ),
    AlertCandidate(
        candidate_id=candidate_id("user-15-vn-banking", ARTICLE_MAX_CARDINALITY.article_id),
        user_id="user-15-vn-banking",
        article_id=ARTICLE_MAX_CARDINALITY.article_id,
        matched_at=moment(18, 1, 15),
        match_reasons=("market:VN", "topic:BANKING"),
        importance=AlertImportance.NORMAL,
        relevance_score=0.435,
        breaking_eligible=False,
    ),
]

# user-13-muted-source deliberately has no candidate: the article that would have matched
# came from a muted source, and mute outranks every positive signal.
# user-14-empty deliberately has no candidate: empty preferences match nothing.

SUPPLEMENTARY_SOURCE = SourceDefinition(
    source_id="synthetic_snippet_approved_source",
    name="Synthetic Source With Approved Snippet Rights",
    market=Market.US,
    language="en",
    source_type=SourceType.GOVERNMENT,
    authority_level=AuthorityLevel.PRIMARY,
    domains=[Domain.LAW_POLICY],
    rights=RightsConfig(
        can_fetch=True,
        can_store_metadata=True,
        can_store_full_text=False,
        can_ai_process=False,
        can_show_snippet=True,
        can_redistribute_full_text=False,
        rights_review_status=RightsReviewStatus.APPROVED,
    ),
    cost=CostConfig(type=CostType.FREE, monthly_fixed_usd=0),
    priority=100,
    acquisition_method=AcquisitionMethod.REST_API,
    poll_interval_minutes=15,
)


def _synthetic_source(source_id: str, name: str, market: Market) -> SourceDefinition:
    """Build a placeholder SourceDefinition mirroring today's real rights posture."""
    return SourceDefinition(
        source_id=source_id,
        name=name,
        market=market,
        language="en",
        source_type=SourceType.GOVERNMENT,
        authority_level=AuthorityLevel.PRIMARY,
        domains=[Domain.LAW_POLICY],
        rights=RightsConfig(
            can_fetch=True,
            can_store_metadata=True,
            can_store_full_text=False,
            can_ai_process=False,
            can_show_snippet=False,
            can_redistribute_full_text=False,
            rights_review_status=RightsReviewStatus.PENDING,
        ),
        cost=CostConfig(type=CostType.FREE, monthly_fixed_usd=0),
        priority=100,
        acquisition_method=AcquisitionMethod.RSS,
        poll_interval_minutes=15,
    )


# The base pack's articles cite these two identities. They are published here so every
# article in the combined pack resolves to a source, which is what the rights gate needs.
BASE_PACK_SOURCES = [
    _synthetic_source("synthetic_source", "Synthetic Source", Market.US),
    _synthetic_source("synthetic_bad_source", "Synthetic Source With Degraded Health", Market.US),
]


def render(records: list[Any]) -> str:
    payload = [record.model_dump(mode="json") for record in records]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    outputs = {
        "articles.supplement.sample.json": ARTICLES,
        "article_classifications.supplement.sample.json": CLASSIFICATIONS,
        "user_preferences.supplement.sample.json": PREFERENCES,
        "alert_candidates.supplement.sample.json": CANDIDATES,
        "sources.supplement.sample.json": [SUPPLEMENTARY_SOURCE, *BASE_PACK_SOURCES],
    }
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, records in outputs.items():
        (OUTPUT_DIRECTORY / filename).write_text(render(records), encoding="utf-8")
        print(f"wrote swe_handoff/{filename} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
