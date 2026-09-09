-- 0004_readonly_role.sql — the dedicated read-only role (spec §3, §4.1).
--
-- This role, not the sqlglot AST walk, is the actual security backstop for
-- `query_closet_items`. It can SELECT from public.closet_query_view and
-- nothing else: no other table, no other column of `items`, and no write of
-- any kind. Its connection string is kept as a separate secret from the main
-- app's DB credentials.
--
-- The role is created WITHOUT a password on purpose so no secret lands in the
-- repo. After applying this migration, run once with a generated password:
--
--     alter role wardrobe_readonly with login password '<generated>';
--
-- and put the resulting connection string in READONLY_DATABASE_URL.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'wardrobe_readonly') then
        create role wardrobe_readonly with login nosuperuser nocreatedb nocreaterole
            noinherit noreplication nobypassrls;
    else
        alter role wardrobe_readonly with login nosuperuser nocreatedb nocreaterole
            noinherit noreplication nobypassrls;
    end if;
end;
$$;

-- Belt and braces: every transaction this role opens is read-only, and every
-- statement is bounded, regardless of what the application sends.
alter role wardrobe_readonly set default_transaction_read_only = on;
alter role wardrobe_readonly set statement_timeout = '2s';
alter role wardrobe_readonly set idle_in_transaction_session_timeout = '10s';
alter role wardrobe_readonly set search_path = public;

-- Start from nothing.
revoke all on schema public from wardrobe_readonly;
revoke all on all tables in schema public from wardrobe_readonly;
revoke all on all sequences in schema public from wardrobe_readonly;
revoke all on all functions in schema public from wardrobe_readonly;
revoke all on schema auth from wardrobe_readonly;
revoke create on schema public from wardrobe_readonly;

-- Grant back exactly one thing.
grant usage on schema public to wardrobe_readonly;
grant select on public.closet_query_view to wardrobe_readonly;

-- Nothing created later in this schema should silently become readable.
alter default privileges in schema public revoke all on tables from wardrobe_readonly;
