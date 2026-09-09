import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';

/**
 * The caller's whole tag vocabulary, most-used first, from `GET /api/tags`.
 *
 * Deriving this from `/api/items` instead would only ever see the page that is
 * currently loaded, so a tag on item 400 would be missing from the filter's
 * suggestions.
 *
 * Returns null — not an empty list — when the endpoint is not there, so the
 * caller can fall back to the tags on the loaded items. Suggestions are a
 * convenience, not correctness: filtering works on typed text either way, so a
 * shorter list is a reasonable thing to degrade to rather than an error worth
 * showing anyone.
 */
export function useTags(): { tags: string[] | null; refresh: () => void } {
  const [tags, setTags] = useState<string[] | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let active = true;
    api.tags().then(
      (found) => active && setTags(found.map((entry) => entry.tag)),
      () => active && setTags(null),
    );
    return () => {
      active = false;
    };
  }, [token]);

  const refresh = useCallback(() => setToken((value) => value + 1), []);

  return { tags, refresh };
}
