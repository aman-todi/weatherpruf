-- verify_rls.sql — Ticket 1 definition-of-done checks that need only SQL.
--
-- Seeds two users, then asserts that RLS blocks every cross-user read and
-- write, and that the column constraints on `items` hold. Run via
-- scripts/db_verify.sh (which also exercises the read-only role, since that
-- needs a separate connection). Raises an exception on the first failure.

\set ON_ERROR_STOP on

begin;

-- ---------------------------------------------------------------------------
-- Fixtures: two users, one item each.
-- ---------------------------------------------------------------------------
insert into auth.users (id, email) values
    ('11111111-1111-1111-1111-111111111111', 'alice@example.test'),
    ('22222222-2222-2222-2222-222222222222', 'bob@example.test')
on conflict (id) do nothing;

insert into public.items (user_id, category_id, colors, brand, warmth_rating, formality, tags, notes)
values ('11111111-1111-1111-1111-111111111111', 'tshirt', array['red'], 'Nike', 1, 'casual',
        array['gym'], 'alice tee'),
       ('22222222-2222-2222-2222-222222222222', 'coat', array['navy'], 'Uniqlo', 5, 'smart_casual',
        array['winter'], 'bob coat');

do $$
declare
    n int;
begin
    -- ---- act as Alice ----------------------------------------------------
    set local role authenticated;
    perform set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', true);

    select count(*) into n from public.items;
    if n <> 1 then
        raise exception 'RLS select: Alice saw % items, expected 1', n;
    end if;

    select count(*) into n from public.items where notes = 'bob coat';
    if n <> 0 then
        raise exception 'RLS select: Alice could read Bob''s item';
    end if;

    -- Alice may not write rows owned by Bob.
    begin
        insert into public.items (user_id, category_id)
        values ('22222222-2222-2222-2222-222222222222', 'hat');
        raise exception 'RLS insert: Alice inserted an item owned by Bob';
    exception
        when insufficient_privilege then null;
    end;

    -- ...nor reassign her own row to him.
    begin
        update public.items
           set user_id = '22222222-2222-2222-2222-222222222222'
         where notes = 'alice tee';
        raise exception 'RLS update: Alice reassigned her item to Bob';
    exception
        when insufficient_privilege then null;
    end;

    -- ...nor delete his.
    delete from public.items where notes = 'bob coat';
    if found then
        raise exception 'RLS delete: Alice deleted Bob''s item';
    end if;

    -- Reference data stays readable.
    select count(*) into n from public.categories;
    if n < 26 then
        raise exception 'categories: expected the full seed list, saw %', n;
    end if;

    -- The query view is not reachable from the authenticated role at all: it
    -- runs with its owner's rights, so exposing it would bypass RLS.
    begin
        perform 1 from public.closet_query_view;
        raise exception 'closet_query_view is readable by the authenticated role';
    exception
        when insufficient_privilege then null;
    end;

    reset role;

    -- ---- act as Bob ------------------------------------------------------
    set local role authenticated;
    perform set_config('request.jwt.claim.sub', '22222222-2222-2222-2222-222222222222', true);

    select count(*) into n from public.items;
    if n <> 1 then
        raise exception 'RLS select: Bob saw % items, expected 1', n;
    end if;

    select count(*) into n from public.items where notes = 'bob coat';
    if n <> 1 then
        raise exception 'RLS select: Bob could not read his own item';
    end if;

    reset role;

    -- ---- an anonymous request sees nothing -------------------------------
    set local role authenticated;
    perform set_config('request.jwt.claim.sub', '', true);
    select count(*) into n from public.items;
    if n <> 0 then
        raise exception 'RLS select: an unauthenticated request saw % items', n;
    end if;
    reset role;

    raise notice 'RLS checks passed';
end;
$$;

-- ---------------------------------------------------------------------------
-- Column constraints
-- ---------------------------------------------------------------------------
do $$
begin
    begin
        insert into public.items (user_id, category_id, colors)
        values ('11111111-1111-1111-1111-111111111111', 'tshirt',
                array['red', 'blue', 'green', 'black', 'white', 'grey']);
        raise exception 'colors: a 6th color was accepted';
    exception
        when check_violation then null;
    end;

    begin
        insert into public.items (user_id, category_id, warmth_rating)
        values ('11111111-1111-1111-1111-111111111111', 'tshirt', 9);
        raise exception 'warmth_rating: 9 was accepted';
    exception
        when check_violation then null;
    end;

    begin
        insert into public.items (user_id, category_id, formality)
        values ('11111111-1111-1111-1111-111111111111', 'tshirt', 'black_tie');
        raise exception 'formality: an unknown value was accepted';
    exception
        when check_violation then null;
    end;

    begin
        insert into public.items (user_id, category_id) values
            ('11111111-1111-1111-1111-111111111111', 'not_a_category');
        raise exception 'category_id: an unknown category was accepted';
    exception
        when foreign_key_violation then null;
    end;

    raise notice 'constraint checks passed';
end;
$$;

rollback;
