-- verify_readonly_role.sql — run AS wardrobe_readonly (spec §3 DoD:
-- "confirm directly, a manual test not just an assumption, that this role
-- cannot INSERT/UPDATE/DELETE even against closet_query_view or items").
--
-- Every block below must fail. If any of them succeeds the role is
-- over-privileged and the safe-query backstop is gone.

\set ON_ERROR_STOP on

do $$
declare
    n int;
    who text;
begin
    select current_user into who;
    if who <> 'wardrobe_readonly' then
        raise exception 'run this file as wardrobe_readonly, not %', who;
    end if;

    -- The one thing it may do.
    select count(*) into n from public.closet_query_view;
    raise notice 'readonly role can select from closet_query_view (% rows)', n;

    -- Everything else must be refused.
    begin
        perform 1 from public.items;
        raise exception 'FAIL: readonly role can select from items directly';
    exception when insufficient_privilege then null;
    end;

    begin
        perform 1 from public.user_profile;
        raise exception 'FAIL: readonly role can select from user_profile';
    exception when insufficient_privilege then null;
    end;

    begin
        perform 1 from public.usage_counters;
        raise exception 'FAIL: readonly role can select from usage_counters';
    exception when insufficient_privilege then null;
    end;

    begin
        perform 1 from auth.users;
        raise exception 'FAIL: readonly role can select from auth.users';
    exception when insufficient_privilege or invalid_schema_name then null;
    end;

    begin
        insert into public.closet_query_view (id, user_id, category)
        values (gen_random_uuid(), gen_random_uuid(), 'tshirt');
        raise exception 'FAIL: readonly role can insert through closet_query_view';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    begin
        update public.closet_query_view set brand = 'pwned';
        raise exception 'FAIL: readonly role can update through closet_query_view';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    begin
        delete from public.closet_query_view;
        raise exception 'FAIL: readonly role can delete through closet_query_view';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    begin
        insert into public.items (user_id, category_id) values (gen_random_uuid(), 'tshirt');
        raise exception 'FAIL: readonly role can insert into items';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    begin
        update public.items set brand = 'pwned';
        raise exception 'FAIL: readonly role can update items';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    begin
        delete from public.items;
        raise exception 'FAIL: readonly role can delete from items';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    begin
        execute 'create table public.pwned (x int)';
        raise exception 'FAIL: readonly role can create tables in public';
    exception when insufficient_privilege or read_only_sql_transaction then null;
    end;

    raise notice 'read-only role checks passed: select on the view only, no writes anywhere';
end;
$$;
