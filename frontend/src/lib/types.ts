/**
 * The REST contract, transcribed from `backend/app/models.py`.
 *
 * Kept as a hand-written mirror rather than generated from the OpenAPI schema:
 * the surface is small, and a hand-written mirror is one file to re-read when
 * the backend changes.
 */

export const FORMALITIES = ['casual', 'smart_casual', 'formal', 'athletic'] as const;
export type Formality = (typeof FORMALITIES)[number];

export const UNIT_PREFERENCES = ['fahrenheit', 'celsius'] as const;
export type UnitPreference = (typeof UNIT_PREFERENCES)[number];

export type FieldType = 'text' | 'enum' | 'number' | 'boolean';

/** Backend caps `colors` at five (`MAX_COLORS` in models.py). */
export const MAX_COLORS = 5;

/** A category-specific field value. `fields` holds these and nothing else. */
export type FieldValue = string | number | boolean | null;

export interface Item {
  id: string;
  category: string;
  colors: string[];
  brand: string | null;
  warmth_rating: number | null;
  formality: Formality | null;
  tags: string[];
  notes: string | null;
  fields: Record<string, FieldValue>;
  created_at: string;
  updated_at: string;
}

/** `POST /api/items` body: the item minus `id` and the timestamps. */
export interface ItemCreate {
  category: string;
  colors: string[];
  brand: string | null;
  warmth_rating: number | null;
  formality: Formality | null;
  tags: string[];
  notes: string | null;
  fields: Record<string, FieldValue>;
}

/** `PATCH /api/items/{id}` body: partial; only supplied keys change. */
export type ItemUpdate = Partial<ItemCreate>;

export interface CategoryFieldDef {
  field_name: string;
  field_type: FieldType;
  required: boolean;
  /** Non-null only for `enum`. */
  allowed_values: string[] | null;
  display_order: number;
}

export interface Category {
  id: string;
  display_name: string;
  sort_order: number;
  fields: CategoryFieldDef[];
}

export interface ItemsPage {
  items: Item[];
  /** The user's *whole closet* count — not the count matching the filters. */
  total: number;
}

export interface UserProfile {
  home_location: string | null;
  unit_preference: UnitPreference;
}

export type UserProfileUpdate = Partial<UserProfile>;

export interface Me {
  user_id: string;
  email: string;
  item_count: number;
  item_limit: number;
  calls_used_today: number;
  daily_call_limit: number;
  /** What the Connect page tells the user to paste into Claude.ai. */
  mcp_url: string;
}

export interface AccountDeletion {
  deleted: Record<string, number>;
}
