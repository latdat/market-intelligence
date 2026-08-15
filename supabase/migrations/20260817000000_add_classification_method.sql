begin;

alter table public.article_classifications
    add column classification_method text null;

-- Existing successful DE-009 rows predate hybrid routing and are historical DeepSeek-first rows.
alter table public.article_classifications
    disable trigger article_classifications_guard_transition;
update public.article_classifications
set classification_method = 'DEEPSEEK'
where status = 'SUCCEEDED';
alter table public.article_classifications
    enable trigger article_classifications_guard_transition;

alter table public.article_classifications
    add constraint article_classifications_method_state_check
        check (
            (
                status = 'SUCCEEDED'
                and classification_method in ('DETERMINISTIC', 'DEEPSEEK')
            )
            or (
                status <> 'SUCCEEDED'
                and classification_method is null
            )
        ),
    add constraint article_classifications_method_metadata_check
        check (
            status <> 'SUCCEEDED'
            or (
                classification_method = 'DETERMINISTIC'
                and provider_model is null
                and provider_request_id is null
                and system_fingerprint is null
                and last_provider_attempts = 0
                and prompt_tokens = 0
                and prompt_cache_hit_tokens = 0
                and prompt_cache_miss_tokens = 0
                and completion_tokens = 0
                and total_tokens = 0
                and estimated_cost_usd = 0
                and last_pricing_id is null
                and last_pricing_window is null
            )
            or (
                classification_method = 'DEEPSEEK'
                and last_provider_attempts between 1 and 3
            )
        );

comment on column public.article_classifications.classification_method is
    'DE-internal successful routing method; excluded from shared ClassifiedArticle.';
comment on column public.article_classifications.requested_model is
    'Immutable enqueue lineage; for classification-v2 this is the configured fallback model and does not prove a provider call.';

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
    p_provider_attempts smallint,
    p_classification_method text
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
    if p_classification_method not in ('DETERMINISTIC', 'DEEPSEEK') then
        raise exception 'invalid classification method' using errcode = '22023';
    end if;
    if p_classification_method = 'DETERMINISTIC'
       and (
           p_provider_attempts is distinct from 0
           or p_provider_model is not null
           or p_provider_request_id is not null
           or p_system_fingerprint is not null
           or p_prompt_tokens is distinct from 0
           or p_prompt_cache_hit_tokens is distinct from 0
           or p_prompt_cache_miss_tokens is distinct from 0
           or p_completion_tokens is distinct from 0
           or p_total_tokens is distinct from 0
           or p_estimated_cost_usd is distinct from 0
           or p_pricing_id is not null
           or p_pricing_window is not null
       ) then
        raise exception 'deterministic success must not contain provider metadata'
            using errcode = '22023';
    end if;
    if p_classification_method = 'DEEPSEEK'
       and (p_provider_attempts is null or p_provider_attempts not between 1 and 3) then
        raise exception 'DeepSeek success provider attempts must be between 1 and 3'
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
        classification_method = p_classification_method,
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

-- Preserve the DE-009 RPC signature for rolling rollback; it remains DeepSeek-first.
create or replace function public.complete_article_classification(
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
language sql
security definer
set search_path = pg_catalog, public
as $$
    select public.complete_article_classification(
        p_article_id,
        p_classifier_version,
        p_claim_token,
        p_is_relevant,
        p_markets,
        p_category,
        p_topics,
        p_confidence,
        p_classified_at,
        p_provider_model,
        p_provider_request_id,
        p_system_fingerprint,
        p_prompt_tokens,
        p_prompt_cache_hit_tokens,
        p_prompt_cache_miss_tokens,
        p_completion_tokens,
        p_total_tokens,
        p_estimated_cost_usd,
        p_pricing_id,
        p_pricing_window,
        p_provider_attempts,
        'DEEPSEEK'
    );
$$;

revoke all on function public.complete_article_classification(
    text, text, uuid, boolean, text[], text, text[], double precision, timestamptz,
    text, text, text, bigint, bigint, bigint, bigint, bigint, numeric, text, text,
    smallint, text
) from public, anon, authenticated;
grant execute on function public.complete_article_classification(
    text, text, uuid, boolean, text[], text, text[], double precision, timestamptz,
    text, text, text, bigint, bigint, bigint, bigint, bigint, numeric, text, text,
    smallint, text
) to service_role;

commit;
