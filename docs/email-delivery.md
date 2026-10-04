# Discovery Call email diagnosis and verification

Date: 2026-10-04. Local repair implemented and tested; **not deployed**. Production Resend acceptance and inbox delivery remain unverified.

## Proven failure and local repair

The old `/api/leads` handler returned HTTP 201 immediately after submitting a background job. Missing Resend credentials, provider rejection, timeouts, and even subsequent database failures could never change that response. The browser treated any HTTP 2xx as success and reset the form. Two regression tests reproduced 201 where 502/503 were required before the fix.

The repository already had multi-recipient merging, contrary to the initial single-recipient description. The repair retains it, validates the full list and sender syntax, preserves any configured legacy `LEAD_NOTIFICATION_EMAIL`, always includes `business@aigrowlabs.media`, and requires a second unique configured inbox. It no longer assumes an unconfigured hard-coded personal address is confirmed. The actual second inbox must be read from Render, not guessed from source.

The updated backend persists the lead, waits for a Resend 2xx response with an email ID, stores the outcome, and only then reports success. It returns 503 for configuration problems, 502 for provider failures, and 500 for storage/processing failures. Logs contain correlation IDs, recipient counts, provider HTTP status, and the Resend email ID, but no secrets or lead contents. Sheets mirroring remains optional/background. The frontend requires `ok:true` and `email_status:"accepted"`, preserves inputs on errors, and uses the requested failure message.

No CSS, page markup, animations, or pre-form JavaScript changed. The business-email link was already correctly spelled in both the repository and live website; no displayed typo was found.

## Actual production topology

Public GET/OPTIONS checks found:

| Target | Observation |
| --- | --- |
| `https://aigrowlabs.media/` | 200, Vercel, form points to `ai-grow-lab.onrender.com` |
| `https://aigrowlabs.media/api/leads` | 404, static frontend has no same-origin lead API |
| `https://ai-grow-lab.onrender.com/api/leads` | OPTIONS 204, allows POST/Content-Type and cross-origin form submission |
| `https://ai-grow-lab-backend.onrender.com/api/leads` | GET/OPTIONS 404, `x-render-routing: no-server`, no CORS headers |

The live Render hostname is intentionally preserved. Do not switch to the proposed `-backend` hostname until a service actually exists there. GET 404 on the active service's POST-only `/api/leads` is normal.

Public MX records point to Hostinger. That establishes mail routing for the domain only, not existence of the `business` mailbox, Resend sender verification, or inbox receipt.

## Access and deployment blocker

Existing authentication allowed read-only Vercel project inspection. The frontend project is `ai-grow-lab-main`, its custom domains are verified, and its latest production deployment was CLI-created from source metadata matching the initial local commit (`c48e13d`). A Vercel deployment does not deploy the Python backend.

No authenticated Render or Resend access was available in the process environment, project email configuration, or conventional Render CLI configuration. The local `.env.local` contains Vercel configuration, not the Render email settings. No private token values were printed or copied into the project.

Therefore the following are **not verified**:

- Actual Render `RESEND_API_KEY` presence/permissions.
- Actual Render `EMAIL_FROM` and Resend sender-domain verification.
- The identity of the second confirmed recipient or other existing working recipients.
- Which repository/branch/deployment the Render service runs, or its restart after an environment change.
- Resend acceptance/delivery events for the production test.
- Receipt in either destination inbox.

Do not infer that the API key is missing merely because access to inspect it is missing. The false-success root cause is proven; the provider-side cause of non-delivery still requires Render/Resend evidence.

## Real production submission

One authorized, clearly labeled test was submitted using headless Chrome and the actual production form, without intercepting the request:

- Marker/company: `Discovery pipeline test 2026-10-04T09:31:40.995Z`.
- Page: `https://aigrowlabs.media/`.
- Observed endpoint: `https://ai-grow-lab.onrender.com/api/leads`.
- HTTP result: **201**, approximately 1.4 seconds after submission.
- Body: `{"ok":true,"message":"Thanks — we’ll reply within 24 hours."}`.
- UI showed the old success message and reset the form.
- No browser JavaScript errors; correct business-email link; no displayed typo.
- No `email_status`, request ID, or Resend email ID in this legacy response.

This proves the production frontend reached the active Render API and received its legacy response. It does **not** prove the Render email worker succeeded or that Resend accepted the message. No authenticated Render logs or recipient inboxes were accessible. This was a diagnostic of the current deployed code, **not a post-deployment verification of the repair**.

Temporary local evidence: `/tmp/aigrow-production-email-smoke.json` and `/tmp/aigrow-production-email-smoke.png`. These are ephemeral and contain only synthetic test details/public configuration.

## Follow-up: reported failure message

A follow-up investigation on 2026-10-04 rechecked both `https://aigrowlabs.media/` and `https://www.aigrowlabs.media/`. Both still serve the old frontend without the explicit email-acceptance check; neither matches the repaired local file. Vercel still lists the existing production deployment, and remote GitHub `main` remains at `c48e13d`. The repaired source remains local and uncommitted.

A second real production form submission, marker `Discovery pipeline test 2026-10-04T09:38:53.563Z`, reached `https://ai-grow-lab.onrender.com/api/leads` and returned the same legacy 201 body in approximately 0.9 seconds. The form displayed success and reset, with no browser JavaScript errors. Thus the newly reported failure message was **not reproduced on the main production site in this test**. It would be unsafe to infer that delivery succeeded, or to diagnose that message without the failing page's actual request/response.

The proposed `ai-grow-lab-backend.onrender.com` still returns `404 / no-server`. No Render/Resend credentials or newly available authenticated service access were found in the relevant process/project/conventional CLI configuration. No production environment variables were changed and nothing was deployed. The latest temporary smoke artifacts now refer to this follow-up submission.

The next action is unchanged: obtain authenticated access to the active Render service and Resend logs, inspect the existing configuration, deploy the repaired backend before the frontend, and verify provider acceptance and receipt. No additional speculative application changes were made in this follow-up.

## Initial email-repair checks actually run

- `python3 -m unittest discover -s backend -p 'test_*.py'`: **31 passed**. Includes real local HTTP handler requests with mocked Resend; confirms failure propagation, synchronous waiting, safe logs, recipient validation, storage/migration, CORS, and no browser configuration override.
- `PLAYWRIGHT_MODULE='/Users/anuragkeshav/Documents/its a web project/node_modules/playwright/index.mjs' node tools/check-site.mjs http://127.0.0.1:8017 /tmp/aigrow-email-browser-checks`: **194 assertions passed**, at 1440, 768, 393, and 320 pixels. API responses are intercepted; no live email from this suite.
- Real Chrome form submission to the actual local Python backend with missing email configuration, without intercepting requests: **503**, exact failure copy, inputs retained, no email sent.
- `node --check tools/check-site.mjs`: passed.
- `python3 -m py_compile backend/app.py backend/test_app.py`: passed.
- `git diff --check`: passed.
- Compared `index.html` before the form script against HEAD: byte-for-byte unchanged.
- No npm build or TypeScript typecheck exists in this static/Python project.

## Rate-limit repair and latest verification

The reported testing lockout had a reproducible local cause: the original 5-per-15-minute counter ran before validation and counted failed email attempts as though they had delivered. It returned only a vague error message, without a machine-readable code or retry time. Three new tests failed against that implementation: invalid-input recovery, definitive provider-rejection recovery, and the missing 429 response contract. Its client identity also blindly trusted the first `X-Forwarded-For` value; no authenticated production evidence establishes the actual proxy chain yet.

The repair in `backend/rate_limit.py` keeps the original **five pending/accepted sends per 15 minutes**, plus a separate **five-request/minute** protection for all POST attempts. Invalid forms do not reserve sends; definitive failures release only their send reservation. Ambiguous provider results retain quota to avoid duplicate/abusive sending. Atomic SQLite reservations prevent concurrent overshoot and survive process restarts when the database file persists. Blocked attempts do not prolong lockouts. There are no test exemptions, disabled limits, public reset endpoints, or production quota resets.

`TRUSTED_PROXY_CIDRS` explicitly configures trusted ingress networks. Direct/untrusted peers cannot spoof their identity through forwarded headers. Trusted chains are walked right-to-left; malformed chains fail closed. IPv4-mapped addresses normalize to one identity, and IPv6 /64 grouping prevents interface-ID rotation. **The actual Render proxy ranges still must be verified/configured before deployment.** Do not infer them from the unit-test addresses. Likewise, persistent single-instance storage must be confirmed; independently scaled instances require shared state.

Local 429 now has `code: rate_limited`, `retry_after`, `email_status: not_attempted`, `Retry-After`, CORS exposure, and no-cache handling. The form preserves fields, displays a specific wait time, blocks repeat clicks during the cooldown, and never retries automatically. Resend's own 429 remains a 502 email-delivery failure, not a client quota error. Page markup/design/CSS/animation code remains unchanged.

Latest checks:

- **58 Python tests passed**, including preflight/page-load isolation, concurrency, persistence, expiration boundaries, spoof resistance, separate clients, validation/provider-rejection recovery, timeout conservatism, CORS, and no send/storage on a rate rejection.
- **222 browser assertions passed** at 1440/768/393/320px, including numeric and HTTP-date `Retry-After`, non-JSON/legacy 429, cooldown expiration, and no auto-resubmission. API requests in this suite are intercepted.
- An additional isolated Chrome → real local backend → real SQLite limiter test used only a **fake outbound Resend response**. Five accepted notifications succeeded and verified both configured recipients in the provider payload; the sixth returned 429 with `not_attempted`, preserved inputs, and disabled repeat submission. The provider fixture saw exactly five calls. No limiter thresholds or production counters were changed for this test.

### Clean production check after the natural window

One actual production submission was made after more than 15 minutes had elapsed since the previous agent submission, without spoofed headers, resetting state, or bypassing rate limiting:

- Marker: `Discovery rate-limit test 2026-10-04T09:59:24.658Z`.
- Browser page: `https://aigrowlabs.media/`.
- Request reached `https://ai-grow-lab.onrender.com/api/leads`.
- HTTP **201**, approximately 1.4 seconds; no `Retry-After` header.
- Response remained the **legacy** `{"ok":true,"message":"Thanks — we’ll reply within 24 hours."}`.
- Production HTML still lacked the new acceptance check. It displayed success, reset fields, and had no browser JavaScript errors.
- Evidence: `/tmp/aigrow-production-rate-smoke.json` and `/tmp/aigrow-production-rate-smoke.png` (temporary local artifacts).

This establishes a legitimate request was no longer rate-rejected at that time. It **does not establish Resend acceptance or recipient delivery**, and it is not a post-deployment test of the local repairs. Render/Resend access remains unavailable; no production deployment or environment changes were made. Full production completion still requires the steps below.

## Required next steps to complete production repair

1. Connect authenticated Render access for the **active** service. Inspect the environment without exposing credentials. Preserve existing working recipients.
2. Set/confirm, on Render only:
   ```env
   RESEND_API_KEY=<existing valid server-side Resend key>
   EMAIL_FROM=AI Grow Lab <a sender at a domain verified in that Resend account>
   LEAD_NOTIFICATION_EMAILS=business@aigrowlabs.media,<second confirmed address>
   ```
   These are placeholders, not deployable literal values. Keep `LEAD_NOTIFICATION_EMAIL` if it contains an existing recipient, or explicitly migrate that address into the plural setting. Use `HOST=0.0.0.0` and Render's assigned `PORT`.
3. Verify Render's immediate/intermediate proxy networks and set `TRUSTED_PROXY_CIDRS` narrowly; confirm persistent single-instance SQLite storage or provide a shared limiter store before scaling. Deploy/restart the Render backend first; check configuration startup logs and its running revision. Then deploy only public frontend assets to Vercel. Do not publish `.env*`, `data/`, backend source, or integration secrets as static assets.
4. Repeat one labeled real form submission on `aigrowlabs.media`; require 201 with `email_status:"accepted"`. Match its request ID to Render's `email_accepted` log and the Resend email ID.
5. Verify the actual recipient list and delivery events in Resend, then confirm receipt with both mailbox owners, including spam folders. Controlled provider failures should be tested in staging rather than by breaking working production credentials.

Until those steps are complete, do not describe this as a fully repaired or verified production email pipeline.
