import { useState } from 'react';
import { COLOR_PALETTE, swatchFor } from '../lib/format';
import { MAX_COLORS } from '../lib/types';

/**
 * Up to five colours. The palette is a shortcut, not an allowlist — anything
 * typed in is accepted, because "vintage wash" is a real answer and the backend
 * stores colours as free text.
 */
export function ColorPicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (colors: string[]) => void;
}) {
  const [custom, setCustom] = useState('');
  const full = value.length >= MAX_COLORS;

  const add = (raw: string) => {
    const color = raw.trim().toLowerCase();
    if (!color || full || value.includes(color)) return;
    onChange([...value, color]);
  };

  const remove = (color: string) => onChange(value.filter((entry) => entry !== color));

  return (
    <div className="color-picker">
      {value.length > 0 && (
        <ul className="chip-list">
          {value.map((color) => (
            <li key={color}>
              <span className="chip chip--color">
                <span className="swatch" style={{ background: swatchFor(color) }} aria-hidden />
                {color}
                <button
                  type="button"
                  className="chip__remove"
                  onClick={() => remove(color)}
                  aria-label={`Remove ${color}`}
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="color-picker__palette" role="group" aria-label="Colour palette">
        {COLOR_PALETTE.map((entry) => {
          const selected = value.includes(entry.name);
          return (
            <button
              key={entry.name}
              type="button"
              className={`swatch-button${selected ? ' is-selected' : ''}`}
              style={{ background: entry.swatch }}
              title={entry.name}
              aria-label={entry.name}
              aria-pressed={selected}
              disabled={full && !selected}
              onClick={() => (selected ? remove(entry.name) : add(entry.name))}
            />
          );
        })}
      </div>

      <div className="color-picker__custom">
        <input
          type="text"
          value={custom}
          placeholder={full ? `${MAX_COLORS} colours is the maximum` : 'Or type a colour…'}
          disabled={full}
          onChange={(event) => setCustom(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return;
            // Enter inside a form would submit it; this input means "add".
            event.preventDefault();
            add(custom);
            setCustom('');
          }}
        />
        <button
          type="button"
          className="button button--quiet"
          disabled={full || !custom.trim()}
          onClick={() => {
            add(custom);
            setCustom('');
          }}
        >
          Add
        </button>
      </div>
      <p className="field__hint">
        {value.length} of {MAX_COLORS} colours
      </p>
    </div>
  );
}
