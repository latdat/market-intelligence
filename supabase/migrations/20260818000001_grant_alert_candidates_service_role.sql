begin;

revoke all on table public.alert_candidates
    from public, anon, authenticated, service_role;
grant select on table public.alert_candidates to service_role;

revoke all on function public.save_alert_candidate(
    text, text, text, timestamptz, text[], text, double precision, boolean
) from service_role;
grant execute on function public.save_alert_candidate(
    text, text, text, timestamptz, text[], text, double precision, boolean
) to service_role;

commit;
