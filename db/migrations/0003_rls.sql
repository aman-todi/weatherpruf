-- 0003_rls.sql — row level security (spec §3).
--
-- Every user-scoped table restricts rows to auth.uid() = user_id, identically
-- whether the request arrived with the web app's session JWT or Claude.ai's
-- OAuth access token — both resolve to the same `authenticated` role with the
-- same `sub` claim.

-- --------------------------------------------------------------------------
-- Reference data: readable by any signed-in user, writable by nobody but the
-- service role (which bypasses RLS).
-- --------------------------------------------------------------------------
alter table public.categories          enable row level security;
alter table public.category_field_defs enable row level security;

drop policy if exists categories_read on public.categories;
create policy categories_read on public.categories
    for select to authenticated using (true);

drop policy if exists category_field_defs_read on public.category_field_defs;
create policy category_field_defs_read on public.category_field_defs
    for select to authenticated using (true);

-- --------------------------------------------------------------------------
-- items
-- --------------------------------------------------------------------------
alter table public.items enable row level security;
alter table public.items force row level security;

drop policy if exists items_select_own on public.items;
create policy items_select_own on public.items
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists items_insert_own on public.items;
create policy items_insert_own on public.items
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists items_update_own on public.items;
create policy items_update_own on public.items
    for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists items_delete_own on public.items;
create policy items_delete_own on public.items
    for delete to authenticated using (auth.uid() = user_id);

-- --------------------------------------------------------------------------
-- user_profile
-- --------------------------------------------------------------------------
alter table public.user_profile enable row level security;
alter table public.user_profile force row level security;

drop policy if exists user_profile_select_own on public.user_profile;
create policy user_profile_select_own on public.user_profile
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists user_profile_insert_own on public.user_profile;
create policy user_profile_insert_own on public.user_profile
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists user_profile_update_own on public.user_profile;
create policy user_profile_update_own on public.user_profile
    for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists user_profile_delete_own on public.user_profile;
create policy user_profile_delete_own on public.user_profile
    for delete to authenticated using (auth.uid() = user_id);

-- --------------------------------------------------------------------------
-- usage_counters — readable by the owner so the assistant can be told how much
-- budget is left; only ever written server-side via the service role.
-- --------------------------------------------------------------------------
alter table public.usage_counters enable row level security;
alter table public.usage_counters force row level security;

drop policy if exists usage_counters_select_own on public.usage_counters;
create policy usage_counters_select_own on public.usage_counters
    for select to authenticated using (auth.uid() = user_id);

-- --------------------------------------------------------------------------
-- Grants for Supabase's built-in roles.
--
-- Note deliberately absent: closet_query_view is NOT granted to `anon` or
-- `authenticated`. The view runs with its owner's privileges (security_invoker
-- is off), so RLS on `items` does not apply through it — exposing it to
-- PostgREST would be a cross-user read. Only the dedicated read-only role in
-- 0004 may select from it, and only ever with a bound user_id predicate.
-- --------------------------------------------------------------------------
grant usage on schema public to anon, authenticated;

grant select on public.categories, public.category_field_defs to anon, authenticated;
grant select, insert, update, delete on public.items to authenticated;
grant select, insert, update, delete on public.user_profile to authenticated;
grant select on public.usage_counters to authenticated;

revoke all on public.closet_query_view from public, anon, authenticated;
