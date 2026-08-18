"""Offline deterministic tests for Matching Runner v1 core orchestration."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import ClassifiedArticle, Topic
from market_intelligence.matching import NORMAL_FRESHNESS, build_alert_candidate_id
from market_intelligence.models import AlertCandidate, UserPreference
from market_intelligence.persistence import (
    AlertCandidatePersistenceError,
    AlertCandidateSaveOutcome,
    AlertCandidateSaveResult,
    MatchingWorkItem,
    MatchingWorkPage,
    MatchingWorkReadError,
    UserPreferencePage,
    UserPreferenceReadError,
)
from market_intelligence.pipelines import (
    MatchingErrorCategory,
    MatchingRunError,
    MatchingRunner,
    MatchingRunnerConfig,
    MatchingStage,
)
from market_intelligence.source_registry import Domain, Market

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
TARGET_VERSION = "classification-v2"


# --------------------------------------------------------------------------------------
# Deterministic offline fakes
# --------------------------------------------------------------------------------------


class FakeClock:
    """Monotonic injected clock that records every read."""

    def __init__(self, start: datetime = NOW, step: timedelta = timedelta(seconds=1)) -> None:
        self._current = start
        self._step = step
        self.reads: list[datetime] = []

    def __call__(self) -> datetime:
        value = self._current
        self._current += self._step
        self.reads.append(value)
        return value


class FakeUserPreferenceReader:
    """In-memory backend-neutral preference reader with bounded keyset pages."""

    def __init__(
        self,
        preferences: Iterable[UserPreference],
        *,
        page_size: int = 100,
        fail_on_call: int | None = None,
    ) -> None:
        self._preferences = tuple(sorted(preferences, key=lambda item: item.user_id))
        self._page_size = page_size
        self._fail_on_call = fail_on_call
        self.calls: list[tuple[str | None, int]] = []

    def get(self, user_id: str) -> UserPreference | None:
        for preference in self._preferences:
            if preference.user_id == user_id:
                return preference
        return None

    def list_page(
        self,
        *,
        after_user_id: str | None = None,
        limit: int = 100,
    ) -> UserPreferencePage:
        if isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        self.calls.append((after_user_id, limit))
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise UserPreferenceReadError("list_page")

        remaining = [
            preference
            for preference in self._preferences
            if after_user_id is None or preference.user_id > after_user_id
        ]
        page_size = min(limit, self._page_size)
        items = tuple(remaining[:page_size])
        has_more = len(remaining) > len(items)
        return UserPreferencePage(
            items=items,
            next_cursor=items[-1].user_id if has_more and items else None,
        )


class FakeMatchingWorkReader:
    """In-memory reader implementing the documented discovery contract."""

    def __init__(
        self,
        items: Iterable[MatchingWorkItem],
        *,
        page_size: int = 100,
        fail_on_call: int | None = None,
        over_select: bool = False,
    ) -> None:
        self._items = tuple(sorted(items, key=lambda item: item.article_id))
        self._page_size = page_size
        self._fail_on_call = fail_on_call
        self._over_select = over_select
        self.calls: list[dict[str, object]] = []

    def list_page(
        self,
        *,
        classifier_version: str,
        run_cutoff: datetime,
        freshness_cutoff: datetime,
        after_article_id: str | None = None,
        limit: int = 100,
    ) -> MatchingWorkPage:
        if isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        for name, cutoff in (("run_cutoff", run_cutoff), ("freshness_cutoff", freshness_cutoff)):
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError(f"{name} must include timezone information")
        self.calls.append(
            {
                "classifier_version": classifier_version,
                "run_cutoff": run_cutoff,
                "freshness_cutoff": freshness_cutoff,
                "after_article_id": after_article_id,
                "limit": limit,
            }
        )
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise MatchingWorkReadError("list_page")

        remaining = [
            item
            for item in self._items
            if (after_article_id is None or item.article_id > after_article_id)
            and item.classification.classifier_version == classifier_version
            and item.classification.classified_at <= run_cutoff
            and (self._over_select or self._is_fresh_superset(item, freshness_cutoff))
        ]
        page_size = min(limit, self._page_size)
        items = tuple(remaining[:page_size])
        has_more = len(remaining) > len(items)
        return MatchingWorkPage(
            items=items,
            next_cursor=items[-1].article_id if has_more and items else None,
        )

    @staticmethod
    def _is_fresh_superset(item: MatchingWorkItem, freshness_cutoff: datetime) -> bool:
        # Conservative superset: never filter on discovered_at alone.
        published_at = item.article.published_at
        return item.article.discovered_at >= freshness_cutoff or (
            published_at is not None and published_at >= freshness_cutoff
        )


class FakeAlertCandidateRepository:
    """First-write-wins in-memory DE-012 boundary keyed by (user_id, article_id)."""

    def __init__(self, *, fail_after_saves: int | None = None) -> None:
        self._by_candidate_id: dict[str, AlertCandidate] = {}
        self._by_logical_key: dict[tuple[str, str], str] = {}
        self.fail_after_saves = fail_after_saves
        self.save_attempts = 0

    def save(self, candidate: AlertCandidate) -> AlertCandidateSaveResult:
        self.save_attempts += 1
        if self.fail_after_saves is not None and self.save_attempts > self.fail_after_saves:
            raise AlertCandidatePersistenceError(candidate.candidate_id, "save")

        logical_key = (candidate.user_id, candidate.article_id)
        existing_id = self._by_logical_key.get(logical_key)
        if existing_id is not None:
            return AlertCandidateSaveResult(
                outcome=AlertCandidateSaveOutcome.ALREADY_EXISTS,
                candidate=self._by_candidate_id[existing_id],
            )

        self._by_logical_key[logical_key] = candidate.candidate_id
        self._by_candidate_id[candidate.candidate_id] = candidate
        return AlertCandidateSaveResult(
            outcome=AlertCandidateSaveOutcome.CREATED,
            candidate=candidate,
        )

    def get(self, candidate_id: str) -> AlertCandidate | None:
        return self._by_candidate_id.get(candidate_id)

    @property
    def logical_keys(self) -> set[tuple[str, str]]:
        return set(self._by_logical_key)

    @property
    def candidates(self) -> tuple[AlertCandidate, ...]:
        return tuple(self._by_candidate_id.values())


# --------------------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------------------


def article(article_id: str = "article-1", **overrides: object) -> CanonicalArticle:
    values: dict[str, object] = {
        "article_id": article_id,
        "source_id": "us_test_source",
        "source_item_id": f"item-{article_id}",
        "url": f"https://example.test/{article_id}",
        "canonical_url": f"https://example.test/{article_id}",
        "title": "Test article",
        "description": "Metadata-only description",
        "language": "en",
        "market": Market.US,
        "published_at": NOW - timedelta(hours=1),
        "discovered_at": NOW - timedelta(minutes=30),
        "content_hash": f"hash-{article_id}",
    }
    values.update(overrides)
    return CanonicalArticle(**values)


def classification(article_id: str = "article-1", **overrides: object) -> ClassifiedArticle:
    values: dict[str, object] = {
        "article_id": article_id,
        "classifier_version": TARGET_VERSION,
        "is_relevant": True,
        "markets": (Market.US,),
        "category": Domain.TECHNOLOGY,
        "topics": (Topic.AI,),
        "confidence": 0.9,
        "classified_at": NOW - timedelta(minutes=10),
    }
    values.update(overrides)
    return ClassifiedArticle(**values)


def preference(user_id: str = "user-1", **overrides: object) -> UserPreference:
    values: dict[str, object] = {
        "user_id": user_id,
        "markets": (Market.US,),
        "categories": (),
        "topics": (),
        "muted_source_ids": (),
        "muted_topics": (),
        "breaking_alert_enabled": True,
        "hourly_update_enabled": True,
        "daily_digest_enabled": True,
    }
    values.update(overrides)
    return UserPreference(**values)


def work_item(
    article_id: str = "article-1",
    *,
    article_overrides: dict[str, object] | None = None,
    classification_overrides: dict[str, object] | None = None,
) -> MatchingWorkItem:
    return MatchingWorkItem(
        article=article(article_id, **(article_overrides or {})),
        classification=classification(article_id, **(classification_overrides or {})),
    )


def build_runner(
    *,
    work_reader: FakeMatchingWorkReader,
    preference_reader: FakeUserPreferenceReader,
    repository: FakeAlertCandidateRepository,
    clock: FakeClock | None = None,
    config: MatchingRunnerConfig | None = None,
) -> MatchingRunner:
    return MatchingRunner(
        work_reader,
        preference_reader,
        repository,
        config=config or MatchingRunnerConfig(target_classifier_version=TARGET_VERSION),
        clock=clock or FakeClock(),
    )


# --------------------------------------------------------------------------------------
# Basic matching
# --------------------------------------------------------------------------------------


def test_matching_work_item_and_matching_preference_creates_one_candidate() -> None:
    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()]),
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    )

    result = runner.run_once()

    assert result.work_items_seen == 1
    assert result.preferences_loaded == 1
    assert result.pairs_evaluated == 1
    assert result.candidate_created_count == 1
    assert result.candidate_already_exists_count == 0
    assert result.no_match_count == 0
    assert repository.logical_keys == {("user-1", "article-1")}
    assert repository.candidates[0].candidate_id == build_alert_candidate_id(
        user_id="user-1",
        article_id="article-1",
    )


def test_non_matching_preference_saves_nothing() -> None:
    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()]),
        preference_reader=FakeUserPreferenceReader([preference(markets=(Market.CN,))]),
        repository=repository,
    )

    result = runner.run_once()

    assert result.pairs_evaluated == 1
    assert result.no_match_count == 1
    assert result.candidate_created_count == 0
    assert repository.save_attempts == 0
    assert repository.candidates == ()


def test_empty_preference_set_completes_without_saves() -> None:
    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()]),
        preference_reader=FakeUserPreferenceReader([]),
        repository=repository,
    )

    result = runner.run_once()

    assert result.preferences_loaded == 0
    assert result.work_items_seen == 1
    assert result.pairs_evaluated == 0
    assert result.candidate_created_count == 0
    assert repository.save_attempts == 0


# --------------------------------------------------------------------------------------
# Idempotent replay and candidate identity
# --------------------------------------------------------------------------------------


def test_replaying_the_same_work_returns_already_exists_and_one_logical_candidate() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader([work_item()])
    preference_reader = FakeUserPreferenceReader([preference()])

    first = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
    ).run_once()
    second = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
        clock=FakeClock(NOW + timedelta(minutes=5)),
    ).run_once()

    assert first.candidate_created_count == 1
    assert first.candidate_already_exists_count == 0
    assert second.candidate_created_count == 0
    assert second.candidate_already_exists_count == 1
    assert len(repository.candidates) == 1
    assert repository.logical_keys == {("user-1", "article-1")}


def test_recomputed_candidate_never_creates_a_second_logical_identity() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader([work_item()])

    build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    ).run_once()
    first_snapshot = repository.candidates[0]

    # Rule-level recomputation: different matched_at and different match reasons.
    replay = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader(
            [preference(markets=(Market.US,), categories=(Domain.TECHNOLOGY,), topics=(Topic.AI,))]
        ),
        repository=repository,
        clock=FakeClock(NOW + timedelta(hours=1)),
    ).run_once()

    assert replay.candidate_already_exists_count == 1
    assert replay.candidate_created_count == 0
    assert len(repository.candidates) == 1
    persisted = repository.candidates[0]
    assert persisted.matched_at == first_snapshot.matched_at
    assert persisted.match_reasons == first_snapshot.match_reasons
    assert persisted.relevance_score == first_snapshot.relevance_score


def test_already_exists_is_a_successful_outcome_not_an_exception() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader([work_item()])
    preference_reader = FakeUserPreferenceReader([preference()])
    build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
    ).run_once()

    result = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
        clock=FakeClock(NOW + timedelta(minutes=1)),
    ).run_once()

    assert result.candidate_already_exists_count == 1
    assert result.no_match_count == 0


# --------------------------------------------------------------------------------------
# Classifier lineage
# --------------------------------------------------------------------------------------


def test_only_the_exact_target_classifier_version_is_processed() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [
            work_item(
                "article-1",
                classification_overrides={"classifier_version": "classification-v1"},
            ),
            work_item("article-2", classification_overrides={"classifier_version": "classification-v2"}),
        ]
    )
    runner = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    )

    result = runner.run_once()

    assert work_reader.calls[0]["classifier_version"] == TARGET_VERSION
    assert result.work_items_seen == 1
    assert repository.logical_keys == {("user-1", "article-2")}


def test_work_item_outside_the_target_lineage_stops_the_run() -> None:
    # An adapter that ignores the requested lineage is a contract violation, not a skip.
    class LineageIgnoringWorkReader(FakeMatchingWorkReader):
        def list_page(self, *, classifier_version: str, **kwargs: object) -> MatchingWorkPage:
            return super().list_page(classifier_version="classification-v1", **kwargs)  # type: ignore[arg-type]

    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=LineageIgnoringWorkReader(
            [
                work_item(
                    "article-1",
                    classification_overrides={"classifier_version": "classification-v1"},
                )
            ]
        ),
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    )

    with pytest.raises(MatchingRunError) as error:
        runner.run_once()

    assert error.value.stage is MatchingStage.WORK_DISCOVERY
    assert error.value.category is MatchingErrorCategory.CONTRACT_VIOLATION
    assert error.value.classifier_version == "classification-v1"
    assert repository.save_attempts == 0


# --------------------------------------------------------------------------------------
# run_cutoff discovery snapshot
# --------------------------------------------------------------------------------------


def test_run_cutoff_excludes_work_classified_after_the_snapshot() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [
            work_item(
                "article-1",
                classification_overrides={"classified_at": NOW + timedelta(minutes=30)},
            )
        ]
    )
    preference_reader = FakeUserPreferenceReader([preference()])

    current = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
    ).run_once()

    assert current.run_cutoff == NOW
    assert current.work_items_seen == 0
    assert repository.save_attempts == 0

    # The next scheduled run has a later snapshot and picks the same work up.
    later = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
        clock=FakeClock(NOW + timedelta(hours=1)),
    ).run_once()

    assert later.work_items_seen == 1
    assert later.candidate_created_count == 1


def test_run_cutoff_is_never_used_as_matched_at() -> None:
    repository = FakeAlertCandidateRepository()
    clock = FakeClock()
    result = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()]),
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
        clock=clock,
    ).run_once()

    assert result.run_cutoff == NOW
    assert repository.candidates[0].matched_at != result.run_cutoff
    assert repository.candidates[0].matched_at > result.run_cutoff


# --------------------------------------------------------------------------------------
# Freshness discovery superset
# --------------------------------------------------------------------------------------


def test_freshness_cutoff_is_derived_from_run_cutoff_and_normal_freshness() -> None:
    work_reader = FakeMatchingWorkReader([work_item()])
    result = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=FakeAlertCandidateRepository(),
    ).run_once()

    assert result.freshness_cutoff == NOW - NORMAL_FRESHNESS
    assert work_reader.calls[0]["freshness_cutoff"] == NOW - NORMAL_FRESHNESS


def test_stale_discovered_at_with_fresh_published_at_stays_in_the_discovery_superset() -> None:
    # Regression guard: CanonicalArticle does not guarantee published_at <= discovered_at,
    # so a discovered_at-only prefilter would drop eligible work.
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [
            work_item(
                "article-1",
                article_overrides={
                    "discovered_at": NOW - timedelta(hours=48),
                    "published_at": NOW - timedelta(hours=6),
                },
            )
        ]
    )
    result = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    ).run_once()

    assert result.work_items_seen == 1
    assert result.candidate_created_count == 1
    assert repository.logical_keys == {("user-1", "article-1")}


def test_anomalous_future_published_at_is_over_selected_then_rejected_by_the_matcher() -> None:
    # The reader may over-select; match_article() stays the semantic freshness authority.
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [
            work_item(
                "article-1",
                article_overrides={
                    "discovered_at": NOW - timedelta(hours=48),
                    "published_at": NOW + timedelta(hours=2),
                },
            )
        ]
    )
    result = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    ).run_once()

    assert result.work_items_seen == 1
    assert result.pairs_evaluated == 1
    assert result.no_match_count == 1
    assert result.candidate_created_count == 0
    assert repository.save_attempts == 0


def test_genuinely_stale_over_selected_work_is_rejected_by_the_matcher() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [
            work_item(
                "article-1",
                article_overrides={
                    "discovered_at": NOW - timedelta(days=3),
                    "published_at": NOW - timedelta(days=3),
                },
            )
        ],
        over_select=True,
    )
    result = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    ).run_once()

    assert result.work_items_seen == 1
    assert result.no_match_count == 1
    assert repository.save_attempts == 0


# --------------------------------------------------------------------------------------
# Per-item matched_at
# --------------------------------------------------------------------------------------


def test_each_evaluation_reads_its_own_matched_at_from_the_injected_clock() -> None:
    repository = FakeAlertCandidateRepository()
    clock = FakeClock()
    result = build_runner(
        work_reader=FakeMatchingWorkReader([work_item("article-1"), work_item("article-2")]),
        preference_reader=FakeUserPreferenceReader([preference("user-1"), preference("user-2")]),
        repository=repository,
        clock=clock,
    ).run_once()

    assert result.pairs_evaluated == 4
    # One run_cutoff read plus one read per evaluated pair.
    assert len(clock.reads) == 5
    matched_at_values = {candidate.matched_at for candidate in repository.candidates}
    assert len(matched_at_values) == 4
    assert result.run_cutoff not in matched_at_values


# --------------------------------------------------------------------------------------
# Pagination and Cartesian coverage
# --------------------------------------------------------------------------------------


def test_every_preference_page_is_exhausted_including_the_tail_user() -> None:
    repository = FakeAlertCandidateRepository()
    preference_reader = FakeUserPreferenceReader(
        [preference("user-1"), preference("user-2"), preference("user-3")],
        page_size=1,
    )
    result = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()]),
        preference_reader=preference_reader,
        repository=repository,
    ).run_once()

    assert result.preference_pages_read == 3
    assert result.preferences_loaded == 3
    assert preference_reader.calls[0][0] is None
    assert [call[0] for call in preference_reader.calls] == [None, "user-1", "user-2"]
    assert ("user-3", "article-1") in repository.logical_keys


def test_every_work_page_is_exhausted_including_the_tail_item() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [work_item("article-1"), work_item("article-2"), work_item("article-3")],
        page_size=1,
    )
    result = build_runner(
        work_reader=work_reader,
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    ).run_once()

    assert result.work_pages_read == 3
    assert result.work_items_seen == 3
    assert [call["after_article_id"] for call in work_reader.calls] == [
        None,
        "article-1",
        "article-2",
    ]
    assert ("user-1", "article-3") in repository.logical_keys


def test_paged_work_and_paged_preferences_produce_full_cartesian_coverage() -> None:
    repository = FakeAlertCandidateRepository()
    work_reader = FakeMatchingWorkReader(
        [work_item("article-1"), work_item("article-2")],
        page_size=1,
    )
    preference_reader = FakeUserPreferenceReader(
        [preference("user-1"), preference("user-2"), preference("user-3")],
        page_size=2,
    )

    result = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
    ).run_once()

    assert result.work_pages_read == 2
    assert result.preference_pages_read == 2
    assert result.pairs_evaluated == 6
    # Page-paired iteration would miss (article-1, user-3) and (article-2, user-1/user-2).
    assert repository.logical_keys == {
        ("user-1", "article-1"),
        ("user-2", "article-1"),
        ("user-3", "article-1"),
        ("user-1", "article-2"),
        ("user-2", "article-2"),
        ("user-3", "article-2"),
    }


def test_preferences_are_read_once_per_run_not_once_per_work_page() -> None:
    preference_reader = FakeUserPreferenceReader([preference("user-1")], page_size=1)
    build_runner(
        work_reader=FakeMatchingWorkReader(
            [work_item("article-1"), work_item("article-2")],
            page_size=1,
        ),
        preference_reader=preference_reader,
        repository=FakeAlertCandidateRepository(),
    ).run_once()

    assert len(preference_reader.calls) == 1


# --------------------------------------------------------------------------------------
# STOP_RUN failure semantics
# --------------------------------------------------------------------------------------


def test_preference_read_failure_stops_the_run() -> None:
    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()]),
        preference_reader=FakeUserPreferenceReader([preference()], fail_on_call=1),
        repository=repository,
    )

    with pytest.raises(MatchingRunError) as error:
        runner.run_once()

    assert error.value.stage is MatchingStage.PREFERENCE_LOAD
    assert error.value.category is MatchingErrorCategory.DEPENDENCY_FAILURE
    assert repository.save_attempts == 0


def test_work_read_failure_stops_the_run() -> None:
    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()], fail_on_call=1),
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    )

    with pytest.raises(MatchingRunError) as error:
        runner.run_once()

    assert error.value.stage is MatchingStage.WORK_DISCOVERY
    assert error.value.category is MatchingErrorCategory.DEPENDENCY_FAILURE
    assert repository.save_attempts == 0


def test_article_classification_identity_mismatch_stops_the_run_without_silent_skip() -> None:
    class MismatchedWorkReader(FakeMatchingWorkReader):
        def list_page(self, **kwargs: object) -> MatchingWorkPage:
            if self.calls:
                return MatchingWorkPage(items=())
            self.calls.append(dict(kwargs))
            return MatchingWorkPage.model_construct(
                items=(
                    MatchingWorkItem.model_construct(
                        article=article("article-a"),
                        classification=classification("article-b"),
                    ),
                ),
                next_cursor=None,
            )

    repository = FakeAlertCandidateRepository()
    runner = build_runner(
        work_reader=MismatchedWorkReader([]),
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=repository,
    )

    with pytest.raises(MatchingRunError) as error:
        runner.run_once()

    assert error.value.stage is MatchingStage.WORK_DISCOVERY
    assert error.value.category is MatchingErrorCategory.CONTRACT_VIOLATION
    assert error.value.article_id == "article-a"
    assert repository.save_attempts == 0


def test_stop_run_error_context_is_structured_and_sanitized() -> None:
    runner = build_runner(
        work_reader=FakeMatchingWorkReader([work_item()], fail_on_call=1),
        preference_reader=FakeUserPreferenceReader([preference()]),
        repository=FakeAlertCandidateRepository(),
    )

    with pytest.raises(MatchingRunError) as error:
        runner.run_once()

    context = error.value.log_context()
    assert context["stage"] == MatchingStage.WORK_DISCOVERY.value
    assert context["error_category"] == MatchingErrorCategory.DEPENDENCY_FAILURE.value
    assert context["classifier_version"] == TARGET_VERSION
    assert context["pipeline_stage"] == "matching_orchestration"
    assert "Test article" not in str(error.value)
    assert "example.test" not in str(error.value)


# --------------------------------------------------------------------------------------
# Partial durable progress and safe replay
# --------------------------------------------------------------------------------------


def test_persistence_failure_stops_the_run_and_prior_candidates_stay_durable() -> None:
    repository = FakeAlertCandidateRepository(fail_after_saves=2)
    work_reader = FakeMatchingWorkReader(
        [work_item("article-1"), work_item("article-2"), work_item("article-3")]
    )
    preference_reader = FakeUserPreferenceReader([preference()])

    with pytest.raises(MatchingRunError) as error:
        build_runner(
            work_reader=work_reader,
            preference_reader=preference_reader,
            repository=repository,
        ).run_once()

    assert error.value.stage is MatchingStage.CANDIDATE_PERSISTENCE
    assert error.value.category is MatchingErrorCategory.DEPENDENCY_FAILURE
    assert error.value.article_id == "article-3"
    assert error.value.user_id == "user-1"
    # No whole-run transaction: the first two candidates remain durable.
    assert repository.logical_keys == {("user-1", "article-1"), ("user-1", "article-2")}

    repository.fail_after_saves = None
    replay = build_runner(
        work_reader=work_reader,
        preference_reader=preference_reader,
        repository=repository,
        clock=FakeClock(NOW + timedelta(minutes=5)),
    ).run_once()

    assert replay.candidate_already_exists_count == 2
    assert replay.candidate_created_count == 1
    assert repository.logical_keys == {
        ("user-1", "article-1"),
        ("user-1", "article-2"),
        ("user-1", "article-3"),
    }
