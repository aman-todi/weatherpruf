/** Small presentation helpers shared by the closet browser and the item form. */

import type { Formality, UnitPreference } from './types';

/**
 * `smart_casual` -> "Smart casual", `sleeve_length` -> "Sleeve length".
 * Works for both category-field names and enum values, which is the whole
 * reason the form can render a field template it has never seen before.
 */
export function humanize(value: string): string {
  const words = value.replace(/[_-]+/g, ' ').trim();
  if (!words) return '';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const FORMALITY_LABELS: Record<Formality, string> = {
  casual: 'Casual',
  smart_casual: 'Smart casual',
  formal: 'Formal',
  athletic: 'Athletic',
};

export const UNIT_LABELS: Record<UnitPreference, string> = {
  fahrenheit: 'Fahrenheit (°F)',
  celsius: 'Celsius (°C)',
};

/** 1 = very light, 5 = heaviest (spec §3). */
export const WARMTH_LABELS: Record<number, string> = {
  1: 'Very light',
  2: 'Light',
  3: 'Mid-weight',
  4: 'Warm',
  5: 'Heaviest',
};

/**
 * The colour picker's starting set. Free text is still allowed — this is a
 * shortcut for the common cases, not an allowlist. Names are lowercase because
 * the backend lowercases `colors` on write.
 */
export const COLOR_PALETTE: { name: string; swatch: string }[] = [
  { name: 'black', swatch: '#111111' },
  { name: 'white', swatch: '#fbfbfa' },
  { name: 'grey', swatch: '#8d8d88' },
  { name: 'charcoal', swatch: '#3b3d40' },
  { name: 'cream', swatch: '#efe6d4' },
  { name: 'beige', swatch: '#d8c8ae' },
  { name: 'tan', swatch: '#c08f5c' },
  { name: 'brown', swatch: '#6f4b32' },
  { name: 'navy', swatch: '#1e2a52' },
  { name: 'blue', swatch: '#2f5fbe' },
  { name: 'light blue', swatch: '#9dc0e8' },
  { name: 'teal', swatch: '#2b7a78' },
  { name: 'green', swatch: '#3f7d4b' },
  { name: 'olive', swatch: '#6f7042' },
  { name: 'yellow', swatch: '#e8c33f' },
  { name: 'orange', swatch: '#df7c37' },
  { name: 'red', swatch: '#bf3b34' },
  { name: 'burgundy', swatch: '#6d2432' },
  { name: 'pink', swatch: '#e3a0b4' },
  { name: 'purple', swatch: '#6b4a8f' },
  { name: 'denim', swatch: '#4a6a92' },
  { name: 'khaki', swatch: '#b7a77e' },
];

const PALETTE_BY_NAME = new Map(COLOR_PALETTE.map((entry) => [entry.name, entry.swatch]));

/**
 * A background colour for a swatch chip. Falls back to whatever CSS makes of
 * the name ("periwinkle" is a real CSS colour, "vintage wash" is not), and then
 * to a neutral so an unrecognised name still renders as a chip.
 */
export function swatchFor(colorName: string): string {
  const name = colorName.trim().toLowerCase();
  const known = PALETTE_BY_NAME.get(name);
  if (known) return known;
  if (typeof CSS !== 'undefined' && CSS.supports?.('color', name)) return name;
  return 'var(--swatch-unknown)';
}

const dateFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
});

export function formatDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? '' : dateFormat.format(parsed);
}
