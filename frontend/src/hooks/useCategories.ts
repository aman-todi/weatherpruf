import { useEffect, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { Category } from '../lib/types';

/**
 * The taxonomy is small, seeded, and effectively static for a session, so the
 * first component to ask for it fetches it and everything else shares that one
 * promise. Cleared on sign-out via `resetCategoryCache`.
 */
let cache: Promise<Category[]> | null = null;

function load(): Promise<Category[]> {
  cache ??= api.categories().catch((error) => {
    cache = null; // let a later mount retry rather than caching the failure
    throw error;
  });
  return cache;
}

export function resetCategoryCache(): void {
  cache = null;
}

export interface CategoriesState {
  categories: Category[];
  loading: boolean;
  error: string | null;
}

export function useCategories(): CategoriesState {
  const [state, setState] = useState<CategoriesState>({
    categories: [],
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    load().then(
      (categories) => active && setState({ categories, loading: false, error: null }),
      (error: unknown) =>
        active && setState({ categories: [], loading: false, error: errorMessage(error) }),
    );
    return () => {
      active = false;
    };
  }, []);

  return state;
}

/** Sorted by `sort_order`, then `display_order` within each field template. */
export function sortCategories(categories: Category[]): Category[] {
  return [...categories]
    .sort((a, b) => a.sort_order - b.sort_order || a.display_name.localeCompare(b.display_name))
    .map((category) => ({
      ...category,
      fields: [...category.fields].sort(
        (a, b) => a.display_order - b.display_order || a.field_name.localeCompare(b.field_name),
      ),
    }));
}
