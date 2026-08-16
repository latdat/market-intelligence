"""Unit tests for LegalCorpusConnector supporting eu_eurlex_cellar."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from market_intelligence.connectors.legal_corpus import CorpusParseError, LegalCorpusConnector
from market_intelligence.source_registry import SourceConfig


@pytest.fixture
def cellar_source() -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "eu_eurlex_cellar",
            "name": "EUR-Lex / CELLAR",
            "market": "EU",
            "language": "en",
            "source_type": "GOVERNMENT",
            "authority_level": "PRIMARY",
            "domains": ["LAW_POLICY"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "acquisition": {
                "method": "REST_API",
                "endpoint_url": "https://publications.europa.eu/webapi/rdf/sparql",
                "poll_interval_minutes": 15,
            },
            "rights": {
                "rights_review_status": "PENDING",
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": False,
                "can_show_snippet": False,
                "can_redistribute_full_text": False,
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


@pytest.fixture
def mock_clock():
    return lambda: datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


def _run_fetch(connector: LegalCorpusConnector, source: SourceConfig):
    return asyncio.run(connector.fetch(source))


def test_fetch_cellar_success(cellar_source, mock_clock):
    sparql_response = b"""{
        "head": {"vars": ["work", "celex", "type", "date", "title"]},
        "results": {
            "bindings": [
                {
                    "work": {"type": "uri", "value": "http://publications.europa.eu/resource/cellar/123"},
                    "celex": {"type": "literal", "value": "32026R1234"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG"},
                    "date": {"type": "literal", "value": "2026-08-15"},
                    "title": {"type": "literal", "value": "Test Regulation"},
                    "title": {"type": "literal", "value": "Test Regulation"}
                },
                {
                    "work": {"type": "uri", "value": "http://publications.europa.eu/resource/cellar/456"},
                    "celex": {"type": "literal", "value": "32026L5678"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DIR"},
                    "date": {"type": "literal", "value": "2026-08-14"}
                },
                {
                    "work": {"type": "uri", "value": "http://publications.europa.eu/resource/cellar/456"},
                    "celex": {"type": "literal", "value": "32026L5678"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DIR"},
                    "date": {"type": "literal", "value": "2026-08-14"},
                    "title": {"type": "literal", "value": "Second expression"}
                }
            ]
        }
    }"""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        assert request.headers.get("Accept") == "application/sparql-results+json"
        assert "LIMIT+9" in request.url.query.decode() or "LIMIT%209" in request.url.query.decode()
        return httpx.Response(200, content=sparql_response, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=3, clock=mock_clock, client=client)

    articles = _run_fetch(connector, cellar_source)

    assert called
    assert len(articles) == 2

    a1 = articles[0]
    assert a1.source_item_id == "32026R1234"
    assert a1.title == "Test Regulation"
    assert a1.url == "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32026R1234"
    assert a1.published_at_raw is None
    assert a1.raw_metadata["document_date"] == "2026-08-15"
    assert a1.raw_metadata["cellar_work_uri"] == "http://publications.europa.eu/resource/cellar/123"
    assert (
        a1.raw_metadata["resource_type"]
        == "http://publications.europa.eu/resource/authority/resource-type/REG"
    )

    a2 = articles[1]
    assert a2.source_item_id == "32026L5678"
    assert a2.title == "Second expression"


def test_fetch_cellar_malformed_celex(cellar_source, mock_clock):
    sparql_response = b"""{
        "results": {
            "bindings": [
                {
                    "work": {"type": "uri", "value": "http://publications.europa.eu/resource/cellar/123"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG"},
                    "date": {"type": "literal", "value": "2026-08-15"}
                }
            ]
        }
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sparql_response, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=3, clock=mock_clock, client=client)

    with pytest.raises(CorpusParseError, match="missing or invalid celex in binding"):
        _run_fetch(connector, cellar_source)


def test_fetch_cellar_missing_celex_value_skips(cellar_source, mock_clock):
    sparql_response = b"""{
        "results": {
            "bindings": [
                {
                    "celex": {"type": "literal", "value": ""},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG"}
                }
            ]
        }
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sparql_response, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=3, clock=mock_clock, client=client)

    articles = _run_fetch(connector, cellar_source)
    assert len(articles) == 0


def test_fetch_cellar_invalid_json(cellar_source, mock_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"Not JSON", request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=3, clock=mock_clock, client=client)

    with pytest.raises(CorpusParseError, match="SPARQL response is not valid JSON"):
        _run_fetch(connector, cellar_source)


def test_fetch_cellar_regression_document_date_is_not_published_at(cellar_source, mock_clock):
    sparql_response = b"""{
        "results": {
            "bindings": [
                {
                    "celex": {"type": "literal", "value": "32026R1234"},
                    "date": {"type": "literal", "value": "2026-08-15"},
                    "title": {"type": "literal", "value": "Test Regulation Title"}
                }
            ]
        }
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sparql_response, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=3, clock=mock_clock, client=client)

    articles = _run_fetch(connector, cellar_source)

    assert len(articles) == 1
    a1 = articles[0]
    assert a1.raw_metadata["document_date"] == "2026-08-15"
    assert a1.published_at_raw is None

    from market_intelligence.normalization.articles import normalize_article

    canonical = normalize_article(a1, cellar_source)
    assert canonical.published_at is None


def test_fetch_cellar_delegated_implementing_variants(cellar_source, mock_clock):
    sparql_response = b"""{
        "results": {
            "bindings": [
                {
                    "celex": {"type": "literal", "value": "32026R0001"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG_DEL"},
                    "title": {"type": "literal", "value": "Valid English Title 1"}
                },
                {
                    "celex": {"type": "literal", "value": "32026R0002"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG_IMPL"},
                    "title": {"type": "literal", "value": "Valid English Title 2"}
                },
                {
                    "celex": {"type": "literal", "value": "32026L0003"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DIR_DEL"},
                    "title": {"type": "literal", "value": "Valid English Title 3"}
                },
                {
                    "celex": {"type": "literal", "value": "32026L0004"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DIR_IMPL"},
                    "title": {"type": "literal", "value": "Valid English Title 4"}
                },
                {
                    "celex": {"type": "literal", "value": "32026D0005"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DEC_DEL"},
                    "title": {"type": "literal", "value": "Valid English Title 5"}
                },
                {
                    "celex": {"type": "literal", "value": "32026D0006"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DEC_IMPL"},
                    "title": {"type": "literal", "value": "Valid English Title 6"}
                }
            ]
        }
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sparql_response, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=10, clock=mock_clock, client=client)

    articles = _run_fetch(connector, cellar_source)
    assert len(articles) == 6
    types = [a.raw_metadata["resource_type"] for a in articles]
    assert "http://publications.europa.eu/resource/authority/resource-type/REG_DEL" in types
    assert "http://publications.europa.eu/resource/authority/resource-type/REG_IMPL" in types
    assert "http://publications.europa.eu/resource/authority/resource-type/DIR_DEL" in types
    assert "http://publications.europa.eu/resource/authority/resource-type/DIR_IMPL" in types
    assert "http://publications.europa.eu/resource/authority/resource-type/DEC_DEL" in types
    assert "http://publications.europa.eu/resource/authority/resource-type/DEC_IMPL" in types


def test_fetch_cellar_max_items_limit(cellar_source, mock_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        # Max items is 1000, query_limit should be 3000
        assert (
            "LIMIT+3000" in request.url.query.decode()
            or "LIMIT%203000" in request.url.query.decode()
        )
        return httpx.Response(200, content=b'{"results": {"bindings": []}}', request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=1000, clock=mock_clock, client=client)
    _run_fetch(connector, cellar_source)


def test_fetch_cellar_regression_corrigendum_and_celex(cellar_source, mock_clock):
    sparql_response = b"""{
        "results": {
            "bindings": [
                {
                    "celex": {"type": "literal", "value": "32026M12504"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DEC"},
                    "title": {"type": "literal", "value": "Merger Decision"}
                },
                {
                    "celex": {"type": "literal", "value": "32026D1234"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/DEC"},
                    "title": {"type": "literal", "value": "Valid Decision"}
                },
                {
                    "celex": {"type": "literal", "value": "32026R1714R(02)"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG_IMPL"},
                    "title": {"type": "literal", "value": "Corrigendum Title"}
                },
                {
                    "celex": {"type": "literal", "value": "32026R1714"},
                    "type": {"type": "uri", "value": "http://publications.europa.eu/resource/authority/resource-type/REG"},
                    "title": {"type": "literal", "value": ""}
                }
            ]
        }
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sparql_response, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    connector = LegalCorpusConnector(max_items=10, clock=mock_clock, client=client)

    articles = _run_fetch(connector, cellar_source)
    # The 32026M12504 is excluded because it starts with 3xxxxM.
    # The 32026R1714 is excluded because it lacks a title.
    assert len(articles) == 2

    a1 = articles[0]
    assert a1.source_item_id == "32026D1234"
    assert a1.title == "Valid Decision"
    assert not a1.raw_metadata.get("is_corrigendum")

    a2 = articles[1]
    assert a2.source_item_id == "32026R1714R(02)"
    assert a2.title == "Corrigendum Title"
    assert a2.raw_metadata.get("is_corrigendum")
