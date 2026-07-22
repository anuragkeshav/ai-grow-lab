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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
DATABASE = ROOT / "data" / "leads.db"
MAX_BODY_BYTES = 16_000
RATE_LIMIT_WINDOW_SECONDS = 15 * 60
RATE_LIMIT_MAX_REQUESTS = 5
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
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

_rate_limit: dict[str, list[float]] = {}
_rate_lock = Lock()
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
        with sqlite3.connect(DATABASE) as connection:
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
                    sheets_status TEXT NOT NULL
                )
                """
            )
        logger.info(f"Database initialized at {DATABASE}")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database: {e}")


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
    if not EMAIL_PATTERN.fullmatch(lead["email"]):
        raise ValueError("Please enter a valid work email.")
    if lead["goal"] not in ALLOWED_GOALS:
        raise ValueError("Please choose a campaign goal from the list.")
    if len(lead["message"]) > 2_000:
        raise ValueError("Please keep the context under 2,000 characters.")
    return lead


def within_rate_limit(source_ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        # Cleanup old entries for all IPs to prevent memory leaks
        keys_to_delete = []
        for ip, timestamps in _rate_limit.items():
            active_for_ip = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SECONDS]
            if active_for_ip:
                _rate_limit[ip] = active_for_ip
            else:
                keys_to_delete.append(ip)
        for key in keys_to_delete:
            del _rate_limit[key]

        # Check current IP
        active = _rate_limit.get(source_ip, [])
        if len(active) >= RATE_LIMIT_MAX_REQUESTS:
            return False
        active.append(now)
        _rate_limit[source_ip] = active
        return True


def send_resend_email(lead: dict[str, str], request_id: str) -> str:
    api_key = setting("RESEND_API_KEY")
    recipient = setting("LEAD_NOTIFICATION_EMAIL", "anuragkeshav03@gmail.com")
    sender = setting("EMAIL_FROM")
    if not api_key or not sender:
        logger.warning(f"[{request_id}] Resend API key or sender email not configured. Skipping email.")
        return "not_configured"

    text = "\n".join(
        [
            "New AI Grow Lab discovery-call request",
            "",
            f"Name: {lead['name']}",
            f"Company: {lead['company']}",
            f"Email: {lead['email']}",
            f"Goal: {lead['goal']}",
            f"Context: {lead['message'] or '—'}",
        ]
    )
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(
            {
                "from": sender,
                "to": [recipient],
                "reply_to": lead["email"],
                "subject": f"New lead — {lead['company']}",
                "text": text,
            }
        ).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "AIGrowLab/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if 200 <= response.status < 300:
                logger.info(f"[{request_id}] Successfully sent email via Resend.")
                return "sent"
            else:
                logger.error(f"[{request_id}] Resend API returned status {response.status}")
    except (HTTPError, URLError, TimeoutError) as e:
        logger.error(f"[{request_id}] Failed to send Resend email: {e}")
        return "failed"
    return "failed"


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
    except (HTTPError, URLError, TimeoutError) as e:
        logger.error(f"[{request_id}] Failed to sync lead to Google Sheets: {e}")
        return "failed"
    return "failed"


def process_webhooks_background(lead: dict[str, str], source_ip: str, created_at: str, request_id: str) -> None:
    """Run external API calls in a background thread so the user doesn't wait."""
    try:
        logger.info(f"[{request_id}] Starting background webhook processing for lead: {lead['email']}")
        email_status = send_resend_email(lead, request_id)
        sheets_status = send_to_google_sheets(lead, created_at, request_id)
        
        # Save to DB inside the background thread to avoid blocking the main thread
        with sqlite3.connect(DATABASE, timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO leads (created_at, name, company, email, goal, message, source_ip, email_status, sheets_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    lead["name"],
                    lead["company"],
                    lead["email"],
                    lead["goal"],
                    lead["message"],
                    source_ip,
                    email_status,
                    sheets_status,
                ),
            )
        logger.info(f"[{request_id}] Successfully saved lead to database.")
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error in background webhook processing: {e}")


class AppHandler(SimpleHTTPRequestHandler):
    """Serve the website and accept form submissions on the same origin."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        # Enterprise Security Headers
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Override to use standard logging instead of sys.stderr
        logger.info(f"{self.client_address[0]} - {format % args}")

    def respond_json(self, status: HTTPStatus, data: dict[str, object]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        request_id = str(uuid.uuid4())[:8]
        if self.path != "/api/leads":
            logger.warning(f"[{request_id}] 404 Not Found: POST {self.path}")
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
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

        source_ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        
        if not within_rate_limit(source_ip):
            logger.warning(f"[{request_id}] 429 Too Many Requests: Rate limit exceeded for IP {source_ip}")
            self.respond_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Please wait a few minutes before sending another request."})
            return
            
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            lead = validate_lead(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            logger.warning(f"[{request_id}] 400 Bad Request: Validation failed - {str(error)}")
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if lead.get("bot"):
            logger.info(f"[{request_id}] 201 Created: Honeypot triggered by IP {source_ip}")
            self.respond_json(HTTPStatus.CREATED, {"ok": True, "message": "Thanks — we’ll reply within 24 hours."})
            return

        created_at = datetime.now(timezone.utc).isoformat()
        
        # Dispatch background task for fast response
        logger.info(f"[{request_id}] 201 Created: Valid lead received. Dispatching background workers.")
        _executor.submit(process_webhooks_background, lead, source_ip, created_at, request_id)
        
        self.respond_json(HTTPStatus.CREATED, {"ok": True, "message": "Thanks — we’ll reply within 24 hours."})


if __name__ == "__main__":
    ensure_database()
    host = setting("HOST", "0.0.0.0")
    port = int(setting("PORT", "8000"))
    logger.info(f"AI Grow Lab Enterprise Server starting at http://{host}:{port}")
    try:
        ThreadingHTTPServer((host, port), AppHandler).serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server gracefully...")
        _executor.shutdown(wait=True)
        logger.info("Server stopped.")
