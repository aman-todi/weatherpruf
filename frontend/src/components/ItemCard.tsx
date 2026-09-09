import { useState } from 'react';
import { FORMALITY_LABELS, WARMTH_LABELS, formatDate, humanize, swatchFor } from '../lib/format';
import type { Category, FieldValue, Item } from '../lib/types';

export function ItemCard({
  item,
  category,
  onEdit,
  onDelete,
}: {
  item: Item;
  /** Undefined if the taxonomy failed to load; the raw id is shown instead. */
  category: Category | undefined;
  onEdit: () => void;
  onDelete: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const fieldEntries = Object.entries(item.fields).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );

  return (
    <article className="item-card">
      <header className="item-card__header">
        <h3>{category?.display_name ?? humanize(item.category)}</h3>
        <span className="item-card__date">Added {formatDate(item.created_at)}</span>
      </header>

      {item.colors.length > 0 && (
        <ul className="item-card__colors">
          {item.colors.map((color) => (
            <li key={color} className="chip chip--color">
              <span className="swatch" style={{ background: swatchFor(color) }} aria-hidden />
              {color}
            </li>
          ))}
        </ul>
      )}

      <dl className="item-card__facts">
        <Fact label="Brand" value={item.brand} />
        <Fact
          label="Warmth"
          value={item.warmth_rating ? `${item.warmth_rating} · ${WARMTH_LABELS[item.warmth_rating]}` : null}
        />
        <Fact label="Formality" value={item.formality ? FORMALITY_LABELS[item.formality] : null} />
        {fieldEntries.map(([key, value]) => (
          <Fact key={key} label={humanize(key)} value={displayValue(value)} />
        ))}
      </dl>

      {item.tags.length > 0 && (
        <ul className="chip-list item-card__tags">
          {item.tags.map((tag) => (
            <li key={tag} className="chip chip--tag">
              {tag}
            </li>
          ))}
        </ul>
      )}

      {item.notes && <p className="item-card__notes">{item.notes}</p>}

      <footer className="item-card__actions">
        {confirming ? (
          <>
            <span className="item-card__confirm">Delete this item?</span>
            <button
              type="button"
              className="button button--quiet"
              onClick={() => setConfirming(false)}
              disabled={deleting}
            >
              Keep
            </button>
            <button
              type="button"
              className="button button--danger"
              disabled={deleting}
              onClick={async () => {
                setDeleting(true);
                try {
                  await onDelete();
                } finally {
                  setDeleting(false);
                  setConfirming(false);
                }
              }}
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="button button--quiet" onClick={onEdit}>
              Edit
            </button>
            <button
              type="button"
              className="button button--quiet"
              onClick={() => setConfirming(true)}
            >
              Delete
            </button>
          </>
        )}
      </footer>
    </article>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function displayValue(value: FieldValue): string {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (value === null) return '';
  return humanize(String(value));
}
