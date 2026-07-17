from concurrent.futures import ThreadPoolExecutor
import base64
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import ANY, patch

from symphonz.service.auth import (
    AuthenticationError,
    AuthService,
    LoginLockedError,
    PasswordRecord,
    hash_password,
    safe_next_path,
    verify_password,
)
from symphonz.service.runtime_store import RuntimeStore


class PasswordTests(unittest.TestCase):
    def test_password_records_are_versioned_and_use_32_byte_hashes(self):
        password = "correct-horse-battery-staple"

        record = hash_password(password)

        self.assertIn(record.algorithm, {"scrypt-v1", "pbkdf2-sha256-v1"})
        self.assertEqual(len(base64.b64decode(record.salt)), 16)
        self.assertEqual(len(base64.b64decode(record.password_hash)), 32)
        self.assertNotIn(password, repr(record))
        self.assertTrue(verify_password(password, record))
        self.assertFalse(verify_password("incorrect", record))

    def test_password_hashing_uses_the_required_hashlib_scrypt_parameters(self):
        with patch("symphonz.service.auth.hashlib.scrypt", return_value=b"x" * 32, create=True) as scrypt:
            record = hash_password("correct")

        self.assertEqual(record.algorithm, "scrypt-v1")
        scrypt.assert_called_once_with(b"correct", salt=ANY, n=2**14, r=8, p=1, dklen=32)

    def test_password_hashing_falls_back_to_versioned_standard_library_pbkdf2(self):
        with (
            patch("symphonz.service.auth.hashlib.scrypt", None, create=True),
            patch("symphonz.service.auth.hashlib.pbkdf2_hmac", return_value=b"x" * 32) as pbkdf2,
        ):
            record = hash_password("correct")

        self.assertEqual(record.algorithm, "pbkdf2-sha256-v1")
        pbkdf2.assert_called_once_with("sha256", b"correct", ANY, 600_000, dklen=32)

    def test_password_verification_dispatches_versioned_pbkdf2_records(self):
        record = PasswordRecord(
            algorithm="pbkdf2-sha256-v1",
            salt=base64.b64encode(b"s" * 16).decode("ascii"),
            password_hash=base64.b64encode(b"x" * 32).decode("ascii"),
        )

        with (
            patch("symphonz.service.auth.hashlib.pbkdf2_hmac", return_value=b"x" * 32) as pbkdf2,
            patch("symphonz.service.auth.hmac.compare_digest", return_value=True) as compare,
        ):
            self.assertTrue(verify_password("correct", record))

        pbkdf2.assert_called_once_with("sha256", b"correct", b"s" * 16, 600_000, dklen=32)
        compare.assert_called_once_with(b"x" * 32, b"x" * 32)

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
        self.session_secret = base64.b64encode(b"a" * 32).decode("ascii")

    def make_service(self, *, username="Admin", session_secret=None, secure_cookie=False):
        return AuthService(
            RuntimeStore(self.database),
            username,
            self.record,
            session_secret=self.session_secret if session_secret is None else session_secret,
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

    def test_session_generation_rejects_tokens_after_auth_secret_rotation(self):
        token = self.make_service().login("admin", "correct", "192.168.1.8").token
        rotated_secret = base64.b64encode(b"b" * 32).decode("ascii")

        self.assertIsNotNone(self.make_service().authenticate(token))
        self.assertIsNone(self.make_service(session_secret=rotated_secret).authenticate(token))

    def test_username_rotation_rejects_existing_sessions(self):
        token = self.make_service().login("admin", "correct", "192.168.1.8").token

        self.assertIsNone(self.make_service(username="operator").authenticate(token))

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

    def test_next_path_rejects_all_ascii_control_characters(self):
        for codepoint in (*range(0x20), 0x7F):
            with self.subTest(codepoint=codepoint):
                self.assertIsNone(safe_next_path(f"/issues/SYM-{chr(codepoint)}1"))

    def test_concurrent_requests_reserve_only_five_slots_before_password_kdf(self):
        service = self.make_service()
        release_kdf = threading.Event()
        five_in_kdf = threading.Event()
        entered_kdf = 0
        entered_lock = threading.Lock()

        def blocked_verification(_password, _record):
            nonlocal entered_kdf
            with entered_lock:
                entered_kdf += 1
                if entered_kdf == 5:
                    five_in_kdf.set()
            release_kdf.wait(timeout=2)
            return False

        def attempt():
            try:
                service.login("admin", "incorrect", "192.168.1.8")
            except AuthenticationError as error:
                return type(error)
            return None

        with patch("symphonz.service.auth.verify_password", side_effect=blocked_verification):
            with ThreadPoolExecutor(max_workers=10) as executor:
                first = [executor.submit(attempt) for _ in range(5)]
                self.assertTrue(five_in_kdf.wait(timeout=2))
                extra = [executor.submit(attempt) for _ in range(5)]
                try:
                    extra_results = [future.result(timeout=1) for future in extra]
                finally:
                    release_kdf.set()
                first_results = [future.result(timeout=2) for future in first]

        self.assertEqual(entered_kdf, 5)
        self.assertEqual(first_results, [AuthenticationError] * 5)
        self.assertEqual(extra_results, [LoginLockedError] * 5)

    def test_global_kdf_limit_applies_across_many_client_buckets(self):
        service = self.make_service()
        client_keys = []
        bucket_keys = set()
        for index in range(1000):
            client_key = f"192.168.2.{index}"
            bucket_key = service._rate_limit_key("admin", client_key)
            if bucket_key not in bucket_keys:
                bucket_keys.add(bucket_key)
                client_keys.append(client_key)
            if len(client_keys) == 6:
                break
        self.assertEqual(len(client_keys), 6)

        release_kdf = threading.Event()
        five_in_kdf = threading.Event()
        active_kdf = 0
        max_active_kdf = 0
        entered_lock = threading.Lock()

        def blocked_verification(_password, _record):
            nonlocal active_kdf, max_active_kdf
            with entered_lock:
                active_kdf += 1
                max_active_kdf = max(max_active_kdf, active_kdf)
                if active_kdf == 5:
                    five_in_kdf.set()
                over_limit = active_kdf > 5
            if not over_limit:
                release_kdf.wait(timeout=2)
            with entered_lock:
                active_kdf -= 1
            return False

        def attempt(client_key):
            try:
                service.login("admin", "incorrect", client_key)
            except AuthenticationError as error:
                return type(error)
            return None

        with patch("symphonz.service.auth.verify_password", side_effect=blocked_verification) as verify:
            with ThreadPoolExecutor(max_workers=6) as executor:
                first = [executor.submit(attempt, client_key) for client_key in client_keys[:5]]
                self.assertTrue(five_in_kdf.wait(timeout=2))
                extra = executor.submit(attempt, client_keys[5])
                try:
                    extra_result = extra.result(timeout=1)
                finally:
                    release_kdf.set()
                first_results = [future.result(timeout=2) for future in first]

        self.assertEqual(max_active_kdf, 5)
        self.assertEqual(verify.call_count, 5)
        self.assertEqual(first_results, [AuthenticationError] * 5)
        self.assertEqual(extra_result, LoginLockedError)

    def test_kdf_exception_releases_global_slot_and_preserves_client_failure(self):
        service = self.make_service()
        raised = threading.Event()

        def raising_verification(_password, _record):
            raised.set()
            raise RuntimeError("KDF failed")

        with patch("symphonz.service.auth.verify_password", side_effect=raising_verification):
            with self.assertRaises(RuntimeError):
                service.login("admin", "incorrect", "192.168.3.1")

        self.assertTrue(raised.is_set())
        attempt = RuntimeStore(self.database).get_login_attempt(
            service._rate_limit_key("admin", "192.168.3.1")
        )
        self.assertEqual(attempt["failures"], 1)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM login_attempt_reservations").fetchone()[0],
                0,
            )

        with patch("symphonz.service.auth.verify_password", return_value=False):
            with self.assertRaises(AuthenticationError):
                service.login("admin", "incorrect", "192.168.3.2")

    def test_random_usernames_share_a_bounded_kdf_budget(self):
        service = self.make_service()

        with patch("symphonz.service.auth.verify_password", return_value=False) as verify:
            for index in range(5):
                with self.assertRaises(AuthenticationError):
                    service.login(f"random-user-{index}", "incorrect", "192.168.1.8")
            with self.assertRaises(AuthenticationError) as locked:
                service.login("another-random-user", "incorrect", "192.168.1.8")

        self.assertEqual(verify.call_count, 5)
        self.assertIsInstance(locked.exception, LoginLockedError)
        with sqlite3.connect(self.database) as connection:
            keys = [row[0] for row in connection.execute("SELECT rate_limit_key FROM login_attempts")]
        self.assertLessEqual(len(keys), 1)
        self.assertFalse(any("random-user" in key for key in keys))

    def test_success_does_not_release_other_in_flight_reservations(self):
        service = self.make_service()
        release_failures = threading.Event()
        initial_entered = threading.Event()
        second_entered = threading.Event()
        four_locked = threading.Event()
        entered_lock = threading.Lock()
        initial_count = 0
        second_count = 0
        locked_count = 0

        def overlapping_verification(password, _record):
            nonlocal initial_count, second_count
            if password == "correct":
                with entered_lock:
                    initial_count += 1
                    if initial_count == 5:
                        initial_entered.set()
                initial_entered.wait(timeout=2)
                return True
            if password == "first-wave":
                with entered_lock:
                    initial_count += 1
                    if initial_count == 5:
                        initial_entered.set()
                release_failures.wait(timeout=3)
                return False
            with entered_lock:
                second_count += 1
                second_entered.set()
            release_failures.wait(timeout=3)
            return False

        def attempt(password):
            nonlocal locked_count
            try:
                return service.login("admin", password, "192.168.1.8")
            except AuthenticationError as error:
                if isinstance(error, LoginLockedError):
                    with entered_lock:
                        locked_count += 1
                        if locked_count == 4:
                            four_locked.set()
                return type(error)

        with patch("symphonz.service.auth.verify_password", side_effect=overlapping_verification):
            with ThreadPoolExecutor(max_workers=10) as executor:
                successful = executor.submit(attempt, "correct")
                first_failures = [executor.submit(attempt, "first-wave") for _ in range(4)]
                self.assertTrue(initial_entered.wait(timeout=2))
                self.assertTrue(successful.result(timeout=2).token)

                second_wave = [executor.submit(attempt, "second-wave") for _ in range(5)]
                self.assertTrue(second_entered.wait(timeout=2))
                try:
                    self.assertTrue(four_locked.wait(timeout=2))
                    self.assertEqual(second_count, 1)
                finally:
                    release_failures.set()

                first_results = [future.result(timeout=2) for future in first_failures]
                second_results = [future.result(timeout=2) for future in second_wave]

        self.assertEqual(first_results, [AuthenticationError] * 4)
        self.assertEqual(second_results.count(AuthenticationError), 1)
        self.assertEqual(second_results.count(LoginLockedError), 4)

    def test_five_concurrent_failures_lock_normalized_username_and_client_key(self):
        service = self.make_service()

        def attempt():
            with self.assertRaises(AuthenticationError):
                service.login(" ADMIN ", "incorrect", "192.168.1.8")

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda _unused: attempt(), range(5)))

        with self.assertRaises(LoginLockedError):
            service.login("admin", "correct", "192.168.1.8")
        attempt_record = RuntimeStore(self.database).get_login_attempt(
            service._rate_limit_key("admin", "192.168.1.8")
        )
        self.assertEqual(attempt_record["failures"], 5)
        self.assertGreater(attempt_record["locked_until"], self.now)

    def test_successful_login_clears_prior_failure_record(self):
        service = self.make_service()
        with self.assertRaises(AuthenticationError):
            service.login("admin", "incorrect", "192.168.1.8")

        service.login("admin", "correct", "192.168.1.8")

        self.assertIsNone(
            RuntimeStore(self.database).get_login_attempt(service._rate_limit_key("admin", "192.168.1.8"))
        )


if __name__ == "__main__":
    unittest.main()
