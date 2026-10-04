"""Transactional, restart-safe limits for this single-instance SQLite backend.

An IP gets five requests/minute AND five pending/accepted sends/15 minutes.
Only definitively unsent attempts release a send reservation. Rejections never
extend a window. All processes must use the same database; independent instances
need a shared transactional store rather than separate SQLite files.
"""

from contextlib import closing
from ipaddress import ip_address, ip_network
from math import ceil
import sqlite3
import time

POLICIES = {"request": (5, 60), "submission": (5, 15 * 60)}


def initialize(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS lead_rate_limits (
            scope TEXT NOT NULL,
            identity TEXT NOT NULL,
            token TEXT NOT NULL,
            expires_at REAL NOT NULL,
            PRIMARY KEY (scope, token)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS lead_rate_expiry ON lead_rate_limits (expires_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS lead_rate_identity ON lead_rate_limits (scope, identity, expires_at)")


def acquire(database, scope, identity, token):
    """Atomically reserve a slot; return 0 or the actual retry delay in seconds."""
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        # Read the clock after acquiring the transaction, not before waiting for it.
        now = time.time()
        connection.execute("DELETE FROM lead_rate_limits WHERE expires_at <= ?", (now,))
        wait = 0
        # Report the longest applicable wait, avoiding repeated 60s -> 15m rejections.
        scopes = POLICIES if scope == "request" else (scope,)
        for checked_scope in scopes:
            rows = connection.execute(
                "SELECT expires_at FROM lead_rate_limits WHERE scope = ? AND identity = ? ORDER BY expires_at",
                (checked_scope, identity),
            ).fetchall()
            capacity, _ = POLICIES[checked_scope]
            if len(rows) >= capacity:
                wait = max(wait, ceil(rows[len(rows) - capacity][0] - now))
        if wait:
            return max(1, wait)
        _, window = POLICIES[scope]
        connection.execute(
            "INSERT INTO lead_rate_limits (scope, identity, token, expires_at) VALUES (?, ?, ?, ?)",
            (scope, identity, token, now + window),
        )
        return 0


def release_submission(database, token):
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute("DELETE FROM lead_rate_limits WHERE scope = 'submission' AND token = ?", (token,))


def trusted_proxies(value):
    """Trust only operator-configured proxy networks; never trust every address."""
    networks = tuple(ip_network(item.strip()) for item in value.split(",") if item.strip())
    if any(network.prefixlen == 0 for network in networks):
        raise ValueError("all_addresses_proxy_trust_is_forbidden")
    return networks


def normalize_ip(value):
    address = ip_address(value.strip())
    if "%" in str(address):
        raise ValueError("scoped_ip_is_not_a_client_identity")
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address


def client_ip(peer, forwarded_for, networks):
    """Walk XFF right-to-left, stopping at the first non-trusted address.

    Headers from untrusted peers are ignored, even if syntactically valid. The
    first/leftmost value is never blindly trusted. Malformed trusted chains fail
    closed, instead of selecting a spoofed IP or a shared proxy bucket.
    """
    current = normalize_ip(peer)
    trusted = lambda address: any(address in network for network in networks)
    if not trusted(current):
        return str(current)
    if not forwarded_for or len(forwarded_for) > 4096:
        raise ValueError("missing_or_invalid_proxy_chain")
    chain = forwarded_for.split(",")
    if len(chain) > 32:
        raise ValueError("proxy_chain_too_long")
    for item in reversed(chain):
        current = normalize_ip(item)
        if not trusted(current):
            return str(current)
    raise ValueError("proxy_chain_has_no_untrusted_client")


def identity(address):
    """Normalize mapped IPv4; group IPv6 /64 so rotating interface IDs cannot evade limits."""
    parsed = normalize_ip(address)
    return str(ip_network(f"{parsed}/64", strict=False)) if parsed.version == 6 else str(parsed)
