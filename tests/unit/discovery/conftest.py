import pytest
from discovery_fixtures import build_fixture_sources

from market_intelligence.source_registry import SourceConfig


@pytest.fixture
def fixture_sources() -> tuple[SourceConfig, ...]:
    return build_fixture_sources()
