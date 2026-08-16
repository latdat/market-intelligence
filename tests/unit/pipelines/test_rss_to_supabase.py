import asyncio
from datetime import UTC, datetime

import pytest

from market_intelligence.articles import CanonicalArticle, RawArticle
from market_intelligence.deduplication import DedupReason
from market_intelligence.persistence import ArticlePersistenceError
from market_intelligence.pipelines import preflight_rss_sources, run_rss_ingestion
from market_intelligence.source_registry import SourceConfig

RETRIEVED_AT = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)


def source_config(
    source_id: str,
    *,
    market: str = "US",
    can_store_metadata: bool = True,
) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": source_id,
            "name": source_id,
            "market": market,
            "language": "en",
            "source_type": "OFFICIAL_ORGANIZATION",
            "authority_level": "PRIMARY",
            "domains": ["TECHNOLOGY"],
            "content_scope": "EDITORIAL_NEWS",
            "acquisition": {
                "method": "RSS",
                "endpoint_url": f"https://example.org/{source_id}.xml",
                "poll_interval_minutes": 15,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": can_store_metadata,
                "can_store_full_text": False,
                "can_ai_process": False,
                "can_show_snippet": False,
                "can_redistribute_full_text": False,
                "rights_review_status": "PENDING",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def raw_article(
    source_id: str,
    item_number: int,
    *,
    url: str | None = None,
    title: str | None = None,
) -> RawArticle:
    return RawArticle(
        source_id=source_id,
        source_item_id=f"{source_id}-{item_number}",
        url=url or f"https://example.org/articles/{source_id}/{item_number}",
        title=title if title is not None else f"Article {item_number} from {source_id}",
        description="Metadata-only description",
        published_at_raw="2026-08-15T07:30:00Z",
        language_hint="en",
        retrieved_at=RETRIEVED_AT,
    )


class StaticFetcher:
    def __init__(self, articles_by_source: dict[str, list[RawArticle]]) -> None:
        self.articles_by_source = articles_by_source

    async def fetch(self, source: SourceConfig) -> list[RawArticle]:
        return self.articles_by_source[source.source_id]


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: list[CanonicalArticle] = []

    def save(self, article: CanonicalArticle) -> None:
        self.saved.append(article)


class SelectiveFailureFetcher(StaticFetcher):
    def __init__(
        self,
        articles_by_source: dict[str, list[RawArticle]],
        failed_source_id: str,
    ) -> None:
        super().__init__(articles_by_source)
        self.failed_source_id = failed_source_id

    async def fetch(self, source: SourceConfig) -> list[RawArticle]:
        if source.source_id == self.failed_source_id:
            raise RuntimeError("simulated source failure")
        return await super().fetch(source)


class SelectiveFailureRepository(RecordingRepository):
    def __init__(self, failed_source_id: str) -> None:
        super().__init__()
        self.failed_source_id = failed_source_id

    def save(self, article: CanonicalArticle) -> None:
        if article.source_id == self.failed_source_id:
            raise ArticlePersistenceError(article.article_id)
        super().save(article)


def test_ingestion_respects_max_items_and_persists_metadata() -> None:
    source = source_config("source_one")
    fetcher = StaticFetcher(
        {"source_one": [raw_article("source_one", index) for index in range(3)]}
    )
    repository = RecordingRepository()

    result = asyncio.run(run_rss_ingestion([source], repository, max_items=2, connector=fetcher))

    assert result.sources[0].fetched_count == 3
    assert result.sources[0].selected_count == 2
    assert result.sources[0].normalized_count == 2
    assert result.sources[0].persisted_count == 2
    assert len(repository.saved) == 2


def test_batch_duplicate_is_reported_without_suppressing_source_records() -> None:
    first_source = source_config("source_one")
    second_source = source_config("source_two", market="EU")
    shared_url = "https://example.org/shared-article"
    fetcher = StaticFetcher(
        {
            "source_one": [raw_article("source_one", 1, url=shared_url)],
            "source_two": [raw_article("source_two", 1, url=shared_url)],
        }
    )
    repository = RecordingRepository()

    result = asyncio.run(
        run_rss_ingestion(
            [first_source, second_source],
            repository,
            max_items=1,
            connector=fetcher,
        )
    )

    assert len(result.duplicate_matches) == 1
    assert result.duplicate_matches[0].reason is DedupReason.CANONICAL_URL
    assert result.sources[1].duplicate_count == 1
    assert len(repository.saved) == 2
    assert repository.saved[0].article_id != repository.saved[1].article_id


def test_metadata_storage_rights_are_enforced() -> None:
    source = source_config("source_one", can_store_metadata=False)
    fetcher = StaticFetcher({"source_one": [raw_article("source_one", 1)]})
    repository = RecordingRepository()

    result = asyncio.run(run_rss_ingestion([source], repository, max_items=1, connector=fetcher))

    assert result.sources[0].persisted_count == 0
    assert result.sources[0].storage_skipped_count == 1
    assert repository.saved == []


def test_invalid_article_is_rejected_without_losing_valid_article() -> None:
    source = source_config("source_one")
    invalid = raw_article("source_one", 1, title="")
    valid = raw_article("source_one", 2)
    fetcher = StaticFetcher({"source_one": [invalid, valid]})
    repository = RecordingRepository()

    result = asyncio.run(run_rss_ingestion([source], repository, max_items=2, connector=fetcher))

    assert result.sources[0].normalized_count == 1
    assert result.sources[0].rejected_count == 1
    assert result.sources[0].persisted_count == 1


def test_repeated_run_produces_the_same_article_ids() -> None:
    source = source_config("source_one")
    fetcher = StaticFetcher({"source_one": [raw_article("source_one", 1)]})
    repository = RecordingRepository()

    first = asyncio.run(run_rss_ingestion([source], repository, max_items=1, connector=fetcher))
    second = asyncio.run(run_rss_ingestion([source], repository, max_items=1, connector=fetcher))

    assert first.sources[0].article_ids == second.sources[0].article_ids
    assert repository.saved[0].article_id == repository.saved[1].article_id


def test_preflight_never_requires_repository() -> None:
    source = source_config("source_one")
    fetcher = StaticFetcher(
        {"source_one": [raw_article("source_one", index) for index in range(3)]}
    )

    result = asyncio.run(preflight_rss_sources([source], max_items=2, connector=fetcher))

    assert result[0].fetched_count == 3
    assert result[0].selected_count == 2
    assert result[0].normalized_count == 2


def test_fetch_failure_is_reported_without_blocking_later_sources() -> None:
    sources = [source_config(f"source_{index}") for index in range(1, 4)]
    fetcher = SelectiveFailureFetcher(
        {source.source_id: [raw_article(source.source_id, 1)] for source in sources},
        failed_source_id="source_2",
    )
    repository = RecordingRepository()

    result = asyncio.run(run_rss_ingestion(sources, repository, max_items=1, connector=fetcher))

    assert [source.status for source in result.sources] == [
        "SUCCESS",
        "FAILED",
        "SUCCESS",
    ]
    assert result.sources[1].error_type == "RuntimeError"
    assert [article.source_id for article in repository.saved] == ["source_1", "source_3"]


def test_preflight_fetch_failure_is_reported_without_blocking_later_sources() -> None:
    sources = [source_config(f"source_{index}") for index in range(1, 4)]
    fetcher = SelectiveFailureFetcher(
        {source.source_id: [raw_article(source.source_id, 1)] for source in sources},
        failed_source_id="source_2",
    )

    result = asyncio.run(preflight_rss_sources(sources, max_items=1, connector=fetcher))

    assert [source.status for source in result] == ["SUCCESS", "FAILED", "SUCCESS"]
    assert result[1].error_type == "RuntimeError"


def test_persistence_failure_is_reported_without_blocking_later_sources() -> None:
    sources = [source_config(f"source_{index}") for index in range(1, 4)]
    fetcher = StaticFetcher(
        {source.source_id: [raw_article(source.source_id, 1)] for source in sources}
    )
    repository = SelectiveFailureRepository("source_2")

    result = asyncio.run(run_rss_ingestion(sources, repository, max_items=1, connector=fetcher))

    assert [source.status for source in result.sources] == [
        "SUCCESS",
        "FAILED",
        "SUCCESS",
    ]
    assert result.sources[1].error_type == "ArticlePersistenceError"
    assert [article.source_id for article in repository.saved] == ["source_1", "source_3"]


@pytest.mark.parametrize("max_items", [0, -1, True])
def test_max_items_must_be_a_positive_integer(max_items: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        asyncio.run(preflight_rss_sources([], max_items=max_items))
