import { LegalPage } from './LegalPage';
import { APP_NAME, CONTACT_EMAIL, GOVERNING_LAW, LAST_UPDATED, LEGAL_ENTITY } from '../lib/legal';

export function TermsPage() {
  return (
    <LegalPage title="Terms of Service" updated={LAST_UPDATED}>
      <p>
        These Terms of Service ("Terms") govern your use of the {APP_NAME} web app and connector (the
        "service"), operated by {LEGAL_ENTITY}. By using the service you agree to these Terms. If you
        do not agree, do not use the service.
      </p>

      <h2>The service</h2>
      <p>
        {APP_NAME} lets you catalogue your wardrobe and receive weather-aware outfit suggestions, and
        optionally connect an AI assistant that can help manage your closet. The service is offered
        as-is and may change over time.
      </p>

      <h2>Eligibility and accounts</h2>
      <ul>
        <li>You must be at least 13 years old (or the minimum age of digital consent where you live) to use the service.</li>
        <li>You are responsible for the activity under your account and for keeping access to your email or Google account secure.</li>
        <li>Provide accurate information and keep it up to date.</li>
        <li>Access may be limited while the service is in a capacity-limited phase.</li>
      </ul>

      <h2>Acceptable use</h2>
      <p>You agree not to:</p>
      <ul>
        <li>Use the service for any unlawful purpose or in violation of these Terms.</li>
        <li>Attempt to access accounts or data that are not yours, or probe or breach security or authentication measures.</li>
        <li>Disrupt or overload the service, or interfere with its normal operation.</li>
        <li>Reverse engineer or misuse the service or connector except as permitted by law.</li>
      </ul>

      <h2>Your content</h2>
      <p>
        You retain ownership of the closet data and other content you add. You grant us a limited
        license to store and process that content solely to provide and improve the service for you.
        You are responsible for the content you submit.
      </p>

      <h2>AI assistant and suggestions</h2>
      <p>
        Outfit suggestions, weather information, and any output from a connected AI assistant are
        provided for convenience and may be inaccurate or incomplete. They are not professional
        advice. Use your own judgment, and verify weather and other information before relying on it.
        AI assistants are operated by third parties under their own terms.
      </p>

      <h2>Third-party services</h2>
      <p>
        The service relies on third parties including Google (sign-in), Supabase, Amazon Web
        Services, Resend, a weather data provider, and any AI assistant you connect. Your use of those
        services is governed by their respective terms and privacy policies.
      </p>

      <h2>Availability and changes</h2>
      <p>
        We may modify, suspend, or discontinue any part of the service at any time, and we may set or
        change limits on use. We are not liable for any modification, suspension, or discontinuation.
      </p>

      <h2>Disclaimers</h2>
      <p>
        The service is provided "as is" and "as available", without warranties of any kind, whether
        express or implied, including merchantability, fitness for a particular purpose, and
        non-infringement. We do not warrant that the service will be uninterrupted, error-free, or
        secure.
      </p>

      <h2>Limitation of liability</h2>
      <p>
        To the fullest extent permitted by law, {LEGAL_ENTITY} will not be liable for any indirect,
        incidental, special, consequential, or punitive damages, or any loss of data, profits, or
        goodwill, arising from your use of the service.
      </p>

      <h2>Termination</h2>
      <p>
        You may stop using the service and delete your account at any time from the Settings page. We
        may suspend or terminate access if you violate these Terms or to protect the service and its
        users.
      </p>

      <h2>Changes to these Terms</h2>
      <p>
        We may update these Terms from time to time. When we do, we will revise the "Last updated"
        date above. Your continued use of the service after changes take effect constitutes
        acceptance of the updated Terms.
      </p>

      <h2>Governing law</h2>
      <p>
        These Terms are governed by the laws of {GOVERNING_LAW}, without regard to its conflict-of-law
        rules.
      </p>

      <h2>Contact us</h2>
      <p>
        Questions about these Terms? Email us at{' '}
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
      </p>
    </LegalPage>
  );
}
