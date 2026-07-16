from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import secrets
import subprocess
import time
from urllib.parse import urlsplit

from symphonz.service.runtime_store import RuntimeStore


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
TOKEN_BYTES = 32
SESSION_COOKIE_NAME = "symphonz_session"


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
    password_hash = _scrypt(password.encode("utf-8"), salt)
    return PasswordRecord(
        algorithm="scrypt",
        salt=base64.b64encode(salt).decode("ascii"),
        password_hash=base64.b64encode(password_hash).decode("ascii"),
    )


def verify_password(password: str, record: PasswordRecord) -> bool:
    if not isinstance(password, str) or record.algorithm != "scrypt":
        return False
    try:
        salt = base64.b64decode(record.salt.encode("ascii"), validate=True)
        expected_hash = base64.b64decode(record.password_hash.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return False
    calculated_hash = _scrypt(password.encode("utf-8"), salt)
    return hmac.compare_digest(calculated_hash, expected_hash)


def _scrypt(password: bytes, salt: bytes) -> bytes:
    native_scrypt = getattr(hashlib, "scrypt", None)
    if native_scrypt is not None:
        return native_scrypt(password, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    payload = json.dumps(
        {"password": base64.b64encode(password).decode("ascii"), "salt": base64.b64encode(salt).decode("ascii")}
    ).encode("utf-8")
    script = (
        "const crypto=require('crypto'),chunks=[];"
        "process.stdin.on('data',chunk=>chunks.push(chunk));"
        "process.stdin.on('end',()=>{const value=JSON.parse(Buffer.concat(chunks).toString());"
        "const hash=crypto.scryptSync(Buffer.from(value.password,'base64'),Buffer.from(value.salt,'base64'),64,"
        "{N:16384,r:8,p:1,maxmem:33554432});process.stdout.write(hash.toString('base64'));});"
    )
    try:
        result = subprocess.run(
            ["node", "-e", script], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Python must provide hashlib.scrypt for dashboard authentication") from error
    return base64.b64decode(result.stdout, validate=True)


def safe_next_path(value: str | None) -> str | None:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or any(character in value for character in "\r\n"):
        return None
    return value


class AuthService:
    """Persistent single-user authentication for the LAN dashboard."""

    def __init__(
        self,
        store: RuntimeStore,
        username: str,
        password_record: PasswordRecord,
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

    def login(self, username: str, password: str, client_key: str) -> LoginResult:
        now = float(self.clock())
        rate_limit_key = self._rate_limit_key(username, client_key)
        attempt = self.store.get_login_attempt(rate_limit_key)
        if attempt and attempt["locked_until"] is not None and float(attempt["locked_until"]) > now:
            raise LoginLockedError("Too many failed login attempts; try again later")

        username_matches = hmac.compare_digest(
            self._normalize_username(username).encode("utf-8"), self._username_key.encode("utf-8")
        )
        password_matches = verify_password(password, self.password_record)
        if not username_matches or not password_matches:
            self.store.record_failed_login_attempt(rate_limit_key, now=now)
            raise AuthenticationError("Invalid username or password")

        self.store.clear_login_attempt(rate_limit_key)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = now + self._session_seconds
        self.store.save_session(
            self.token_hash(token),
            expires_at=expires_at,
            created_at=now,
            metadata={"username": self.username},
        )
        return LoginResult(token=token, expires_at=expires_at, set_cookie=self._set_cookie(token))

    def authenticate(self, token: str) -> Session | None:
        if not token:
            return None
        stored = self.store.get_session(self.token_hash(token), now=float(self.clock()))
        if stored is None:
            return None
        username = stored["metadata"].get("username")
        if not isinstance(username, str) or not username:
            return None
        return Session(username=username, expires_at=float(stored["expires_at"]), created_at=float(stored["created_at"]))

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
