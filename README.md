# AI Grow Lab website + lead backend

The contact form now posts to a same-origin Python backend. Every accepted lead is stored in a local SQLite database, and can additionally trigger an email alert and be mirrored to Google Sheets.

## What is already wired

- Form confirmation: **“Thanks — we’ll reply within 24 hours.”**
- Lead-notification recipient: `anuragkeshav03@gmail.com`
- Public business email: `buisness@aigrowlabs.media`
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

This project uses [Resend](https://resend.com/) for reliable email delivery.

1. Create a Resend account and verify `aigrowlabs.media`.
2. Create an API key with email-sending access.
3. In `.env`, set:

   ```env
   EMAIL_FROM=AI Grow Lab <leads@aigrowlabs.media>
   RESEND_API_KEY=your_key_here
   ```

Keep the key in `.env`; never put it in `index.html` or share it in chat.

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

## Before publishing

- Confirm `buisness@aigrowlabs.media` is the intended spelling.
- Configure a real hosting provider that can run Python; static-only hosting will not run the lead API.
- Set `HOST=0.0.0.0` and the platform-provided `PORT` on the production host.
- Add a privacy policy before accepting public leads.
