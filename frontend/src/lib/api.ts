/**
 * The single place the frontend talks to the backend.
 *
 * Every route is under `/api`, every route needs the Supabase access token, and
 * every failure comes back as `{error, message, details}`. Rather than repeat
 * that three times per page, this module does it once and hands callers either
 * a typed result or an `ApiError` carrying the stable `error` code.
 */

import { supabase } from './supabase';
import type {
  AccountDeletion,
  Category,
  Item,
  ItemCreate,
  ItemUpdate,
  ItemsPage,
  Me,
  UserProfile,
  UserProfileUpdate,
} from './types';

const BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || 'http://localhost:8000').replace(
  /\/+$/,
  '',
);

/** Stable `error` codes from `backend/app/errors.py` worth branching on. */
export type ApiErrorCode =
  | 'closet_full'
  | 'validation_failed'
  | 'unknown_category'
  | 'not_found'
  | 'daily_limit_reached'
  | 'unsafe_query'
  | 'unauthorized'
  | 'network_error'
  | 'error';

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(code: ApiErrorCode, message: string, status: number, details = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * The `message` on the envelope is written to be shown to a person, so prefer
 * it. Anything that never reached the backend gets a generic sentence instead
 * of leaking a `TypeError: Failed to fetch` into the UI.
 */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong.';
}

type QueryValue = string | number | string[] | undefined | null;

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  query?: Record<string, QueryValue>;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(`${BASE_URL}/api${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue;
    // `tags` is repeatable with AND semantics, so append rather than set.
    if (Array.isArray(value)) value.forEach((entry) => url.searchParams.append(key, entry));
    else url.searchParams.append(key, String(value));
  }
  return url.toString();
}

async function accessToken(): Promise<string> {
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session) {
    throw new ApiError('unauthorized', 'Your session has expired. Sign in again.', 401);
  }
  return data.session.access_token;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = await accessToken();
  const { method = 'GET', body, query } = options;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new ApiError(
      'network_error',
      `Could not reach the server at ${BASE_URL}. Check that the backend is running.`,
      0,
    );
  }

  if (response.status === 204) return undefined as T;

  const payload: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const envelope = (payload ?? {}) as {
      error?: string;
      message?: string;
      details?: Record<string, unknown>;
      detail?: unknown;
    };
    throw new ApiError(
      (envelope.error as ApiErrorCode) ?? (response.status === 401 ? 'unauthorized' : 'error'),
      envelope.message ?? fallbackMessage(response.status),
      response.status,
      envelope.details ?? {},
    );
  }

  return payload as T;
}

function fallbackMessage(status: number): string {
  if (status === 401) return 'Your session has expired. Sign in again.';
  if (status === 403) return 'You do not have access to that.';
  if (status >= 500) return 'The server had a problem. Try again in a moment.';
  return `Request failed (${status}).`;
}

export const api = {
  categories: () => request<Category[]>('/categories'),

  listItems: (params: {
    category?: string | undefined;
    tags?: string[] | undefined;
    limit?: number | undefined;
    offset?: number | undefined;
  }) =>
    request<ItemsPage>('/items', {
      query: {
        category: params.category ?? null,
        tags: params.tags?.length ? params.tags : null,
        limit: params.limit ?? null,
        offset: params.offset ?? null,
      },
    }),

  getItem: (id: string) => request<Item>(`/items/${id}`),

  createItem: (item: ItemCreate) => request<Item>('/items', { method: 'POST', body: item }),

  updateItem: (id: string, patch: ItemUpdate) =>
    request<Item>(`/items/${id}`, { method: 'PATCH', body: patch }),

  deleteItem: (id: string) => request<void>(`/items/${id}`, { method: 'DELETE' }),

  getProfile: () => request<UserProfile>('/profile'),

  updateProfile: (patch: UserProfileUpdate) =>
    request<UserProfile>('/profile', { method: 'PUT', body: patch }),

  me: () => request<Me>('/me'),

  deleteAccount: () => request<AccountDeletion>('/account', { method: 'DELETE' }),
};
