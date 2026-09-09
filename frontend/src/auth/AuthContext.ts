import { createContext, useContext } from 'react';
import type { Session } from '@supabase/supabase-js';

export interface AuthValue {
  session: Session | null;
  /** True until the first `getSession()` resolves, so we don't flash the login page. */
  loading: boolean;
  /** Sends a magic link. Resolves on success, rejects with a readable message. */
  signIn: (email: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}
