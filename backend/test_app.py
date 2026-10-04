"""Run with python3 -m unittest discover -s backend -p 'test_*.py'. No live email."""

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

# Import without loading private .env credentials.
with patch.object(Path, "exists", return_value=False):
    import app


LEAD = {
    "name": "Test Founder",
    "company": "Example Brand",
    "email": "founder@example.com",
    "goal": "Launch a product",
    "message": "Launch in November, reaching new customers.",
}
EMAIL_ID = "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"
ENVIRONMENT = {
    "LEAD_NOTIFICATION_EMAIL": "connected@example.com",
    "LEAD_NOTIFICATION_EMAILS": "",
    "RESEND_API_KEY": "server-side-test-key",
    "EMAIL_FROM": "AI Grow Lab <leads@aigrowlabs.media>",
}
FAILURE = "We couldn't send your request. Please try again or email us directly."


def provider_response(body=None, status=200):
    response = MagicMock()
    response.__enter__.return_value.status = status
    response.__enter__.return_value.read.return_value = (
        json.dumps({"id": EMAIL_ID}).encode() if body is None else body
    )
    return response


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, ENVIRONMENT, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_preserves_existing_recipient_and_adds_business(self):
        self.assertEqual(app.notification_recipients(), ["connected@example.com", "business@aigrowlabs.media"])

    def test_plural_configuration_works_without_legacy_variable(self):
        del os.environ["LEAD_NOTIFICATION_EMAIL"]
        os.environ["LEAD_NOTIFICATION_EMAILS"] = " business@aigrowlabs.media, second@example.com "
        self.assertEqual(app.notification_recipients(), ["business@aigrowlabs.media", "second@example.com"])

    def test_additional_recipients_are_trimmed_and_deduplicated(self):
        os.environ["LEAD_NOTIFICATION_EMAILS"] = " SECOND@example.com,CONNECTED@example.com, business@aigrowlabs.media, second@example.com, "
        self.assertEqual(app.notification_recipients(), ["connected@example.com", "business@aigrowlabs.media", "second@example.com"])

    def test_missing_second_recipient_fails_instead_of_inventing_a_default(self):
        for value in ("", " \t ", "business@aigrowlabs.media"):
            with self.subTest(value=value), patch.dict(os.environ, {"LEAD_NOTIFICATION_EMAIL": value}):
                with self.assertRaisesRegex(ValueError, "second_confirmed_recipient_required"):
                    app.notification_recipients()

    def test_connected_recipient_is_trimmed_and_deduplicated_too(self):
        os.environ["LEAD_NOTIFICATION_EMAIL"] = " CONNECTED@EXAMPLE.COM "
        os.environ["LEAD_NOTIFICATION_EMAILS"] = " connected@example.com, "
        self.assertEqual(app.notification_recipients(), ["connected@example.com", "business@aigrowlabs.media"])

    def test_one_provider_request_contains_all_recipients_and_fields(self):
        os.environ["LEAD_NOTIFICATION_EMAILS"] = "second@example.com, SECOND@example.com"
        with patch.object(app, "urlopen", return_value=provider_response()) as provider:
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
        self.assertNotIn("server-side-test-key", request.data.decode())
        self.assertEqual(request.get_header("Idempotency-key"), "discovery-call/unique-request")

    def test_invalid_configuration_does_not_send_partial_notifications(self):
        invalid = ("[second confirmed email address]", "bad@example.com;other@example.com",
                   "Name <a@example.com>", "a@-example.com", "a..b@example.com", "a@b.com\r\nBcc:x@y.com")
        for variable in ("LEAD_NOTIFICATION_EMAIL", "LEAD_NOTIFICATION_EMAILS"):
            for value in invalid:
                with self.subTest(variable=variable, value=value), patch.dict(os.environ, {variable: value}):
                    with patch.object(app, "urlopen") as provider:
                        self.assertEqual(app.send_resend_email(LEAD, "invalid-recipient"), "not_configured")
                    provider.assert_not_called()

    def test_provider_recipient_limit_is_validated(self):
        os.environ["LEAD_NOTIFICATION_EMAILS"] = ",".join(f"person{i}@example.com" for i in range(50))
        with self.assertRaisesRegex(ValueError, "too_many_notification_recipients"):
            app.notification_recipients()

    def test_missing_key_or_sender_means_no_network_request(self):
        for variable in ("RESEND_API_KEY", "EMAIL_FROM"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: ""}):
                with patch.object(app, "urlopen") as provider:
                    self.assertEqual(app.send_resend_email(LEAD, "missing-config"), "not_configured")
                provider.assert_not_called()

    def test_sender_syntax_is_validated_before_sending(self):
        for sender in ("invalid", "Name <bad>", "a@example.com,b@example.com", "Name <a@example.com>\nBcc:x@y.com"):
            with self.subTest(sender=sender), patch.dict(os.environ, {"EMAIL_FROM": sender}):
                with patch.object(app, "urlopen") as provider:
                    self.assertEqual(app.send_resend_email(LEAD, "invalid-sender"), "not_configured")
                provider.assert_not_called()

    def test_bare_sender_address_is_valid(self):
        os.environ["EMAIL_FROM"] = "leads@aigrowlabs.media"
        with patch.object(app, "urlopen", return_value=provider_response()):
            self.assertEqual(app.send_resend_email(LEAD, "bare-sender"), "sent")

    def test_provider_errors_return_failed_without_retrying(self):
        errors = [HTTPError("https://api.resend.com/emails", code, "Forbidden", {}, None)
                  for code in (401, 403, 422, 429, 500)]
        errors.extend([URLError("test connection error"), TimeoutError("test timeout"), ConnectionResetError()])
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(app, "urlopen", side_effect=error) as provider:
                expected = "failed" if isinstance(error, HTTPError) and error.code < 500 else "uncertain"
                self.assertEqual(app.send_resend_email(LEAD, "provider-failure"), expected)
                provider.assert_called_once()

    def test_malformed_or_missing_provider_id_is_not_acceptance(self):
        for body in (b"{}", b"null", b"[]", b"not JSON", b'{"id":null}', b'{"id":"not-an-email-id"}'):
            with self.subTest(body=body), patch.object(app, "urlopen", return_value=provider_response(body)):
                self.assertEqual(app.send_resend_email(LEAD, "invalid-response"), "uncertain")

    def test_unexpected_provider_status_is_failure(self):
        with patch.object(app, "urlopen", return_value=provider_response(status=503)):
            self.assertEqual(app.send_resend_email(LEAD, "bad-status"), "uncertain")

    def test_safe_logs_include_acceptance_id_and_failure_status_without_secrets(self):
        with self.assertLogs(app.logger, level="INFO") as logs:
            with patch.object(app, "urlopen", return_value=provider_response()):
                app.send_resend_email(LEAD, "safe-log-test")
            error = HTTPError("https://api.resend.com/emails", 403, "server-side-test-key", {}, None)
            with patch.object(app, "urlopen", side_effect=error):
                app.send_resend_email(LEAD, "safe-log-test")
            with patch.object(app, "urlopen", side_effect=URLError("server-side-test-key")):
                app.send_resend_email(LEAD, "safe-log-test")
        text = "\n".join(logs.output)
        for expected in ("email_attempt", "status=200", "status=403", "delivery_failure", EMAIL_ID, "safe-log-test"):
            self.assertIn(expected, text)
        for secret in ("server-side-test-key", "Authorization", LEAD["email"], LEAD["message"], "connected@example.com"):
            self.assertNotIn(secret, text)

    def test_provider_domain_rejection_logs_only_a_safe_hint(self):
        body = json.dumps({"name": "validation_error", "message":
            "The aigrowlabs.media domain is not verified. server-side-test-key founder@example.com"}).encode()
        error = HTTPError("https://api.resend.com/emails", 403, "Forbidden", {}, BytesIO(body))
        with self.assertLogs(app.logger) as logs, patch.object(app, "urlopen", side_effect=error):
            self.assertEqual(app.send_resend_email(LEAD, "domain-rejection"), "failed")
        text = "\n".join(logs.output)
        self.assertIn("provider_error=validation_error", text)
        self.assertIn("hint=sender_domain_unverified", text)
        self.assertNotIn("server-side-test-key", text)
        self.assertNotIn("founder@example.com", text)
        self.assertNotIn("The aigrowlabs.media domain", text)

    def test_other_provider_diagnostics_use_fixed_codes(self):
        cases = (
            ("validation_error", "You can only send testing emails to your own email address", "sandbox_recipient_restriction"),
            ("restricted_api_key", "Restricted resource", "api_key_or_permission"),
            ("rate_limit_exceeded", "Too many requests", "provider_quota"),
        )
        for name, message, hint in cases:
            body = json.dumps({"name": name, "message": message}).encode()
            error = HTTPError("https://api.resend.com/emails", 403, "Forbidden", {}, BytesIO(body))
            with self.subTest(name=name), closing(error):
                self.assertEqual(app.resend_failure_details(error), (name, hint))

    def test_unknown_provider_errors_never_echo_untrusted_bodies_or_names(self):
        for body in (b'not JSON: server-side-test-key', b'null',
                     b'{"name":"server-side-test-key","message":"private data"}'):
            error = HTTPError("https://api.resend.com/emails", 403, "Forbidden", {}, BytesIO(body))
            with self.subTest(body=body), self.assertLogs(app.logger) as logs, patch.object(app, "urlopen", side_effect=error):
                self.assertEqual(app.send_resend_email(LEAD, "unknown-rejection"), "failed")
            self.assertNotIn("server-side-test-key", "\n".join(logs.output))
            self.assertNotIn("private data", "\n".join(logs.output))

    def test_empty_context_still_has_a_field_in_the_email(self):
        with patch.object(app, "urlopen", return_value=provider_response()) as provider:
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

    def request(self, method, path, payload=None, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        try:
            connection.request(method, path, body, {"Content-Type": "application/json", "Origin": "https://aigrowlabs.media", **(headers or {})})
            response = connection.getresponse()
            self.response_headers = dict(response.getheaders())
            return response.status, response.read()
        finally:
            connection.close()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (patch.dict(os.environ, ENVIRONMENT, clear=True),
                        patch.object(app, "DATABASE", Path(directory.name) / "leads.db")):
            patcher.start()
            self.addCleanup(patcher.stop)
        app.ensure_database()
        provider = patch.object(app, "urlopen", return_value=provider_response())
        self.provider = provider.start()
        self.addCleanup(provider.stop)

    def stored_leads(self):
        with closing(sqlite3.connect(app.DATABASE)) as connection:
            return connection.execute("SELECT name, company, email, goal, message, email_status, request_id FROM leads").fetchall()

    def test_resend_failure_must_not_report_success(self):
        self.provider.side_effect = HTTPError("https://api.resend.com/emails", 403, "Forbidden", {}, None)
        status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 502)
        result = json.loads(content)
        self.assertEqual(result["error"], FAILURE)
        self.assertNotIn("ok", result)
        self.assertEqual(self.stored_leads(), [(*LEAD.values(), "failed", result["request_id"])])

    def test_missing_configuration_must_not_report_success(self):
        del os.environ["RESEND_API_KEY"]
        status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(content)["error"], FAILURE)
        self.assertNotIn("message", json.loads(content))
        self.assertEqual(self.stored_leads()[0][-2], "not_configured")
        self.provider.assert_not_called()

    def test_one_valid_submission_is_stored_and_sent_before_success(self):
        with patch.object(app._executor, "submit") as submit, self.assertLogs(app.logger) as logs:
            status, content = self.request("POST", "/api/leads", LEAD)
        result = json.loads(content)
        self.assertEqual(status, 201)
        self.assertIs(result["ok"], True)
        self.assertEqual(result["email_status"], "accepted")
        self.assertEqual(result["message"], "Thanks — we'll reply within 24 hours.")
        self.assertEqual(self.stored_leads(), [(*LEAD.values(), "sent", result["request_id"])])
        self.provider.assert_called_once()
        submit.assert_not_called()
        self.assertIn("lead_request_received", "\n".join(logs.output))
        self.assertIn("lead_processed", "\n".join(logs.output))

    def test_success_waits_for_provider_acceptance(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        result = []

        def slow_provider(*args, **kwargs):
            entered.set()
            release.wait(3)
            return provider_response()

        def post():
            result.append(self.request("POST", "/api/leads", LEAD))
            completed.set()

        self.provider.side_effect = slow_provider
        thread = threading.Thread(target=post)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(completed.wait(0.1), "API replied before Resend finished")
            self.assertEqual(self.stored_leads()[0][-2], "pending")
        finally:
            release.set()
            thread.join(4)
        self.assertTrue(completed.is_set())
        self.assertEqual(result[0][0], 201)

    def test_browser_cannot_override_server_email_configuration(self):
        payload = {**LEAD, "LEAD_NOTIFICATION_EMAIL": "injected@example.com",
                   "LEAD_NOTIFICATION_EMAILS": "injected@example.com", "EMAIL_FROM": "injected@example.com",
                   "RESEND_API_KEY": "browser-key", "to": ["injected@example.com"]}
        status, content = self.request("POST", "/api/leads", payload)
        self.assertEqual(status, 201)
        request = self.provider.call_args.args[0]
        self.assertEqual(json.loads(request.data)["to"], ["connected@example.com", "business@aigrowlabs.media"])
        self.assertEqual(request.get_header("Authorization"), "Bearer server-side-test-key")
        for private in ("server-side-test-key", "connected@example.com", "EMAIL_FROM", "browser-key"):
            self.assertNotIn(private.encode(), content)

    def test_invalid_form_and_honeypot_do_not_send_or_claim_success(self):
        for payload in ({**LEAD, "email": "invalid"}, {**LEAD, "website": "bot"}):
            status, content = self.request("POST", "/api/leads", payload)
            self.assertEqual(status, 400)
            self.assertNotIn("ok", json.loads(content))
        self.provider.assert_not_called()
        self.assertEqual(self.stored_leads(), [])

    def test_database_failure_prevents_sending_and_returns_json_error(self):
        with patch.object(app, "store_lead", side_effect=sqlite3.OperationalError("private detail")):
            status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(content)["error"], FAILURE)
        self.provider.assert_not_called()
        self.assertNotIn(b"private detail", content)

    def test_unexpected_processing_failure_is_not_success(self):
        with patch.object(app, "send_resend_email", side_effect=RuntimeError("private detail")):
            status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(content)["error"], FAILURE)

    def test_sheets_remains_optional_and_only_runs_after_accepted_email(self):
        os.environ["GOOGLE_SHEETS_WEBHOOK_URL"] = "https://example.com/sheets"
        with patch.object(app._executor, "submit") as submit:
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)
            submit.assert_called_once()
            args = submit.call_args.args
            self.assertIs(args[0], app.sync_sheets_background)
            self.assertEqual(args[1], LEAD)
        self.provider.side_effect = TimeoutError()
        with patch.object(app._executor, "submit") as submit:
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 502)
            submit.assert_not_called()

    def test_sheets_worker_updates_the_existing_lead_without_resending(self):
        lead_id = app.store_lead(LEAD, "127.0.0.1", "2026-10-04T00:00:00Z", "sheets-test")
        app.update_email_status(lead_id, "sent")
        with patch.object(app, "send_to_google_sheets", return_value="failed"):
            app.sync_sheets_background(LEAD, "2026-10-04T00:00:00Z", "sheets-test", lead_id)
        self.provider.assert_not_called()
        with closing(sqlite3.connect(app.DATABASE)) as connection:
            self.assertEqual(connection.execute("SELECT email_status, sheets_status FROM leads").fetchall(), [("sent", "failed")])

    def test_preflight_allows_production_frontend(self):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request("OPTIONS", "/api/leads", headers={"Origin": "https://aigrowlabs.media", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
            response = connection.getresponse()
            self.assertEqual(response.status, 204)
            self.assertIn(response.getheader("Access-Control-Allow-Origin"), ("*", "https://aigrowlabs.media"))
            self.assertIn("POST", response.getheader("Access-Control-Allow-Methods"))
            self.assertIn("content-type", response.getheader("Access-Control-Allow-Headers").lower())
            response.read()
        finally:
            connection.close()

    def test_cors_preflights_and_page_loads_do_not_consume_submission_limits(self):
        for _ in range(6):
            self.assertEqual(self.request("OPTIONS", "/api/leads")[0], 204)
            self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)
        self.provider.assert_called_once()

    def test_invalid_requests_do_not_consume_the_long_submission_quota(self):
        with patch.object(app.rate_limit.time, "time", return_value=1000):
            for _ in range(5):
                self.assertEqual(self.request("POST", "/api/leads", {**LEAD, "email": "invalid"})[0], 400)
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 429)
        with patch.object(app.rate_limit.time, "time", return_value=1061):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)

    def test_provider_failure_retry_does_not_wait_fifteen_minutes(self):
        self.provider.side_effect = HTTPError("https://api.resend.com/emails", 403, "Forbidden", {}, None)
        with patch.object(app.rate_limit.time, "time", return_value=1000):
            for _ in range(5):
                self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 502)
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 429)
        self.provider.side_effect = None
        with patch.object(app.rate_limit.time, "time", return_value=1061):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)

    def test_rate_limit_provides_a_distinct_code_and_retry_after(self):
        for _ in range(5):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)
        status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 429)
        result = json.loads(content)
        self.assertEqual(result["code"], "rate_limited")
        self.assertEqual(result["email_status"], "not_attempted")
        self.assertIn("This request was not sent", result["error"])
        self.assertGreaterEqual(result["retry_after"], 899)
        self.assertEqual(self.response_headers["Retry-After"], str(result["retry_after"]))
        self.assertEqual(self.response_headers["Cache-Control"], "no-store")
        self.assertIn("Retry-After", self.response_headers["Access-Control-Expose-Headers"])
        self.assertEqual(self.provider.call_count, 5)
        self.assertEqual(len(self.stored_leads()), 5)

    def test_rate_limit_does_not_send_or_store_a_lead(self):
        with patch.object(app.rate_limit, "acquire", return_value=60):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 429)
        self.provider.assert_not_called()
        self.assertEqual(self.stored_leads(), [])

    def test_delivery_failure_code_is_not_a_local_rate_limit(self):
        self.provider.side_effect = HTTPError("https://api.resend.com/emails", 429, "Provider quota", {}, None)
        status, content = self.request("POST", "/api/leads", LEAD)
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(content)["code"], "email_delivery_failed")
        self.assertNotIn("Retry-After", self.response_headers)

    def test_missing_config_does_not_burn_the_send_quota(self):
        with patch.object(app.rate_limit.time, "time", return_value=1000):
            with patch.dict(os.environ, {"RESEND_API_KEY": ""}):
                for _ in range(5):
                    status, content = self.request("POST", "/api/leads", LEAD)
                    self.assertEqual(status, 503)
                    self.assertEqual(json.loads(content)["code"], "email_not_configured")
        with patch.object(app.rate_limit.time, "time", return_value=1061):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)
        self.provider.assert_called_once()

    def test_ambiguous_provider_timeout_keeps_send_quota(self):
        self.provider.side_effect = TimeoutError()
        with patch.object(app.rate_limit.time, "time", return_value=1000):
            for _ in range(5):
                self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 502)
        self.provider.side_effect = None
        with patch.object(app.rate_limit.time, "time", return_value=1061):
            status, content = self.request("POST", "/api/leads", LEAD)
            self.assertEqual(status, 429)
            self.assertEqual(json.loads(content)["retry_after"], 839)
        with patch.object(app.rate_limit.time, "time", return_value=1900):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 201)

    def test_malformed_input_is_still_short_term_rate_limited(self):
        with patch.object(app.rate_limit.time, "time", return_value=1000):
            for _ in range(5):
                self.assertEqual(self.request("POST", "/api/leads", {**LEAD, "website": "bot"})[0], 400)
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 429)
        self.provider.assert_not_called()
        self.assertEqual(self.stored_leads(), [])

    def test_untrusted_forwarded_headers_cannot_bypass_limits(self):
        for i in range(5):
            self.assertEqual(self.request("POST", "/api/leads", LEAD, {"X-Forwarded-For": f"192.0.2.{i + 1}"})[0], 201)
        self.assertEqual(self.request("POST", "/api/leads", LEAD, {"X-Forwarded-For": "198.51.100.9"})[0], 429)

    def test_trusted_proxy_keeps_distinct_clients_separate(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_CIDRS": "127.0.0.1/32"}):
            for _ in range(5):
                self.assertEqual(self.request("POST", "/api/leads", LEAD, {"X-Forwarded-For": "192.0.2.1"})[0], 201)
            self.assertEqual(self.request("POST", "/api/leads", LEAD, {"X-Forwarded-For": "192.0.2.1"})[0], 429)
            self.assertEqual(self.request("POST", "/api/leads", LEAD, {"X-Forwarded-For": "192.0.2.2"})[0], 201)

    def test_unusable_proxy_chain_or_rate_store_fails_closed(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_CIDRS": "127.0.0.1/32"}):
            status, content = self.request("POST", "/api/leads", LEAD)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(content)["code"], "service_unavailable")
        with patch.object(app.rate_limit, "acquire", side_effect=sqlite3.OperationalError()):
            self.assertEqual(self.request("POST", "/api/leads", LEAD)[0], 503)
        self.provider.assert_not_called()

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

    def test_existing_database_is_migrated_without_losing_leads(self):
        with closing(sqlite3.connect(app.DATABASE)) as connection, connection:
            connection.execute("ALTER TABLE leads DROP COLUMN request_id")
            connection.execute("INSERT INTO leads (created_at,name,company,email,goal,message,source_ip,email_status,sheets_status) VALUES ('old','old','old','old@example.com','Create UGC','','','sent','sent')")
        app.ensure_database()
        app.ensure_database()
        with closing(sqlite3.connect(app.DATABASE)) as connection:
            self.assertEqual(connection.execute("SELECT name, email_status, request_id FROM leads").fetchall(), [("old", "sent", None)])


if __name__ == "__main__":
    unittest.main()
