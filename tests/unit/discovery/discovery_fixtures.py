"""Synthetic builders shared by the discovery unit tests.

Everything here is test-only fixture data. No test in this package may assert anything about
the real production source registry or real publisher rights.
"""

from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.discovery import (
    DiscoveryCandidate,
    DiscoveryProvider,
    IdentityMode,
    PublisherRoute,
)
from market_intelligence.source_registry import ContentScope, SourceConfig

FIXTURE_ROUTES = Path(__file__).parents[2] / "fixtures" / "discovery" / "publisher_routes.toml"

EDITORIAL_SOURCE_ID = "xx_editorial_fixture_source"
REGULATORY_SOURCE_ID = "xx_regulatory_fixture_source"
RIGHTS_DENIED_SOURCE_ID = "xx_rights_denied_fixture_source"


def build_source(
    source_id: str,
    *,
    content_scope: str = "EDITORIAL_NEWS",
    can_fetch: bool = True,
    can_store_metadata: bool = True,
    can_ai_process: bool = False,
    rights_review_status: str = "PENDING",
) -> SourceConfig:
    """Build a synthetic source config; never assert against the real production registry."""
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": "Synthetic Fixture Source",
            "market": "US",
            "language": "en",
            "source_type": "NEWS",
            "authority_level": "SECONDARY",
            "domains": ["FINANCE"],
            "content_scope": content_scope,
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://fixture.example.test/feed.xml",
                "poll_interval_minutes": 15,
                "rate_limit": None,
            },
            "rights": {
                "can_fetch": can_fetch,
                "can_store_metadata": can_store_metadata,
                "can_store_full_text": False,
                "can_ai_process": can_ai_process,
                "can_show_snippet": False,
                "can_redistribute_full_text": False,
                "rights_review_status": rights_review_status,
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def build_route(
    hostname: str,
    path_prefix: str,
    target_source_id: str,
    *,
    content_scope: ContentScope = ContentScope.EDITORIAL_NEWS,
    identity_mode: IdentityMode = IdentityMode.CANONICAL_URL_FALLBACK,
) -> PublisherRoute:
    return PublisherRoute(
        hostname=hostname,
        path_prefix=path_prefix,
        target_source_id=target_source_id,
        content_scope=content_scope,
        identity_mode=identity_mode,
    )


def build_candidate(url: str, *, query_id: str = "us_finance") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        provider=DiscoveryProvider.GDELT_DOC_2_0,
        query_id=query_id,
        observed_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
        original_url=url,
        title="Synthetic candidate title",
    )


def build_fixture_sources() -> tuple[SourceConfig, ...]:
    """The synthetic sources targeted by tests/fixtures/discovery/publisher_routes.toml."""
    return (
        build_source(EDITORIAL_SOURCE_ID),
        build_source(REGULATORY_SOURCE_ID, content_scope="FORMAL_REGULATORY_LEGAL"),
        build_source(RIGHTS_DENIED_SOURCE_ID, can_store_metadata=False),
    )
