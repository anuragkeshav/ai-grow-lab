# AI Grow Lab website + lead backend

Production frontend: https://aigrowlabs.media/ (Vercel).

The Discovery Call form posts to a Python backend on Render. Public checks on 2026-10-04 found the running service at `https://ai-grow-lab.onrender.com/api/leads`. The proposed `https://ai-grow-lab-backend.onrender.com` returned Render's `404 / no-server`; do not switch to it unless Render confirms a running service there. Local development uses a same-origin API.

Every valid submission is stored in SQLite before the backend synchronously calls Resend. Only confirmed API acceptance produces the success response. Google Sheets remains an optional background mirror.

## What is already wired

- Form confirmation after Resend acceptance: **“Thanks — we'll reply within 24 hours.”**
- Delivery/configuration/network failure: **“We couldn't send your request. Please try again or email us directly.”**
- Notification recipients: `business@aigrowlabs.media` plus the second confirmed inbox configured on Render; preserve any existing `LEAD_NOTIFICATION_EMAIL` recipient
- Public business email: `business@aigrowlabs.media`
- Recipient configuration uses server-side `LEAD_NOTIFICATION_EMAILS`. The actual production second inbox still requires authenticated Render verification; do not guess it.
- Instagram: `@aigrowlabs_`
- Basic validation, a hidden bot field, request-size limits and rate limiting
- Local lead backup: `data/leads.db` (created automatically and excluded from Git)

## Run it locally

1. In this folder, copy `.env.example` to `.env`.
2. Fill in the required email configuration below; Google Sheets is optional.
3. Run `python3 backend/app.py`.
4. Open `http://127.0.0.1:8000` in your browser.

Opening `index.html` directly will show the website but will not submit the form; use the server command for lead capture.

## Set up email notifications

This project sends notifications through [Resend](https://resend.com/). Configure the **active Render service**, not the Vercel frontend. The checked-in code cannot prove that production has these variables or that the sender domain is verified.

1. Use your Resend account and verify the sender domain (`aigrowlabs.media` for the sender below).
2. Create an API key with email-sending access.
3. In the private `.env` or your hosting provider's environment settings, set:

   ```env
   EMAIL_FROM=AI Grow Lab <leads@aigrowlabs.media>
   RESEND_API_KEY=your_key_here
   LEAD_NOTIFICATION_EMAILS=business@aigrowlabs.media,SECOND_CONFIRMED_ADDRESS
   ```

   `your_key_here` and `SECOND_CONFIRMED_ADDRESS` are placeholders; replace them only in the server environment. Preserve the existing key and connected `LEAD_NOTIFICATION_EMAIL` value. `EMAIL_FROM` must use a sender domain verified in the same Resend account, with a key permitted to send from it. The example sender is not proof of verification. Hosting environment variables take precedence over `.env`; `.env.local` on Vercel is **not** loaded by this backend. Save changes and redeploy/restart Render so the running process receives them.

### Recipient rules

- `LEAD_NOTIFICATION_EMAILS` is the preferred comma-separated list of confirmed recipients.
- `LEAD_NOTIFICATION_EMAIL` is still supported and merged into that list so an existing working inbox is not lost. It may be removed only after intentionally migrating its confirmed address into the plural setting.
- `business@aigrowlabs.media` is always included. At least one other unique, environment-configured address is required. The former hard-coded personal fallback is no longer treated as a confirmed recipient; missing configuration fails closed rather than guessing an inbox.
- Leading/trailing whitespace is trimmed, addresses are lowercased, and duplicates across the entire list are removed. Empty list entries are ignored. An invalid nonempty address fails the whole email attempt instead of silently delivering to only part of the list.
- Each email attempt makes **one provider request** with all unique recipients in `to` (so recipients can see each other's addresses). Its plain-text body includes all five form fields: name, brand/company, work email, campaign objective, and context. Empty optional context appears as `—`. The submitter's work email is `reply_to`, not an extra notification recipient.

### Credentials and delivery status

Keep the API key and recipient configuration server-side, in the private `.env` or hosting environment; never put credentials in `index.html`, commit them, or share them in chat. Browser-submitted recipient/key overrides are ignored. The included Python server blocks public access to configuration, backend source, and lead data; any external static host or reverse proxy must also keep those files private.

The lead is persisted first with `email_status=pending` and a generated request ID. The request then waits for Resend (10-second provider timeout) and stores the result before responding:

| Outcome | HTTP | Stored email status | Frontend |
| --- | --- | --- | --- |
| Resend 2xx **and valid email ID** | 201, `ok:true`, `email_status:"accepted"` | `sent` | Success and reset |
| Missing/invalid email configuration | 503 | `not_configured` | Failure, preserve inputs |
| Definitive Resend rejection | 502, `code:"email_delivery_failed"` | `failed` | Failure, preserve inputs |
| Ambiguous timeout, upstream 5xx, bad acceptance response | 502, `code:"email_delivery_failed"` | `uncertain` | Failure, preserve inputs; investigate before resending |
| Storage/unexpected processing error | 500 | Depends on failure stage | Failure, preserve inputs |
| Validation/honeypot | 400 | No lead inserted | Failure, preserve inputs |
| Local rate limit | 429, `code:"rate_limited"`, `retry_after` | No lead inserted or email attempted | Distinct cooldown message, preserve inputs |

The frontend also rejects legacy queued-only `201` responses, malformed JSON, and network errors; a 2xx alone is not enough. It stops waiting after 90 seconds. Logs include `lead_request_received`, `email_attempt`, `resend_response status=...`, `email_accepted resend_email_id=...`, and `delivery_failure`, correlated by request ID. Raw provider bodies, API keys, authorization headers, and lead contents are not logged. Startup logs report configuration validity and recipient count, never the key. Sender syntax is checked locally; **Resend**, not a regex, verifies permission to send from the domain.

`sent`/`accepted` means Resend API acceptance, **not inbox delivery**. Check Resend's delivery events and both recipient inboxes, including spam. No automatic retries or durable delivery queue are added: an ambiguous timeout might still have been accepted upstream; inspect logs before manually resending. A process stopped mid-send can leave a `pending` record. The per-request Resend idempotency key is not cross-submission deduplication. Use persistent storage on Render if SQLite records must survive redeployments. Static-only GitHub Pages cannot run this backend.

### Rate limiting and production proxy configuration

Rate limiting is enforced in SQLite transactions rather than per-process memory:

- **Request protection:** at most 5 POST attempts per client per 60 seconds, including invalid forms, honeypots, and email failures. Rejected requests do not extend the window.
- **Notification protection:** the original cap of **5 pending/accepted notifications per client per 15 minutes** remains. Reserve a slot before sending so concurrent requests cannot overshoot it.
- Invalid forms never use notification slots. Confirmed provider rejections, missing email configuration, and pre-send storage failures release their notification slot, but **not** their short request-limit entry. A legitimate retry can proceed once that short window expires rather than being locked out for 15 minutes by an unsuccessful attempt.
- Timeouts, upstream 5xx, malformed acceptance responses, and unexpected send exceptions may have sent email; they retain the notification slot conservatively. Do not automatically retry these.
- IPv4-mapped IPv6 addresses are normalized; IPv6 clients are grouped by /64 to prevent simple address rotation. Different verified client IPs have independent budgets; people behind the same real NAT still share a budget.
- HTTP 429 includes `code:"rate_limited"`, `email_status:"not_attempted"`, an accurate `retry_after`, and a matching `Retry-After` header. CORS exposes this header to Vercel; JSON responses use `Cache-Control: no-store`. Provider-side 429 is an **email failure (HTTP 502)**, not a visitor rate-limit rejection.
- The frontend displays the cooldown separately from email failure, keeps inputs, prevents repeat clicks until the indicated time, and **never auto-resubmits**. Legacy/non-JSON 429s still display a rate-limit message.

**Proxy identity is security-sensitive.** By default, the backend ignores `X-Forwarded-For` and uses its direct TCP peer. On Render, configure `TRUSTED_PROXY_CIDRS` with the **verified immediate ingress and intermediate proxy networks**. The backend walks the chain right-to-left and stops at the first untrusted address; it does not trust arbitrary client-supplied first entries. Malformed trusted chains fail closed. Trust-all ranges (`0.0.0.0/0`, `::/0`) are rejected. Do not invent Render CIDRs or broadly trust every private network. Without the correct topology, traffic can share a proxy bucket and incorrectly throttle unrelated users. This configuration remains a production verification requirement, not an established fact from local tests.

The counters survive worker restarts only when the same database file persists. Mount `data/` on a persistent Render disk to survive redeploys. Multiple processes sharing that file are transactionally coordinated; **separate Render instances need a shared transactional store before horizontal scaling**. There is no public reset route, test bypass, or production quota-clearing command. Automated tests use temporary databases and mocked provider responses; repeated successful live tests correctly consume the real quota and must wait for it to expire.

### Run email regression tests (no live email)

From the project folder:

```sh
python3 -m unittest discover -s backend -p 'test_*.py'
```

The suite bypasses private `.env` loading, uses synthetic settings and mocked provider calls, and checks recipient/sender validation, preservation/deduplication, payloads, safe logs, acceptance IDs, synchronous API success/failure, storage/migration, CORS, browser override rejection, and private-file access. `tools/check-site.mjs` tests browser success, failure, malformed/legacy responses, and network errors using intercepted requests. These checks do not verify live credentials or inbox delivery.

### Production deployment and verification

1. In the active Render service, confirm `RESEND_API_KEY` exists, `EMAIL_FROM` uses a verified Resend domain, and the confirmed recipients are present. Verify the actual proxy chain and set `TRUSTED_PROXY_CIDRS` accordingly; confirm a single instance with persistent `data/` storage (or implement a shared limiter before scaling). Check service URL, repository/branch, and start command (`python3 backend/app.py` from the repository root). Use `HOST=0.0.0.0` and Render's assigned `PORT`.
2. Deploy the backend **first** and verify its startup configuration log. Preserve existing SQLite data; startup adds the `request_id` column without replacing the table.
3. Deploy the public frontend to the linked Vercel project. Publish **only public assets** (`index.html`, icons, `logo.jpg`), never `.env*`, `data/`, backend source, or integration secrets. Vercel cannot deploy the Render backend. Do not deploy the new acceptance-checking frontend ahead of the backend.
4. Submit one clearly labeled test using the real form at `https://aigrowlabs.media/#contact`. Check browser Network: the active Render `POST /api/leads` must return `201`, `ok:true`, `email_status:"accepted"`, and a request ID. Error responses must retain inputs and show the failure message.
5. In Render logs, find the same request ID, recipient count, provider status, and Resend email ID. In Resend, check the `to` list and delivery events for **every** configured recipient; have the mailbox owner confirm inbox receipt. Provider acceptance alone is not proof of receipt.
6. Exercise a controlled provider/configuration failure in staging, not by disabling working production credentials. Expect 502/503 and no success UI.

Until Render/Resend access and inbox confirmation are available, production delivery remains **unverified**. See [`docs/email-delivery.md`](docs/email-delivery.md) for the latest diagnosis and test evidence.

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

- Confirm the `business@aigrowlabs.media` mailbox is active and preserve the second confirmed inbox from the Render environment in `LEAD_NOTIFICATION_EMAILS` or the legacy setting.
- Configure a real hosting provider that can run Python; static-only hosting will not run the lead API.
- Set `HOST=0.0.0.0` and the platform-provided `PORT` on the production host.
- Add a privacy policy before accepting public leads.
