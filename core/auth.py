"""Built-in authentication for local and hosted Yuki deployments.

Uses only the Python standard library. Passwords are PBKDF2-HMAC-SHA256 hashes;
bearer session secrets are random and only their SHA-256 digests are persisted.
This module is a service layer, not an HTTP server or a substitute for TLS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3

_MIN_ITERATIONS = 100_000
_DEFAULT_ITERATIONS = 600_000
_MAX_PASSWORD_LENGTH = 1024
_SESSION_BYTES = 32
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{2,127}$")
_ROLE_PERMISSIONS = {
    "admin": frozenset({"workflow:read", "workflow:approve", "users:manage"}),
    "operator": frozenset({"workflow:read", "workflow:approve"}),
    "user": frozenset({"workflow:read"}),
}


def _utcnow():
    return datetime.now(timezone.utc)


def _timestamp(value):
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str
    role: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class IssuedSession:
    token: str
    user: AuthenticatedUser
    expires_at: str


class AuthenticationError(PermissionError):
    """Generic authentication failure; avoids account enumeration."""


class BuiltInAuthService:
    """SQLite accounts and opaque server-side sessions for local or hosted use."""

    def __init__(self, database_path, *, password_iterations=_DEFAULT_ITERATIONS,
                 session_ttl_seconds=12 * 60 * 60, max_failed_logins=5,
                 lockout_seconds=300):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if isinstance(password_iterations, bool) or not isinstance(password_iterations, int) or not _MIN_ITERATIONS <= password_iterations <= 2_000_000:
            raise ValueError("password_iterations must be between 100000 and 2000000.")
        if isinstance(session_ttl_seconds, bool) or not isinstance(session_ttl_seconds, int) or not 60 <= session_ttl_seconds <= 30 * 24 * 3600:
            raise ValueError("session_ttl_seconds must be between 60 seconds and 30 days.")
        if isinstance(max_failed_logins, bool) or not isinstance(max_failed_logins, int) or not 3 <= max_failed_logins <= 20:
            raise ValueError("max_failed_logins must be between 3 and 20.")
        if isinstance(lockout_seconds, bool) or not isinstance(lockout_seconds, int) or not 30 <= lockout_seconds <= 86400:
            raise ValueError("lockout_seconds must be between 30 seconds and 24 hours.")
        self.database_path = str(Path(database_path).expanduser())
        self.password_iterations = password_iterations
        self.session_ttl_seconds = session_ttl_seconds
        self.max_failed_logins = max_failed_logins
        self.lockout_seconds = lockout_seconds
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    password_iterations INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','operator','user')),
                    failed_logins INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    created_at TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at)")

    def _connect(self):
        return _Connection(sqlite3.connect(self.database_path, timeout=5.0))

    @staticmethod
    def _validate_username(username):
        if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
            raise ValueError("Username must be 3-128 characters using letters, digits, '.', '_', '@', '+', or '-'.")
        return username

    @staticmethod
    def _validate_password(password):
        if not isinstance(password, str) or not 12 <= len(password) <= _MAX_PASSWORD_LENGTH or not password.strip():
            raise ValueError("Password must contain between 12 and 1024 non-blank characters.")
        return password

    @staticmethod
    def _user(row):
        return AuthenticatedUser(row["user_id"], row["username"], row["role"], _ROLE_PERMISSIONS[row["role"]])

    def _password_digest(self, password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.password_iterations)

    def create_account(self, username, password):
        """Create a least-privileged account; public signup policy belongs to the app."""
        username = self._validate_username(username)
        password = self._validate_password(password)
        salt = secrets.token_bytes(16)
        digest = self._password_digest(password, salt)
        user_id, now = secrets.token_urlsafe(18), _timestamp(_utcnow())
        with self._connect() as db:
            db.execute(
                "INSERT INTO auth_users(user_id,username,password_salt,password_hash,password_iterations,role,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, username, salt, digest, self.password_iterations, "user", now),
            )
        return AuthenticatedUser(user_id, username, "user", _ROLE_PERMISSIONS["user"])

    def bootstrap_admin(self, username, password):
        """Create the initial admin exactly once, including under concurrent bootstrap attempts."""
        username = self._validate_username(username)
        password = self._validate_password(password)
        salt = secrets.token_bytes(16)
        digest = self._password_digest(password, salt)
        user_id, now = secrets.token_urlsafe(18), _timestamp(_utcnow())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone():
                raise PermissionError("Initial administrator already exists.")
            db.execute(
                "INSERT INTO auth_users(user_id,username,password_salt,password_hash,password_iterations,role,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, username, salt, digest, self.password_iterations, "admin", now),
            )
        return AuthenticatedUser(user_id, username, "admin", _ROLE_PERMISSIONS["admin"])

    def authenticate(self, username, password):
        if not isinstance(username, str) or len(username) > 128 or not isinstance(password, str) or len(password) > _MAX_PASSWORD_LENGTH:
            raise AuthenticationError("Invalid username or password.")
        now = _utcnow()
        with self._connect() as db:
            row = db.execute("SELECT * FROM auth_users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
            if row is None:
                hashlib.pbkdf2_hmac("sha256", (password or "invalid").encode("utf-8"), b"yuki-auth-dummy-salt", self.password_iterations)
                raise AuthenticationError("Invalid username or password.")
            if row["disabled"]:
                raise AuthenticationError("Invalid username or password.")
            if row["locked_until"]:
                try:
                    if datetime.fromisoformat(row["locked_until"]) > now:
                        raise AuthenticationError("Invalid username or password.")
                except ValueError:
                    pass
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), row["password_salt"], row["password_iterations"])
            if not hmac.compare_digest(candidate, row["password_hash"]):
                failures = row["failed_logins"] + 1
                locked_until = _timestamp(now + timedelta(seconds=self.lockout_seconds)) if failures >= self.max_failed_logins else None
                db.execute("UPDATE auth_users SET failed_logins=?,locked_until=? WHERE user_id=?", (failures, locked_until, row["user_id"]))
                raise AuthenticationError("Invalid username or password.")
            db.execute("UPDATE auth_users SET failed_logins=0,locked_until=NULL WHERE user_id=?", (row["user_id"],))
            token = secrets.token_urlsafe(_SESSION_BYTES)
            expires = now + timedelta(seconds=self.session_ttl_seconds)
            db.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (hashlib.sha256(token.encode("ascii")).hexdigest(), row["user_id"], _timestamp(now), _timestamp(expires)),
            )
            return IssuedSession(token, self._user(row), _timestamp(expires))

    def resolve_session(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            return None
        digest, now = hashlib.sha256(token.encode("utf-8")).hexdigest(), _timestamp(_utcnow())
        with self._connect() as db:
            row = db.execute("""
                SELECT u.* FROM auth_sessions s JOIN auth_users u ON u.user_id=s.user_id
                WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.disabled=0
            """, (digest, now)).fetchone()
            return self._user(row) if row else None

    def revoke_session(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as db:
            result = db.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (_timestamp(_utcnow()), digest),
            )
            return result.rowcount == 1

    def disable_user(self, user_id):
        if not isinstance(user_id, str) or not user_id:
            return False
        with self._connect() as db:
            result = db.execute("UPDATE auth_users SET disabled=1 WHERE user_id=?", (user_id,))
            db.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (_timestamp(_utcnow()), user_id))
            return result.rowcount == 1

    def list_users(self):
        with self._connect() as db:
            return [self._user(row) for row in db.execute(
                "SELECT user_id,username,role FROM auth_users ORDER BY username COLLATE NOCASE"
            ).fetchall()]


class _Connection:
    def __init__(self, connection):
        self.connection = connection
    def __enter__(self):
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        return self.connection
    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()
        return False


def workflow_principal_resolver(auth_service):
    """Resolve workflow approval identity from a trusted, transport-extracted session token."""
    from automation.workflow_approval import AuthenticatedPrincipal
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService")

    def resolve(request_context):
        user = auth_service.resolve_session(request_context)
        if user is None:
            raise AuthenticationError("Authentication required.")
        return AuthenticatedPrincipal(user.user_id, request_context, user.permissions)
    return resolve
