"""Bounded one-shot GDELT discovery runner (GDELT-002B).

The runner lives in ``pipelines`` alongside the other orchestrators: it composes the
``discovery`` admission boundary with the ``persistence`` aggregate boundary, and neither of
those packages may depend on it.

The runner owns the retrieval window, drives query cells strictly sequentially, reuses the
GDELT-002A admission boundary unchanged, and folds every outcome into the existing bounded
discovery aggregates. It stops there: nothing here promotes a candidate into ``articles``,
classification, matching, benchmark evidence, or notification.

There is deliberately no claim/lease/heartbeat lifecycle, no durable cursor, and no per-sighting
storage table. One invocation is bounded by the configured window, page limits, split depth, and
retry budget.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from market_intelligence.discovery import (
    DiscoveryCandidate,
    GdeltDocClient,
    GdeltProviderError,
    GdeltQueryConfigError,
    GdeltQueryRejectedError,
    GdeltQuerySpec,
    GdeltWindowStatus,
    ObservationStatus,
    PublisherRoute,
    admit_candidate,
    build_source_index,
    dedupe_candidates,
    floor_to_utc_second,
)
from market_intelligence.normalization import ArticleNormalizationError, canonicalize_url
from market_intelligence.persistence import (
    DiscoveryObservation,
    DiscoveryObservationKey,
    DiscoveryObservationRepository,
    DiscoveryObservationSample,
)
from market_intelligence.source_registry import Domain, Market, SourceConfig

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GdeltCellRunStatus(StrEnum):
    """Provider execution outcome for one query cell.

    This axis is deliberately separate from :class:`ObservationStatus`, which describes what
    happened to one *candidate* at the admission gate. Provider execution failures never become
    admission outcomes.
    """

    COMPLETE = "COMPLETE"
    SATURATED_INCOMPLETE = "SATURATED_INCOMPLETE"
    QUERY_REJECTED = "QUERY_REJECTED"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class GdeltRunStopReason(StrEnum):
    """Why a run stopped before attempting every configured cell."""

    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class GdeltRunnerConfig(BaseModel):
    """Validated bounded runtime settings for one GDELT discovery run."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    lookback_minutes: int = Field(default=60, ge=1, le=1_440)


@dataclass(frozen=True, slots=True)
class GdeltCellRunResult:
    """Immutable per-cell counters and provider execution status."""

    query_id: str
    market: Market
    domain: Domain
    window_start: datetime
    window_end: datetime
    status: GdeltCellRunStatus
    request_count: int = 0
    raw_result_count: int = 0
    valid_record_count: int = 0
    invalid_record_count: int = 0
    unique_candidate_count: int = 0
    admitted_count: int = 0
    unknown_count: int = 0
    ambiguous_route_count: int = 0
    rights_metadata_denied_count: int = 0
    identity_incompatible_count: int = 0


@dataclass(frozen=True, slots=True)
class GdeltDiscoveryRunResult:
    """Immutable result of one bounded GDELT discovery invocation."""

    run_started_at: datetime
    window_start: datetime
    window_end: datetime
    cell_results: tuple[GdeltCellRunResult, ...]
    stop_reason: GdeltRunStopReason | None = None

    @property
    def cells_attempted(self) -> int:
        return len(self.cell_results)

    @property
    def request_count(self) -> int:
        return sum(cell.request_count for cell in self.cell_results)

    @property
    def raw_result_count(self) -> int:
        return sum(cell.raw_result_count for cell in self.cell_results)

    @property
    def invalid_record_count(self) -> int:
        return sum(cell.invalid_record_count for cell in self.cell_results)

    @property
    def unique_candidate_count(self) -> int:
        return sum(cell.unique_candidate_count for cell in self.cell_results)

    @property
    def admitted_count(self) -> int:
        return sum(cell.admitted_count for cell in self.cell_results)


def build_observation_sample(candidate: DiscoveryCandidate) -> DiscoveryObservationSample | None:
    """Return a locator-only diagnostic sample, or ``None`` when one cannot be formed safely.

    Sampling is strictly best-effort. A candidate whose URL cannot be canonicalized still
    produces a persisted observation — it simply carries no sample — so diagnostic enrichment
    can never fail a run or drop a real sighting.
    """
    try:
        canonical_url = canonicalize_url(candidate.original_url)
    except ArticleNormalizationError:
        return None

    hostname = urlsplit(canonical_url).hostname
    if not hostname:
        return None
    return DiscoveryObservationSample(url=canonical_url, hostname=hostname)


class GdeltDiscoveryRunner:
    """Run bounded GDELT discovery cells and record their admission outcomes."""

    def __init__(
        self,
        client: GdeltDocClient,
        query_specs: Sequence[GdeltQuerySpec],
        routes: Sequence[PublisherRoute],
        sources: Sequence[SourceConfig],
        repository: DiscoveryObservationRepository,
        *,
        config: GdeltRunnerConfig | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        if not query_specs:
            # An empty catalog means GDELT discovery is disabled. Refusing construction keeps a
            # disabled deployment from producing a run result that looks like an ordinary
            # successful zero-cell discovery run.
            raise GdeltQueryConfigError("GDELT discovery is disabled: no query specs configured")

        self._client = client
        self._query_specs = tuple(query_specs)
        self._routes = tuple(routes)
        # Fails before any network access when routes or sources are mis-authored.
        self._source_by_id = build_source_index(sources)
        self._repository = repository
        self._config = config or GdeltRunnerConfig()
        self._clock = clock

    @property
    def config(self) -> GdeltRunnerConfig:
        return self._config

    async def run_once(
        self,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> GdeltDiscoveryRunResult:
        """Run every configured cell sequentially over one explicit bounded window."""
        run_started_at = self._current_utc_time()
        resolved_end = (
            floor_to_utc_second(window_end) if window_end is not None else run_started_at
        )
        resolved_start = (
            floor_to_utc_second(window_start)
            if window_start is not None
            else resolved_end - timedelta(minutes=self._config.lookback_minutes)
        )
        if resolved_start >= resolved_end:
            raise ValueError("window_start must be earlier than window_end")

        cell_results: list[GdeltCellRunResult] = []
        stop_reason: GdeltRunStopReason | None = None

        for spec in self._query_specs:
            cell_result, provider_failed = await self._run_cell(
                spec, resolved_start, resolved_end
            )
            cell_results.append(cell_result)
            if provider_failed:
                # The shared provider is unavailable: stop rather than hammer remaining cells.
                stop_reason = GdeltRunStopReason.PROVIDER_UNAVAILABLE
                break

        result = GdeltDiscoveryRunResult(
            run_started_at=run_started_at,
            window_start=resolved_start,
            window_end=resolved_end,
            cell_results=tuple(cell_results),
            stop_reason=stop_reason,
        )
        logger.info(
            "gdelt_discovery_run_completed",
            extra={
                "pipeline_stage": "secondary_discovery",
                "status": "STOPPED" if stop_reason is not None else "COMPLETED",
                "cells_configured": len(self._query_specs),
                "cells_attempted": result.cells_attempted,
                "request_count": result.request_count,
                "raw_result_count": result.raw_result_count,
                "unique_candidate_count": result.unique_candidate_count,
                "admitted_count": result.admitted_count,
                "stop_reason": stop_reason.value if stop_reason is not None else None,
            },
        )
        return result

    async def _run_cell(
        self,
        spec: GdeltQuerySpec,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[GdeltCellRunResult, bool]:
        try:
            outcome = await self._client.fetch_cell(spec, window_start, window_end)
        except GdeltQueryRejectedError:
            # One badly authored expression must not prevent the other cells from running.
            logger.error(
                "gdelt_cell_query_rejected",
                extra={
                    "query_id": spec.query_id,
                    "pipeline_stage": "secondary_discovery",
                    "status": "QUERY_REJECTED",
                },
            )
            return self._empty_cell_result(
                spec, window_start, window_end, GdeltCellRunStatus.QUERY_REJECTED
            ), False
        except GdeltProviderError as error:
            logger.error(
                "gdelt_cell_provider_failed",
                extra={
                    "query_id": spec.query_id,
                    "pipeline_stage": "secondary_discovery",
                    "status": "PROVIDER_FAILED",
                    "error_category": error.category,
                    "status_code": error.status_code,
                    "attempts": error.attempts,
                },
            )
            return self._empty_cell_result(
                spec, window_start, window_end, GdeltCellRunStatus.PROVIDER_FAILED
            ), True

        candidates = dedupe_candidates(outcome.candidates)
        status_counts: dict[ObservationStatus, int] = dict.fromkeys(ObservationStatus, 0)

        for candidate in candidates:
            decision = admit_candidate(candidate, self._routes, self._source_by_id)
            observation = DiscoveryObservation(
                key=DiscoveryObservationKey(
                    observation_day=candidate.observed_at.date(),
                    query_id=spec.query_id,
                    domain=spec.domain,
                    observation_status=decision.observation_status,
                ),
                observed_at=candidate.observed_at,
                sample=build_observation_sample(candidate),
            )
            # A persistence failure stops the run; aggregates already folded stay durable.
            await asyncio.to_thread(self._repository.record_observation, observation)
            status_counts[decision.observation_status] += 1

        cell_status = (
            GdeltCellRunStatus.COMPLETE
            if outcome.status is GdeltWindowStatus.COMPLETE
            else GdeltCellRunStatus.SATURATED_INCOMPLETE
        )
        return GdeltCellRunResult(
            query_id=spec.query_id,
            market=spec.market,
            domain=spec.domain,
            window_start=window_start,
            window_end=window_end,
            status=cell_status,
            request_count=outcome.request_count,
            raw_result_count=outcome.provider_record_count,
            valid_record_count=outcome.valid_record_count,
            invalid_record_count=outcome.invalid_record_count,
            unique_candidate_count=len(candidates),
            admitted_count=status_counts[ObservationStatus.ADMITTED],
            unknown_count=status_counts[ObservationStatus.UNKNOWN],
            ambiguous_route_count=status_counts[ObservationStatus.AMBIGUOUS_ROUTE],
            rights_metadata_denied_count=status_counts[ObservationStatus.RIGHTS_METADATA_DENIED],
            identity_incompatible_count=status_counts[ObservationStatus.IDENTITY_INCOMPATIBLE],
        ), False

    @staticmethod
    def _empty_cell_result(
        spec: GdeltQuerySpec,
        window_start: datetime,
        window_end: datetime,
        status: GdeltCellRunStatus,
    ) -> GdeltCellRunResult:
        return GdeltCellRunResult(
            query_id=spec.query_id,
            market=spec.market,
            domain=spec.domain,
            window_start=window_start,
            window_end=window_end,
            status=status,
        )

    def _current_utc_time(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("GDELT runner clock must return a timezone-aware timestamp")
        return floor_to_utc_second(value)
