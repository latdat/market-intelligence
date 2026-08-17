begin;

revoke all on table public.discovery_observation_daily
    from public, anon, authenticated, service_role;
grant select on table public.discovery_observation_daily to service_role;

revoke all on table public.discovery_benchmark_evidence
    from public, anon, authenticated, service_role;
grant select on table public.discovery_benchmark_evidence to service_role;

revoke all on function public.record_discovery_observation(
    date, text, text, text, timestamptz, text, text
) from service_role;
grant execute on function public.record_discovery_observation(
    date, text, text, text, timestamptz, text, text
) to service_role;

revoke all on function public.record_discovery_benchmark_evidence(
    text, text, text, text, date, timestamptz, timestamptz, timestamptz
) from service_role;
grant execute on function public.record_discovery_benchmark_evidence(
    text, text, text, text, date, timestamptz, timestamptz, timestamptz
) to service_role;

revoke all on function public.prune_discovery_records(date, date, date)
    from service_role;
grant execute on function public.prune_discovery_records(date, date, date)
    to service_role;

commit;
