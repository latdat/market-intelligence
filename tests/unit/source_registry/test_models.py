import tomllib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from market_intelligence.source_registry import (
    ContentScope,
    CostType,
    HealthStatus,
    Market,
    SourceConfig,
    SourceDefinition,
    SourceOperationalState,
)

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "source_registry" / "valid_source.toml"
DYNAMIC_STATE_FIELDS = {"health_status", "last_success_at", "last_failure_at"}


def valid_source_config_data() -> dict[str, object]:
    return {
        "source_id": "us_federal_register",
        "name": "Federal Register",
        "market": "US",
        "language": "en",
        "source_type": "GOVERNMENT",
        "authority_level": "PRIMARY",
        "domains": ["LAW_POLICY"],
        "content_scope": "FORMAL_REGULATORY_LEGAL",
        "acquisition": {
            "method": "REST_API",
            "endpoint_url": "https://www.federalregister.gov/api/v1/documents.json",
            "poll_interval_minutes": 15,
            "rate_limit": None,
        },
        "rights": {
            "can_fetch": True,
            "can_store_metadata": True,
            "can_store_full_text": "REVIEWED",
            "can_ai_process": False,
            "can_show_snippet": "REVIEWED",
            "can_redistribute_full_text": False,
            "rights_review_status": "APPROVED",
        },
        "cost": {"type": "FREE", "monthly_fixed_usd": 0},
        "priority": 100,
    }


def nested_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], payload[key])


def test_valid_source_config() -> None:
    config = SourceConfig.model_validate(valid_source_config_data())

    assert config.source_id == "us_federal_register"
    assert config.market is Market.US
    assert config.content_scope is ContentScope.FORMAL_REGULATORY_LEGAL
    assert config.acquisition.rate_limit is None
    assert config.cost.type is CostType.FREE


def test_valid_source_definition() -> None:
    config = SourceConfig.model_validate(valid_source_config_data())
    definition = SourceDefinition.from_parts(config)

    validated = SourceDefinition.model_validate(definition.model_dump(mode="json"))

    assert validated == definition
    assert validated.health_status is HealthStatus.UNKNOWN


def test_invalid_market_is_rejected() -> None:
    payload = valid_source_config_data()
    payload["market"] = "UK"

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize("source_id", ["US_federal_register", "federal-register", ""])
def test_invalid_source_id_is_rejected(source_id: str) -> None:
    payload = valid_source_config_data()
    payload["source_id"] = source_id

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_missing_source_id_is_rejected() -> None:
    payload = valid_source_config_data()
    del payload["source_id"]

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize("domains", [[], ["LAW_POLICY", "LAW_POLICY"]])
def test_empty_or_duplicate_domains_are_rejected(domains: list[str]) -> None:
    payload = valid_source_config_data()
    payload["domains"] = domains

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize("poll_interval", [0, -1, "15", True])
def test_invalid_poll_interval_is_rejected(poll_interval: object) -> None:
    payload = valid_source_config_data()
    nested_dict(payload, "acquisition")["poll_interval_minutes"] = poll_interval

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize(
    "endpoint_url",
    ["", "ftp://example.org/feed.xml", "example.org/feed.xml"],
)
def test_invalid_acquisition_endpoint_is_rejected(endpoint_url: str) -> None:
    payload = valid_source_config_data()
    nested_dict(payload, "acquisition")["endpoint_url"] = endpoint_url

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_missing_acquisition_endpoint_is_rejected() -> None:
    payload = valid_source_config_data()
    del nested_dict(payload, "acquisition")["endpoint_url"]

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize(
    "rate_limit",
    [
        {"max_requests": 0, "period_seconds": 60},
        {"max_requests": 10, "period_seconds": 0},
        {"max_requests": 10},
    ],
)
def test_invalid_rate_limit_is_rejected(rate_limit: dict[str, object]) -> None:
    payload = valid_source_config_data()
    nested_dict(payload, "acquisition")["rate_limit"] = rate_limit

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_unknown_fields_are_rejected() -> None:
    payload = valid_source_config_data()
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize(
    "content_scope",
    ["EDITORIAL_NEWS", "FORMAL_REGULATORY_LEGAL"],
)
def test_content_scope_accepts_contract_values(content_scope: str) -> None:
    payload = valid_source_config_data()
    payload["content_scope"] = content_scope

    config = SourceConfig.model_validate(payload)

    assert config.content_scope.value == content_scope


def test_missing_content_scope_is_rejected() -> None:
    payload = valid_source_config_data()
    del payload["content_scope"]

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_unknown_content_scope_is_rejected() -> None:
    payload = valid_source_config_data()
    payload["content_scope"] = "OTHER"

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize("decision", [True, False, "REVIEWED"])
def test_rights_decision_accepts_contract_values(decision: object) -> None:
    payload = valid_source_config_data()
    nested_dict(payload, "rights")["can_store_full_text"] = decision

    config = SourceConfig.model_validate(payload)

    assert config.rights.can_store_full_text == decision


@pytest.mark.parametrize("decision", [1, "ALLOWED", None])
def test_invalid_rights_decision_is_rejected(decision: object) -> None:
    payload = valid_source_config_data()
    nested_dict(payload, "rights")["can_store_full_text"] = decision

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


@pytest.mark.parametrize("decision", ["REVIEWED", "true", 1, 0, None])
def test_can_ai_process_rejects_non_boolean_values(decision: object) -> None:
    payload = valid_source_config_data()
    nested_dict(payload, "rights")["can_ai_process"] = decision

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_missing_can_ai_process_is_rejected() -> None:
    payload = valid_source_config_data()
    del nested_dict(payload, "rights")["can_ai_process"]

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_rights_are_not_subject_to_unapproved_policy_inference() -> None:
    payload = valid_source_config_data()
    rights = nested_dict(payload, "rights")
    rights["can_store_full_text"] = False
    rights["can_redistribute_full_text"] = True
    rights["rights_review_status"] = "PENDING"

    config = SourceConfig.model_validate(payload)

    assert config.rights.can_redistribute_full_text is True
    assert config.rights.can_store_full_text is False


@pytest.mark.parametrize(
    "cost",
    [
        {"type": "FREE", "monthly_fixed_usd": -1},
        {"type": "FREE", "monthly_fixed_usd": 1},
        {"type": "FIXED_MONTHLY", "monthly_fixed_usd": 0},
        {"type": "VARIABLE", "monthly_fixed_usd": 1},
        {"type": "FREE", "monthly_fixed_usd": True},
    ],
)
def test_invalid_cost_is_rejected(cost: dict[str, object]) -> None:
    payload = valid_source_config_data()
    payload["cost"] = cost

    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_fixed_monthly_cost_requires_positive_amount() -> None:
    payload = valid_source_config_data()
    payload["cost"] = {"type": "FIXED_MONTHLY", "monthly_fixed_usd": 19.5}

    config = SourceConfig.model_validate(payload)

    assert config.cost.monthly_fixed_usd == 19.5


def test_language_is_trimmed_and_must_be_non_empty() -> None:
    payload = valid_source_config_data()
    payload["language"] = " zh-CN "

    assert SourceConfig.model_validate(payload).language == "zh-CN"

    payload["language"] = "   "
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(payload)


def test_operational_state_defaults() -> None:
    state = SourceOperationalState()

    assert state.health_status is HealthStatus.UNKNOWN
    assert state.last_success_at is None
    assert state.last_failure_at is None


def test_timezone_aware_timestamps_are_normalized_to_utc() -> None:
    source_time = datetime(2026, 8, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4)))

    state = SourceOperationalState(last_success_at=source_time)

    assert state.last_success_at == datetime(2026, 8, 14, 14, 0, tzinfo=UTC)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceOperationalState(last_success_at=datetime(2026, 8, 14, 14, 0))


def test_source_config_and_state_build_flat_definition() -> None:
    config = SourceConfig.model_validate(valid_source_config_data())
    state = SourceOperationalState(
        health_status="HEALTHY",
        last_success_at="2026-08-14T14:05:00Z",
    )

    definition = SourceDefinition.from_parts(config, state)
    serialized = definition.model_dump(mode="json")
    config_serialized = config.model_dump(mode="json")

    assert config_serialized["content_scope"] == "FORMAL_REGULATORY_LEGAL"
    assert serialized["acquisition_method"] == "REST_API"
    assert serialized["poll_interval_minutes"] == 15
    assert serialized["rate_limit"] is None
    assert "endpoint_url" not in serialized
    assert serialized["health_status"] == "HEALTHY"
    assert "acquisition" not in serialized
    assert "content_scope" not in serialized


def test_source_definition_json_round_trip_is_stable() -> None:
    definition = SourceDefinition.from_parts(
        SourceConfig.model_validate(valid_source_config_data())
    )

    restored = SourceDefinition.model_validate_json(definition.model_dump_json())

    assert restored == definition


def test_toml_fixture_validates_as_static_source_config() -> None:
    with FIXTURE_PATH.open("rb") as fixture_file:
        payload = tomllib.load(fixture_file)

    config = SourceConfig.model_validate(payload)

    assert config.source_id == "us_federal_register"
    assert config.acquisition.method.value == "REST_API"


def test_toml_fixture_excludes_dynamic_operational_state() -> None:
    with FIXTURE_PATH.open("rb") as fixture_file:
        payload = tomllib.load(fixture_file)

    assert DYNAMIC_STATE_FIELDS.isdisjoint(payload)
