# AI Grow Lab website + lead backend

live url : https://anuragkeshav.github.io/ai-grow-lab/

The contact form now posts to a same-origin Python backend. Every accepted lead is stored in a local SQLite database, and can additionally trigger an email alert and be mirrored to Google Sheets.

## What is already wired

- Form confirmation: **“Thanks — we’ll reply within 24 hours.”**
- Lead-notification recipients: the connected `LEAD_NOTIFICATION_EMAIL` inbox (default `anuragkeshav03@gmail.com`) **plus** `business@aigrowlabs.media`
- Public business email: `business@aigrowlabs.media`
- Additional confirmed inboxes: supported through server-side `LEAD_NOTIFICATION_EMAILS`; no second additional address has been supplied, so it remains blank
- Instagram: `@aigrowlabs_`
- Basic validation, a hidden bot field, request-size limits and rate limiting
- Local lead backup: `data/leads.db` (created automatically and excluded from Git)

## Run it locally

1. In this folder, copy `.env.example` to `.env`.
2. Fill in the optional email and Google Sheets configuration below.
3. Run `python3 backend/app.py`.
4. Open `http://127.0.0.1:8000` in your browser.

Opening `index.html` directly will show the website but will not submit the form; use the server command for lead capture.

## Set up email notifications

This project sends notifications through [Resend](https://resend.com/). Recipient routing is already implemented; live delivery still depends on valid server-side configuration and provider acceptance.

1. Use your Resend account and verify the sender domain (`aigrowlabs.media` for the sender below).
2. Create an API key with email-sending access.
3. In the private `.env` or your hosting provider's environment settings, set:

   ```env
   EMAIL_FROM=AI Grow Lab <leads@aigrowlabs.media>
   RESEND_API_KEY=your_key_here
   ```

   `your_key_here` is documentation only; replace it with the real key on the server. Preserve existing credentials and the connected `LEAD_NOTIFICATION_EMAIL` value rather than overwriting a configured `.env` with the example. Hosting environment variables take precedence over `.env`; restart the Python server after changes.

### Recipient rules

- `LEAD_NOTIFICATION_EMAIL` preserves the connected inbox. If unset or blank, it defaults to `anuragkeshav03@gmail.com`.
- `business@aigrowlabs.media` is always included by the backend; no extra environment setting is required for it.
- `LEAD_NOTIFICATION_EMAILS` is an optional comma-separated list of **additional confirmed addresses**, not a replacement for the connected inbox. **The second additional address has not been provided. Leave this setting blank until it is confirmed; no address has been invented or configured.**
- Leading/trailing whitespace is trimmed, addresses are lowercased, and duplicates across the entire list are removed. Empty list entries are ignored. An invalid nonempty address fails the whole email attempt instead of silently delivering to only part of the list.
- Each email attempt makes **one provider request** with all unique recipients in `to` (so recipients can see each other's addresses). Its plain-text body includes all five form fields: name, brand/company, work email, campaign objective, and context. Empty optional context appears as `—`. The submitter's work email is `reply_to`, not an extra notification recipient.

### Credentials and delivery status

Keep the API key and recipient configuration server-side, in the private `.env` or hosting environment; never put credentials in `index.html`, commit them, or share them in chat. Browser-submitted recipient/key overrides are ignored. The included Python server blocks public access to configuration, backend source, and lead data; any external static host or reverse proxy must also keep those files private.

Email runs in a background worker. The form's success response confirms that work was queued, **not that email reached any inbox**. With either `EMAIL_FROM` or `RESEND_API_KEY` missing, email is skipped (`not_configured`). Invalid recipient settings, provider errors, and timeouts yield `failed`; a provider 2xx response yields `sent`, meaning API acceptance only, not confirmed delivery. These statuses are saved alongside the lead in `data/leads.db` when background processing completes.

There is no durable delivery queue, automatic retry, or bounce/delivery webhook handling. A stopped process can lose queued work, including the eventual database write. Sender verification, account restrictions, mailbox existence, spam filtering, and actual inbox delivery must be checked separately in Resend and the destination inboxes. Static-only GitHub Pages cannot run this backend or send these notifications.

### Run email regression tests (no live email)

From the project folder:

```sh
python3 -m unittest discover -s backend -p 'test_*.py'
```

The suite bypasses private `.env` loading, uses synthetic test settings and mocked provider calls, and checks routing, deduplication, all five fields, error handling, browser override rejection, and private-file access. It does not verify live credentials or inbox delivery.

## Set up Google Sheets

1. Create a Google Sheet, then copy its Sheet ID from the URL.
2. Open **Extensions → Apps Script** in that Sheet.
3. Paste the code from `integrations/google-apps-script.gs`.
4. Replace `SHEET_ID` and `SHARED_SECRET`, then deploy it as a **Web app** accessible to anyone.
5. Add the deployed URL and the same secret to `.env`:

   ```env
   GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/your-deployment-id/exec
   GOOGLE_SHEETS_SHARED_SECRET=the_same_long_random_value
   ```

## Scrolling performance and browser checks

See [`docs/performance.md`](docs/performance.md) for the measured bottlenecks, before/after results, design comparisons, test limitations, and exact reproduction commands. [`docs/performance-results.json`](docs/performance-results.json) preserves the individual measured runs.

The optional tools in `tools/` use an existing Playwright installation and Chrome; they add no production dependency. Browser form tests intercept requests rather than sending real leads.

## Before publishing

- Confirm the `business@aigrowlabs.media` mailbox is active and provide the pending second additional inbox before adding it to `LEAD_NOTIFICATION_EMAILS`.
- Configure a real hosting provider that can run Python; static-only hosting will not run the lead API.
- Set `HOST=0.0.0.0` and the platform-provided `PORT` on the production host.
- Add a privacy policy before accepting public leads.
