import { useId, useState } from 'react';

/**
 * Free-form tags. Enter or comma commits one; Backspace on an empty input
 * removes the last, which is the behaviour people expect from a chip input.
 */
export function TagInput({
  value,
  onChange,
  placeholder = 'Add a tag and press Enter',
  suggestions = [],
}: {
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
}) {
  const [draft, setDraft] = useState('');
  const listId = useId();

  const add = (raw: string) => {
    const tag = raw.trim().toLowerCase();
    if (!tag || value.includes(tag)) return;
    onChange([...value, tag]);
  };

  const unused = suggestions.filter((tag) => !value.includes(tag)).slice(0, 40);

  return (
    <div className="tag-input">
      {value.length > 0 && (
        <ul className="chip-list">
          {value.map((tag) => (
            <li key={tag}>
              <span className="chip">
                {tag}
                <button
                  type="button"
                  className="chip__remove"
                  onClick={() => onChange(value.filter((entry) => entry !== tag))}
                  aria-label={`Remove ${tag}`}
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <input
        type="text"
        value={draft}
        placeholder={placeholder}
        list={unused.length ? listId : undefined}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          add(draft);
          setDraft('');
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault();
            add(draft);
            setDraft('');
          } else if (event.key === 'Backspace' && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
      />
      {unused.length > 0 && (
        <datalist id={listId}>
          {unused.map((tag) => (
            <option key={tag} value={tag} />
          ))}
        </datalist>
      )}
    </div>
  );
}
