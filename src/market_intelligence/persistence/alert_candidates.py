"""Durable alert-candidate persistence contracts owned by the data layer."""

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from market_intelligence.models import AlertCandidate


class AlertCandidatePersistenceModel(BaseModel):
    """Strict immutable models returned by the alert-candidate persistence boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AlertCandidateSaveOutcome(StrEnum):
    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class AlertCandidateSaveResult(AlertCandidatePersistenceModel):
    outcome: AlertCandidateSaveOutcome
    candidate: AlertCandidate


class AlertCandidatePersistenceError(RuntimeError):
    """Sanitized failure that never includes candidate payload or credentials."""

    def __init__(self, candidate_id: str, operation: str) -> None:
        self.candidate_id = candidate_id
        self.operation = operation
        super().__init__(f"alert candidate persistence {operation} failed for {candidate_id}")


class AlertCandidateRepository(Protocol):
    """Durable first-write-wins boundary for logical user/article alert candidates."""

    def save(self, candidate: AlertCandidate) -> AlertCandidateSaveResult:
        """Persist one candidate atomically, or return the existing first-seen snapshot."""
        ...

    def get(self, candidate_id: str) -> AlertCandidate | None:
        """Return one persisted candidate by stable candidate ID, or None."""
        ...
