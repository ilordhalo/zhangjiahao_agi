from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import secrets
import time
from urllib.parse import urlsplit

from symphonz.service.runtime_store import RuntimeStore


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
PASSWORD_HASH_BYTES = 32
SESSION_SECRET_BYTES = 32
TOKEN_BYTES = 32
SESSION_COOKIE_NAME = "symphonz_session"
SCRYPT_ALGORITHM = "scrypt-v1"
PBKDF2_ALGORITHM = "pbkdf2-sha256-v1"
SUPPORTED_PASSWORD_ALGORITHMS = frozenset({SCRYPT_ALGORITHM, PBKDF2_ALGORITHM})


class AuthenticationError(ValueError):
    """Raised when dashboard credentials are invalid."""


class LoginLockedError(AuthenticationError):
    """Raised when a username and client address are temporarily locked."""


@dataclass(frozen=True)
class PasswordRecord:
    algorithm: str
    salt: str
    password_hash: str


@dataclass(frozen=True)
class DashboardAuth:
    password_record: PasswordRecord
    session_secret: str


@dataclass(frozen=True)
class Session:
    username: str
    expires_at: float
    created_at: float


@dataclass(frozen=True)
class LoginResult:
    token: str
    expires_at: float
    set_cookie: str


def hash_password(password: str) -> PasswordRecord:
    if not isinstance(password, str) or not password:
        raise ValueError("Dashboard password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    algorithm = SCRYPT_ALGORITHM if callable(getattr(hashlib, "scrypt", None)) else PBKDF2_ALGORITHM
    password_hash = _derive_password(password.encode("utf-8"), salt, algorithm)
    return PasswordRecord(
        algorithm=algorithm,
        salt=base64.b64encode(salt).decode("ascii"),
        password_hash=base64.b64encode(password_hash).decode("ascii"),
    )


def verify_password(password: str, record: PasswordRecord) -> bool:
    if not isinstance(password, str):
        return False
    try:
        salt, expected_hash = validate_password_record(record)
        calculated_hash = _derive_password(password.encode("utf-8"), salt, record.algorithm)
    except (RuntimeError, ValueError):
        return False
    return hmac.compare_digest(calculated_hash, expected_hash)


def validate_password_record(record: PasswordRecord) -> tuple[bytes, bytes]:
    if record.algorithm not in SUPPORTED_PASSWORD_ALGORITHMS:
        raise ValueError(f"unsupported password algorithm {record.algorithm!r}")
    salt = _decode_base64(record.salt, "salt", SALT_BYTES)
    password_hash = _decode_base64(record.password_hash, "password_hash", PASSWORD_HASH_BYTES)
    if record.algorithm == SCRYPT_ALGORITHM and not callable(getattr(hashlib, "scrypt", None)):
        raise RuntimeError("scrypt password record is unavailable on this Python runtime")
    return salt, password_hash


def session_generation(session_secret: str) -> str:
    secret = _decode_base64(session_secret, "session_secret", SESSION_SECRET_BYTES)
    return hashlib.sha256(b"symphonz-session-generation-v1\0" + secret).hexdigest()


def _decode_base64(value: str, field: str, expected_bytes: int) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ValueError(f"{field} must be valid Base64") from error
    if len(decoded) != expected_bytes:
        raise ValueError(f"{field} must decode to exactly {expected_bytes} bytes")
    return decoded


def _derive_password(password: bytes, salt: bytes, algorithm: str) -> bytes:
    if algorithm == SCRYPT_ALGORITHM:
        native_scrypt = getattr(hashlib, "scrypt", None)
        if not callable(native_scrypt):
            raise RuntimeError("scrypt password record is unavailable on this Python runtime")
        return native_scrypt(
            password,
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=PASSWORD_HASH_BYTES,
        )
    if algorithm == PBKDF2_ALGORITHM:
        return hashlib.pbkdf2_hmac(
            "sha256", password, salt, PBKDF2_ITERATIONS, dklen=PASSWORD_HASH_BYTES
        )
    raise ValueError(f"unsupported password algorithm {algorithm!r}")


def safe_next_path(value: str | None) -> str | None:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    return value


class AuthService:
    """Persistent single-user authentication for the LAN dashboard."""

    def __init__(
        self,
        store: RuntimeStore,
        username: str,
        password_record: PasswordRecord,
        session_secret: str,
        session_days: int,
        secure_cookie: bool = False,
        clock=time.time,
    ):
        if not username.strip():
            raise ValueError("Dashboard username must not be empty")
        if session_days <= 0:
            raise ValueError("Dashboard session duration must be positive")
        self.store = store
        self.username = username
        self.password_record = password_record
        self.session_days = session_days
        self.secure_cookie = secure_cookie
        self.clock = clock
        self._username_key = self._normalize_username(username)
        self._session_generation = session_generation(session_secret)

    def login(self, username: str, password: str, client_key: str) -> LoginResult:
        now = float(self.clock())
        rate_limit_key = self._rate_limit_key(username, client_key)
        reservation = self.store.reserve_login_attempt(rate_limit_key, now=now)
        if not reservation["reserved"]:
            raise LoginLockedError("Too many failed login attempts; try again later")

        username_matches = hmac.compare_digest(
            self._normalize_username(username).encode("utf-8"), self._username_key.encode("utf-8")
        )
        password_matches = verify_password(password, self.password_record)
        if not username_matches or not password_matches:
            raise AuthenticationError("Invalid username or password")

        self.store.clear_login_attempt(rate_limit_key)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = now + self._session_seconds
        self.store.save_session(
            self.token_hash(token),
            expires_at=expires_at,
            created_at=now,
            metadata={
                "username": self.username,
                "username_key": self._username_key,
                "auth_generation": self._session_generation,
            },
        )
        return LoginResult(token=token, expires_at=expires_at, set_cookie=self._set_cookie(token))

    def authenticate(self, token: str) -> Session | None:
        if not token:
            return None
        stored = self.store.get_session(self.token_hash(token), now=float(self.clock()))
        if stored is None:
            return None
        metadata = stored["metadata"]
        username_key = metadata.get("username_key")
        auth_generation = metadata.get("auth_generation")
        if not isinstance(username_key, str) or not isinstance(auth_generation, str):
            return None
        if not hmac.compare_digest(username_key.encode("utf-8"), self._username_key.encode("utf-8")):
            return None
        if not hmac.compare_digest(auth_generation.encode("utf-8"), self._session_generation.encode("ascii")):
            return None
        return Session(
            username=self.username,
            expires_at=float(stored["expires_at"]),
            created_at=float(stored["created_at"]),
        )

    def authenticate_cookie(self, header: str | None) -> Session | None:
        if not header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            return None
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return self.authenticate(morsel.value) if morsel is not None else None

    def logout(self, token: str) -> None:
        if token:
            self.store.delete_session(self.token_hash(token))

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().casefold()

    def _rate_limit_key(self, username: str, client_key: str) -> str:
        return f"{self._normalize_username(username)}:{client_key.strip()}"

    @property
    def _session_seconds(self) -> int:
        return int(self.session_days * 24 * 60 * 60)

    def _set_cookie(self, token: str) -> str:
        cookie = SimpleCookie()
        cookie[SESSION_COOKIE_NAME] = token
        morsel = cookie[SESSION_COOKIE_NAME]
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        morsel["path"] = "/"
        morsel["max-age"] = str(self._session_seconds)
        if self.secure_cookie:
            morsel["secure"] = True
        return morsel.OutputString()
