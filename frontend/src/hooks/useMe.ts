import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { Me } from '../lib/types';

export interface MeState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * `/api/me` carries the closet cap, today's assistant call count and the MCP
 * URL. Three pages want pieces of it and it changes as the user adds items, so
 * it is fetched per mount rather than cached like the taxonomy.
 */
export function useMe(): MeState {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.me().then(
      (value) => {
        if (!active) return;
        setMe(value);
        setError(null);
        setLoading(false);
      },
      (cause: unknown) => {
        if (!active) return;
        setError(errorMessage(cause));
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [token]);

  const refresh = useCallback(() => setToken((value) => value + 1), []);

  return { me, loading, error, refresh };
}
