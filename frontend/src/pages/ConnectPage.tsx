import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Banner } from '../components/Banner';
import { useMe } from '../hooks/useMe';

const EXAMPLES = [
  'Add a navy Uniqlo oxford shirt, long sleeve, smart casual.',
  'What should I wear today? I have a dinner out tonight.',
  'Do I own anything waterproof?',
  'Tag my black boots as work.',
];

export function ConnectPage() {
  const { me, loading, error } = useMe();

  return (
    <div className="page connect-page">
      <div className="page__header">
        <div>
          <h1>Connect your assistant</h1>
          <p className="page__subtitle">
            This page is the handoff. Everything after it happens in chat.
          </p>
        </div>
      </div>

      {error && <Banner>{error}</Banner>}

      <section className="card">
        <h2>Your MCP server URL</h2>
        <p className="card__lede">
          One URL, tied to your account. Anyone who has it and can sign in as you can read your
          closet, so treat it like a login.
        </p>
        {loading ? <p className="placeholder">Loading…</p> : <CopyableUrl url={me?.mcp_url ?? ''} />}
      </section>

      <section className="card">
        <h2>Adding it to Claude.ai</h2>
        <ol className="steps">
          <li>
            Open <strong>Settings → Connectors</strong> in Claude.ai and choose{' '}
            <strong>Add custom connector</strong>.
          </li>
          <li>Paste the URL above and give it a name — “weatherpruf” does the job.</li>
          <li>
            Claude sends you through a sign-in step. Use the same email you used here; that is what
            ties the connector to your closet.
          </li>
          <li>
            Start a new chat and ask it something from the list below. The first question of a
            conversation usually costs an extra call while it reads your closet's structure.
          </li>
        </ol>
      </section>

      <section className="card">
        <h2>What to say once it is connected</h2>
        <ul className="examples">
          {EXAMPLES.map((example) => (
            <li key={example}>“{example}”</li>
          ))}
        </ul>
        <p className="card__lede">
          It reads the weather itself and picks from what you actually own — so the more of your
          closet is in here, the better its answers get. Set your{' '}
          <Link to="/settings">home location</Link> first, or it will have to ask every time.
        </p>
      </section>

      <section className="card">
        <h2>The daily limit</h2>
        <p className="card__lede">
          Each account gets <strong>{me?.daily_call_limit ?? 10} assistant calls per UTC day</strong>
          , and a single question can spend more than one — reading your closet structure is a call,
          and so is each search it runs. That is tight on purpose.
          {me && (
            <>
              {' '}
              You have used <strong>{me.calls_used_today}</strong> of{' '}
              <strong>{me.daily_call_limit}</strong> today.
            </>
          )}
        </p>
        <p className="card__lede">
          When it runs out, the assistant says so and you wait for the UTC day to roll over. This
          web app is not rate-limited — browsing, adding and editing here always works, which is
          part of why bulk cataloguing is better done on the <Link to="/">closet page</Link>.
        </p>
      </section>
    </div>
  );
}

function CopyableUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission).
      // The URL is on screen and selectable, so this is not worth an error.
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="copy-field">
      <code className="copy-field__value">{url || 'Unavailable'}</code>
      <button
        type="button"
        className="button button--primary"
        onClick={copy}
        disabled={!url}
        aria-live="polite"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}
