import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import DeepSeekSettings, DeepSeekV4FlashClassifier
from market_intelligence.classification.pricing import load_pricing_catalog
from market_intelligence.source_registry import SourceConfig

FIXED_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def approved_source() -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "approved_regulator",
            "name": "Approved Regulator",
            "market": "EU",
            "language": "en",
            "source_type": "REGULATOR",
            "authority_level": "PRIMARY",
            "domains": ["LAW_POLICY", "TECHNOLOGY"],
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://regulator.example/feed.xml",
                "poll_interval_minutes": 15,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": True,
                "can_show_snippet": True,
                "can_redistribute_full_text": False,
                "rights_review_status": "APPROVED",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def article() -> CanonicalArticle:
    return CanonicalArticle(
        article_id="integration-article",
        source_id="approved_regulator",
        url="https://regulator.example/article",
        canonical_url="https://regulator.example/article",
        title="Regulator publishes AI governance rule",
        description="The rule applies to semiconductor and AI providers.",
        language="en",
        market="EU",
        discovered_at=FIXED_TIME,
        content_hash="integration-content-hash",
    )


def test_mocked_http_adapter_enforces_wire_contract_and_returns_validated_result() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "response-123",
                "model": "provider-model-identifier",
                "system_fingerprint": "fingerprint-123",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "is_relevant": True,
                                    "markets": ["EU", "US"],
                                    "category": "LAW_POLICY",
                                    "topics": ["REGULATION", "AI"],
                                    "confidence": 0.94,
                                }
                            )
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_cache_hit_tokens": 25,
                    "prompt_cache_miss_tokens": 75,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    async def run() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            classifier = DeepSeekV4FlashClassifier(
                DeepSeekSettings(api_key="offline-test-key"),
                load_pricing_catalog(Path("config/classification/deepseek_pricing.toml")),
                client,
                clock=lambda: FIXED_TIME,
                monotonic=lambda: 1.0,
            )
            return await classifier.classify(article(), approved_source())

    result = asyncio.run(run())
    request_body = json.loads(requests[0].content)

    assert result.classified_article.model_dump(mode="json") == {
        "is_relevant": True,
        "markets": ["US", "EU"],
        "category": "LAW_POLICY",
        "topics": ["AI", "REGULATION"],
        "confidence": 0.94,
        "article_id": "integration-article",
        "classifier_version": "classification-v1",
        "classified_at": "2026-08-15T12:00:00Z",
    }
    assert result.provider_attempts == 1
    assert result.provider_request_id == "response-123"
    assert request_body["response_format"] == {"type": "json_object"}
    assert request_body["thinking"] == {"type": "disabled"}
    assert "integration-article" not in requests[0].content.decode()
