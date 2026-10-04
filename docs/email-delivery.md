# Discovery Call email diagnosis and verification

Date: 2026-10-04. **Email configuration is now loaded, but Resend rejects the unverified sender domain.** The latest findings below supersede earlier missing-configuration and access blockers recorded later in this document.

## Latest result after the owner added email settings

Render now contains a sending-only `RESEND_API_KEY`, an `EMAIL_FROM` on `aigrowlabs.media`, and the two real notification recipients (the business mailbox plus the configured Gmail inbox). Runtime startup initially confirmed `recipient_count=2`. The key is recognized by Resend, but `/domains` returns `401 restricted_api_key` because this key can send only; it cannot read domain metadata.

A subsequent environment edit left a literal second-email placeholder alongside the two actual addresses. This caused `invalid_notification_recipient` and HTTP 503. The agent removed **only the known placeholder** via the single-variable Render API, preserved both real recipients, and verified the API key and sender were unchanged. No recipient was invented or silently dropped.

To diagnose the provider's 403 without exposing raw response bodies, fixed-code rejection hints and tests were added in commit `fdb545842fd15f88c5631657a5fdc4d76d923d94`, pushed to GitHub, and deployed to Render. The latest live deployment after the configuration correction is `dep-db131s60tbcc7399b1g0`. **61 Python tests passed**, including body/name redaction and domain-rejection diagnostics. Frontend assets and design did not change; the existing Vercel deployment remains current.

The actual post-correction test was:

- Marker: `Discovery sender-verification test 2026-10-04T10:57:46.953Z`.
- Page: `https://aigrowlabs.media/`; endpoint: `https://ai-grow-lab.onrender.com/api/leads`.
- Backend response: **502**, `code: email_delivery_failed`.
- Request ID: `5a0d2cc2-04dd-470d-a8d1-f3bc0648180b`.
- Render logs confirm `lead_request_received`, `email_attempt provider=resend recipient_count=2`, then **`resend_response status=403 ... provider_error=validation_error hint=sender_domain_unverified`**.
- The form retained inputs and correctly displayed failure. **Resend rejected the email; neither recipient was sent this test notification.**
- Temporary local evidence: `/tmp/aigrow-sender-verification-smoke.json`.

Authoritative DNS is hosted by Hostinger (`atlas.dns-parking.com` / `hyperion.dns-parking.com`). Queries directly to the authoritative server returned NXDOMAIN for `resend._domainkey.aigrowlabs.media` and `send.aigrowlabs.media`; this is not merely a stale local DNS-cache observation. Existing inbound MX records are `mx1.hostinger.com` and `mx2.hostinger.com` and must be preserved.

The remaining email blocker requires verifying `aigrowlabs.media` in Resend: obtain the account-specific sending/DKIM records from Resend Domains, add them to Hostinger DNS using exactly the supplied names/types/values, and wait for Resend's **Verified** status. The agent has Render/GitHub access, but not Hostinger DNS access or Resend domain-management permission. Do not use a sandbox sender, remove the business recipient, or weaken validation to bypass this requirement. Existing proxy-trust/persistent-storage verification notes below also remain applicable.

## Earlier authenticated production findings (missing settings since resolved)

Render and GitHub access are now connected. GitHub `main` and the active Render deployment were both running `c8289d60a304289c7feb9da814493874893b1c20` when inspected. The active service is `ai-grow-lab` (`srv-db12dopsrm7s739oairg`), linked to `anuragkeshav/ai-grow-lab`, branch `main`, with root directory `backend/`, build command `pip install -r requirements.txt`, and start command `python3 app.py`. It is a single **free** instance. Its live deployment is `dep-db12eelg1s2s7388bvtg`.

The authenticated Render environment-variable API returned **zero service variables**, and the workspace has **zero environment groups**. Specifically, `RESEND_API_KEY`, `EMAIL_FROM`, `LEAD_NOTIFICATION_EMAIL`, `LEAD_NOTIFICATION_EMAILS`, and `TRUSTED_PROXY_CIDRS` are absent. The startup log confirms `Email configuration invalid reason=missing_RESEND_API_KEY`. No sender or second recipient can be treated as configured/confirmed. Vercel also has no production environment variables; there is no existing Resend configuration there to migrate.

Vercel was still serving the old frontend. The matching public assets were deployed to the existing `ai-grow-lab-main` project, deployment `dpl_B8sSoWJvmTKAsZ8vcamc2QKMw6pN` (`https://ai-grow-lab-main-lqx8vuret-client-limited-co.vercel.app`). Both `aigrowlabs.media` and `www.aigrowlabs.media` now serve an exact byte match of the checked-in HTML, including acceptance checks and rate-limit cooldown handling. Only HTML/images/icons were uploaded; configuration, backend source, and lead data return 404 on Vercel.

A real post-deployment submission was made through `https://aigrowlabs.media/`:

- Marker: `Discovery connected-access test 2026-10-04T10:27:38.187Z`.
- Endpoint: `https://ai-grow-lab.onrender.com/api/leads`.
- HTTP **503**, `code: email_not_configured`.
- Request ID: `47eed974-3121-49a0-8a85-0d57a2843aa0`.
- Render logs for that exact ID show `lead_request_received`, followed by `delivery_failure reason=missing_RESEND_API_KEY`.
- The frontend correctly displayed the failure message and retained the form inputs.
- **Resend was not called and no notification was sent by this test.** This is now a proven configuration failure, not an inference from an HTTP success response.
- Evidence: `/tmp/aigrow-connected-production-smoke.json` (temporary local artifact).

The runtime access log shows the direct TCP peer as `127.0.0.1`, while trusted-proxy configuration is empty. Actual ingress header topology still requires verification before choosing narrow trusted networks. The service's free/ephemeral storage is not a verified durable limiter store across redeployments; do not claim persistence beyond an unchanged database file or purchase/upgrade infrastructure without approval.

Required user input is now the **actual Resend key**, a **verified sender**, and the **second confirmed recipient**, entered directly into Render's Environment settings—not chat or GitHub. Account access alone does not create these values. After saving/restarting, verify configuration and repeat a single production submission, then check provider delivery events and recipient inboxes.

The browser smoke harness was also corrected to expose `Retry-After` on cross-origin mocked responses (matching the real backend) and construct HTTP-date retry values when sending the response. The old harness passed on localhost but incorrectly hid the header when run against Vercel. After correction, **222 browser assertions passed against `https://aigrowlabs.media`** across four viewport sizes (API responses intercepted; no mail from this suite), and **58 Python tests passed**. Syntax and diff checks also passed. The separate, unmocked production submission above still correctly failed on missing configuration.

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

## Historical access and deployment blocker (resolved)

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

1. Open Resend Domains, select/add `aigrowlabs.media`, and copy the exact required DNS records into Hostinger's DNS zone. Preserve the existing root-domain Hostinger MX records; Resend's sending MX/SPF normally use a separate subdomain. Never invent the verification values.
2. Wait until Resend marks the sender domain **Verified**. The actual key, sender, and two-recipient list are already configured on Render. Preserve these values; do not paste placeholders or keys into source/chat. The sending-only key does not need broader permissions merely to send from a verified, permitted domain.
3. Verify Render's immediate/intermediate proxy networks and set `TRUSTED_PROXY_CIDRS` narrowly; address persistent single-instance storage or a shared limiter before scaling, with approval for any paid infrastructure. Only restart Render if environment values change; DNS verification alone does not require changing the sender/key. Current backend and public frontend code are already deployed; do not publish `.env*`, `data/`, backend source, or integration secrets as static assets.
4. Repeat one labeled real form submission on `aigrowlabs.media`; require 201 with `email_status:"accepted"`. Match its request ID to Render's `email_accepted` log and the Resend email ID.
5. Verify the actual recipient list and delivery events in Resend, then confirm receipt with both mailbox owners, including spam folders. Controlled provider failures should be tested in staging rather than by breaking working production credentials.

Until those steps are complete, do not describe this as a fully repaired or verified production email pipeline.
