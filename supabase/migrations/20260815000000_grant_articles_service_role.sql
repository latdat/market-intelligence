begin;

grant select, insert, delete on table public.articles to service_role;

commit;
