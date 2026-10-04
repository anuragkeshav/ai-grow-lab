"""No network, no live leads, no production clock/quota changes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import rate_limit


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "leads.db"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            rate_limit.initialize(connection)
        clock = patch.object(rate_limit.time, "time", return_value=1000)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def acquire(self, scope, token, identity="192.0.2.1"):
        return rate_limit.acquire(self.database, scope, identity, token)

    def test_five_requests_then_short_cooldown_without_sliding_extension(self):
        for i in range(5):
            self.assertEqual(self.acquire("request", str(i)), 0)
        self.assertEqual(self.acquire("request", "blocked"), 60)
        self.clock.return_value = 1059.2
        self.assertEqual(self.acquire("request", "still-blocked"), 1)
        self.clock.return_value = 1060
        self.assertEqual(self.acquire("request", "allowed"), 0)

    def test_accepted_reservations_keep_original_five_per_fifteen_minutes(self):
        for i in range(5):
            self.assertEqual(self.acquire("request", str(i)), 0)
            self.assertEqual(self.acquire("submission", str(i)), 0)
        self.assertEqual(self.acquire("request", "blocked"), 900)
        self.clock.return_value = 1060
        self.assertEqual(self.acquire("request", "still-blocked"), 840)
        self.clock.return_value = 1900
        self.assertEqual(self.acquire("request", "allowed"), 0)
        self.assertEqual(self.acquire("submission", "allowed"), 0)

    def test_releasing_failed_send_does_not_refund_request_guard(self):
        for i in range(5):
            self.acquire("request", str(i))
            self.acquire("submission", str(i))
            rate_limit.release_submission(self.database, str(i))
        self.assertEqual(self.acquire("request", "blocked"), 60)
        self.clock.return_value = 1060
        self.assertEqual(self.acquire("request", "retry"), 0)
        self.assertEqual(self.acquire("submission", "retry"), 0)

    def test_concurrent_request_reservations_cannot_exceed_capacity(self):
        with ThreadPoolExecutor(max_workers=12) as executor:
            waits = list(executor.map(lambda i: self.acquire("request", str(i)), range(30)))
        self.assertEqual(waits.count(0), 5)
        self.assertEqual(waits.count(60), 25)

    def test_concurrent_pending_submissions_cannot_exceed_capacity(self):
        with ThreadPoolExecutor(max_workers=12) as executor:
            waits = list(executor.map(lambda i: self.acquire("submission", str(i)), range(30)))
        self.assertEqual(waits.count(0), 5)
        self.assertEqual(waits.count(900), 25)

    def test_state_survives_reinitialization_and_new_database_connections(self):
        for i in range(5):
            self.acquire("submission", str(i))
        with closing(sqlite3.connect(self.database)) as connection, connection:
            rate_limit.initialize(connection)
        self.assertEqual(self.acquire("request", "after-restart"), 900)

    def test_expired_buckets_are_pruned(self):
        self.acquire("request", "old")
        self.clock.return_value = 1060
        self.acquire("request", "new", "192.0.2.2")
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT token FROM lead_rate_limits").fetchall(), [("new",)])

    def test_different_clients_have_independent_buckets(self):
        for i in range(5):
            self.acquire("submission", str(i), "192.0.2.1")
        self.assertEqual(self.acquire("request", "blocked", "192.0.2.1"), 900)
        self.assertEqual(self.acquire("request", "other", "192.0.2.2"), 0)


class ProxyIdentityTests(unittest.TestCase):
    def test_default_ignores_all_forwarded_headers(self):
        for header in ("198.51.100.1", "garbage", "", "192.0.2.1, 10.0.0.1"):
            self.assertEqual(rate_limit.client_ip("203.0.113.1", header, ()), "203.0.113.1")

    def test_untrusted_direct_peer_cannot_impersonate_a_proxy(self):
        networks = rate_limit.trusted_proxies("10.0.0.0/24")
        self.assertEqual(rate_limit.client_ip("203.0.113.1", "198.51.100.1", networks), "203.0.113.1")

    def test_walks_trusted_chain_from_the_right_ignoring_spoofed_prefix(self):
        networks = rate_limit.trusted_proxies("10.0.0.0/24, 192.0.2.0/24")
        header = "spoofed-prefix, 198.51.100.1, 192.0.2.4"
        self.assertEqual(rate_limit.client_ip("10.0.0.2", header, networks), "198.51.100.1")

    def test_invalid_trusted_chain_fails_closed(self):
        networks = rate_limit.trusted_proxies("10.0.0.0/24")
        for header in ("", "unknown", "198.51.100.1, garbage", "10.0.0.1", "192.0.2.1," * 40):
            with self.subTest(header=header), self.assertRaises(ValueError):
                rate_limit.client_ip("10.0.0.2", header, networks)

    def test_trust_all_and_invalid_configuration_are_rejected(self):
        for setting in ("0.0.0.0/0", "::/0", "not-a-cidr"):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                rate_limit.trusted_proxies(setting)

    def test_mapped_ipv4_cannot_create_an_alternate_bucket(self):
        self.assertEqual(rate_limit.identity("::ffff:192.0.2.1"), rate_limit.identity("192.0.2.1"))

    def test_ipv6_rotation_within_a_subnet_cannot_create_alternate_buckets(self):
        self.assertEqual(rate_limit.identity("2001:db8:1234:5678::1"), rate_limit.identity("2001:db8:1234:5678::ffff"))
        self.assertNotEqual(rate_limit.identity("2001:db8:1234:5678::1"), rate_limit.identity("2001:db8:1234:5679::1"))

    def test_scoped_ipv6_is_not_accepted_from_a_header(self):
        with self.assertRaises(ValueError):
            rate_limit.normalize_ip("fe80::1%eth0")


if __name__ == "__main__":
    unittest.main()
