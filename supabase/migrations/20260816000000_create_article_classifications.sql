begin;

create function public.classification_markets_are_canonical(p_values text[])
returns boolean
language sql
immutable
strict
parallel safe
as $$
    select
        p_values <@ array['VN', 'US', 'EU', 'CN']::text[]
        and cardinality(p_values) <= 4
        and p_values = array(
            select value
            from (
                select distinct unnest(p_values) as value
            ) as distinct_values
            order by case value
                when 'VN' then 1
                when 'US' then 2
                when 'EU' then 3
                when 'CN' then 4
            end
        );
$$;

create function public.classification_topics_are_canonical(p_values text[])
returns boolean
language sql
immutable
strict
parallel safe
as $$
    select
        p_values <@ array[
            'AI',
            'BANKING',
            'INTEREST_RATES',
            'OIL_GAS',
            'REAL_ESTATE',
            'REGULATION',
            'RENEWABLE_ENERGY',
            'SEMICONDUCTORS'
        ]::text[]
        and cardinality(p_values) <= 5
        and p_values = array(
            select value
            from (
                select distinct unnest(p_values) as value
            ) as distinct_values
            order by value
        );
$$;

create table public.article_classifications (
    article_id text not null,
    classifier_version text not null,
    status text not null default 'RETRYABLE',

    is_relevant boolean null,
    markets text[] null,
    category text null,
    topics text[] null,
    confidence double precision null,
    classified_at timestamptz null,

    requested_model text not null,
    provider_model text null,
    prompt_version text not null,
    taxonomy_version text not null,
    provider_request_id text null,
    system_fingerprint text null,

    prompt_tokens bigint not null default 0,
    prompt_cache_hit_tokens bigint not null default 0,
    prompt_cache_miss_tokens bigint not null default 0,
    completion_tokens bigint not null default 0,
    total_tokens bigint not null default 0,
    estimated_cost_usd numeric(24, 12) not null default 0,
    last_pricing_id text null,
    last_pricing_window text null,

    attempt_count smallint not null default 0,
    max_attempts smallint not null default 3,
    last_provider_attempts smallint null,

    claim_token uuid null,
    claimed_at timestamptz null,
    lease_expires_at timestamptz null,

    next_attempt_at timestamptz null default now(),
    last_error_category text null,
    last_http_status integer null,
    last_error_retryable boolean null,
    last_error_at timestamptz null,
    quarantined_at timestamptz null,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint article_classifications_pk
        primary key (article_id, classifier_version),
    constraint article_classifications_article_fk
        foreign key (article_id)
        references public.articles(article_id)
        on delete restrict,
    constraint article_classifications_classifier_version_check
        check (
            length(classifier_version) <= 64
            and classifier_version ~ '^classification-v[1-9][0-9]*$'
        ),
    constraint article_classifications_status_check
        check (status in ('PROCESSING', 'RETRYABLE', 'SUCCEEDED', 'QUARANTINED')),
    constraint article_classifications_lineage_check
        check (
            length(requested_model) between 1 and 128
            and length(prompt_version) between 1 and 128
            and length(taxonomy_version) between 1 and 128
        ),
    constraint article_classifications_usage_nonnegative_check
        check (
            prompt_tokens >= 0
            and prompt_cache_hit_tokens >= 0
            and prompt_cache_miss_tokens >= 0
            and completion_tokens >= 0
            and total_tokens >= 0
            and estimated_cost_usd >= 0
        ),
    constraint article_classifications_usage_totals_check
        check (
            prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens
            and total_tokens = prompt_tokens + completion_tokens
        ),
    constraint article_classifications_attempts_check
        check (
            attempt_count between 0 and max_attempts
            and max_attempts between 1 and 12
            and (last_provider_attempts is null or last_provider_attempts between 0 and 3)
        ),
    constraint article_classifications_http_status_check
        check (last_http_status is null or last_http_status between 100 and 599),
    constraint article_classifications_category_check
        check (
            category is null
            or category in ('LAW_POLICY', 'ENERGY', 'TECHNOLOGY', 'REAL_ESTATE', 'FINANCE')
        ),
    constraint article_classifications_confidence_check
        check (
            confidence is null
            or (
                confidence between 0 and 1
                and confidence <> 'NaN'::double precision
                and confidence <> 'Infinity'::double precision
                and confidence <> '-Infinity'::double precision
            )
        ),
    constraint article_classifications_markets_check
        check (
            markets is null
            or public.classification_markets_are_canonical(markets)
        ),
    constraint article_classifications_topics_check
        check (
            topics is null
            or public.classification_topics_are_canonical(topics)
        ),
    constraint article_classifications_semantic_state_check
        check (
            (
                status = 'SUCCEEDED'
                and is_relevant is not null
                and markets is not null
                and topics is not null
                and confidence is not null
                and classified_at is not null
                and (
                    (
                        is_relevant
                        and cardinality(markets) between 1 and 4
                        and category is not null
                        and cardinality(topics) between 0 and 5
                    )
                    or (
                        not is_relevant
                        and cardinality(markets) = 0
                        and category is null
                        and cardinality(topics) = 0
                    )
                )
            )
            or (
                status <> 'SUCCEEDED'
                and is_relevant is null
                and markets is null
                and category is null
                and topics is null
                and confidence is null
                and classified_at is null
                and provider_model is null
                and provider_request_id is null
                and system_fingerprint is null
            )
        ),
    constraint article_classifications_lifecycle_state_check
        check (
            (
                status = 'PROCESSING'
                and attempt_count between 1 and max_attempts
                and claim_token is not null
                and claimed_at is not null
                and lease_expires_at is not null
                and lease_expires_at > claimed_at
                and next_attempt_at is null
                and quarantined_at is null
            )
            or (
                status = 'RETRYABLE'
                and attempt_count < max_attempts
                and claim_token is null
                and claimed_at is null
                and lease_expires_at is null
                and next_attempt_at is not null
                and quarantined_at is null
            )
            or (
                status = 'SUCCEEDED'
                and claim_token is null
                and claimed_at is null
                and lease_expires_at is null
                and next_attempt_at is null
                and quarantined_at is null
                and last_error_category is null
                and last_http_status is null
                and last_error_retryable is null
                and last_error_at is null
            )
            or (
                status = 'QUARANTINED'
                and claim_token is null
                and claimed_at is null
                and lease_expires_at is null
                and next_attempt_at is null
                and quarantined_at is not null
                and length(last_error_category) > 0
            )
        )
);

comment on table public.article_classifications is
    'Durable classification lifecycle keyed by article and classifier behavior version.';
comment on column public.article_classifications.attempt_count is
    'Number of durable classification invocations successfully claimed; not provider HTTP calls.';
comment on column public.article_classifications.last_provider_attempts is
    'Observed HTTP calls in the most recently persisted DE-008 invocation; independent of attempt_count.';
comment on column public.article_classifications.estimated_cost_usd is
    'Cumulative estimate from observed provider usage; not an authoritative billing ledger.';

create index article_classifications_retryable_claim_idx
on public.article_classifications (
    classifier_version,
    next_attempt_at,
    created_at,
    article_id
)
where status = 'RETRYABLE';

create index article_classifications_expired_lease_idx
on public.article_classifications (
    lease_expires_at,
    classifier_version,
    article_id
)
where status = 'PROCESSING';

create unique index article_classifications_claim_token_uidx
on public.article_classifications (claim_token)
where claim_token is not null;

create function public.guard_article_classification_transition()
returns trigger
language plpgsql
as $$
begin
    if tg_op = 'INSERT' then
        if new.status <> 'RETRYABLE' or new.attempt_count <> 0 then
            raise exception 'classification rows must start RETRYABLE with attempt_count zero'
                using errcode = '23514';
        end if;
        return new;
    end if;

    if tg_op = 'DELETE' then
        if old.status in ('SUCCEEDED', 'QUARANTINED') then
            raise exception 'terminal classification rows are immutable'
                using errcode = '55000';
        end if;
        return old;
    end if;

    if old.status in ('SUCCEEDED', 'QUARANTINED') then
        raise exception 'terminal classification rows are immutable'
            using errcode = '55000';
    end if;

    if old.status = 'RETRYABLE' and new.status not in ('PROCESSING', 'QUARANTINED') then
        raise exception 'invalid classification transition from RETRYABLE to %', new.status
            using errcode = '23514';
    end if;

    if old.status = 'PROCESSING'
       and new.status not in ('PROCESSING', 'RETRYABLE', 'SUCCEEDED', 'QUARANTINED') then
        raise exception 'invalid classification transition from PROCESSING to %', new.status
            using errcode = '23514';
    end if;

    return new;
end;
$$;

create function public.set_article_classification_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = clock_timestamp();
    return new;
end;
$$;

create trigger article_classifications_guard_transition
before insert or update or delete on public.article_classifications
for each row execute function public.guard_article_classification_transition();

create trigger article_classifications_set_updated_at
before update on public.article_classifications
for each row execute function public.set_article_classification_updated_at();

create function public.enqueue_article_classification(
    p_article_id text,
    p_classifier_version text,
    p_requested_model text,
    p_prompt_version text,
    p_taxonomy_version text,
    p_max_attempts smallint default 3
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_row public.article_classifications%rowtype;
    v_mismatched_fields text[];
begin
    if p_max_attempts is null or p_max_attempts not between 1 and 12 then
        raise exception 'max attempts must be between 1 and 12' using errcode = '22023';
    end if;

    insert into public.article_classifications (
        article_id,
        classifier_version,
        requested_model,
        prompt_version,
        taxonomy_version,
        max_attempts,
        next_attempt_at
    ) values (
        p_article_id,
        p_classifier_version,
        p_requested_model,
        p_prompt_version,
        p_taxonomy_version,
        p_max_attempts,
        clock_timestamp()
    )
    on conflict (article_id, classifier_version) do nothing
    returning * into v_row;

    if found then
        return jsonb_build_object('outcome', 'CREATED', 'record', to_jsonb(v_row));
    end if;

    select * into strict v_row
    from public.article_classifications
    where article_id = p_article_id
      and classifier_version = p_classifier_version;

    v_mismatched_fields := array_remove(array[
        case when v_row.requested_model is distinct from p_requested_model
            then 'requested_model' end,
        case when v_row.prompt_version is distinct from p_prompt_version
            then 'prompt_version' end,
        case when v_row.taxonomy_version is distinct from p_taxonomy_version
            then 'taxonomy_version' end
    ], null);

    if cardinality(v_mismatched_fields) > 0 then
        return jsonb_build_object(
            'outcome', 'LINEAGE_MISMATCH',
            'mismatched_fields', to_jsonb(v_mismatched_fields),
            'record', to_jsonb(v_row)
        );
    end if;

    return jsonb_build_object(
        'outcome', case v_row.status
            when 'RETRYABLE' then 'EXISTING_RETRYABLE'
            when 'PROCESSING' then 'EXISTING_PROCESSING'
            when 'SUCCEEDED' then 'ALREADY_SUCCEEDED'
            when 'QUARANTINED' then 'QUARANTINED'
        end,
        'record', to_jsonb(v_row)
    );
end;
$$;

create function public.claim_next_article_classification(
    p_classifier_version text,
    p_claim_token uuid,
    p_lease_seconds integer default 300
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_row public.article_classifications%rowtype;
begin
    if p_claim_token is null then
        raise exception 'claim token is required' using errcode = '22023';
    end if;
    if p_lease_seconds not between 30 and 900 then
        raise exception 'lease seconds must be between 30 and 900' using errcode = '22023';
    end if;

    select * into v_row
    from public.article_classifications
    where classifier_version = p_classifier_version
      and (
          (status = 'RETRYABLE' and next_attempt_at <= v_now)
          or (status = 'PROCESSING' and lease_expires_at <= v_now)
      )
    order by
        case when status = 'RETRYABLE' then next_attempt_at else lease_expires_at end,
        created_at,
        article_id
    for update skip locked
    limit 1;

    if not found then
        return jsonb_build_object('outcome', 'EMPTY', 'record', null);
    end if;

    if v_row.status = 'PROCESSING' and v_row.attempt_count >= v_row.max_attempts then
        update public.article_classifications
        set status = 'QUARANTINED',
            claim_token = null,
            claimed_at = null,
            lease_expires_at = null,
            next_attempt_at = null,
            last_error_category = 'lease_expired_attempt_budget_exhausted',
            last_error_retryable = false,
            last_error_at = v_now,
            quarantined_at = v_now
        where article_id = v_row.article_id
          and classifier_version = v_row.classifier_version
        returning * into v_row;

        return jsonb_build_object(
            'outcome', 'RECOVERED_QUARANTINED',
            'record', to_jsonb(v_row)
        );
    end if;

    update public.article_classifications
    set status = 'PROCESSING',
        attempt_count = attempt_count + 1,
        claim_token = p_claim_token,
        claimed_at = v_now,
        lease_expires_at = v_now + make_interval(secs => p_lease_seconds),
        next_attempt_at = null,
        quarantined_at = null
    where article_id = v_row.article_id
      and classifier_version = v_row.classifier_version
      and attempt_count < max_attempts
    returning * into v_row;

    if not found then
        raise exception 'classification invocation budget exhausted' using errcode = '40001';
    end if;

    return jsonb_build_object('outcome', 'CLAIMED', 'record', to_jsonb(v_row));
end;
$$;

create function public.renew_article_classification_lease(
    p_article_id text,
    p_classifier_version text,
    p_claim_token uuid,
    p_lease_seconds integer default 300
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_row public.article_classifications%rowtype;
begin
    if p_lease_seconds not between 30 and 900 then
        raise exception 'lease seconds must be between 30 and 900' using errcode = '22023';
    end if;

    update public.article_classifications
    set lease_expires_at = v_now + make_interval(secs => p_lease_seconds)
    where article_id = p_article_id
      and classifier_version = p_classifier_version
      and status = 'PROCESSING'
      and claim_token = p_claim_token
      and lease_expires_at > v_now
    returning * into v_row;

    if not found then
        return jsonb_build_object('outcome', 'LOST_CLAIM', 'record', null);
    end if;
    return jsonb_build_object('outcome', 'RENEWED', 'record', to_jsonb(v_row));
end;
$$;

create function public.complete_article_classification(
    p_article_id text,
    p_classifier_version text,
    p_claim_token uuid,
    p_is_relevant boolean,
    p_markets text[],
    p_category text,
    p_topics text[],
    p_confidence double precision,
    p_classified_at timestamptz,
    p_provider_model text,
    p_provider_request_id text,
    p_system_fingerprint text,
    p_prompt_tokens bigint,
    p_prompt_cache_hit_tokens bigint,
    p_prompt_cache_miss_tokens bigint,
    p_completion_tokens bigint,
    p_total_tokens bigint,
    p_estimated_cost_usd numeric,
    p_pricing_id text,
    p_pricing_window text,
    p_provider_attempts smallint
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_row public.article_classifications%rowtype;
begin
    if p_provider_attempts is null or p_provider_attempts not between 1 and 3 then
        raise exception 'successful provider attempts must be between 1 and 3'
            using errcode = '22023';
    end if;
    if p_prompt_tokens < 0
       or p_prompt_cache_hit_tokens < 0
       or p_prompt_cache_miss_tokens < 0
       or p_completion_tokens < 0
       or p_total_tokens < 0
       or p_estimated_cost_usd < 0
       or p_prompt_tokens <> p_prompt_cache_hit_tokens + p_prompt_cache_miss_tokens
       or p_total_tokens <> p_prompt_tokens + p_completion_tokens then
        raise exception 'invalid provider usage totals' using errcode = '22023';
    end if;

    update public.article_classifications
    set status = 'SUCCEEDED',
        is_relevant = p_is_relevant,
        markets = p_markets,
        category = p_category,
        topics = p_topics,
        confidence = p_confidence,
        classified_at = p_classified_at,
        provider_model = p_provider_model,
        provider_request_id = p_provider_request_id,
        system_fingerprint = p_system_fingerprint,
        prompt_tokens = prompt_tokens + p_prompt_tokens,
        prompt_cache_hit_tokens = prompt_cache_hit_tokens + p_prompt_cache_hit_tokens,
        prompt_cache_miss_tokens = prompt_cache_miss_tokens + p_prompt_cache_miss_tokens,
        completion_tokens = completion_tokens + p_completion_tokens,
        total_tokens = total_tokens + p_total_tokens,
        estimated_cost_usd = estimated_cost_usd + p_estimated_cost_usd,
        last_pricing_id = p_pricing_id,
        last_pricing_window = p_pricing_window,
        last_provider_attempts = p_provider_attempts,
        claim_token = null,
        claimed_at = null,
        lease_expires_at = null,
        next_attempt_at = null,
        last_error_category = null,
        last_http_status = null,
        last_error_retryable = null,
        last_error_at = null,
        quarantined_at = null
    where article_id = p_article_id
      and classifier_version = p_classifier_version
      and status = 'PROCESSING'
      and claim_token = p_claim_token
      and lease_expires_at > v_now
    returning * into v_row;

    if found then
        return jsonb_build_object('outcome', 'SUCCEEDED', 'record', to_jsonb(v_row));
    end if;

    select * into v_row
    from public.article_classifications
    where article_id = p_article_id
      and classifier_version = p_classifier_version;

    if found and v_row.status = 'SUCCEEDED' then
        return jsonb_build_object(
            'outcome', 'ALREADY_SUCCEEDED',
            'record', to_jsonb(v_row)
        );
    end if;
    return jsonb_build_object('outcome', 'LOST_CLAIM', 'record', null);
end;
$$;

create function public.fail_article_classification(
    p_article_id text,
    p_classifier_version text,
    p_claim_token uuid,
    p_error_category text,
    p_last_http_status integer,
    p_error_retryable boolean,
    p_disposition text,
    p_prompt_tokens bigint,
    p_prompt_cache_hit_tokens bigint,
    p_prompt_cache_miss_tokens bigint,
    p_completion_tokens bigint,
    p_total_tokens bigint,
    p_estimated_cost_usd numeric,
    p_pricing_id text,
    p_pricing_window text,
    p_provider_attempts smallint
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_row public.article_classifications%rowtype;
begin
    if p_provider_attempts is null or p_provider_attempts not between 0 and 3 then
        raise exception 'failed provider attempts must be between 0 and 3'
            using errcode = '22023';
    end if;
    if p_disposition not in ('RETRY_15_MINUTES', 'RETRY_60_MINUTES', 'QUARANTINE') then
        raise exception 'invalid failure disposition' using errcode = '22023';
    end if;
    if p_error_category is null or length(p_error_category) = 0 then
        raise exception 'error category is required' using errcode = '22023';
    end if;
    if p_error_retryable is null then
        raise exception 'error retryable flag is required' using errcode = '22023';
    end if;
    if p_last_http_status is not null and p_last_http_status not between 100 and 599 then
        raise exception 'invalid HTTP status' using errcode = '22023';
    end if;
    if p_prompt_tokens < 0
       or p_prompt_cache_hit_tokens < 0
       or p_prompt_cache_miss_tokens < 0
       or p_completion_tokens < 0
       or p_total_tokens < 0
       or p_estimated_cost_usd < 0
       or p_prompt_tokens <> p_prompt_cache_hit_tokens + p_prompt_cache_miss_tokens
       or p_total_tokens <> p_prompt_tokens + p_completion_tokens then
        raise exception 'invalid provider usage totals' using errcode = '22023';
    end if;

    update public.article_classifications
    set status = case
            when not p_error_retryable
              or p_disposition = 'QUARANTINE'
              or attempt_count >= max_attempts
                then 'QUARANTINED'
            else 'RETRYABLE'
        end,
        prompt_tokens = prompt_tokens + p_prompt_tokens,
        prompt_cache_hit_tokens = prompt_cache_hit_tokens + p_prompt_cache_hit_tokens,
        prompt_cache_miss_tokens = prompt_cache_miss_tokens + p_prompt_cache_miss_tokens,
        completion_tokens = completion_tokens + p_completion_tokens,
        total_tokens = total_tokens + p_total_tokens,
        estimated_cost_usd = estimated_cost_usd + p_estimated_cost_usd,
        last_pricing_id = p_pricing_id,
        last_pricing_window = p_pricing_window,
        last_provider_attempts = p_provider_attempts,
        claim_token = null,
        claimed_at = null,
        lease_expires_at = null,
        next_attempt_at = case
            when not p_error_retryable
              or p_disposition = 'QUARANTINE'
              or attempt_count >= max_attempts then null
            when p_disposition = 'RETRY_60_MINUTES' then v_now + interval '60 minutes'
            else v_now + interval '15 minutes'
        end,
        last_error_category = case
            when p_error_retryable
              and p_disposition <> 'QUARANTINE'
              and attempt_count >= max_attempts
                then 'attempt_budget_exhausted'
            else p_error_category
        end,
        last_http_status = p_last_http_status,
        last_error_retryable = p_error_retryable,
        last_error_at = v_now,
        quarantined_at = case
            when not p_error_retryable
              or p_disposition = 'QUARANTINE'
              or attempt_count >= max_attempts then v_now
            else null
        end
    where article_id = p_article_id
      and classifier_version = p_classifier_version
      and status = 'PROCESSING'
      and claim_token = p_claim_token
      and lease_expires_at > v_now
    returning * into v_row;

    if not found then
        return jsonb_build_object('outcome', 'LOST_CLAIM', 'record', null);
    end if;
    return jsonb_build_object(
        'outcome', case when v_row.status = 'RETRYABLE'
            then 'RETRY_SCHEDULED'
            else 'QUARANTINED'
        end,
        'record', to_jsonb(v_row)
    );
end;
$$;

alter table public.article_classifications enable row level security;

revoke all on function public.classification_markets_are_canonical(text[]) from public;
revoke all on function public.classification_topics_are_canonical(text[]) from public;
revoke all on function public.enqueue_article_classification(
    text, text, text, text, text, smallint
) from public;
revoke all on function public.claim_next_article_classification(text, uuid, integer)
    from public;
revoke all on function public.renew_article_classification_lease(text, text, uuid, integer)
    from public;
revoke all on function public.complete_article_classification(
    text, text, uuid, boolean, text[], text, text[], double precision, timestamptz,
    text, text, text, bigint, bigint, bigint, bigint, bigint, numeric, text, text, smallint
) from public;
revoke all on function public.fail_article_classification(
    text, text, uuid, text, integer, boolean, text, bigint, bigint, bigint, bigint,
    bigint, numeric, text, text, smallint
) from public;

commit;
