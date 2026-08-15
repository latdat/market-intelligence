"""Backend-neutral read boundary for shared user preferences."""

from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from market_intelligence.models import UserPreference


class UserPreferenceReadModel(BaseModel):
    """Strict immutable models returned by the user-preference read boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class UserPreferencePage(UserPreferenceReadModel):
    """One deterministic keyset page of validated user preferences."""

    items: tuple[UserPreference, ...]
    next_cursor: str | None = None

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        user_ids = tuple(item.user_id for item in self.items)

        if len(user_ids) != len(set(user_ids)):
            raise ValueError("items must not contain duplicate user_id values")
        if user_ids != tuple(sorted(user_ids)):
            raise ValueError("items must be ordered by user_id ascending")

        if self.next_cursor is not None:
            if not self.next_cursor.strip():
                raise ValueError("next_cursor must not be blank")
            if not self.items:
                raise ValueError("empty pages cannot have next_cursor")
            if self.next_cursor != user_ids[-1]:
                raise ValueError("next_cursor must equal the last user_id in items")

        return self


class UserPreferenceReadError(RuntimeError):
    """Sanitized failure for user-preference reads."""

    def __init__(self, operation: str, user_id: str | None = None) -> None:
        self.operation = operation
        self.user_id = user_id
        suffix = f" for user {user_id}" if user_id is not None else ""
        super().__init__(f"user preference read {operation} failed{suffix}")


class UserPreferenceReader(Protocol):
    """Read validated preferences without owning their persistence or write lifecycle."""

    def get(self, user_id: str) -> UserPreference | None:
        """Return one preference by user ID, or None when it does not exist."""
        ...

    def list_page(
        self,
        *,
        after_user_id: str | None = None,
        limit: int = 100,
    ) -> UserPreferencePage:
        """Return a bounded user_id-ascending keyset page.

        Concrete adapters must enforce the limit range: 1 <= limit <= 1000 inclusive,
        with a default of 100. Boolean limits are invalid.
        """
        ...
