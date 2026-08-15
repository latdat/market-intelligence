import pytest
from pydantic import ValidationError

from market_intelligence.models import UserPreference
from market_intelligence.persistence import UserPreferencePage, UserPreferenceReadError
from market_intelligence.source_registry import Market


def preference(user_id: str) -> UserPreference:
    return UserPreference(
        user_id=user_id,
        markets=(Market.US,),
        categories=(),
        topics=(),
        muted_source_ids=(),
        muted_topics=(),
        breaking_alert_enabled=True,
        hourly_update_enabled=True,
        daily_digest_enabled=False,
    )


def test_valid_page_preserves_user_id_order_and_cursor() -> None:
    page = UserPreferencePage(
        items=(preference("user-1"), preference("user-2")),
        next_cursor="user-2",
    )

    assert tuple(item.user_id for item in page.items) == ("user-1", "user-2")
    assert page.next_cursor == "user-2"


def test_terminal_non_empty_page_may_have_no_cursor() -> None:
    page = UserPreferencePage(items=(preference("user-1"),), next_cursor=None)

    assert page.next_cursor is None


def test_duplicate_user_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate user_id"):
        UserPreferencePage(items=(preference("user-1"), preference("user-1")))


def test_unsorted_page_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ordered by user_id ascending"):
        UserPreferencePage(items=(preference("user-2"), preference("user-1")))


def test_empty_page_cannot_have_cursor() -> None:
    with pytest.raises(ValidationError, match="empty pages cannot have next_cursor"):
        UserPreferencePage(items=(), next_cursor="user-1")


def test_cursor_must_equal_last_user_id() -> None:
    with pytest.raises(ValidationError, match="last user_id"):
        UserPreferencePage(items=(preference("user-1"), preference("user-2")), next_cursor="user-1")


@pytest.mark.parametrize("cursor", ["", " ", "\t"])
def test_blank_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(ValidationError, match="next_cursor must not be blank"):
        UserPreferencePage(items=(preference("user-1"),), next_cursor=cursor)


def test_read_error_is_sanitized_and_retains_operation_context() -> None:
    error = UserPreferenceReadError("get", "user-1")

    assert error.operation == "get"
    assert error.user_id == "user-1"
    assert str(error) == "user preference read get failed for user user-1"
