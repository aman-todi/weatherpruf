import { LegalPage } from './LegalPage';
import { APP_NAME, CONTACT_EMAIL, LAST_UPDATED } from '../lib/legal';

export function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy" updated={LAST_UPDATED}>
      <p>
        This Privacy Policy explains what information {APP_NAME} ("we", "us") collects when you use
        the {APP_NAME} web app and connector, how we use it, and the choices you have. By using the
        service you agree to the practices described here.
      </p>

      <h2>Information we collect</h2>
      <ul>
        <li>
          <strong>Account information.</strong> When you sign in with a magic link we store your
          email address. When you sign in with Google we receive your name, email address, and
          profile picture as provided by your Google account. We never receive your Google password.
        </li>
        <li>
          <strong>Profile and preferences.</strong> The home location and temperature unit you set,
          so we can tailor weather-based suggestions.
        </li>
        <li>
          <strong>Your closet data.</strong> The clothing items you add — descriptions, categories,
          and attributes you record.
        </li>
        <li>
          <strong>Technical and usage data.</strong> Standard server logs such as IP address,
          browser and device information, and timestamps, used to operate and secure the service.
        </li>
      </ul>
      <p>
        We do <strong>not</strong> collect payment information (the service takes no payments), and
        we do not use third-party advertising or cross-site tracking.
      </p>

      <h2>How we use your information</h2>
      <ul>
        <li>To provide the service — cataloguing your closet and generating weather-aware outfit suggestions.</li>
        <li>To authenticate you and keep your account secure.</li>
        <li>To respond to your questions and support requests.</li>
        <li>To maintain, debug, and improve reliability and performance.</li>
        <li>To comply with legal obligations and enforce our Terms of Service.</li>
      </ul>

      <h2>Connecting an AI assistant</h2>
      <p>
        {APP_NAME} lets you connect an AI assistant (such as Claude) through a connector. When you
        do, the assistant can read and modify your closet data on your behalf to answer your
        requests. That access is initiated and controlled by you, and your use of the assistant is
        also governed by that provider's own terms and privacy policy. You can disconnect the
        assistant at any time.
      </p>

      <h2>How we share information</h2>
      <p>
        We do not sell your personal information. We share it only with service providers who process
        data on our behalf to run the service:
      </p>
      <ul>
        <li><strong>Supabase</strong> — authentication and database hosting for your account and closet data.</li>
        <li><strong>Amazon Web Services (AWS)</strong> — application hosting and infrastructure.</li>
        <li><strong>Google</strong> — sign-in, if you choose to use it.</li>
        <li><strong>Resend</strong> — delivery of sign-in and account emails.</li>
        <li><strong>A weather data provider</strong> — to fetch forecasts for your home location.</li>
      </ul>
      <p>
        We may also disclose information if required by law, or to protect the rights, safety, and
        security of our users and the service.
      </p>

      <h2>Weather lookups</h2>
      <p>
        To provide forecasts, your home location may be sent to a third-party weather provider. We
        send only what is needed to retrieve the forecast.
      </p>

      <h2>Data retention and deletion</h2>
      <p>
        We keep your information for as long as your account is active. You can delete your closet
        and account at any time from the Settings page; doing so removes your closet data and
        associated profile. Some records may persist briefly in backups or where retention is
        required by law, after which they are deleted.
      </p>

      <h2>Security</h2>
      <p>
        We protect your data in transit with HTTPS/TLS and apply access controls to the systems that
        store it. No method of transmission or storage is completely secure, so we cannot guarantee
        absolute security.
      </p>

      <h2>Where your data is processed</h2>
      <p>
        The service is operated in the United States (AWS, US region). If you access it from
        elsewhere, your information will be processed in the United States.
      </p>

      <h2>Children</h2>
      <p>
        The service is not directed to children under 13, and we do not knowingly collect personal
        information from them. If you believe a child has provided us information, contact us and we
        will delete it.
      </p>

      <h2>Your rights</h2>
      <p>
        Depending on where you live, you may have the right to access, correct, export, or delete
        your personal information, or to object to certain processing. You can exercise many of these
        directly in the app, or contact us using the details below.
      </p>

      <h2>Changes to this policy</h2>
      <p>
        We may update this Privacy Policy from time to time. When we do, we will revise the "Last
        updated" date above, and significant changes will be made prominent in the app.
      </p>

      <h2>Contact us</h2>
      <p>
        Questions about this policy or your data? Email us at{' '}
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
      </p>
    </LegalPage>
  );
}
