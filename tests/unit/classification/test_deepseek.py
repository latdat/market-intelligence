import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import (
    CLASSIFIER_VERSION,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
    ClassificationConfigurationError,
    ClassificationError,
    ClassificationErrorCategory,
    ClassificationResult,
    DeepSeekSettings,
    DeepSeekV4FlashClassifier,
    Topic,
    create_deepseek_classifier_from_environment,
)
from market_intelligence.classification.pricing import load_pricing_catalog
from market_intelligence.source_registry import Market, SourceConfig

PRICING_PATH = Path("config/classification/deepseek_pricing.toml")
FIXED_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

type HandlerItem = httpx.Response | Exception | tuple[int, dict[str, str], object]


def usage(
    *,
    prompt: int = 10,
    hit: int = 4,
    miss: int = 6,
    completion: int = 3,
) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def semantic_output(**overrides: object) -> dict[str, object]:
    output: dict[str, object] = {
        "is_relevant": True,
        "markets": ["US"],
        "category": "FINANCE",
        "topics": ["INTEREST_RATES"],
        "confidence": 0.93,
    }
    output.update(overrides)
    return output


def provider_payload(
    *,
    content: str | None = None,
    finish_reason: str = "stop",
    observed_usage: object | None = None,
    include_usage: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "provider-request-id",
        "model": "deepseek-v4-flash-provider-revision",
        "system_fingerprint": "fingerprint-1",
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": (json.dumps(semantic_output()) if content is None else content)
                },
            }
        ],
    }
    if include_usage:
        payload["usage"] = usage() if observed_usage is None else observed_usage
    return payload


def queued_handler(
    items: Sequence[HandlerItem],
    requests: list[httpx.Request],
) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(items)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return httpx.Response(
                item.status_code,
                content=item.content,
                headers=item.headers,
                request=request,
            )
        status_code, headers, body = item
        if isinstance(body, (dict, list)):
            return httpx.Response(
                status_code,
                headers=headers,
                json=body,
                request=request,
            )
        return httpx.Response(
            status_code,
            headers=headers,
            content=str(body).encode(),
            request=request,
        )

    return handler


async def classify_with_items(
    article: CanonicalArticle,
    source: SourceConfig,
    items: Sequence[HandlerItem],
    *,
    random_value: Callable[[], float] = lambda: 0.5,
    clock: Callable[[], datetime] = lambda: FIXED_TIME,
    max_attempts: int = 3,
) -> tuple[ClassificationResult, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    transport = httpx.MockTransport(queued_handler(items, requests))
    async with httpx.AsyncClient(transport=transport) as client:
        classifier = DeepSeekV4FlashClassifier(
            DeepSeekSettings(api_key="unit-test-api-key"),
            load_pricing_catalog(PRICING_PATH),
            client,
            max_attempts=max_attempts,
            sleep=fake_sleep,
            clock=clock,
            monotonic=lambda: 10.0,
            random_value=random_value,
        )
        result = await classifier.classify(article, source)
    return result, requests, sleeps


async def classify_expect_error(
    article: CanonicalArticle,
    source: SourceConfig,
    items: Sequence[HandlerItem],
    *,
    max_attempts: int = 3,
) -> tuple[ClassificationError, list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    transport = httpx.MockTransport(queued_handler(items, requests))
    async with httpx.AsyncClient(transport=transport) as client:
        classifier = DeepSeekV4FlashClassifier(
            DeepSeekSettings(api_key="unit-test-api-key"),
            load_pricing_catalog(PRICING_PATH),
            client,
            max_attempts=max_attempts,
            sleep=fake_sleep,
            clock=lambda: FIXED_TIME,
            monotonic=lambda: 10.0,
            random_value=lambda: 0.5,
        )
        try:
            await classifier.classify(article, source)
        except ClassificationError as error:
            return error, requests, sleeps
    raise AssertionError("classification was expected to fail")


def test_success_builds_shared_and_internal_lineage_from_application_and_provider(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    result, requests, sleeps = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [(200, {}, provider_payload())],
        )
    )

    assert result.classified_article.article_id == canonical_article.article_id
    assert result.classified_article.classifier_version == CLASSIFIER_VERSION
    assert result.classified_article.classified_at == FIXED_TIME
    assert result.requested_model == "deepseek-v4-flash"
    assert result.provider_model == "deepseek-v4-flash-provider-revision"
    assert result.prompt_version == PROMPT_VERSION
    assert result.taxonomy_version == TAXONOMY_VERSION
    assert result.provider_request_id == "provider-request-id"
    assert result.system_fingerprint == "fingerprint-1"
    assert result.provider_attempts == 1
    assert len(requests) == 1
    assert sleeps == []


def test_request_uses_json_output_and_only_approved_provider_input(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    _, requests, _ = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [(200, {}, provider_payload())],
        )
    )
    request_body = json.loads(requests[0].content)
    serialized_body = requests[0].content.decode()

    assert request_body["model"] == "deepseek-v4-flash"
    assert request_body["response_format"] == {"type": "json_object"}
    assert request_body["thinking"] == {"type": "disabled"}
    assert request_body["temperature"] == 0
    assert request_body["stream"] is False
    assert request_body["max_tokens"] == 256
    assert "Return JSON only" in request_body["messages"][0]["content"]
    assert "untrusted article metadata" in request_body["messages"][1]["content"]
    for forbidden_value in [
        canonical_article.article_id,
        canonical_article.source_id,
        canonical_article.source_item_id,
        canonical_article.url,
        canonical_article.canonical_url,
        canonical_article.content_hash,
        "unit-test-api-key",
    ]:
        assert forbidden_value is not None
        assert forbidden_value not in serialized_body
    for forbidden_key in [
        '"article_id"',
        '"source_id"',
        '"url"',
        '"canonical_url"',
        '"raw_metadata"',
        '"content_hash"',
        '"credentials"',
    ]:
        assert forbidden_key not in serialized_body


def test_valid_unsorted_output_is_normalized_without_retry(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    content = json.dumps(
        semantic_output(
            markets=["EU", "US"],
            topics=["REGULATION", "AI"],
        )
    )
    result, requests, sleeps = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [(200, {}, provider_payload(content=content))],
        )
    )

    assert result.classified_article.markets == (Market.US, Market.EU)
    assert result.classified_article.topics == (Topic.AI, Topic.REGULATION)
    assert len(requests) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "invalid_output",
    [
        semantic_output(markets=["US", "US"]),
        semantic_output(topics=["AI", "AI"]),
        semantic_output(markets=["UK"]),
        semantic_output(topics=["CRYPTO"]),
    ],
)
def test_duplicate_or_unsupported_output_retries_then_fails_validation(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    invalid_output: dict[str, object],
) -> None:
    item = (200, {}, provider_payload(content=json.dumps(invalid_output)))
    error, requests, sleeps = asyncio.run(
        classify_expect_error(
            canonical_article,
            approved_source,
            [item, item, item],
        )
    )

    assert error.category is ClassificationErrorCategory.INVALID_OUTPUT
    assert error.provider_attempts == 3
    assert len(requests) == 3
    assert len(sleeps) == 2


def test_usage_and_cost_aggregate_malformed_attempt_and_success(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    first_usage = usage(prompt=10, hit=4, miss=6, completion=3)
    second_usage = usage(prompt=20, hit=5, miss=15, completion=4)
    result, requests, _ = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [
                (
                    200,
                    {},
                    provider_payload(content="not-json", observed_usage=first_usage),
                ),
                (200, {}, provider_payload(observed_usage=second_usage)),
            ],
        )
    )

    assert result.provider_attempts == 2
    assert len(requests) == 2
    assert result.usage.prompt_tokens == 30
    assert result.usage.prompt_cache_hit_tokens == 9
    assert result.usage.prompt_cache_miss_tokens == 21
    assert result.usage.completion_tokens == 7
    assert result.usage.total_tokens == 37
    assert result.estimated_cost_usd == Decimal("0.0000049252")


def test_timeout_attempt_does_not_invent_usage_or_cost(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    result, requests, _ = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [httpx.ReadTimeout("secret timeout", request=request), (200, {}, provider_payload())],
        )
    )

    assert result.provider_attempts == 2
    assert len(requests) == 2
    assert result.usage.prompt_tokens == 10
    assert result.usage.total_tokens == 13
    assert result.estimated_cost_usd == Decimal("0.0000016912")


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_transient_http_status_retries_to_success(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    status_code: int,
) -> None:
    result, requests, sleeps = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [
                (status_code, {}, {"error": {"message": "provider unavailable"}}),
                (200, {}, provider_payload()),
            ],
        )
    )

    assert result.provider_attempts == 2
    assert len(requests) == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    "exception_type",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError],
)
def test_transport_failure_retries_to_success(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    exception_type: type[httpx.RequestError],
) -> None:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    result, requests, _ = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [exception_type("transport failed", request=request), (200, {}, provider_payload())],
        )
    )

    assert result.provider_attempts == 2
    assert len(requests) == 2


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 404, 422])
def test_non_retryable_http_status_fails_after_one_call(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    status_code: int,
) -> None:
    error, requests, sleeps = asyncio.run(
        classify_expect_error(
            canonical_article,
            approved_source,
            [(status_code, {}, {"error": {"message": "do not expose this"}})],
        )
    )

    assert error.category is ClassificationErrorCategory.PROVIDER_HTTP
    assert error.retryable is False
    assert error.last_http_status == status_code
    assert error.provider_attempts == 1
    assert len(requests) == 1
    assert sleeps == []


def test_insufficient_system_resource_is_retryable_and_usage_is_aggregated(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    result, requests, _ = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [
                (
                    200,
                    {},
                    provider_payload(finish_reason="insufficient_system_resource"),
                ),
                (200, {}, provider_payload()),
            ],
        )
    )

    assert result.provider_attempts == 2
    assert len(requests) == 2
    assert result.usage.prompt_tokens == 20
    assert result.usage.total_tokens == 26


@pytest.mark.parametrize(
    ("finish_reason", "category"),
    [
        ("content_filter", ClassificationErrorCategory.CONTENT_FILTER),
        ("length", ClassificationErrorCategory.OUTPUT_TRUNCATED),
        ("tool_calls", ClassificationErrorCategory.UNEXPECTED_TOOL_CALL),
    ],
)
def test_non_retryable_finish_reasons_fail_once_and_never_supply_tools(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    finish_reason: str,
    category: ClassificationErrorCategory,
) -> None:
    error, requests, sleeps = asyncio.run(
        classify_expect_error(
            canonical_article,
            approved_source,
            [
                (
                    200,
                    {},
                    provider_payload(
                        finish_reason=finish_reason,
                        include_usage=False,
                    ),
                )
            ],
        )
    )

    assert error.category is category
    assert error.retryable is False
    assert error.provider_attempts == 1
    assert len(requests) == 1
    assert "tools" not in json.loads(requests[0].content)
    assert sleeps == []


@pytest.mark.parametrize(
    ("items", "category"),
    [
        (
            [(200, {}, provider_payload(content="   "))] * 3,
            ClassificationErrorCategory.EMPTY_CONTENT,
        ),
        (
            [(200, {}, provider_payload(content="not-json"))] * 3,
            ClassificationErrorCategory.INVALID_OUTPUT,
        ),
        (
            [(200, {}, "not-json-envelope")] * 3,
            ClassificationErrorCategory.MALFORMED_RESPONSE,
        ),
        (
            [(200, {}, provider_payload(include_usage=False))] * 3,
            ClassificationErrorCategory.INVALID_USAGE,
        ),
    ],
)
def test_retryable_response_failures_stop_at_maximum_three_attempts(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    items: Sequence[HandlerItem],
    category: ClassificationErrorCategory,
) -> None:
    error, requests, sleeps = asyncio.run(
        classify_expect_error(canonical_article, approved_source, items)
    )

    assert error.category is category
    assert error.retryable is True
    assert error.provider_attempts == 3
    assert len(requests) == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize(
    "invalid_usage",
    [
        usage(prompt=-1, hit=0, miss=-1),
        usage(prompt=10, hit=4, miss=6) | {"total_tokens": 999},
        usage() | {"prompt_tokens": "10"},
    ],
)
def test_invalid_usage_is_retried_and_never_aggregated(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    invalid_usage: dict[str, Any],
) -> None:
    result, _, _ = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [
                (200, {}, provider_payload(observed_usage=invalid_usage)),
                (200, {}, provider_payload()),
            ],
        )
    )

    assert result.provider_attempts == 2
    assert result.usage.prompt_tokens == 10
    assert result.usage.total_tokens == 13


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        ("7", 7.0),
        ("999", 30.0),
        ("Sat, 15 Aug 2026 12:00:09 GMT", 9.0),
    ],
)
def test_retry_after_is_parsed_and_bounded(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    retry_after: str,
    expected_delay: float,
) -> None:
    _, _, sleeps = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [
                (429, {"Retry-After": retry_after}, {"error": {}}),
                (200, {}, provider_payload()),
            ],
        )
    )

    assert sleeps == [expected_delay]


def test_generic_gateway_retry_after_is_ignored_in_favor_of_backoff(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    _, _, sleeps = asyncio.run(
        classify_with_items(
            canonical_article,
            approved_source,
            [
                (502, {"Retry-After": "9"}, {"error": {}}),
                (200, {}, provider_payload()),
            ],
        )
    )

    assert sleeps == [0.25]


def test_terminal_error_and_logs_do_not_leak_sensitive_values(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_values = [
        "unit-test-api-key",
        canonical_article.title,
        canonical_article.description,
        "raw-secret-provider-body",
    ]
    caplog.set_level(logging.INFO)
    error, _, _ = asyncio.run(
        classify_expect_error(
            canonical_article,
            approved_source,
            [(401, {}, {"error": {"message": "raw-secret-provider-body"}})],
        )
    )
    combined = str(error) + "\n" + caplog.text

    assert error.article_id == canonical_article.article_id
    assert error.last_http_status == 401
    assert "category=provider_http" in str(error)
    for secret in secret_values:
        assert secret is not None
        assert secret not in combined
    assert "unit-test-api-key" not in repr(DeepSeekSettings(api_key="unit-test-api-key"))


def test_environment_factory_requires_secret_and_valid_pricing_path() -> None:
    with pytest.raises(ClassificationConfigurationError, match="DEEPSEEK_API_KEY"):
        create_deepseek_classifier_from_environment({})
    with pytest.raises(ClassificationConfigurationError, match="pricing"):
        create_deepseek_classifier_from_environment(
            {
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_PRICING_CONFIG_PATH": "config/classification/missing.toml",
            }
        )


@pytest.mark.parametrize("max_attempts", [0, 4, True])
def test_invalid_attempt_configuration_fails_before_http(max_attempts: int) -> None:
    with pytest.raises(ClassificationConfigurationError, match="max_attempts"):
        DeepSeekV4FlashClassifier(
            DeepSeekSettings(api_key="test-key"),
            load_pricing_catalog(PRICING_PATH),
            max_attempts=max_attempts,
        )


def test_naive_injected_clock_fails_before_http(
    canonical_article: CanonicalArticle,
    approved_source: SourceConfig,
) -> None:
    requests: list[httpx.Request] = []

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(queued_handler([], requests))
        ) as client:
            classifier = DeepSeekV4FlashClassifier(
                DeepSeekSettings(api_key="test-key"),
                load_pricing_catalog(PRICING_PATH),
                client,
                clock=lambda: datetime(2026, 8, 15, 12, 0),
            )
            with pytest.raises(ClassificationConfigurationError, match="timezone-aware"):
                await classifier.classify(canonical_article, approved_source)

    asyncio.run(run())
    assert requests == []
