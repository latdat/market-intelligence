import json

import pytest
from pydantic import ValidationError

from market_intelligence.classification import Topic
from market_intelligence.models import UserPreference
from market_intelligence.source_registry import Domain, Market


def preference_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "user_id": "user-1",
        "markets": ["VN", "US"],
        "categories": ["TECHNOLOGY", "FINANCE"],
        "topics": ["AI", "BANKING"],
        "muted_source_ids": ["source-2", "source-1"],
        "muted_topics": ["REGULATION"],
        "breaking_alert_enabled": True,
        "hourly_update_enabled": True,
        "daily_digest_enabled": False,
    }
    values.update(overrides)
    return values


def validate_json(**overrides: object) -> UserPreference:
    return UserPreference.model_validate_json(json.dumps(preference_payload(**overrides)))


def test_valid_shared_preference_parses_controlled_codes_and_preserves_order() -> None:
    preference = validate_json()

    assert preference.user_id == "user-1"
    assert preference.markets == (Market.VN, Market.US)
    assert preference.categories == (Domain.TECHNOLOGY, Domain.FINANCE)
    assert preference.topics == (Topic.AI, Topic.BANKING)
    assert preference.muted_source_ids == ("source-2", "source-1")
    assert preference.muted_topics == (Topic.REGULATION,)


def test_empty_interest_and_mute_collections_are_valid() -> None:
    preference = validate_json(
        markets=[],
        categories=[],
        topics=[],
        muted_source_ids=[],
        muted_topics=[],
    )

    assert preference.markets == ()
    assert preference.categories == ()
    assert preference.topics == ()


def test_all_current_controlled_codes_are_accepted() -> None:
    preference = validate_json(
        markets=[value.value for value in Market],
        categories=[value.value for value in Domain],
        topics=[value.value for value in Topic],
        muted_topics=[value.value for value in Topic],
    )

    assert preference.markets == tuple(Market)
    assert preference.categories == tuple(Domain)
    assert preference.topics == tuple(Topic)
    assert preference.muted_topics == tuple(Topic)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("markets", ["APAC"]),
        ("categories", ["SPORTS"]),
        ("topics", ["CRYPTO"]),
        ("muted_topics", ["CRYPTO"]),
    ],
)
def test_unknown_controlled_codes_are_rejected(field_name: str, invalid_value: object) -> None:
    with pytest.raises(ValidationError):
        validate_json(**{field_name: invalid_value})


@pytest.mark.parametrize(
    ("field_name", "duplicate_value"),
    [
        ("markets", ["US", "US"]),
        ("categories", ["FINANCE", "FINANCE"]),
        ("topics", ["AI", "AI"]),
        ("muted_source_ids", ["source-1", "source-1"]),
        ("muted_topics", ["AI", "AI"]),
    ],
)
def test_duplicate_collection_values_are_rejected(
    field_name: str,
    duplicate_value: object,
) -> None:
    with pytest.raises(ValidationError, match=f"{field_name} must not contain duplicates"):
        validate_json(**{field_name: duplicate_value})


@pytest.mark.parametrize("user_id", ["", " ", "\t\n"])
def test_blank_user_id_is_rejected(user_id: str) -> None:
    with pytest.raises(ValidationError):
        validate_json(user_id=user_id)


@pytest.mark.parametrize("source_id", ["", " ", "\t"])
def test_blank_muted_source_id_is_rejected(source_id: str) -> None:
    with pytest.raises(ValidationError):
        validate_json(muted_source_ids=[source_id])


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("breaking_alert_enabled", "true"),
        ("hourly_update_enabled", 1),
        ("daily_digest_enabled", 0),
    ],
)
def test_notification_flags_are_strict_booleans(field_name: str, invalid_value: object) -> None:
    with pytest.raises(ValidationError):
        validate_json(**{field_name: invalid_value})


def test_unknown_fields_are_rejected() -> None:
    payload = preference_payload()
    payload["timezone"] = "Asia/Ho_Chi_Minh"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        UserPreference.model_validate_json(json.dumps(payload))


def test_all_contract_fields_are_required() -> None:
    payload = preference_payload()
    del payload["daily_digest_enabled"]

    with pytest.raises(ValidationError):
        UserPreference.model_validate_json(json.dumps(payload))
