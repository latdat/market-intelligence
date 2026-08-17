"""Real-SQL verification of the bounded discovery aggregate semantics.

Mock-client unit tests can only prove which RPC was called. First-seen preservation, the
three-sample cap keyed by the full 4-tuple, earliest-timestamp preservation, and retention
pruning are SQL semantics, so they are verified here against an ephemeral local PostgreSQL
cluster with every migration applied.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DiscoveryPostgresHarness:
    psql: str
    port: int

    def run(self, sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PGHOST": "127.0.0.1",
            "PGPORT": str(self.port),
            "PGUSER": "postgres",
            "PGDATABASE": "postgres",
        }
        return subprocess.run(
            [self.psql, "-X", "-v", "ON_ERROR_STOP=1", "-A", "-t", "-c", sql],
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
def discovery_database(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[DiscoveryPostgresHarness]:
    initdb = _find_postgres_binary("initdb")
    pg_ctl = _find_postgres_binary("pg_ctl")
    psql = _find_postgres_binary("psql")
    if initdb is None or pg_ctl is None or psql is None:
        pytest.skip("local PostgreSQL binaries are required for discovery integration tests")

    cluster_directory = tmp_path_factory.mktemp("gdelt002a-postgres")
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

    database = DiscoveryPostgresHarness(psql=psql, port=port)
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
            [pg_ctl, "-D", str(cluster_directory), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


def _sql_text(value: str | None) -> str:
    return "null" if value is None else f"'{value}'"


def _moment(value: object) -> datetime:
    """Parse a timestamptz the database rendered in its own session timezone."""
    assert isinstance(value, str)
    return datetime.fromisoformat(value).astimezone(UTC)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _record_observation(
    database: DiscoveryPostgresHarness,
    *,
    day: str,
    query_id: str,
    domain: str,
    status: str,
    observed_at: str,
    sample_url: str | None = None,
    sample_hostname: str | None = None,
) -> dict[str, object]:
    return database.payload(
        f"""
        select public.record_discovery_observation(
            '{day}'::date,
            '{query_id}',
            '{domain}',
            '{status}',
            '{observed_at}'::timestamptz,
            {_sql_text(sample_url)},
            {_sql_text(sample_hostname)}
        );
        """
    )


def _record_evidence(
    database: DiscoveryPostgresHarness,
    *,
    run_id: str,
    sentinel_key: str,
    day: str,
    published_at: str | None = None,
    direct_first_seen_at: str | None = None,
    gdelt_first_usable_seen_at: str | None = None,
) -> dict[str, object]:
    """Record durable evidence.

    ``gdelt_first_usable_seen_at`` is only ever supplied here for a sighting that already
    passed admission with ``ADMITTED``. An unusable sighting (UNKNOWN, AMBIGUOUS_ROUTE,
    RIGHTS_METADATA_DENIED, IDENTITY_INCOMPATIBLE) is recorded with it left null.
    """
    return database.payload(
        f"""
        select public.record_discovery_benchmark_evidence(
            '{run_id}',
            '{sentinel_key}',
            'xx_editorial_fixture_source',
            'us_finance',
            '{day}'::date,
            {_sql_text(published_at)}::timestamptz,
            {_sql_text(direct_first_seen_at)}::timestamptz,
            {_sql_text(gdelt_first_usable_seen_at)}::timestamptz
        );
        """
    )


def _sample_count(
    database: DiscoveryPostgresHarness,
    *,
    day: str,
    query_id: str,
    domain: str,
    status: str,
) -> int:
    return int(
        database.scalar(
            f"""
            select jsonb_array_length(samples)
            from public.discovery_observation_daily
            where observation_day = '{day}'::date
              and query_id = '{query_id}'
              and domain = '{domain}'
              and observation_status = '{status}';
            """
        )
    )


def test_first_seen_is_preserved_and_last_seen_advances(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    query_id = "first_seen_cell"

    created = _record_observation(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="FINANCE",
        status="ADMITTED",
        observed_at="2026-08-18T12:00:00+00",
    )
    later = _record_observation(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="FINANCE",
        status="ADMITTED",
        observed_at="2026-08-18T18:00:00+00",
    )
    out_of_order = _record_observation(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="FINANCE",
        status="ADMITTED",
        observed_at="2026-08-18T09:00:00+00",
    )

    assert created["outcome"] == "CREATED"
    assert later["outcome"] == "UPDATED"
    assert out_of_order["outcome"] == "UPDATED"
    assert out_of_order["observation_count"] == 3
    assert _moment(out_of_order["first_seen_at"]) == _utc("2026-08-18T09:00:00+00:00")
    assert _moment(out_of_order["last_seen_at"]) == _utc("2026-08-18T18:00:00+00:00")


def test_samples_are_capped_at_three_per_aggregate_key(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    query_id = "sample_cap_cell"

    for index in range(5):
        result = _record_observation(
            discovery_database,
            day="2026-08-18",
            query_id=query_id,
            domain="FINANCE",
            status="UNKNOWN",
            observed_at="2026-08-18T12:00:00+00",
            sample_url=f"https://unlisted.example.test/story-{index}",
            sample_hostname="unlisted.example.test",
        )

    assert result["observation_count"] == 5
    assert result["sample_count"] == 3
    assert (
        _sample_count(
            discovery_database,
            day="2026-08-18",
            query_id=query_id,
            domain="FINANCE",
            status="UNKNOWN",
        )
        == 3
    )


def test_sample_cap_is_independent_for_each_domain(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    query_id = "domain_cap_cell"

    for domain in ("FINANCE", "ENERGY"):
        for index in range(4):
            _record_observation(
                discovery_database,
                day="2026-08-18",
                query_id=query_id,
                domain=domain,
                status="UNKNOWN",
                observed_at="2026-08-18T12:00:00+00",
                sample_url=f"https://unlisted.example.test/{domain}-{index}",
                sample_hostname="unlisted.example.test",
            )

    finance_samples = _sample_count(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="FINANCE",
        status="UNKNOWN",
    )
    energy_samples = _sample_count(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="ENERGY",
        status="UNKNOWN",
    )

    assert finance_samples == 3
    assert energy_samples == 3


def test_each_observation_status_is_its_own_aggregate(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    query_id = "status_cell"

    admitted = _record_observation(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="FINANCE",
        status="ADMITTED",
        observed_at="2026-08-18T12:00:00+00",
    )
    identity_incompatible = _record_observation(
        discovery_database,
        day="2026-08-18",
        query_id=query_id,
        domain="FINANCE",
        status="IDENTITY_INCOMPATIBLE",
        observed_at="2026-08-18T12:00:00+00",
    )

    assert admitted["outcome"] == "CREATED"
    assert identity_incompatible["outcome"] == "CREATED"


def test_invalid_observation_input_is_rejected(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    failed = discovery_database.run(
        """
        select public.record_discovery_observation(
            '2026-08-18'::date, 'invalid_cell', 'NOT_A_DOMAIN', 'ADMITTED',
            '2026-08-18T12:00:00+00'::timestamptz, null, null
        );
        """,
        check=False,
    )
    partial_sample = discovery_database.run(
        """
        select public.record_discovery_observation(
            '2026-08-18'::date, 'invalid_cell', 'FINANCE', 'ADMITTED',
            '2026-08-18T12:00:00+00'::timestamptz, 'https://unlisted.example.test/a', null
        );
        """,
        check=False,
    )

    assert failed.returncode != 0
    assert "invalid discovery domain" in failed.stderr
    assert partial_sample.returncode != 0
    assert "must be provided together" in partial_sample.stderr


def test_benchmark_evidence_keeps_the_earliest_usable_first_seen(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    created = _record_evidence(
        discovery_database,
        run_id="run-earliest",
        sentinel_key="sentinel-1",
        day="2026-08-18",
        published_at="2026-08-18T10:00:00+00",
        gdelt_first_usable_seen_at="2026-08-18T11:00:00+00",
    )
    later_sighting = _record_evidence(
        discovery_database,
        run_id="run-earliest",
        sentinel_key="sentinel-1",
        day="2026-08-18",
        gdelt_first_usable_seen_at="2026-08-18T15:00:00+00",
    )
    earlier_sighting = _record_evidence(
        discovery_database,
        run_id="run-earliest",
        sentinel_key="sentinel-1",
        day="2026-08-18",
        gdelt_first_usable_seen_at="2026-08-18T09:30:00+00",
    )

    assert created["outcome"] == "CREATED"
    assert later_sighting["outcome"] == "UPDATED"
    assert _moment(later_sighting["gdelt_first_usable_seen_at"]) == _utc(
        "2026-08-18T11:00:00+00:00"
    )
    assert _moment(earlier_sighting["gdelt_first_usable_seen_at"]) == _utc(
        "2026-08-18T09:30:00+00:00"
    )
    assert _moment(earlier_sighting["published_at"]) == _utc("2026-08-18T10:00:00+00:00")
    assert (
        discovery_database.scalar(
            "select count(*) from public.discovery_benchmark_evidence "
            "where benchmark_run_id = 'run-earliest';"
        )
        == "1"
    )


def test_evidence_exists_before_any_usable_sighting_and_is_filled_later(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    """An unusable sighting creates the row without claiming a usable capture time.

    A sentinel first seen as UNKNOWN at 10:00 and only ADMITTED at 10:20 must record 10:20 as
    its usable capture, never 10:00.
    """
    unusable_sighting = _record_evidence(
        discovery_database,
        run_id="run-usable",
        sentinel_key="sentinel-late-admission",
        day="2026-08-18",
        published_at="2026-08-18T09:50:00+00",
    )
    first_usable_sighting = _record_evidence(
        discovery_database,
        run_id="run-usable",
        sentinel_key="sentinel-late-admission",
        day="2026-08-18",
        gdelt_first_usable_seen_at="2026-08-18T10:20:00+00",
    )

    assert unusable_sighting["outcome"] == "CREATED"
    assert unusable_sighting["gdelt_first_usable_seen_at"] is None
    assert first_usable_sighting["outcome"] == "UPDATED"
    assert _moment(first_usable_sighting["gdelt_first_usable_seen_at"]) == _utc(
        "2026-08-18T10:20:00+00:00"
    )


def test_unusable_sighting_never_moves_a_stored_usable_timestamp(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    _record_evidence(
        discovery_database,
        run_id="run-usable-guard",
        sentinel_key="sentinel-1",
        day="2026-08-18",
        gdelt_first_usable_seen_at="2026-08-18T10:20:00+00",
    )
    after_unusable = _record_evidence(
        discovery_database,
        run_id="run-usable-guard",
        sentinel_key="sentinel-1",
        day="2026-08-18",
    )

    assert _moment(after_unusable["gdelt_first_usable_seen_at"]) == _utc(
        "2026-08-18T10:20:00+00:00"
    )


def test_retention_pruning_respects_every_window(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    query_id = "retention_cell"
    for day in ("2026-01-01", "2026-02-15", "2026-06-01"):
        _record_observation(
            discovery_database,
            day=day,
            query_id=query_id,
            domain="FINANCE",
            status="UNKNOWN",
            observed_at=f"{day}T12:00:00+00",
            sample_url=f"https://unlisted.example.test/{day}",
            sample_hostname="unlisted.example.test",
        )
    for day, sentinel in (("2026-01-05", "expired"), ("2026-06-05", "current")):
        _record_evidence(
            discovery_database,
            run_id="run-retention",
            sentinel_key=sentinel,
            day=day,
            gdelt_first_usable_seen_at=f"{day}T12:00:00+00",
        )

    result = discovery_database.payload(
        """
        select public.prune_discovery_records(
            '2026-02-01'::date, '2026-03-01'::date, '2026-02-01'::date
        );
        """
    )

    remaining_days = discovery_database.scalar(
        f"""
        select string_agg(observation_day::text, ',' order by observation_day)
        from public.discovery_observation_daily
        where query_id = '{query_id}';
        """
    )
    remaining_sentinels = discovery_database.scalar(
        "select string_agg(sentinel_article_key, ',' order by sentinel_article_key) "
        "from public.discovery_benchmark_evidence where benchmark_run_id = 'run-retention';"
    )

    assert result["deleted_observations"] == 1
    assert result["cleared_samples"] == 1
    assert result["deleted_benchmark_evidence"] == 1
    assert remaining_days == "2026-02-15,2026-06-01"
    assert remaining_sentinels == "current"
    assert (
        _sample_count(
            discovery_database,
            day="2026-02-15",
            query_id=query_id,
            domain="FINANCE",
            status="UNKNOWN",
        )
        == 0
    )
    assert (
        _sample_count(
            discovery_database,
            day="2026-06-01",
            query_id=query_id,
            domain="FINANCE",
            status="UNKNOWN",
        )
        == 1
    )


def test_pruning_rejects_cutoffs_that_expire_samples_after_aggregates(
    discovery_database: DiscoveryPostgresHarness,
) -> None:
    failed = discovery_database.run(
        """
        select public.prune_discovery_records(
            '2026-03-01'::date, '2026-02-01'::date, '2026-02-01'::date
        );
        """,
        check=False,
    )

    assert failed.returncode != 0
    assert "sample cutoff must not precede" in failed.stderr
