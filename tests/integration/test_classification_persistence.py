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


def _complete_with_method(
    database: PostgresHarness,
    article_id: str,
    classifier_version: str,
    token: UUID,
    *,
    classification_method: str,
    provider_attempts: int,
) -> subprocess.CompletedProcess[str]:
    deterministic = classification_method == "DETERMINISTIC"
    provider_model = "null" if deterministic else "'deepseek-provider-model'"
    provider_request_id = "null" if deterministic else "'provider-request-1'"
    fingerprint = "null" if deterministic else "'system-fingerprint-1'"
    prompt_tokens = 0 if deterministic else 8
    cache_hit_tokens = 0 if deterministic else 3
    cache_miss_tokens = 0 if deterministic else 5
    completion_tokens = 0 if deterministic else 2
    total_tokens = 0 if deterministic else 10
    estimated_cost = "0" if deterministic else "0.000004"
    pricing_id = "null" if deterministic else "'pricing-v1'"
    pricing_window = "null" if deterministic else "'off_peak'"
    return database.run(
        f"""
        select public.complete_article_classification(
            '{article_id}', '{classifier_version}', '{token}'::uuid,
            true, array['US']::text[], 'FINANCE', array['BANKING']::text[],
            0.9::double precision, now(), {provider_model},
            {provider_request_id}, {fingerprint},
            {prompt_tokens}::bigint, {cache_hit_tokens}::bigint,
            {cache_miss_tokens}::bigint, {completion_tokens}::bigint,
            {total_tokens}::bigint, {estimated_cost}::numeric,
            {pricing_id}, {pricing_window}, {provider_attempts}::smallint,
            '{classification_method}'
        );
        """,
        check=False,
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
    assert (
        database.scalar(
            """
            select is_nullable
            from information_schema.columns
            where table_schema = 'public'
              and table_name = 'article_classifications'
              and column_name = 'classification_method';
            """
        )
        == "YES"
    )
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


def test_hybrid_method_constraints_and_version_identities(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database

    deterministic_article = "deterministic-success"
    deterministic_version = "classification-v2"
    deterministic_token = UUID(int=901)
    _insert_article(database, deterministic_article)
    _enqueue(database, deterministic_article, deterministic_version)
    _claim(database, deterministic_version, deterministic_token)
    deterministic = _complete_with_method(
        database,
        deterministic_article,
        deterministic_version,
        deterministic_token,
        classification_method="DETERMINISTIC",
        provider_attempts=0,
    )
    assert deterministic.returncode == 0
    assert (
        database.scalar(
            f"""
            select classification_method || '|' || last_provider_attempts::text || '|' ||
                   total_tokens::text || '|' || estimated_cost_usd::text || '|' ||
                   coalesce(provider_model, 'NULL')
            from public.article_classifications
            where article_id = '{deterministic_article}'
              and classifier_version = '{deterministic_version}';
            """
        )
        == "DETERMINISTIC|0|0|0.000000000000|NULL"
    )

    rejected_article = "deepseek-zero-attempts"
    rejected_token = UUID(int=902)
    _insert_article(database, rejected_article)
    _enqueue(database, rejected_article, deterministic_version)
    _claim(database, deterministic_version, rejected_token)
    rejected = _complete_with_method(
        database,
        rejected_article,
        deterministic_version,
        rejected_token,
        classification_method="DEEPSEEK",
        provider_attempts=0,
    )
    assert rejected.returncode != 0
    assert "DeepSeek success provider attempts" in rejected.stderr

    invalid_non_success = database.run(
        f"""
        update public.article_classifications
        set classification_method = 'DEEPSEEK'
        where article_id = '{rejected_article}'
          and classifier_version = '{deterministic_version}';
        """,
        check=False,
    )
    assert invalid_non_success.returncode != 0

    identity_article = "hybrid-version-identity"
    _insert_article(database, identity_article)
    _enqueue(database, identity_article, "classification-v1")
    _enqueue(database, identity_article, "classification-v2")
    assert (
        database.scalar(
            f"""
            select count(*)
            from public.article_classifications
            where article_id = '{identity_article}'
              and classifier_version in ('classification-v1', 'classification-v2');
            """
        )
        == "2"
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


# Append these DE-012 tests to tests/integration/test_classification_persistence.py.
# Reuse the existing PostgresHarness and classification_database fixture.
#
# Also add:
#     from concurrent.futures import ThreadPoolExecutor
# to that file's imports if it is not already present.


def _insert_de012_article(
    database: PostgresHarness,
    article_id: str,
) -> None:
    database.run(
        f"""
        insert into public.articles (
            article_id,
            source_id,
            url,
            canonical_url,
            title,
            language,
            market,
            discovered_at,
            content_hash
        ) values (
            '{article_id}',
            'de012_source',
            'https://example.test/{article_id}',
            'https://example.test/{article_id}',
            'DE-012 integration article',
            'en',
            'US',
            '2026-08-15T08:00:00Z',
            'de012-content-hash-{article_id}'
        )
        on conflict (article_id) do nothing;
        """
    )


def _save_de012_candidate_sql(
    *,
    candidate_id: str,
    user_id: str,
    article_id: str,
    matched_at: str = "2026-08-15T08:05:00Z",
    reasons: str = "array['market:US','category:TECHNOLOGY']::text[]",
    importance: str = "HIGH",
    score: str = "0.95",
    breaking: str = "true",
) -> str:
    return f"""
        select public.save_alert_candidate(
            '{candidate_id}',
            '{user_id}',
            '{article_id}',
            '{matched_at}'::timestamptz,
            {reasons},
            '{importance}',
            {score}::double precision,
            {breaking}
        )::text;
    """


def test_alert_candidate_schema_identity_and_access_boundary(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database

    assert (
        database.scalar(
            """
            select pg_get_constraintdef(oid)
            from pg_constraint
            where conrelid = 'public.alert_candidates'::regclass
              and contype = 'p';
            """
        )
        == "PRIMARY KEY (candidate_id)"
    )
    assert (
        database.scalar(
            """
            select pg_get_constraintdef(oid)
            from pg_constraint
            where conname = 'alert_candidates_user_article_uidx';
            """
        )
        == "UNIQUE (user_id, article_id)"
    )
    assert (
        database.scalar(
            """
            select pg_get_constraintdef(oid)
            from pg_constraint
            where conname = 'alert_candidates_article_fk';
            """
        )
        == "FOREIGN KEY (article_id) REFERENCES articles(article_id) ON DELETE RESTRICT"
    )
    assert (
        database.scalar(
            """
            select relrowsecurity
            from pg_class
            where oid = 'public.alert_candidates'::regclass;
            """
        )
        == "t"
    )
    assert (
        database.scalar(
            """
            select
                has_table_privilege('service_role', 'public.alert_candidates', 'SELECT'),
                has_table_privilege('service_role', 'public.alert_candidates', 'INSERT'),
                has_table_privilege('service_role', 'public.alert_candidates', 'UPDATE'),
                has_table_privilege('service_role', 'public.alert_candidates', 'DELETE'),
                has_table_privilege('anon', 'public.alert_candidates', 'SELECT'),
                has_table_privilege('authenticated', 'public.alert_candidates', 'SELECT');
            """
        )
        == "t|f|f|f|f|f"
    )
    assert (
        database.scalar(
            """
            select
                has_function_privilege(
                    'service_role',
                    'public.save_alert_candidate(text,text,text,timestamptz,text[],text,double precision,boolean)',
                    'EXECUTE'
                ),
                has_function_privilege(
                    'anon',
                    'public.save_alert_candidate(text,text,text,timestamptz,text[],text,double precision,boolean)',
                    'EXECUTE'
                ),
                has_function_privilege(
                    'authenticated',
                    'public.save_alert_candidate(text,text,text,timestamptz,text[],text,double precision,boolean)',
                    'EXECUTE'
                );
            """
        )
        == "t|f|f"
    )


def test_alert_candidate_save_is_first_write_wins_and_idempotent(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    _insert_de012_article(database, "de012-article-first-write")

    first = database.scalar(
        _save_de012_candidate_sql(
            candidate_id="de012-candidate-first-write",
            user_id="de012-user-first-write",
            article_id="de012-article-first-write",
        )
    )
    second = database.scalar(
        _save_de012_candidate_sql(
            candidate_id="de012-candidate-first-write",
            user_id="de012-user-first-write",
            article_id="de012-article-first-write",
            matched_at="2026-08-15T09:05:00Z",
            reasons="array['topic:AI']::text[]",
            importance="NORMAL",
            score="0.8",
            breaking="false",
        )
    )

    assert '"outcome": "CREATED"' in first
    assert '"outcome": "ALREADY_EXISTS"' in second
    assert (
        database.scalar(
            """
            select count(*)
            from public.alert_candidates
            where user_id = 'de012-user-first-write'
              and article_id = 'de012-article-first-write';
            """
        )
        == "1"
    )
    assert (
        database.scalar(
            """
            select
                (matched_at at time zone 'UTC')::text || '+00|' ||
                array_to_string(match_reasons, ',') || '|' ||
                importance || '|' ||
                relevance_score::text || '|' ||
                breaking_eligible::text
            from public.alert_candidates
            where candidate_id = 'de012-candidate-first-write';
            """
        )
        == "2026-08-15 08:05:00+00|market:US,category:TECHNOLOGY|HIGH|0.95|true"
    )


def test_alert_candidate_concurrent_replay_creates_exactly_one_row(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    _insert_de012_article(database, "de012-article-concurrent")
    sql = _save_de012_candidate_sql(
        candidate_id="de012-candidate-concurrent",
        user_id="de012-user-concurrent",
        article_id="de012-article-concurrent",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(executor.map(lambda _: database.scalar(sql), range(2)))

    assert sum('"outcome": "CREATED"' in output for output in outputs) == 1
    assert sum('"outcome": "ALREADY_EXISTS"' in output for output in outputs) == 1
    assert (
        database.scalar(
            """
            select count(*)
            from public.alert_candidates
            where user_id = 'de012-user-concurrent'
              and article_id = 'de012-article-concurrent';
            """
        )
        == "1"
    )


def test_alert_candidate_same_pair_with_different_candidate_id_is_rejected(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    _insert_de012_article(database, "de012-article-pair-conflict")
    database.run(
        _save_de012_candidate_sql(
            candidate_id="de012-candidate-pair-original",
            user_id="de012-user-pair-conflict",
            article_id="de012-article-pair-conflict",
        )
    )

    conflict = database.run(
        _save_de012_candidate_sql(
            candidate_id="de012-candidate-pair-other",
            user_id="de012-user-pair-conflict",
            article_id="de012-article-pair-conflict",
        ),
        check=False,
    )

    assert conflict.returncode != 0
    assert (
        database.scalar(
            """
            select count(*)
            from public.alert_candidates
            where user_id = 'de012-user-pair-conflict'
              and article_id = 'de012-article-pair-conflict';
            """
        )
        == "1"
    )


def test_alert_candidate_same_candidate_id_for_different_pair_is_rejected(
    classification_database: PostgresHarness,
) -> None:
    database = classification_database
    _insert_de012_article(database, "de012-article-id-collision-a")
    _insert_de012_article(database, "de012-article-id-collision-b")
    database.run(
        _save_de012_candidate_sql(
            candidate_id="de012-candidate-id-collision",
            user_id="de012-user-id-collision-a",
            article_id="de012-article-id-collision-a",
        )
    )

    conflict = database.run(
        _save_de012_candidate_sql(
            candidate_id="de012-candidate-id-collision",
            user_id="de012-user-id-collision-b",
            article_id="de012-article-id-collision-b",
        ),
        check=False,
    )

    assert conflict.returncode != 0
    assert (
        database.scalar(
            """
            select count(*)
            from public.alert_candidates
            where candidate_id = 'de012-candidate-id-collision';
            """
        )
        == "1"
    )
