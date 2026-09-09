-- 00_auth_shim.sql — LOCAL DEVELOPMENT ONLY.
--
-- Supabase provides the `auth` schema, `auth.users`, `auth.uid()` and the
-- anon/authenticated/service_role roles. A bare Postgres does not. This file
-- fakes just enough of them to apply the real migrations unchanged against a
-- local cluster and exercise RLS. Never run it against a Supabase project.

create schema if not exists auth;

create table if not exists auth.users (
    id         uuid primary key default gen_random_uuid(),
    email      text unique,
    created_at timestamptz not null default now()
);

-- Same shape as Supabase's: read `sub` out of the request-scoped JWT claims.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
    select coalesce(
        nullif(current_setting('request.jwt.claim.sub', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
    )::uuid;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin noinherit bypassrls;
    end if;
end;
$$;

grant usage on schema auth to authenticated, service_role;
