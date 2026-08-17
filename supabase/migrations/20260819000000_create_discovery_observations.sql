begin;

create function public.discovery_observation_samples_are_valid(p_samples jsonb)
returns boolean
language sql
immutable
strict
parallel safe
as $$
    select
        jsonb_typeof(p_samples) = 'array'
        and jsonb_array_length(p_samples) <= 3
        and not exists (
            select 1
            from jsonb_array_elements(p_samples) as sample
            where case
                when jsonb_typeof(sample.value) <> 'object' then true
                when jsonb_typeof(sample.value -> 'url') is distinct from 'string' then true
                when jsonb_typeof(sample.value -> 'hostname') is distinct from 'string' then true
                when jsonb_typeof(sample.value -> 'observed_at') is distinct from 'string' then true
                else (sample.value - 'url' - 'hostname' - 'observed_at') <> '{}'::jsonb
            end
        );
$$;

comment on function public.discovery_observation_samples_are_valid(jsonb) is
    'Bounds discovery samples to at most three locator-only objects; body text is never stored.';

create table public.discovery_observation_daily (
    observation_day date not null,
    query_id text not null,
    domain text not null,
    observation_status text not null,
    observation_count bigint not null,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    samples jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),

    constraint discovery_observation_daily_pk
        primary key (observation_day, query_id, domain, observation_status),
    constraint discovery_observation_daily_query_nonblank_check
        check (length(btrim(query_id)) > 0),
    constraint discovery_observation_daily_domain_check
        check (domain in ('LAW_POLICY', 'ENERGY', 'TECHNOLOGY', 'REAL_ESTATE', 'FINANCE')),
    constraint discovery_observation_daily_status_check
        check (
            observation_status in (
                'UNKNOWN',
                'AMBIGUOUS_ROUTE',
                'RIGHTS_METADATA_DENIED',
                'IDENTITY_INCOMPATIBLE',
                'ADMITTED'
            )
        ),
    constraint discovery_observation_daily_count_check
        check (observation_count >= 1),
    constraint discovery_observation_daily_seen_order_check
        check (first_seen_at <= last_seen_at),
    constraint discovery_observation_daily_samples_check
        check (public.discovery_observation_samples_are_valid(samples))
);

comment on table public.discovery_observation_daily is
    'Bounded daily discovery aggregates; never a per-candidate store and never promotable to articles.';
comment on constraint discovery_observation_daily_pk on public.discovery_observation_daily is
    'Aggregate identity is the full 4-tuple; domain is part of the key including the sample cap.';
comment on column public.discovery_observation_daily.observation_status is
    'Umbrella outcome across both admission stages: route resolution and the admission gate.';
comment on column public.discovery_observation_daily.samples is
    'At most three locator-only samples; retained 7 days while the aggregate row is retained 30 days.';

alter table public.discovery_observation_daily enable row level security;

create table public.discovery_benchmark_evidence (
    benchmark_run_id text not null,
    sentinel_article_key text not null,
    source_id text not null,
    query_id text not null,
    evidence_day date not null,
    published_at timestamptz,
    direct_first_seen_at timestamptz,
    gdelt_first_usable_seen_at timestamptz,
    created_at timestamptz not null default now(),

    constraint discovery_benchmark_evidence_pk
        primary key (benchmark_run_id, sentinel_article_key),
    constraint discovery_benchmark_evidence_identity_nonblank_check
        check (
            length(btrim(benchmark_run_id)) > 0
            and length(btrim(sentinel_article_key)) > 0
            and length(btrim(source_id)) > 0
            and length(btrim(query_id)) > 0
        )
);

comment on table public.discovery_benchmark_evidence is
    'One row per sentinel article per benchmark run, retained 90 days; repeated sightings only move gdelt_first_usable_seen_at earlier.';
comment on column public.discovery_benchmark_evidence.published_at is
    'Publisher timestamp when available; null sentinels are excluded from latency and capture metrics.';
comment on column public.discovery_benchmark_evidence.gdelt_first_usable_seen_at is
    'Earliest sighting that already passed discovery admission with observation_status ADMITTED; callers must leave it null for UNKNOWN, AMBIGUOUS_ROUTE, RIGHTS_METADATA_DENIED, and IDENTITY_INCOMPATIBLE sightings. Never a raw provider observation time.';

alter table public.discovery_benchmark_evidence enable row level security;

create function public.record_discovery_observation(
    p_observation_day date,
    p_query_id text,
    p_domain text,
    p_observation_status text,
    p_observed_at timestamptz,
    p_sample_url text default null,
    p_sample_hostname text default null
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_row public.discovery_observation_daily%rowtype;
    v_sample jsonb := null;
begin
    if p_observation_day is null then
        raise exception 'observation day is required' using errcode = '22023';
    end if;
    if p_query_id is null or length(btrim(p_query_id)) = 0 then
        raise exception 'query id is required' using errcode = '22023';
    end if;
    if p_domain is null
       or p_domain not in ('LAW_POLICY', 'ENERGY', 'TECHNOLOGY', 'REAL_ESTATE', 'FINANCE') then
        raise exception 'invalid discovery domain' using errcode = '22023';
    end if;
    if p_observation_status is null
       or p_observation_status not in (
            'UNKNOWN',
            'AMBIGUOUS_ROUTE',
            'RIGHTS_METADATA_DENIED',
            'IDENTITY_INCOMPATIBLE',
            'ADMITTED'
       ) then
        raise exception 'invalid observation status' using errcode = '22023';
    end if;
    if p_observed_at is null then
        raise exception 'observed_at is required' using errcode = '22023';
    end if;
    if (p_sample_url is null) <> (p_sample_hostname is null) then
        raise exception 'sample url and hostname must be provided together'
            using errcode = '22023';
    end if;
    if p_sample_url is not null
       and (length(btrim(p_sample_url)) = 0 or length(btrim(p_sample_hostname)) = 0) then
        raise exception 'sample url and hostname must not be blank' using errcode = '22023';
    end if;

    if p_sample_url is not null then
        v_sample := jsonb_build_object(
            'url', p_sample_url,
            'hostname', p_sample_hostname,
            'observed_at', to_jsonb(p_observed_at)
        );
    end if;

    insert into public.discovery_observation_daily as daily (
        observation_day,
        query_id,
        domain,
        observation_status,
        observation_count,
        first_seen_at,
        last_seen_at,
        samples
    ) values (
        p_observation_day,
        p_query_id,
        p_domain,
        p_observation_status,
        1,
        p_observed_at,
        p_observed_at,
        case when v_sample is null then '[]'::jsonb else jsonb_build_array(v_sample) end
    )
    on conflict (observation_day, query_id, domain, observation_status) do update
    set observation_count = daily.observation_count + 1,
        first_seen_at = least(daily.first_seen_at, excluded.first_seen_at),
        last_seen_at = greatest(daily.last_seen_at, excluded.last_seen_at),
        samples = case
            when v_sample is null then daily.samples
            when jsonb_array_length(daily.samples) >= 3 then daily.samples
            else daily.samples || jsonb_build_array(v_sample)
        end
    returning * into v_row;

    -- observation_count starts at 1 on insert and only ever increments, so a count of 1 is an
    -- exact, race-free signal that this call created the aggregate row.
    return jsonb_build_object(
        'outcome', case when v_row.observation_count = 1 then 'CREATED' else 'UPDATED' end,
        'observation_count', v_row.observation_count,
        'first_seen_at', to_jsonb(v_row.first_seen_at),
        'last_seen_at', to_jsonb(v_row.last_seen_at),
        'sample_count', jsonb_array_length(v_row.samples)
    );
end;
$$;

comment on function public.record_discovery_observation(
    date, text, text, text, timestamptz, text, text
) is
    'Folds one sighting into its 4-tuple aggregate: increments the count, preserves first_seen_at, advances last_seen_at, and caps samples at three per aggregate key.';

create function public.record_discovery_benchmark_evidence(
    p_benchmark_run_id text,
    p_sentinel_article_key text,
    p_source_id text,
    p_query_id text,
    p_evidence_day date,
    p_published_at timestamptz default null,
    p_direct_first_seen_at timestamptz default null,
    p_gdelt_first_usable_seen_at timestamptz default null
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_created boolean;
    v_published_at timestamptz;
    v_direct_first_seen_at timestamptz;
    v_gdelt_first_usable_seen_at timestamptz;
begin
    if p_benchmark_run_id is null or length(btrim(p_benchmark_run_id)) = 0 then
        raise exception 'benchmark run id is required' using errcode = '22023';
    end if;
    if p_sentinel_article_key is null or length(btrim(p_sentinel_article_key)) = 0 then
        raise exception 'sentinel article key is required' using errcode = '22023';
    end if;
    if p_source_id is null or length(btrim(p_source_id)) = 0 then
        raise exception 'source id is required' using errcode = '22023';
    end if;
    if p_query_id is null or length(btrim(p_query_id)) = 0 then
        raise exception 'query id is required' using errcode = '22023';
    end if;
    if p_evidence_day is null then
        raise exception 'evidence day is required' using errcode = '22023';
    end if;

    insert into public.discovery_benchmark_evidence as evidence (
        benchmark_run_id,
        sentinel_article_key,
        source_id,
        query_id,
        evidence_day,
        published_at,
        direct_first_seen_at,
        gdelt_first_usable_seen_at
    ) values (
        p_benchmark_run_id,
        p_sentinel_article_key,
        p_source_id,
        p_query_id,
        p_evidence_day,
        p_published_at,
        p_direct_first_seen_at,
        p_gdelt_first_usable_seen_at
    )
    on conflict (benchmark_run_id, sentinel_article_key) do update
    -- least() ignores nulls, so an earlier timestamp is never replaced by a later one and a
    -- repeated sighting can only move these timestamps earlier. A sighting that was not
    -- ADMITTED must arrive with a null p_gdelt_first_usable_seen_at, which then leaves any
    -- stored usable timestamp untouched.
    set published_at = coalesce(evidence.published_at, excluded.published_at),
        direct_first_seen_at = least(
            evidence.direct_first_seen_at,
            excluded.direct_first_seen_at
        ),
        gdelt_first_usable_seen_at = least(
            evidence.gdelt_first_usable_seen_at,
            excluded.gdelt_first_usable_seen_at
        )
    returning
        (xmax = 0),
        evidence.published_at,
        evidence.direct_first_seen_at,
        evidence.gdelt_first_usable_seen_at
    into v_created, v_published_at, v_direct_first_seen_at, v_gdelt_first_usable_seen_at;

    return jsonb_build_object(
        'outcome', case when v_created then 'CREATED' else 'UPDATED' end,
        'published_at', to_jsonb(v_published_at),
        'direct_first_seen_at', to_jsonb(v_direct_first_seen_at),
        'gdelt_first_usable_seen_at', to_jsonb(v_gdelt_first_usable_seen_at)
    );
end;
$$;

comment on function public.record_discovery_benchmark_evidence(
    text, text, text, text, date, timestamptz, timestamptz, timestamptz
) is
    'Records one sentinel per benchmark run, preserving the earliest timestamps; p_gdelt_first_usable_seen_at must be supplied only for an ADMITTED sighting and is never re-validated here.';

create function public.prune_discovery_records(
    p_observation_cutoff_day date,
    p_sample_cutoff_day date,
    p_evidence_cutoff_day date
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_deleted_observations bigint := 0;
    v_cleared_samples bigint := 0;
    v_deleted_evidence bigint := 0;
begin
    if p_observation_cutoff_day is null
       or p_sample_cutoff_day is null
       or p_evidence_cutoff_day is null then
        raise exception 'all retention cutoffs are required' using errcode = '22023';
    end if;
    if p_sample_cutoff_day < p_observation_cutoff_day then
        raise exception 'sample cutoff must not precede the observation cutoff'
            using errcode = '22023';
    end if;

    delete from public.discovery_observation_daily
    where observation_day < p_observation_cutoff_day;
    get diagnostics v_deleted_observations = row_count;

    update public.discovery_observation_daily
    set samples = '[]'::jsonb
    where observation_day < p_sample_cutoff_day
      and samples <> '[]'::jsonb;
    get diagnostics v_cleared_samples = row_count;

    delete from public.discovery_benchmark_evidence
    where evidence_day < p_evidence_cutoff_day;
    get diagnostics v_deleted_evidence = row_count;

    return jsonb_build_object(
        'deleted_observations', v_deleted_observations,
        'cleared_samples', v_cleared_samples,
        'deleted_benchmark_evidence', v_deleted_evidence
    );
end;
$$;

comment on function public.prune_discovery_records(date, date, date) is
    'Deletes discovery rows strictly older than each cutoff day and clears expired samples in place; invocation scheduling is owned outside the database.';

revoke all on function public.discovery_observation_samples_are_valid(jsonb)
    from public, anon, authenticated;
revoke all on function public.record_discovery_observation(
    date, text, text, text, timestamptz, text, text
) from public, anon, authenticated;
revoke all on function public.record_discovery_benchmark_evidence(
    text, text, text, text, date, timestamptz, timestamptz, timestamptz
) from public, anon, authenticated;
revoke all on function public.prune_discovery_records(date, date, date)
    from public, anon, authenticated;

commit;
