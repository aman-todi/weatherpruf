import { Link } from 'react-router-dom';

const FEATURES = [
  {
    title: 'Catalogue once',
    body: 'Add each item to your closet a single time — descriptions, categories, and the details that matter.',
  },
  {
    title: 'Dressed for the weather',
    body: "Every morning, get outfit suggestions built around your local forecast and what you actually own.",
  },
  {
    title: 'Bring your own assistant',
    body: 'Connect an AI assistant like Claude to browse and manage your wardrobe in plain language.',
  },
];

/**
 * Public homepage for logged-out visitors. The app root must describe the
 * product without requiring an account (Google OAuth verification rejects a
 * homepage that is just a login screen). Signed-in users never see this — the
 * router sends them to the closet.
 */
export function LandingPage() {
  return (
    <div className="landing">
      <header className="landing__nav">
        <span className="brand">
          <span className="brand__mark" aria-hidden />
          Weatherpruf
        </span>
        <Link to="/login" className="button button--quiet">
          Sign in
        </Link>
      </header>

      <main className="landing__hero">
        <h1 className="landing__title">Your closet, catalogued once — then dressed every morning.</h1>
        <p className="landing__lede">
          Weatherpruf keeps a catalogue of everything you own and suggests what to wear based on the
          day's weather — so you never have to guess at the forecast before getting dressed.
        </p>
        <div className="landing__cta">
          <Link to="/login" className="button button--primary">
            Get started
          </Link>
        </div>

        <ul className="landing__features">
          {FEATURES.map((feature) => (
            <li key={feature.title} className="landing__feature">
              <h2>{feature.title}</h2>
              <p>{feature.body}</p>
            </li>
          ))}
        </ul>
      </main>

      <footer className="landing__footer">
        <Link to="/privacy">Privacy Policy</Link>
        <Link to="/terms">Terms of Service</Link>
      </footer>
    </div>
  );
}
