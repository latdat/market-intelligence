from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PostgresHarness:
    psql: str
    port: int

    def run(
        self,
        sql: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PGHOST": "127.0.0.1",
            "PGPORT": str(self.port),
            "PGUSER": "postgres",
            "PGDATABASE": "postgres",
        }
        return subprocess.run(
            [
                self.psql,
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-A",
                "-t",
                "-c",
                sql,
            ],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )

    def scalar(self, sql: str) -> str:
        return self.run(sql).stdout.strip()

    def payload(self, sql: str) -> dict[str, object]:
        value = json.loads(self.scalar(sql))
        assert isinstance(value, dict)
        return value


def _find_postgres_binary(name: str) -> str | None:
    binary_directory = os.environ.get("DE009_TEST_PG_BIN")
    if binary_directory:
        candidate = Path(binary_directory) / name
        if os.name == "nt":
            candidate = candidate.with_suffix(".exe")
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run_setup_command(command: list[str], *, background_child: bool = False) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=not background_child,
        stdout=subprocess.DEVNULL if background_child else None,
        stderr=subprocess.DEVNULL if background_child else None,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        pytest.fail(f"PostgreSQL test setup failed: {completed.stderr or completed.stdout}")


@pytest.fixture(scope="session")
def classification_database(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[PostgresHarness]:
    initdb = _find_postgres_binary("initdb")
    pg_ctl = _find_postgres_binary("pg_ctl")
    psql = _find_postgres_binary("psql")
    if initdb is None or pg_ctl is None or psql is None:
        pytest.skip("local PostgreSQL binaries are required for DE-009 integration tests")

    cluster_directory = tmp_path_factory.mktemp("de009-postgres")
    log_path = cluster_directory / "postgres.log"
    port = _free_local_port()
    _run_setup_command(
        [
            initdb,
            "-D",
            str(cluster_directory),
            "-A",
            "trust",
            "-U",
            "postgres",
            "--no-locale",
            "--encoding=UTF8",
        ]
    )
    _run_setup_command(
        [
            pg_ctl,
            "-D",
            str(cluster_directory),
            "-l",
            str(log_path),
            "-o",
            f"-h 127.0.0.1 -p {port}",
            "-w",
            "start",
        ],
        background_child=True,
    )

    database = PostgresHarness(psql=psql, port=port)
    try:
        database.run(
            "create role anon nologin; "
            "create role authenticated nologin; "
            "create role service_role nologin bypassrls;"
        )
        for migration in sorted((REPOSITORY_ROOT / "supabase" / "migrations").glob("*.sql")):
            _run_setup_command(
                [
                    psql,
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(port),
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-f",
                    str(migration),
                ]
            )
        yield database
    finally:
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(cluster_directory),
                "-m",
                "fast",
                "-w",
                "stop",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


def _insert_article(database: PostgresHarness, article_id: str) -> None:
    database.run(
        f"""
        insert into public.articles (
            article_id, source_id, source_item_id, url, canonical_url, title,
            description, language, market, published_at, discovered_at, content_hash
        ) values (
            '{article_id}', 'source-1', null, 'https://example.test/{article_id}',
            'https://example.test/{article_id}', 'Title', null, 'en', 'US',
            null, now(), 'hash-{article_id}'
        );
        """
    )


def _enqueue(
    database: PostgresHarness,
    article_id: str,
    classifier_version: str,
    *,
    requested_model: str = "deepseek-v4-flash",
    prompt_version: str = "classification-prompt-v1",
    taxonomy_version: str = "classification-taxonomy-v1",
    max_attempts: int = 3,
) -> dict[str, object]:
    return database.payload(
        f"""
        select public.enqueue_article_classification(
            '{article_id}', '{classifier_version}', '{requested_model}',
            '{prompt_version}', '{taxonomy_version}', {max_attempts}::smallint
        )::text;
        """
    )


def _claim(
    database: PostgresHarness,
    classifier_version: str,
    token: UUID,
) -> dict[str, object]:
    return database.payload(
        f"""
        select public.claim_next_article_classification(
            '{classifier_version}', '{token}'::uuid, 300
        )::text;
        """
    )


def _renew(
    database: PostgresHarness,
    article_id: str,
    classifier_version: str,
    token: UUID,
) -> dict[str, object]:
    return database.payload(
        f"""
        select public.renew_article_classification_lease(
            '{article_id}', '{classifier_version}', '{token}'::uuid, 300
        )::text;
        """
    )


def _complete(
    database: PostgresHarness,
    article_id: str,
    classifier_version: str,
    token: UUID,
) -> dict[str, object]:
    return database.payload(
        f"""
        select public.complete_article_classification(
            '{article_id}', '{classifier_version}', '{token}'::uuid,
            true, array['US']::text[], 'FINANCE', array['BANKING']::text[],
            0.9::double precision, now(), 'deepseek-provider-model',
            'provider-request-1', 'system-fingerprint-1',
            8::bigint, 3::bigint, 5::bigint, 2::bigint, 10::bigint,
            0.000004::numeric, 'pricing-v1', 'off_peak', 2::smallint
        )::text;
        """
    )


def _fail(
    database: PostgresHarness,
    article_id: str,
    classifier_version: str,
    token: UUID,
    disposition: str,
    *,
    prompt_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    completion_tokens: int,
    provider_attempts: int,
    retryable: bool = True,
) -> dict[str, object]:
    total_tokens = prompt_tokens + completion_tokens
    return database.payload(
        f"""
        select public.fail_article_classification(
            '{article_id}', '{classifier_version}', '{token}'::uuid,
            'timeout', 503, {str(retryable).lower()}, '{disposition}',
            {prompt_tokens}::bigint, {cache_hit_tokens}::bigint,
            {cache_miss_tokens}::bigint, {completion_tokens}::bigint,
            {total_tokens}::bigint, 0.000003::numeric,
            'pricing-v1', 'off_peak', {provider_attempts}::smallint
        )::text;
        """
    )


def test_schema_identity_constraints_indexes_and_access_boundary(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    primary_key = database.scalar(
        """
        select pg_get_constraintdef(oid)
        from pg_constraint
        where conrelid = 'public.article_classifications'::regclass
          and contype = 'p';
        """
    )
    lifecycle_constraint = database.scalar(
        """
        select pg_get_constraintdef(oid)
        from pg_constraint
        where conname = 'article_classifications_lifecycle_state_check';
        """
    )
    indexes = database.scalar(
        """
        select string_agg(indexname, ',' order by indexname)
        from pg_indexes
        where schemaname = 'public'
          and tablename = 'article_classifications';
        """
    )

    assert primary_key == "PRIMARY KEY (article_id, classifier_version)"
    assert "attempt_count < max_attempts" in lifecycle_constraint
    assert "article_classifications_retryable_claim_idx" in indexes
    assert "article_classifications_expired_lease_idx" in indexes
    assert (
        database.scalar(
            """
        select relrowsecurity
        from pg_class
        where oid = 'public.article_classifications'::regclass;
        """
        )
        == "t"
    )
    assert (
        database.scalar(
            """
        select
            has_table_privilege('service_role', 'public.article_classifications', 'SELECT'),
            has_table_privilege('service_role', 'public.article_classifications', 'INSERT'),
            has_table_privilege('authenticated', 'public.article_classifications', 'SELECT');
        """
        )
        == "t|f|f"
    )
    assert (
        database.scalar(
            """
        select
            has_function_privilege(
                'service_role',
                'public.claim_next_article_classification(text,uuid,integer)',
                'EXECUTE'
            ),
            has_function_privilege(
                'authenticated',
                'public.claim_next_article_classification(text,uuid,integer)',
                'EXECUTE'
            );
        """
        )
        == "t|f"
    )

    denied_select = database.run(
        "set role authenticated; select * from public.article_classifications;",
        check=False,
    )
    denied_insert = database.run(
        """
        set role service_role;
        insert into public.article_classifications (
            article_id, classifier_version, requested_model,
            prompt_version, taxonomy_version
        ) values (
            'not-allowed', 'classification-v1', 'model', 'prompt', 'taxonomy'
        );
        """,
        check=False,
    )
    assert denied_select.returncode != 0
    assert denied_insert.returncode != 0


def test_enqueue_is_idempotent_but_rejects_lineage_mismatch(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "enqueue-lineage"
    version = "classification-v101"
    _insert_article(database, article_id)

    created = _enqueue(database, article_id, version)
    repeated = _enqueue(database, article_id, version)
    mismatch = _enqueue(
        database,
        article_id,
        version,
        requested_model="different-model",
        prompt_version="different-prompt",
        taxonomy_version="different-taxonomy",
    )

    assert created["outcome"] == "CREATED"
    assert repeated["outcome"] == "EXISTING_RETRYABLE"
    assert mismatch["outcome"] == "LINEAGE_MISMATCH"
    assert mismatch["mismatched_fields"] == [
        "requested_model",
        "prompt_version",
        "taxonomy_version",
    ]
    assert database.scalar(
        f"""
        select requested_model || '|' || prompt_version || '|' || taxonomy_version
        from public.article_classifications
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
    ) == ("deepseek-v4-flash|classification-prompt-v1|classification-taxonomy-v1")


def test_concurrent_workers_claim_one_row_at_most_once(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "concurrent-claim"
    version = "classification-v102"
    _insert_article(database, article_id)
    _enqueue(database, article_id, version)
    tokens = [UUID(int=value) for value in range(100, 108)]

    with ThreadPoolExecutor(max_workers=len(tokens)) as executor:
        outcomes = list(
            executor.map(
                lambda token: _claim(database, version, token)["outcome"],
                tokens,
            )
        )

    assert outcomes.count("CLAIMED") == 1
    assert outcomes.count("EMPTY") == len(tokens) - 1
    assert (
        database.scalar(
            f"""
        select status || '|' || attempt_count::text
        from public.article_classifications
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
        )
        == "PROCESSING|1"
    )


def test_expired_lease_reclaim_fences_stale_worker_and_success_is_immutable(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "fencing-success"
    version = "classification-v103"
    first_token = UUID(int=201)
    second_token = UUID(int=202)
    _insert_article(database, article_id)
    _enqueue(database, article_id, version)
    first_claim = _claim(database, version, first_token)
    database.run("select pg_sleep(0.01);")
    renewal = _renew(database, article_id, version, first_token)
    assert renewal["outcome"] == "RENEWED"
    assert renewal["record"]["updated_at"] > first_claim["record"]["updated_at"]
    assert first_claim["outcome"] == "CLAIMED"
    database.run(
        f"""
        update public.article_classifications
        set claimed_at = now() - interval '10 minutes',
            lease_expires_at = now() - interval '5 minutes'
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
    )

    second_claim = _claim(database, version, second_token)
    stale_renewal = _renew(database, article_id, version, first_token)
    stale_completion = _complete(database, article_id, version, first_token)
    assert stale_renewal["outcome"] == "LOST_CLAIM"
    active_completion = _complete(database, article_id, version, second_token)
    before_replay = database.scalar(
        f"""
        select to_jsonb(article_classifications)::text
        from public.article_classifications
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
    )
    replay = _complete(database, article_id, version, second_token)
    after_replay = database.scalar(
        f"""
        select to_jsonb(article_classifications)::text
        from public.article_classifications
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
    )

    assert second_claim["outcome"] == "CLAIMED"
    assert second_claim["record"]["attempt_count"] == 2
    assert stale_completion["outcome"] == "LOST_CLAIM"
    assert active_completion["outcome"] == "SUCCEEDED"
    assert replay["outcome"] == "ALREADY_SUCCEEDED"
    assert before_replay == after_replay
    assert _enqueue(database, article_id, version)["outcome"] == "ALREADY_SUCCEEDED"

    immutable_update = database.run(
        f"""
        update public.article_classifications
        set confidence = 0.1
        where article_id = '{article_id}' and classifier_version = '{version}';
        """,
        check=False,
    )
    assert immutable_update.returncode != 0


def test_failures_aggregate_observed_usage_and_quarantine_at_invocation_budget(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "failure-budget"
    version = "classification-v104"
    tokens = [UUID(int=value) for value in range(301, 304)]
    _insert_article(database, article_id)
    _enqueue(database, article_id, version, max_attempts=3)

    first_claim = _claim(database, version, tokens[0])
    database.run("select pg_sleep(0.01);")
    first_failure = _fail(
        database,
        article_id,
        version,
        tokens[0],
        "RETRY_15_MINUTES",
        prompt_tokens=8,
        cache_hit_tokens=3,
        cache_miss_tokens=5,
        completion_tokens=2,
        provider_attempts=3,
    )
    assert first_claim["record"]["attempt_count"] == 1
    assert first_failure["outcome"] == "RETRY_SCHEDULED"
    assert first_failure["record"]["updated_at"] > first_claim["record"]["updated_at"]

    database.run(
        f"""
        set session_replication_role = replica;
        update public.article_classifications
        set next_attempt_at = now() - interval '1 second'
        where article_id = '{article_id}' and classifier_version = '{version}';
        set session_replication_role = origin;
        """
    )
    second_claim = _claim(database, version, tokens[1])
    second_failure = _fail(
        database,
        article_id,
        version,
        tokens[1],
        "RETRY_60_MINUTES",
        prompt_tokens=5,
        cache_hit_tokens=1,
        cache_miss_tokens=4,
        completion_tokens=1,
        provider_attempts=1,
    )
    assert second_claim["record"]["attempt_count"] == 2
    assert second_failure["outcome"] == "RETRY_SCHEDULED"

    database.run(
        f"""
        set session_replication_role = replica;
        update public.article_classifications
        set next_attempt_at = now() - interval '1 second'
        where article_id = '{article_id}' and classifier_version = '{version}';
        set session_replication_role = origin;
        """
    )
    third_claim = _claim(database, version, tokens[2])
    terminal = _fail(
        database,
        article_id,
        version,
        tokens[2],
        "RETRY_15_MINUTES",
        prompt_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=0,
        completion_tokens=0,
        provider_attempts=0,
    )

    assert third_claim["record"]["attempt_count"] == 3
    assert terminal["outcome"] == "QUARANTINED"
    terminal_record = terminal["record"]
    assert terminal_record["status"] == "QUARANTINED"
    assert terminal_record["attempt_count"] == 3
    assert terminal_record["last_provider_attempts"] == 0
    assert terminal_record["prompt_tokens"] == 13
    assert terminal_record["completion_tokens"] == 3
    assert terminal_record["total_tokens"] == 16
    assert terminal_record["estimated_cost_usd"] == 0.000009
    assert terminal_record["last_error_category"] == "attempt_budget_exhausted"
    assert terminal_record["next_attempt_at"] is None


def test_failure_commit_replay_is_fenced_and_does_not_double_count_usage(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "failure-replay"
    version = "classification-v108"
    token = UUID(int=355)
    _insert_article(database, article_id)
    _enqueue(database, article_id, version)
    _claim(database, version, token)
    first = _fail(
        database,
        article_id,
        version,
        token,
        "RETRY_15_MINUTES",
        prompt_tokens=8,
        cache_hit_tokens=3,
        cache_miss_tokens=5,
        completion_tokens=2,
        provider_attempts=2,
    )
    replay = _fail(
        database,
        article_id,
        version,
        token,
        "RETRY_15_MINUTES",
        prompt_tokens=8,
        cache_hit_tokens=3,
        cache_miss_tokens=5,
        completion_tokens=2,
        provider_attempts=2,
    )

    assert first["outcome"] == "RETRY_SCHEDULED"
    assert replay["outcome"] == "LOST_CLAIM"
    assert (
        database.scalar(
            f"""
        select prompt_tokens::text || '|' || total_tokens::text || '|' ||
               estimated_cost_usd::text
        from public.article_classifications
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
        )
        == "8|10|0.000003000000"
    )


def test_database_rejects_duplicate_taxonomy_values_and_inconsistent_usage(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "invalid-success-payload"
    version = "classification-v107"
    token = UUID(int=360)
    _insert_article(database, article_id)
    _enqueue(database, article_id, version)
    _claim(database, version, token)

    duplicate_markets = database.run(
        f"""
        select public.complete_article_classification(
            '{article_id}', '{version}', '{token}'::uuid,
            true, array['US', 'US']::text[], 'FINANCE', array[]::text[],
            0.9::double precision, now(), 'provider-model', null, null,
            8::bigint, 3::bigint, 5::bigint, 2::bigint, 10::bigint,
            0::numeric, null, null, 1::smallint
        );
        """,
        check=False,
    )
    inconsistent_usage = database.run(
        f"""
        select public.complete_article_classification(
            '{article_id}', '{version}', '{token}'::uuid,
            true, array['US']::text[], 'FINANCE', array[]::text[],
            0.9::double precision, now(), 'provider-model', null, null,
            8::bigint, 2::bigint, 5::bigint, 2::bigint, 10::bigint,
            0::numeric, null, null, 1::smallint
        );
        """,
        check=False,
    )

    assert duplicate_markets.returncode != 0
    assert "article_classifications_markets_check" in duplicate_markets.stderr
    assert inconsistent_usage.returncode != 0
    assert "invalid provider usage totals" in inconsistent_usage.stderr
    assert (
        database.scalar(
            f"""
        select status || '|' || claim_token::text
        from public.article_classifications
        where article_id = '{article_id}' and classifier_version = '{version}';
        """
        )
        == f"PROCESSING|{token}"
    )


def test_non_retryable_failure_is_quarantined_even_with_retry_disposition(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    article_id = "non-retryable-failure"
    version = "classification-v106"
    token = UUID(int=350)
    _insert_article(database, article_id)
    _enqueue(database, article_id, version)
    _claim(database, version, token)

    terminal = _fail(
        database,
        article_id,
        version,
        token,
        "RETRY_15_MINUTES",
        prompt_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=0,
        completion_tokens=0,
        provider_attempts=1,
        retryable=False,
    )

    assert terminal["outcome"] == "QUARANTINED"
    assert terminal["record"]["last_error_category"] == "timeout"
    assert terminal["record"]["next_attempt_at"] is None


def test_expired_exhausted_recovery_is_bounded_to_one_locked_candidate(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    version = "classification-v105"
    expired_articles = ["expired-exhausted-a", "expired-exhausted-b"]
    waiting_article = "still-waiting"
    for article_id in [*expired_articles, waiting_article]:
        _insert_article(database, article_id)
        _enqueue(database, article_id, version, max_attempts=1)

    for value in range(2):
        claim = _claim(database, version, UUID(int=400 + value))
        assert claim["outcome"] == "CLAIMED"
    database.run(
        f"""
        update public.article_classifications
        set claimed_at = now() - interval '20 minutes',
            lease_expires_at = now() - interval '10 minutes'
        where classifier_version = '{version}' and status = 'PROCESSING';
        """
    )

    recovery = _claim(database, version, UUID(int=499))

    assert recovery["outcome"] == "RECOVERED_QUARANTINED"
    assert (
        database.scalar(
            f"""
        select
            count(*) filter (where status = 'QUARANTINED')::text || '|' ||
            count(*) filter (where status = 'PROCESSING')::text || '|' ||
            count(*) filter (where status = 'RETRYABLE')::text
        from public.article_classifications
        where classifier_version = '{version}';
        """
        )
        == "1|1|1"
    )
