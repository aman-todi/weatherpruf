import { useMemo, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import { FORMALITY_LABELS, WARMTH_LABELS, humanize } from '../lib/format';
import {
  FORMALITIES,
  type Category,
  type CategoryFieldDef,
  type FieldValue,
  type Formality,
  type Item,
  type ItemCreate,
  type ItemUpdate,
} from '../lib/types';
import { Banner } from './Banner';
import { ColorPicker } from './ColorPicker';
import { TagInput } from './TagInput';

/**
 * Category-specific inputs are held as strings (or booleans for checkboxes)
 * while editing and coerced once, on submit. Keeping a half-typed "1" out of
 * `fields` until then is what lets a number input behave like a text input.
 */
type RawFields = Record<string, string | boolean>;

function toRawFields(fields: Record<string, FieldValue>): RawFields {
  const raw: RawFields = {};
  for (const [key, value] of Object.entries(fields)) {
    if (value === null || value === undefined) continue;
    raw[key] = typeof value === 'boolean' ? value : String(value);
  }
  return raw;
}

/** True when a required non-boolean field has been left empty. */
function isBlank(value: string | boolean | undefined): boolean {
  return value === undefined || (typeof value === 'string' && value.trim() === '');
}

export function ItemForm({
  categories,
  item,
  tagSuggestions,
  onSaved,
  onCancel,
}: {
  categories: Category[];
  /** Null for "add", an existing item for "edit". */
  item: Item | null;
  tagSuggestions: string[];
  onSaved: (item: Item) => void;
  onCancel: () => void;
}) {
  const [category, setCategory] = useState(item?.category ?? '');
  const [colors, setColors] = useState<string[]>(item?.colors ?? []);
  const [brand, setBrand] = useState(item?.brand ?? '');
  const [warmth, setWarmth] = useState<number | null>(item?.warmth_rating ?? null);
  const [formality, setFormality] = useState<Formality | null>(item?.formality ?? null);
  const [tags, setTags] = useState<string[]>(item?.tags ?? []);
  const [notes, setNotes] = useState(item?.notes ?? '');
  const [rawFields, setRawFields] = useState<RawFields>(toRawFields(item?.fields ?? {}));

  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const selected = useMemo(
    () => categories.find((entry) => entry.id === category) ?? null,
    [categories, category],
  );
  const defs: CategoryFieldDef[] = selected?.fields ?? [];

  /**
   * Switching category drops values that the new category has no field for —
   * `fields` holds category-specific values only, and the backend rejects keys
   * outside the template.
   */
  const changeCategory = (next: string) => {
    setCategory(next);
    const allowed = new Set(
      (categories.find((entry) => entry.id === next)?.fields ?? []).map((def) => def.field_name),
    );
    setRawFields((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => allowed.has(key))),
    );
    setFieldErrors({});
  };

  const setField = (name: string, value: string | boolean) =>
    setRawFields((current) => ({ ...current, [name]: value }));

  /** Coerce the raw inputs, collecting per-field errors as we go. */
  const buildFields = (errors: Record<string, string>): Record<string, FieldValue> => {
    const out: Record<string, FieldValue> = {};
    for (const def of defs) {
      const raw = rawFields[def.field_name];

      if (def.field_type === 'boolean') {
        // A checkbox has no "unset": unchecked means the key is simply absent.
        if (raw === true) out[def.field_name] = true;
        continue;
      }

      if (isBlank(raw)) {
        if (def.required) errors[def.field_name] = `${humanize(def.field_name)} is required.`;
        continue;
      }

      const text = String(raw).trim();
      if (def.field_type === 'number') {
        const parsed = Number(text);
        if (!Number.isFinite(parsed)) {
          errors[def.field_name] = `${humanize(def.field_name)} must be a number.`;
          continue;
        }
        out[def.field_name] = parsed;
      } else {
        out[def.field_name] = text;
      }
    }
    return out;
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);

    const errors: Record<string, string> = {};
    if (!category) errors.category = 'Pick a category.';
    const fields = buildFields(errors);

    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    const payload: ItemCreate = {
      category,
      colors,
      brand: brand.trim() || null,
      warmth_rating: warmth,
      formality,
      tags,
      notes: notes.trim() || null,
      fields,
    };

    setSaving(true);
    try {
      const saved = item ? await api.updateItem(item.id, diff(item, payload)) : await api.createItem(payload);
      onSaved(saved);
    } catch (error) {
      setFormError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="item-form" onSubmit={submit} noValidate>
      {formError && <Banner onDismiss={() => setFormError(null)}>{formError}</Banner>}

      <label className="field">
        <span className="field__label">
          Category <span className="field__required">required</span>
        </span>
        <select value={category} onChange={(event) => changeCategory(event.target.value)}>
          <option value="">Choose a category…</option>
          {categories.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.display_name}
            </option>
          ))}
        </select>
        {fieldErrors.category && <span className="field__error">{fieldErrors.category}</span>}
      </label>

      <div className="field">
        <span className="field__label">Colours</span>
        <ColorPicker value={colors} onChange={setColors} />
      </div>

      <label className="field">
        <span className="field__label">Brand</span>
        <input
          type="text"
          value={brand}
          placeholder="Uniqlo, Nike, thrifted…"
          onChange={(event) => setBrand(event.target.value)}
        />
      </label>

      <div className="field">
        <span className="field__label">Warmth</span>
        <div className="segmented" role="group" aria-label="Warmth rating">
          {[1, 2, 3, 4, 5].map((level) => (
            <button
              key={level}
              type="button"
              className={`segmented__option${warmth === level ? ' is-selected' : ''}`}
              aria-pressed={warmth === level}
              title={WARMTH_LABELS[level]}
              onClick={() => setWarmth(warmth === level ? null : level)}
            >
              {level}
            </button>
          ))}
        </div>
        <span className="field__hint">
          {warmth ? WARMTH_LABELS[warmth] : '1 = very light, 5 = heaviest. Tap again to clear.'}
        </span>
      </div>

      <label className="field">
        <span className="field__label">Formality</span>
        <select
          value={formality ?? ''}
          onChange={(event) => setFormality((event.target.value || null) as Formality | null)}
        >
          <option value="">Not set</option>
          {FORMALITIES.map((value) => (
            <option key={value} value={value}>
              {FORMALITY_LABELS[value]}
            </option>
          ))}
        </select>
      </label>

      <div className="field">
        <span className="field__label">Tags</span>
        <TagInput value={tags} onChange={setTags} suggestions={tagSuggestions} />
        <span className="field__hint">
          Free-form labels the assistant can filter on — <code>gym</code>, <code>date-night</code>,
          <code> rainy-day</code>.
        </span>
      </div>

      <label className="field">
        <span className="field__label">Notes</span>
        <textarea
          value={notes}
          rows={3}
          maxLength={2000}
          placeholder="Anything that doesn't fit a field — fits loose, runs hot, small stain on the cuff…"
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>

      {selected && defs.length > 0 && (
        <fieldset className="field-group">
          <legend>{selected.display_name} details</legend>
          {defs.map((def) => (
            <CategoryField
              key={def.field_name}
              def={def}
              value={rawFields[def.field_name]}
              error={fieldErrors[def.field_name]}
              onChange={(value) => setField(def.field_name, value)}
            />
          ))}
        </fieldset>
      )}

      <div className="item-form__actions">
        <button type="button" className="button button--quiet" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="button button--primary" disabled={saving}>
          {saving ? 'Saving…' : item ? 'Save changes' : 'Add to closet'}
        </button>
      </div>
    </form>
  );
}

/** One category-specific input, rendered from its `field_type`. */
function CategoryField({
  def,
  value,
  error,
  onChange,
}: {
  def: CategoryFieldDef;
  value: string | boolean | undefined;
  error: string | undefined;
  onChange: (value: string | boolean) => void;
}) {
  const label = (
    <span className="field__label">
      {humanize(def.field_name)}
      {def.required && <span className="field__required">required</span>}
    </span>
  );

  if (def.field_type === 'boolean') {
    return (
      <label className="field field--checkbox">
        <input
          type="checkbox"
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="field__label">{humanize(def.field_name)}</span>
      </label>
    );
  }

  return (
    <label className="field">
      {label}
      {def.field_type === 'enum' ? (
        <select
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Not set</option>
          {(def.allowed_values ?? []).map((option) => (
            <option key={option} value={option}>
              {humanize(option)}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={def.field_type === 'number' ? 'number' : 'text'}
          value={typeof value === 'string' ? value : ''}
          step="any"
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {error && <span className="field__error">{error}</span>}
    </label>
  );
}

/**
 * PATCH is partial, so send only what actually changed. Beyond being tidy, it
 * keeps an edit from clobbering a field the assistant changed in the meantime.
 */
function diff(original: Item, next: ItemCreate): ItemUpdate {
  const patch: ItemUpdate = {};
  if (original.category !== next.category) patch.category = next.category;
  if (!sameList(original.colors, next.colors)) patch.colors = next.colors;
  if ((original.brand ?? null) !== next.brand) patch.brand = next.brand;
  if ((original.warmth_rating ?? null) !== next.warmth_rating)
    patch.warmth_rating = next.warmth_rating;
  if ((original.formality ?? null) !== next.formality) patch.formality = next.formality;
  if (!sameList(original.tags, next.tags)) patch.tags = next.tags;
  if ((original.notes ?? null) !== next.notes) patch.notes = next.notes;
  if (JSON.stringify(sortKeys(original.fields)) !== JSON.stringify(sortKeys(next.fields)))
    patch.fields = next.fields;
  return patch;
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function sortKeys(fields: Record<string, FieldValue>): Record<string, FieldValue> {
  return Object.fromEntries(Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)));
}
