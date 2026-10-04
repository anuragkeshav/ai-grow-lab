"""AI Grow Lab: dependency-free website server and lead capture API.

Run locally with: python3 backend/app.py
Then open: http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid

import rate_limit
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parseaddr
from http import HTTPStatus
from http.client import HTTPException
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
DATABASE = ROOT / "data" / "leads.db"
MAX_BODY_BYTES = 16_000
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)
EMAIL_FAILURE_MESSAGE = "We couldn't send your request. Please try again or email us directly."
EMAIL_SUCCESS_MESSAGE = "Thanks — we'll reply within 24 hours."
ALLOWED_GOALS = {
    "Launch a product",
    "Build brand awareness",
    "Create UGC",
    "Develop creator partnerships",
    "Something else",
}

# Configure Enterprise Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ai_grow_lab")

_executor = ThreadPoolExecutor(max_workers=10)


def load_env(path: Path) -> None:
    if not path.exists():
        logger.warning(f"Environment file not found at {path}. Relying on system environment variables.")
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    logger.info("Environment variables loaded successfully.")


load_env(ENV_FILE)


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def ensure_database() -> None:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(DATABASE)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    company TEXT NOT NULL,
                    email TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source_ip TEXT NOT NULL,
                    email_status TEXT NOT NULL,
                    sheets_status TEXT NOT NULL,
                    request_id TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(leads)")}
            if "request_id" not in columns:
                connection.execute("ALTER TABLE leads ADD COLUMN request_id TEXT")
            rate_limit.initialize(connection)
        logger.info("Lead database initialized.")
    except sqlite3.Error:
        logger.error("Lead database initialization failed.")
        raise


def valid_email(address: str) -> bool:
    local_part = address.split("@", 1)[0]
    return bool(
        len(address) <= 254 and len(local_part) <= 64
        and not local_part.startswith(".") and not local_part.endswith(".")
        and ".." not in local_part and EMAIL_PATTERN.fullmatch(address)
    )


def validate_lead(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Please submit the form again.")

    if str(payload.get("website", "")).strip():
        return {"bot": "true"}

    lead = {
        "name": str(payload.get("name", "")).strip(),
        "company": str(payload.get("company", "")).strip(),
        "email": str(payload.get("email", "")).strip().lower(),
        "goal": str(payload.get("goal", "")).strip(),
        "message": str(payload.get("message", "")).strip(),
    }
    if not 2 <= len(lead["name"]) <= 100:
        raise ValueError("Please enter your name.")
    if not 2 <= len(lead["company"]) <= 120:
        raise ValueError("Please enter your company name.")
    if not valid_email(lead["email"]):
        raise ValueError("Please enter a valid work email.")
    if lead["goal"] not in ALLOWED_GOALS:
        raise ValueError("Please choose a campaign goal from the list.")
    if len(lead["message"]) > 2_000:
        raise ValueError("Please keep the context under 2,000 characters.")
    return lead


def notification_recipients() -> list[str]:
    """Merge confirmed server-side inboxes, retaining the legacy recipient."""
    configured = [
        setting("LEAD_NOTIFICATION_EMAIL"),
        "business@aigrowlabs.media",
        *setting("LEAD_NOTIFICATION_EMAILS").split(","),
    ]
    recipients = []
    seen = set()
    for value in configured:
        address = value.strip().lower()
        if not address or address in seen:
            continue
        if not valid_email(address):
            raise ValueError("invalid_notification_recipient")
        seen.add(address)
        recipients.append(address)
    if len(recipients) < 2:
        raise ValueError("second_confirmed_recipient_required")
    if len(recipients) > 50:
        raise ValueError("too_many_notification_recipients")
    return recipients


def email_configuration() -> tuple[str, str, list[str]]:
    """Validate local configuration; Resend enforces sender-domain verification."""
    api_key = setting("RESEND_API_KEY")
    sender = setting("EMAIL_FROM")
    if not api_key:
        raise ValueError("missing_RESEND_API_KEY")
    if not sender:
        raise ValueError("missing_EMAIL_FROM")
    _, address = parseaddr(sender)
    if ("\r" in sender or "\n" in sender or not valid_email(address)
            or (sender != address and not sender.endswith(f"<{address}>"))):
        raise ValueError("invalid_EMAIL_FROM")
    return api_key, sender, notification_recipients()


def resend_failure_details(error: HTTPError) -> tuple[str, str]:
    """Reduce provider errors to fixed diagnostic codes, never log raw bodies."""
    try:
        payload = json.loads(error.read(16_000))
    except (ValueError, OSError, HTTPException, AttributeError, TypeError):
        return "unknown", "unknown"
    if not isinstance(payload, dict):
        return "unknown", "unknown"
    allowed_names = {
        "validation_error", "invalid_api_key", "missing_api_key", "restricted_api_key",
        "rate_limit_exceeded", "daily_quota_exceeded", "monthly_quota_exceeded",
        "application_error", "internal_server_error", "invalid_access",
    }
    name = payload.get("name")
    name = name if isinstance(name, str) and name in allowed_names else "unknown"
    message = payload.get("message", "")
    message = message.lower() if isinstance(message, str) else ""
    if "domain" in message and ("not verified" in message or "verify your domain" in message):
        hint = "sender_domain_unverified"
    elif "own email address" in message or "only send testing emails" in message:
        hint = "sandbox_recipient_restriction"
    elif name in {"invalid_api_key", "missing_api_key", "restricted_api_key"}:
        hint = "api_key_or_permission"
    elif "quota" in name or name == "rate_limit_exceeded":
        hint = "provider_quota"
    else:
        hint = "unknown"
    return name, hint


def send_resend_email(lead: dict[str, str], request_id: str) -> str:
    try:
        api_key, sender, recipients = email_configuration()
    except ValueError as error:
        # These errors are fixed codes, never configuration values.
        logger.error("[%s] delivery_failure reason=%s", request_id, error)
        return "not_configured"

    text = "\n".join(
        [
            "New AI Grow Lab discovery-call request",
            "",
            f"Name: {lead['name']}",
            f"Brand / Company: {lead['company']}",
            f"Work Email: {lead['email']}",
            f"Campaign objective: {lead['goal']}",
            f"Context: {lead['message'] or '—'}",
        ]
    )
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(
            {
                "from": sender,
                "to": recipients,
                "reply_to": lead["email"],
                "subject": f"New lead — {lead['company']}",
                "text": text,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AIGrowLab/1.0",
            "Idempotency-Key": f"discovery-call/{request_id}",
        },
        method="POST",
    )
    logger.info("[%s] email_attempt provider=resend recipient_count=%d", request_id, len(recipients))
    try:
        with urlopen(request, timeout=10) as response:
            logger.info("[%s] resend_response status=%d", request_id, response.status)
            if not 200 <= response.status < 300:
                logger.error("[%s] delivery_failure reason=provider_status", request_id)
                return "uncertain" if response.status >= 500 or response.status == 408 else "failed"
            # A 2xx without Resend's email ID is not proof of acceptance.
            result = json.loads(response.read(16_000))
            email_id = str(uuid.UUID(result["id"]))
            logger.info("[%s] email_accepted resend_email_id=%s", request_id, email_id)
            return "sent"
    except HTTPError as error:
        # Never log raw provider bodies/exception strings: they may echo secrets.
        provider_error, hint = resend_failure_details(error)
        logger.error("[%s] resend_response status=%d delivery_failure reason=provider_rejected provider_error=%s hint=%s",
                     request_id, error.code, provider_error, hint)
        error.close()
        return "uncertain" if error.code >= 500 or error.code == 408 else "failed"
    except (URLError, OSError, HTTPException) as error:
        logger.error("[%s] delivery_failure reason=transport_error type=%s", request_id, type(error).__name__)
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.error("[%s] delivery_failure reason=invalid_provider_response", request_id)
    # An ambiguous provider outcome might still have sent mail. Keep its quota.
    return "uncertain"


def send_to_google_sheets(lead: dict[str, str], created_at: str, request_id: str) -> str:
    endpoint = setting("GOOGLE_SHEETS_WEBHOOK_URL")
    if not endpoint:
        logger.warning(f"[{request_id}] Google Sheets webhook URL not configured. Skipping Google Sheets sync.")
        return "not_configured"

    payload = {**lead, "created_at": created_at, "token": setting("GOOGLE_SHEETS_SHARED_SECRET")}
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if 200 <= response.status < 300:
                logger.info(f"[{request_id}] Successfully synced lead to Google Sheets.")
                return "sent"
            else:
                logger.error(f"[{request_id}] Google Sheets API returned status {response.status}")
    except (HTTPError, URLError, OSError, HTTPException) as error:
        logger.error("[%s] Sheets sync failed type=%s", request_id, type(error).__name__)
        if isinstance(error, HTTPError):
            error.close()
        return "failed"
    return "failed"


def store_lead(lead: dict[str, str], source_ip: str, created_at: str, request_id: str) -> int:
    """Persist before sending, so even a failed attempt can be investigated."""
    with closing(sqlite3.connect(DATABASE, timeout=10)) as connection, connection:
        cursor = connection.execute(
            """
            INSERT INTO leads (created_at, name, company, email, goal, message, source_ip, email_status, sheets_status, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at, lead["name"], lead["company"], lead["email"], lead["goal"],
             lead["message"], source_ip, "pending", "not_configured", request_id),
        )
        return cursor.lastrowid


def update_email_status(lead_id: int, status: str) -> None:
    with closing(sqlite3.connect(DATABASE, timeout=10)) as connection, connection:
        connection.execute("UPDATE leads SET email_status = ? WHERE id = ?", (status, lead_id))


def sync_sheets_background(lead: dict[str, str], created_at: str, request_id: str, lead_id: int) -> None:
    """Only the optional Sheets mirror runs after the email response."""
    try:
        status = send_to_google_sheets(lead, created_at, request_id)
        with closing(sqlite3.connect(DATABASE, timeout=10)) as connection, connection:
            connection.execute("UPDATE leads SET sheets_status = ? WHERE id = ?", (status, lead_id))
    except Exception as error:
        logger.error("[%s] Sheets sync failed type=%s", request_id, type(error).__name__)


class AppHandler(SimpleHTTPRequestHandler):
    """Serve the website and accept form submissions on the same origin."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def allow_public_file(self) -> bool:
        # The document root also contains private configuration and lead data.
        # Serve only public website assets, including through encoded paths.
        path = Path(self.translate_path(self.path)).resolve()
        public_files = {ROOT / "index.html", ROOT / "favicon-32.png", ROOT / "apple-touch-icon.png"}
        if path == ROOT:
            self.path = "/index.html"
        elif path not in public_files:
            self.send_error(HTTPStatus.NOT_FOUND)
            return False
        return True

    def do_GET(self) -> None:
        if not self.allow_public_file():
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self.allow_public_file():
            return
        super().do_HEAD()

    def end_headers(self) -> None:
        # Enterprise Security Headers
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Retry-After")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Override to use standard logging instead of sys.stderr
        logger.info(f"{self.client_address[0]} - {format % args}")

    def respond_json(self, status: HTTPStatus, data: dict[str, object], retry_after: int = 0) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if retry_after:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(body)

    def reject_rate_limit(self, request_id: str, retry_after: int) -> None:
        logger.warning("[%s] rate_limit_rejected retry_after=%d email_attempted=false", request_id, retry_after)
        self.respond_json(HTTPStatus.TOO_MANY_REQUESTS, {
            "code": "rate_limited", "request_id": request_id,
            "retry_after": retry_after, "email_status": "not_attempted",
            "error": f"Too many requests. Please wait {retry_after} {'second' if retry_after == 1 else 'seconds'} before trying again. This request was not sent.",
        }, retry_after=retry_after)

    def do_POST(self) -> None:
        request_id = str(uuid.uuid4())
        if self.path != "/api/leads":
            logger.warning(f"[{request_id}] 404 Not Found: POST {self.path}")
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
            
        logger.info("[%s] lead_request_received", request_id)
        try:
            proxies = rate_limit.trusted_proxies(setting("TRUSTED_PROXY_CIDRS"))
            source_ip = rate_limit.client_ip(
                self.client_address[0], ",".join(self.headers.get_all("X-Forwarded-For", [])), proxies,
            )
            identity = rate_limit.identity(source_ip)
            retry_after = rate_limit.acquire(DATABASE, "request", identity, request_id)
        except (ValueError, sqlite3.Error) as error:
            logger.error("[%s] request_guard_unavailable type=%s", request_id, type(error).__name__)
            self.respond_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "code": "service_unavailable", "error": EMAIL_FAILURE_MESSAGE, "request_id": request_id,
            })
            return
        if retry_after:
            self.reject_rate_limit(request_id, retry_after)
            return

        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            logger.warning(f"[{request_id}] 415 Unsupported Media Type")
            self.respond_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Please submit the form again."})
            return
            
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
            
        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            logger.warning(f"[{request_id}] 400 Bad Request: Invalid content length ({content_length} bytes)")
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "That request is too large. Please try again."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            lead = validate_lead(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            logger.warning(f"[{request_id}] 400 Bad Request: Validation failed - {str(error)}")
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if lead.get("bot"):
            logger.info("[%s] lead_request_rejected", request_id)
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": EMAIL_FAILURE_MESSAGE, "request_id": request_id})
            return

        try:
            retry_after = rate_limit.acquire(DATABASE, "submission", identity, request_id)
        except sqlite3.Error:
            logger.error("[%s] submission_guard_unavailable", request_id)
            self.respond_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "code": "service_unavailable", "error": EMAIL_FAILURE_MESSAGE, "request_id": request_id,
            })
            return
        if retry_after:
            self.reject_rate_limit(request_id, retry_after)
            return

        created_at = datetime.now(timezone.utc).isoformat()
        email_status = "uncertain"
        email_attempted = False
        processing_failed = False
        try:
            lead_id = store_lead(lead, source_ip, created_at, request_id)
            email_attempted = True
            email_status = send_resend_email(lead, request_id)
            update_email_status(lead_id, email_status)
        except Exception as error:
            processing_failed = True
            logger.error("[%s] delivery_failure reason=processing_error type=%s", request_id, type(error).__name__)
        finally:
            if not email_attempted or email_status in ("failed", "not_configured"):
                try:
                    rate_limit.release_submission(DATABASE, request_id)
                except sqlite3.Error:
                    # Fail closed: never claim a slot was released if storage failed.
                    logger.error("[%s] submission_quota_release_failed", request_id)

        if processing_failed:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "code": "processing_error", "error": EMAIL_FAILURE_MESSAGE, "request_id": request_id,
            })
            return
        if email_status != "sent":
            status = HTTPStatus.SERVICE_UNAVAILABLE if email_status == "not_configured" else HTTPStatus.BAD_GATEWAY
            code = "email_not_configured" if email_status == "not_configured" else "email_delivery_failed"
            self.respond_json(status, {"code": code, "error": EMAIL_FAILURE_MESSAGE, "request_id": request_id})
            return

        if setting("GOOGLE_SHEETS_WEBHOOK_URL"):
            try:
                _executor.submit(sync_sheets_background, lead, created_at, request_id, lead_id)
            except RuntimeError:
                logger.error("[%s] Sheets background worker unavailable", request_id)
        logger.info("[%s] lead_processed email_status=accepted", request_id)
        self.respond_json(HTTPStatus.CREATED, {
            "ok": True, "email_status": "accepted", "message": EMAIL_SUCCESS_MESSAGE, "request_id": request_id,
        })


if __name__ == "__main__":
    ensure_database()
    try:
        _, sender, recipients = email_configuration()
        logger.info("Email configuration loaded recipient_count=%d sender_domain=%s", len(recipients), parseaddr(sender)[1].split("@")[1])
    except ValueError as error:
        logger.error("Email configuration invalid reason=%s; lead submissions will fail closed", error)
    proxies = rate_limit.trusted_proxies(setting("TRUSTED_PROXY_CIDRS"))
    logger.info("Request guard ready request_limit=5/min submission_limit=5/15min trusted_proxy_networks=%d", len(proxies))
    if setting("RENDER") and not proxies:
        logger.warning("Render proxy trust is not configured; forwarded headers are ignored. Configure verified ingress CIDRs before production traffic.")
    host = setting("HOST", "0.0.0.0")
    port = int(setting("PORT", "8000"))
    logger.info(f"AI Grow Lab Enterprise Server starting at http://{host}:{port}")
    try:
        ThreadingHTTPServer((host, port), AppHandler).serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server gracefully...")
        _executor.shutdown(wait=True)
        logger.info("Server stopped.")
