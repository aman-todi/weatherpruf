import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL?.trim();
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY?.trim();

/**
 * Whether the three `VITE_*` values are present. When they are not, the app
 * renders a setup screen naming the missing variables instead of a blank page
 * with a console error — the first thing a new contributor hits is a missing
 * `.env.local`, and a white screen is a bad way to learn that.
 */
export const supabaseConfigured = Boolean(url && anonKey);

export const missingEnvVars = [
  ['VITE_SUPABASE_URL', url],
  ['VITE_SUPABASE_ANON_KEY', anonKey],
  ['VITE_API_BASE_URL', import.meta.env.VITE_API_BASE_URL?.trim()],
]
  .filter(([, value]) => !value)
  .map(([name]) => name as string);

/**
 * Placeholders keep `createClient` from throwing on a malformed URL when the
 * environment is unset; nothing calls into it before the setup screen renders.
 */
export const supabase = createClient(url || 'http://localhost:54321', anonKey || 'anon-key', {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    // The magic link comes back as a hash fragment on whatever page it lands on.
    detectSessionInUrl: true,
  },
});
