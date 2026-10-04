"""Run with python3 -m unittest discover -s backend -p 'test_*.py'."""

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

# Import without loading the developer's private .env or its credentials.
with patch.object(Path, "exists", return_value=False):
    import app


LEAD = {
    "name": "Test Founder",
    "company": "Example Brand",
    "email": "founder@example.com",
    "goal": "Launch a product",
    "message": "Launch in November, reaching new customers.",
}


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "LEAD_NOTIFICATION_EMAIL": "connected@example.com",
            "LEAD_NOTIFICATION_EMAILS": "",
            "RESEND_API_KEY": "server-side-test-key",
            "EMAIL_FROM": "AI Grow Lab <leads@aigrowlabs.media>",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_preserves_existing_recipient_and_adds_business(self):
        self.assertEqual(app.notification_recipients(), ["connected@example.com", "business@aigrowlabs.media"])

    def test_additional_recipients_are_trimmed_and_deduplicated(self):
        os.environ["LEAD_NOTIFICATION_EMAILS"] = " SECOND@example.com,CONNECTED@example.com, business@aigrowlabs.media, second@example.com, "
        self.assertEqual(app.notification_recipients(), ["connected@example.com", "business@aigrowlabs.media", "second@example.com"])

    def test_default_connected_recipient_is_preserved(self):
        del os.environ["LEAD_NOTIFICATION_EMAIL"]
        self.assertEqual(app.notification_recipients(), ["anuragkeshav03@gmail.com", "business@aigrowlabs.media"])

    def test_blank_connected_recipient_keeps_the_default_inbox(self):
        for value in ("", " \t "):
            with self.subTest(value=value):
                os.environ["LEAD_NOTIFICATION_EMAIL"] = value
                self.assertEqual(app.notification_recipients(), ["anuragkeshav03@gmail.com", "business@aigrowlabs.media"])

    def test_connected_recipient_is_trimmed_and_deduplicated_too(self):
        os.environ["LEAD_NOTIFICATION_EMAIL"] = " BUSINESS@AIGROWLABS.MEDIA "
        os.environ["LEAD_NOTIFICATION_EMAILS"] = " business@aigrowlabs.media, "
        self.assertEqual(app.notification_recipients(), ["business@aigrowlabs.media"])

    def test_one_provider_request_contains_all_recipients_and_fields(self):
        os.environ["LEAD_NOTIFICATION_EMAILS"] = "second@example.com, SECOND@example.com"
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(app, "urlopen", return_value=response) as provider:
            self.assertEqual(app.send_resend_email(LEAD, "unique-request"), "sent")
        provider.assert_called_once()
        request = provider.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["to"], ["connected@example.com", "business@aigrowlabs.media", "second@example.com"])
        self.assertEqual(payload["from"], os.environ["EMAIL_FROM"])
        self.assertEqual(payload["reply_to"], LEAD["email"])
        self.assertEqual(payload["subject"], f"New lead — {LEAD['company']}")
        for label, field in (("Name", "name"), ("Brand / Company", "company"),
                             ("Work Email", "email"), ("Campaign objective", "goal"),
                             ("Context", "message")):
            self.assertIn(f"{label}: {LEAD[field]}", payload["text"])
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(provider.call_args.kwargs["timeout"], 10)
        self.assertEqual(request.get_header("Authorization"), "Bearer server-side-test-key")
        self.assertNotIn("server-side-test-key", request.data.decode("utf-8"))
        self.assertEqual(request.get_header("Idempotency-key"), "discovery-call/unique-request")

    def test_invalid_configuration_does_not_send_partial_notifications(self):
        for variable in ("LEAD_NOTIFICATION_EMAIL", "LEAD_NOTIFICATION_EMAILS"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "[second confirmed email address]"}):
                with patch.object(app, "urlopen") as provider:
                    self.assertEqual(app.send_resend_email(LEAD, "invalid-recipient"), "failed")
                provider.assert_not_called()

    def test_missing_key_or_sender_means_no_network_request(self):
        for variable in ("RESEND_API_KEY", "EMAIL_FROM"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: ""}):
                with patch.object(app, "urlopen") as provider:
                    self.assertEqual(app.send_resend_email(LEAD, "missing-config"), "not_configured")
                provider.assert_not_called()

    def test_provider_errors_return_failed_without_retrying(self):
        errors = (
            HTTPError("https://api.resend.com/emails", 403, "Forbidden", {}, None),
            URLError("test connection error"),
            TimeoutError("test timeout"),
        )
        for error in errors:
            if isinstance(error, HTTPError):
                self.addCleanup(error.close)
            with self.subTest(error=type(error).__name__), patch.object(app, "urlopen", side_effect=error) as provider:
                self.assertEqual(app.send_resend_email(LEAD, "provider-failure"), "failed")
                provider.assert_called_once()

    def test_empty_context_still_has_a_field_in_the_email(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(app, "urlopen", return_value=response) as provider:
            self.assertEqual(app.send_resend_email({**LEAD, "message": ""}, "empty-context"), "sent")
        self.assertIn("Context: —", json.loads(provider.call_args.args[0].data)["text"])


class LeadApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, payload=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        status, content = response.status, response.read()
        connection.close()
        return status, content

    def setUp(self):
        with app._rate_lock:
            app._rate_limit.clear()

    def test_one_valid_submission_dispatches_one_background_job(self):
        with patch.object(app._executor, "submit") as submit:
            status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(content)["ok"])
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[1], LEAD)

    def test_browser_cannot_override_server_email_configuration(self):
        payload = {
            **LEAD,
            "LEAD_NOTIFICATION_EMAIL": "injected@example.com",
            "LEAD_NOTIFICATION_EMAILS": "injected@example.com",
            "EMAIL_FROM": "injected@example.com",
            "RESEND_API_KEY": "browser-key",
            "to": ["injected@example.com"],
        }
        with patch.object(app._executor, "submit") as submit:
            status, content = self.request("POST", "/api/leads", payload)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(content), {"ok": True, "message": "Thanks — we’ll reply within 24 hours."})
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[1], LEAD)

    def test_invalid_form_and_honeypot_do_not_dispatch_delivery(self):
        with patch.object(app._executor, "submit") as submit:
            self.assertEqual(self.request("POST", "/api/leads", {**LEAD, "email": "invalid"})[0], 400)
            self.assertEqual(self.request("POST", "/api/leads", {**LEAD, "website": "bot"})[0], 201)
        submit.assert_not_called()

    def test_private_configuration_and_lead_data_are_not_public(self):
        for method in ("GET", "HEAD"):
            for path in ("/.env", "/%2eenv", "/.env.example", "/data/leads.db", "/backend/app.py", "/backend/", "/integrations/google-apps-script.gs"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.request(method, path)[0], 404)

    def test_website_is_served_and_head_has_no_body(self):
        status, content = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Request a Discovery Call", content)
        self.assertEqual(self.request("HEAD", "/"), (200, b""))

    def test_background_delivery_stores_one_complete_submission_for_each_email_status(self):
        for email_status in ("sent", "failed", "not_configured"):
            with self.subTest(email_status=email_status), tempfile.TemporaryDirectory() as directory:
                with patch.object(app, "DATABASE", Path(directory) / "leads.db"), \
                        patch.object(app, "send_resend_email", return_value=email_status) as email, \
                        patch.object(app, "send_to_google_sheets", return_value="not_configured"):
                    app.ensure_database()
                    app.process_webhooks_background(LEAD, "127.0.0.1", "2026-10-04T00:00:00Z", "storage-test")
                    email.assert_called_once_with(LEAD, "storage-test")
                    with closing(sqlite3.connect(app.DATABASE)) as connection:
                        rows = connection.execute("SELECT name, company, email, goal, message, email_status FROM leads").fetchall()
                    self.assertEqual(rows, [(*LEAD.values(), email_status)])


if __name__ == "__main__":
    unittest.main()
