begin;

revoke all on table public.article_classifications from anon, authenticated;
revoke all on table public.article_classifications from service_role;
grant select on table public.article_classifications to service_role;

revoke all on function public.classification_markets_are_canonical(text[])
    from anon, authenticated, service_role;
revoke all on function public.classification_topics_are_canonical(text[])
    from anon, authenticated, service_role;

revoke all on function public.enqueue_article_classification(
    text, text, text, text, text, smallint
) from anon, authenticated;
revoke all on function public.claim_next_article_classification(text, uuid, integer)
    from anon, authenticated;
revoke all on function public.renew_article_classification_lease(text, text, uuid, integer)
    from anon, authenticated;
revoke all on function public.complete_article_classification(
    text, text, uuid, boolean, text[], text, text[], double precision, timestamptz,
    text, text, text, bigint, bigint, bigint, bigint, bigint, numeric, text, text, smallint
) from anon, authenticated;
revoke all on function public.fail_article_classification(
    text, text, uuid, text, integer, boolean, text, bigint, bigint, bigint, bigint,
    bigint, numeric, text, text, smallint
) from anon, authenticated;

grant execute on function public.enqueue_article_classification(
    text, text, text, text, text, smallint
) to service_role;
grant execute on function public.claim_next_article_classification(text, uuid, integer)
    to service_role;
grant execute on function public.renew_article_classification_lease(text, text, uuid, integer)
    to service_role;
grant execute on function public.complete_article_classification(
    text, text, uuid, boolean, text[], text, text[], double precision, timestamptz,
    text, text, text, bigint, bigint, bigint, bigint, bigint, numeric, text, text, smallint
) to service_role;
grant execute on function public.fail_article_classification(
    text, text, uuid, text, integer, boolean, text, bigint, bigint, bigint, bigint,
    bigint, numeric, text, text, smallint
) to service_role;

commit;
