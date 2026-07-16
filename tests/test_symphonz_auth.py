from concurrent.futures import ThreadPoolExecutor
import base64
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import ANY, patch

from symphonz.service.auth import (
    AuthenticationError,
    AuthService,
    LoginLockedError,
    hash_password,
    safe_next_path,
    verify_password,
)
from symphonz.service.runtime_store import RuntimeStore


class PasswordTests(unittest.TestCase):
    def test_scrypt_password_records_verify_without_storing_the_password(self):
        password = "correct-horse-battery-staple"

        record = hash_password(password)

        self.assertEqual(record.algorithm, "scrypt")
        self.assertEqual(len(base64.b64decode(record.salt)), 16)
        self.assertNotIn(password, repr(record))
        self.assertTrue(verify_password(password, record))
        self.assertFalse(verify_password("incorrect", record))

    def test_password_hashing_uses_the_required_hashlib_scrypt_parameters(self):
        with patch("symphonz.service.auth.hashlib.scrypt", return_value=b"x" * 64, create=True) as scrypt:
            hash_password("correct")

        scrypt.assert_called_once_with(b"correct", salt=ANY, n=2**14, r=8, p=1)

    def test_password_verification_uses_constant_time_comparison(self):
        record = hash_password("correct")

        with patch("symphonz.service.auth.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as compare:
            self.assertTrue(verify_password("correct", record))

        compare.assert_called_once()


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "runtime.sqlite3"
        self.now = 1_000.0
        self.record = hash_password("correct")

    def make_service(self, *, secure_cookie=False):
        return AuthService(
            RuntimeStore(self.database),
            "Admin",
            self.record,
            session_days=30,
            secure_cookie=secure_cookie,
            clock=lambda: self.now,
        )

    def test_login_session_survives_auth_service_restart(self):
        first = self.make_service()

        token = first.login("admin", "correct", "192.168.1.8").token
        second = self.make_service()

        session = second.authenticate(token)
        self.assertIsNotNone(session)
        self.assertEqual(session.username, "Admin")

    def test_expired_session_is_rejected_and_removed(self):
        service = self.make_service()
        token = service.login("admin", "correct", "192.168.1.8").token
        self.now += 30 * 24 * 60 * 60

        self.assertIsNone(service.authenticate(token))
        self.assertIsNone(RuntimeStore(self.database).get_session(service.token_hash(token), now=self.now))

    def test_logout_deletes_the_persistent_session(self):
        service = self.make_service()
        token = service.login("admin", "correct", "192.168.1.8").token

        service.logout(token)

        self.assertIsNone(service.authenticate(token))

    def test_session_database_contains_only_the_token_digest(self):
        service = self.make_service()
        token = service.login("admin", "correct", "192.168.1.8").token

        with sqlite3.connect(self.database) as connection:
            token_hash = connection.execute("SELECT token_hash FROM dashboard_sessions").fetchone()[0]

        self.assertEqual(token_hash, service.token_hash(token))
        self.assertNotEqual(token_hash, token)

    def test_cookie_authentication_and_flags(self):
        service = self.make_service(secure_cookie=True)
        result = service.login("admin", "correct", "192.168.1.8")

        self.assertIn("symphonz_session=", result.set_cookie)
        self.assertIn("HttpOnly", result.set_cookie)
        self.assertIn("SameSite=Lax", result.set_cookie)
        self.assertIn("Path=/", result.set_cookie)
        self.assertIn("Max-Age=2592000", result.set_cookie)
        self.assertIn("Secure", result.set_cookie)
        self.assertEqual(service.authenticate_cookie(result.set_cookie).username, "Admin")
        self.assertIsNone(service.authenticate_cookie("other=value"))

    def test_next_path_rejects_open_redirects(self):
        self.assertEqual(safe_next_path("/issues/SYM-1/report"), "/issues/SYM-1/report")
        self.assertIsNone(safe_next_path("//evil.example"))
        self.assertIsNone(safe_next_path("https://evil.example"))
        self.assertIsNone(safe_next_path("dashboard"))

    def test_five_concurrent_failures_lock_normalized_username_and_client_key(self):
        service = self.make_service()

        def attempt():
            with self.assertRaises(AuthenticationError):
                service.login(" ADMIN ", "incorrect", "192.168.1.8")

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda _unused: attempt(), range(5)))

        with self.assertRaises(LoginLockedError):
            service.login("admin", "correct", "192.168.1.8")
        attempt_record = RuntimeStore(self.database).get_login_attempt("admin:192.168.1.8")
        self.assertEqual(attempt_record["failures"], 5)
        self.assertGreater(attempt_record["locked_until"], self.now)

    def test_successful_login_clears_prior_failure_record(self):
        service = self.make_service()
        with self.assertRaises(AuthenticationError):
            service.login("admin", "incorrect", "192.168.1.8")

        service.login("admin", "correct", "192.168.1.8")

        self.assertIsNone(RuntimeStore(self.database).get_login_attempt("admin:192.168.1.8"))


if __name__ == "__main__":
    unittest.main()
