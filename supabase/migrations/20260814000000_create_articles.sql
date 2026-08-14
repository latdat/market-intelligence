begin;

create table public.articles (
    article_id text primary key,
    source_id text not null,
    source_item_id text null,
    url text not null,
    canonical_url text not null,
    title text not null,
    description text null,
    language text not null,
    market text not null,
    published_at timestamptz null,
    discovered_at timestamptz not null,
    content_hash text not null,
    constraint articles_market_check check (market in ('VN', 'US', 'EU', 'CN'))
);

comment on table public.articles is
    'First-seen CanonicalArticle snapshots persisted by the data pipeline.';
comment on column public.articles.discovered_at is
    'First time the data pipeline discovered this source-local article.';

alter table public.articles enable row level security;

commit;
