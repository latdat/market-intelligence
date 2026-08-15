"""Public source connector interfaces and errors."""

from market_intelligence.connectors.government_api import (
    ApiConfigurationError,
    ApiFetchError,
    ApiParseError,
    GovernmentApiConnector,
    GovernmentApiConnectorError,
)
from market_intelligence.connectors.rss_atom import (
    FeedConfigurationError,
    FeedFetchError,
    FeedParseError,
    RssAtomConnector,
    RssAtomConnectorError,
)

__all__ = [
    "ApiConfigurationError",
    "ApiFetchError",
    "ApiParseError",
    "GovernmentApiConnector",
    "GovernmentApiConnectorError",
    "FeedConfigurationError",
    "FeedFetchError",
    "FeedParseError",
    "RssAtomConnector",
    "RssAtomConnectorError",
]
