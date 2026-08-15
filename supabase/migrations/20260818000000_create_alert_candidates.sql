begin;

create function public.alert_candidate_reasons_are_valid(p_values text[])
returns boolean
language sql
immutable
strict
parallel safe
as $$
    select
        cardinality(p_values) >= 1
        and not exists (
            select 1
            from unnest(p_values) as reason
            where reason is null or length(btrim(reason)) = 0
        )
        and cardinality(p_values) = (
            select count(distinct reason)
            from unnest(p_values) as reason
        );
$$;

create table public.alert_candidates (
    candidate_id text not null,
    user_id text not null,
    article_id text not null,
    matched_at timestamptz not null,
    match_reasons text[] not null,
    importance text not null,
    relevance_score double precision not null,
    breaking_eligible boolean not null,
    created_at timestamptz not null default now(),

    constraint alert_candidates_pk
        primary key (candidate_id),
    constraint alert_candidates_user_article_uidx
        unique (user_id, article_id),
    constraint alert_candidates_article_fk
        foreign key (article_id)
        references public.articles(article_id)
        on delete restrict,
    constraint alert_candidates_identity_nonblank_check
        check (
            length(btrim(candidate_id)) > 0
            and length(btrim(user_id)) > 0
            and length(btrim(article_id)) > 0
        ),
    constraint alert_candidates_reasons_check
        check (public.alert_candidate_reasons_are_valid(match_reasons)),
    constraint alert_candidates_importance_check
        check (importance in ('NORMAL', 'HIGH')),
    constraint alert_candidates_relevance_score_check
        check (
            relevance_score between 0 and 1
            and relevance_score <> 'NaN'::double precision
            and relevance_score <> 'Infinity'::double precision
            and relevance_score <> '-Infinity'::double precision
        )
);

comment on table public.alert_candidates is
    'Immutable first-seen AlertCandidate snapshots keyed by stable candidate identity.';
comment on constraint alert_candidates_user_article_uidx on public.alert_candidates is
    'Enforces one logical alert candidate per user/article pair across repeated and concurrent runs.';
comment on column public.alert_candidates.created_at is
    'Database insertion time; not part of the shared AlertCandidate contract.';

alter table public.alert_candidates enable row level security;

create function public.save_alert_candidate(
    p_candidate_id text,
    p_user_id text,
    p_article_id text,
    p_matched_at timestamptz,
    p_match_reasons text[],
    p_importance text,
    p_relevance_score double precision,
    p_breaking_eligible boolean
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_row public.alert_candidates%rowtype;
begin
    if p_candidate_id is null or length(btrim(p_candidate_id)) = 0 then
        raise exception 'candidate id is required' using errcode = '22023';
    end if;
    if p_user_id is null or length(btrim(p_user_id)) = 0 then
        raise exception 'user id is required' using errcode = '22023';
    end if;
    if p_article_id is null or length(btrim(p_article_id)) = 0 then
        raise exception 'article id is required' using errcode = '22023';
    end if;
    if p_matched_at is null then
        raise exception 'matched_at is required' using errcode = '22023';
    end if;
    if p_match_reasons is null
       or not public.alert_candidate_reasons_are_valid(p_match_reasons) then
        raise exception 'invalid match reasons' using errcode = '22023';
    end if;
    if p_importance is null or p_importance not in ('NORMAL', 'HIGH') then
        raise exception 'invalid alert importance' using errcode = '22023';
    end if;
    if p_relevance_score is null
       or p_relevance_score < 0
       or p_relevance_score > 1
       or p_relevance_score = 'NaN'::double precision
       or p_relevance_score = 'Infinity'::double precision
       or p_relevance_score = '-Infinity'::double precision then
        raise exception 'invalid relevance score' using errcode = '22023';
    end if;
    if p_breaking_eligible is null then
        raise exception 'breaking eligibility is required' using errcode = '22023';
    end if;

    insert into public.alert_candidates (
        candidate_id,
        user_id,
        article_id,
        matched_at,
        match_reasons,
        importance,
        relevance_score,
        breaking_eligible
    ) values (
        p_candidate_id,
        p_user_id,
        p_article_id,
        p_matched_at,
        p_match_reasons,
        p_importance,
        p_relevance_score,
        p_breaking_eligible
    )
    on conflict do nothing
    returning * into v_row;

    if found then
        return jsonb_build_object('outcome', 'CREATED', 'record', to_jsonb(v_row));
    end if;

    select * into v_row
    from public.alert_candidates
    where user_id = p_user_id
      and article_id = p_article_id;

    if found then
        if v_row.candidate_id is distinct from p_candidate_id then
            raise exception 'alert candidate logical identity conflict'
                using errcode = '23505';
        end if;
        return jsonb_build_object('outcome', 'ALREADY_EXISTS', 'record', to_jsonb(v_row));
    end if;

    select * into v_row
    from public.alert_candidates
    where candidate_id = p_candidate_id;

    if found then
        raise exception 'alert candidate identifier collision'
            using errcode = '23505';
    end if;

    raise exception 'alert candidate conflict could not be resolved'
        using errcode = '40001';
end;
$$;

revoke all on function public.alert_candidate_reasons_are_valid(text[])
    from public, anon, authenticated;
revoke all on function public.save_alert_candidate(
    text, text, text, timestamptz, text[], text, double precision, boolean
) from public, anon, authenticated;

commit;
