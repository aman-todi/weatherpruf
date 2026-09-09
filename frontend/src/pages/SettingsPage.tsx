import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Banner } from '../components/Banner';
import { resetCategoryCache } from '../hooks/useCategories';
import { useMe } from '../hooks/useMe';
import { api, errorMessage } from '../lib/api';
import { UNIT_LABELS } from '../lib/format';
import { UNIT_PREFERENCES, type UnitPreference } from '../lib/types';

/** Typed verbatim before the account can be deleted. */
const CONFIRM_PHRASE = 'delete my closet';

export function SettingsPage() {
  const { signOut } = useAuth();
  const { me } = useMe();

  const [homeLocation, setHomeLocation] = useState('');
  const [unit, setUnit] = useState<UnitPreference>('fahrenheit');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.getProfile().then(
      (profile) => {
        if (!active) return;
        setHomeLocation(profile.home_location ?? '');
        setUnit(profile.unit_preference);
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
  }, []);

  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await api.updateProfile({
        home_location: homeLocation.trim() || null,
        unit_preference: unit,
      });
      setHomeLocation(saved.home_location ?? '');
      setUnit(saved.unit_preference);
      setNotice('Settings saved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page settings-page">
      <div className="page__header">
        <div>
          <h1>Settings</h1>
          <p className="page__subtitle">{me?.email}</p>
        </div>
      </div>

      {error && <Banner onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && (
        <Banner tone="success" onDismiss={() => setNotice(null)}>
          {notice}
        </Banner>
      )}

      <section className="card">
        <h2>Where you are</h2>
        <p className="card__lede">
          The assistant looks up the forecast itself — this is the location it looks up, and the
          units it answers in. The app never fetches weather on its own.
        </p>

        {loading ? (
          <p className="placeholder">Loading…</p>
        ) : (
          <form className="stack" onSubmit={saveProfile}>
            <label className="field">
              <span className="field__label">Home location</span>
              <input
                type="text"
                value={homeLocation}
                placeholder="Flint, MI"
                onChange={(event) => setHomeLocation(event.target.value)}
              />
              <span className="field__hint">
                Free text — a city and state, a postcode, whatever a person would say.
              </span>
            </label>

            <div className="field">
              <span className="field__label">Units</span>
              <div className="segmented" role="radiogroup" aria-label="Unit preference">
                {UNIT_PREFERENCES.map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={unit === option}
                    className={`segmented__option${unit === option ? ' is-selected' : ''}`}
                    onClick={() => setUnit(option)}
                  >
                    {UNIT_LABELS[option]}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <button type="submit" className="button button--primary" disabled={saving}>
                {saving ? 'Saving…' : 'Save settings'}
              </button>
            </div>
          </form>
        )}
      </section>

      {me && (
        <section className="card">
          <h2>Your account</h2>
          <dl className="item-card__facts">
            <div className="fact">
              <dt>Email</dt>
              <dd>{me.email}</dd>
            </div>
            <div className="fact">
              <dt>Items</dt>
              <dd>
                {me.item_count} of {me.item_limit}
              </dd>
            </div>
            <div className="fact">
              <dt>Assistant calls today</dt>
              <dd>
                {me.calls_used_today} of {me.daily_call_limit}
              </dd>
            </div>
          </dl>
        </section>
      )}

      <DeleteAccount
        itemCount={me?.item_count ?? null}
        onDeleted={() => {
          resetCategoryCache();
          void signOut();
        }}
      />
    </div>
  );
}

function DeleteAccount({
  itemCount,
  onDeleted,
}: {
  itemCount: number | null;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirmed = phrase.trim().toLowerCase() === CONFIRM_PHRASE;

  const remove = async () => {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAccount();
      onDeleted();
    } catch (cause) {
      setError(errorMessage(cause));
      setDeleting(false);
    }
  };

  return (
    <section className="card card--danger">
      <h2>Delete my account</h2>
      <p className="card__lede">
        This removes {itemCount === null ? 'every item in your closet' : `all ${itemCount} items`},
        your location and units, your usage counters, and your login itself. It cannot be undone,
        and it is deliberately not something the assistant can do for you.
      </p>

      {error && <Banner onDismiss={() => setError(null)}>{error}</Banner>}

      {open ? (
        <div className="stack">
          <label className="field">
            <span className="field__label">
              Type <code>{CONFIRM_PHRASE}</code> to confirm
            </span>
            <input
              type="text"
              value={phrase}
              autoFocus
              autoComplete="off"
              onChange={(event) => setPhrase(event.target.value)}
            />
          </label>
          <div className="button-row">
            <button
              type="button"
              className="button button--quiet"
              disabled={deleting}
              onClick={() => {
                setOpen(false);
                setPhrase('');
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--danger"
              disabled={!confirmed || deleting}
              onClick={remove}
            >
              {deleting ? 'Deleting…' : 'Delete my account permanently'}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="button button--danger" onClick={() => setOpen(true)}>
          Delete my account
        </button>
      )}
    </section>
  );
}
