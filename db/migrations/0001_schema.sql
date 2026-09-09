-- 0001_schema.sql — core tables for the wardrobe app (Ticket 1, spec §3).
--
-- Applies to a Supabase Postgres project. `auth.users` and `auth.uid()` are
-- provided by Supabase; db/local/00_auth_shim.sql fakes them for local dev.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- categories: fixed top-level garment types.
-- ---------------------------------------------------------------------------
create table if not exists public.categories (
    id           text primary key,
    display_name text not null,
    sort_order   int  not null default 0
);

-- ---------------------------------------------------------------------------
-- category_field_defs: category-SPECIFIC fields only. The six common fields
-- (colors, brand, warmth_rating, formality, tags, notes) are columns on
-- `items` instead, so the safe query tool can allowlist a small stable set.
-- ---------------------------------------------------------------------------
create table if not exists public.category_field_defs (
    id             uuid primary key default gen_random_uuid(),
    category_id    text not null references public.categories (id) on delete cascade,
    field_name     text not null,
    field_type     text not null check (field_type in ('text', 'enum', 'number', 'boolean')),
    required       boolean not null default false,
    allowed_values text[],
    display_order  int not null default 0,
    unique (category_id, field_name),
    -- enum fields must enumerate; non-enum fields must not.
    constraint category_field_defs_enum_values_ck check (
        (field_type = 'enum' and allowed_values is not null and array_length(allowed_values, 1) > 0)
        or (field_type <> 'enum' and allowed_values is null)
    )
);

create index if not exists category_field_defs_category_idx
    on public.category_field_defs (category_id, display_order);

-- ---------------------------------------------------------------------------
-- items: one garment. Six common fields as columns, niche ones in `fields`.
-- ---------------------------------------------------------------------------
create table if not exists public.items (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users (id) on delete cascade,
    category_id   text not null references public.categories (id),
    colors        text[] not null default '{}',
    brand         text,
    warmth_rating int,
    formality     text,
    tags          text[] not null default '{}',
    notes         text,
    fields        jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    constraint items_colors_max_ck check (coalesce(array_length(colors, 1), 0) <= 5),
    constraint items_warmth_rating_ck check (warmth_rating is null or warmth_rating between 1 and 5),
    constraint items_formality_ck check (
        formality is null or formality in ('casual', 'smart_casual', 'formal', 'athletic')
    ),
    constraint items_fields_is_object_ck check (jsonb_typeof(fields) = 'object')
);

create index if not exists items_user_idx on public.items (user_id);
create index if not exists items_user_category_idx on public.items (user_id, category_id);
create index if not exists items_tags_idx on public.items using gin (tags);
create index if not exists items_fields_idx on public.items using gin (fields);

-- ---------------------------------------------------------------------------
-- user_profile: the single place for account-level info.
-- ---------------------------------------------------------------------------
create table if not exists public.user_profile (
    user_id         uuid primary key references auth.users (id) on delete cascade,
    home_location   text,
    unit_preference text not null default 'fahrenheit'
        check (unit_preference in ('fahrenheit', 'celsius')),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- usage_counters: backs the per-user daily MCP call cap (spec §5).
-- ---------------------------------------------------------------------------
create table if not exists public.usage_counters (
    user_id    uuid not null references auth.users (id) on delete cascade,
    day        date not null,
    call_count int  not null default 0,
    primary key (user_id, day)
);

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists items_touch_updated_at on public.items;
create trigger items_touch_updated_at
    before update on public.items
    for each row execute function public.touch_updated_at();

drop trigger if exists user_profile_touch_updated_at on public.user_profile;
create trigger user_profile_touch_updated_at
    before update on public.user_profile
    for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- closet_query_view: the ONLY relation the safe query tool may select from.
-- Deliberately omits created_at/updated_at so the allowlist in the app and the
-- view's own column list agree.
-- ---------------------------------------------------------------------------
create or replace view public.closet_query_view as
select id,
       user_id,
       category_id as category,
       colors,
       brand,
       warmth_rating,
       formality,
       tags,
       notes,
       fields
from public.items;
