"""Small password/session authentication store for the PaperMint web app.

Production uses PostgreSQL through ``DATABASE_URL``. Local development falls
back to SQLite so the account flow can be exercised without extra services.
Only hashes are persisted: passwords use scrypt and session tokens use SHA-256.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PASSWORD_MIN_LENGTH = 8
SESSION_SECONDS = 30 * 24 * 60 * 60


class AuthError(ValueError):
    """Safe authentication error that may be shown to the user."""


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str


@dataclass(frozen=True)
class AiUsage:
    period: str
    documents: int
    questions: int


def normalize_email(value: str) -> str:
    email = (value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise AuthError("Enter a valid email address.")
    return email


def validate_password(value: str) -> str:
    password = value or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(f"Password must contain at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > 128:
        raise AuthError("Password can contain up to 128 characters.")
    return password


def hash_password(password: str) -> str:
    password = validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            (password or "").encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class AuthStore:
    def __init__(self, database_url: str | None = None, sqlite_path: Path | None = None):
        self.database_url = (database_url or "").strip()
        self.sqlite_path = sqlite_path or Path(
            os.getenv("PAPERMINT_AUTH_DB", "data/papermint.sqlite3")
        )
        self.postgres = self.database_url.startswith(("postgres://", "postgresql://"))

        if not self.postgres:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

        self._usage_lock = threading.Lock()
        self.initialize()

    @contextmanager
    def _connect(self):
        if self.postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise RuntimeError("PostgreSQL authentication requires psycopg.") from exc
            connection = psycopg.connect(self.database_url)
        else:
            connection = sqlite3.connect(self.sqlite_path, timeout=10)

        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @property
    def _placeholder(self) -> str:
        return "%s" if self.postgres else "?"

    def initialize(self) -> None:
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires_at)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL,
                    current_period_end BIGINT,
                    updated_at BIGINT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_usage (
                    user_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    documents INTEGER NOT NULL DEFAULT 0,
                    questions INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, period),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )

    def register(self, email_value: str, password_value: str) -> AuthUser:
        email = normalize_email(email_value)
        password_hash = hash_password(password_value)
        user = AuthUser(id=uuid.uuid4().hex, email=email)
        placeholder = self._placeholder

        try:
            with self._connect() as connection:
                connection.cursor().execute(
                    f"INSERT INTO users (id, email, password_hash, created_at) "
                    f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})",
                    (user.id, user.email, password_hash, int(time.time())),
                )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise AuthError("An account with this email already exists.") from exc
            raise

        return user

    def authenticate(self, email_value: str, password_value: str) -> AuthUser:
        email = normalize_email(email_value)
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT id, email, password_hash FROM users WHERE email = {placeholder}",
                (email,),
            )
            row = cursor.fetchone()

        if not row or not verify_password(password_value, row[2]):
            raise AuthError("Incorrect email or password.")
        return AuthUser(id=row[0], email=row[1])

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = int(time.time())
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"DELETE FROM sessions WHERE expires_at <= {placeholder}",
                (now,),
            )
            cursor.execute(
                f"INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})",
                (token_hash, user_id, now, now + SESSION_SECONDS),
            )
        return token

    def user_for_session(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT users.id, users.email
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = {placeholder}
                  AND sessions.expires_at > {placeholder}
                """,
                (token_hash, int(time.time())),
            )
            row = cursor.fetchone()
        return AuthUser(id=row[0], email=row[1]) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        placeholder = self._placeholder
        with self._connect() as connection:
            connection.cursor().execute(
                f"DELETE FROM sessions WHERE token_hash = {placeholder}",
                (token_hash,),
            )

    @staticmethod
    def _usage_period(timestamp: int | None = None) -> str:
        return time.strftime("%Y-%m", time.gmtime(timestamp or time.time()))

    def plan_for_user(self, user: AuthUser, timestamp: int | None = None) -> str:
        """Return ``pro`` only for an active paid subscription.

        ``PAPERMINT_PRO_EMAILS`` is intentionally supported for owner/beta testing
        before the Stripe webhook is connected. It never comes from the browser.
        """
        beta_emails = {
            item.strip().lower()
            for item in os.getenv("PAPERMINT_PRO_EMAILS", "").split(",")
            if item.strip()
        }
        if user.email.lower() in beta_emails:
            return "pro"

        now = int(timestamp or time.time())
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT plan, current_period_end FROM subscriptions "
                f"WHERE user_id = {placeholder}",
                (user.id,),
            )
            row = cursor.fetchone()
        if not row or row[0] not in {"monthly", "yearly", "pro"}:
            return "free"
        if row[1] is not None and int(row[1]) <= now:
            return "free"
        return "pro"

    def set_subscription(
        self,
        user_id: str,
        plan: str,
        current_period_end: int | None = None,
    ) -> None:
        """Upsert subscription state for the future Stripe webhook and tests."""
        if plan not in {"free", "monthly", "yearly", "pro"}:
            raise AuthError("Unknown subscription plan.")
        placeholder = self._placeholder
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.cursor()
            if self.postgres:
                cursor.execute(
                    """
                    INSERT INTO subscriptions (user_id, plan, current_period_end, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        plan = EXCLUDED.plan,
                        current_period_end = EXCLUDED.current_period_end,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (user_id, plan, current_period_end, now),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO subscriptions (user_id, plan, current_period_end, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        plan = excluded.plan,
                        current_period_end = excluded.current_period_end,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, plan, current_period_end, now),
                )

    def ai_usage(self, user_id: str, timestamp: int | None = None) -> AiUsage:
        period = self._usage_period(timestamp)
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT documents, questions FROM ai_usage "
                f"WHERE user_id = {placeholder} AND period = {placeholder}",
                (user_id, period),
            )
            row = cursor.fetchone()
        return AiUsage(period, int(row[0]), int(row[1])) if row else AiUsage(period, 0, 0)

    def consume_ai_usage(
        self,
        user_id: str,
        *,
        documents: int = 0,
        questions: int = 0,
        document_limit: int,
        question_limit: int,
        timestamp: int | None = None,
    ) -> AiUsage:
        """Atomically consume monthly AI quota and return the new counters."""
        if documents < 0 or questions < 0 or not (documents or questions):
            raise AuthError("Invalid AI usage increment.")
        period = self._usage_period(timestamp)
        placeholder = self._placeholder
        with self._usage_lock:
            with self._connect() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    f"SELECT documents, questions FROM ai_usage "
                    f"WHERE user_id = {placeholder} AND period = {placeholder}",
                    (user_id, period),
                )
                row = cursor.fetchone()
                used_documents, used_questions = (int(row[0]), int(row[1])) if row else (0, 0)
                new_documents = used_documents + documents
                new_questions = used_questions + questions
                if new_documents > document_limit:
                    raise AuthError("Monthly AI document limit reached.")
                if new_questions > question_limit:
                    raise AuthError("Monthly AI question limit reached.")

                if row:
                    cursor.execute(
                        f"UPDATE ai_usage SET documents = {placeholder}, questions = {placeholder} "
                        f"WHERE user_id = {placeholder} AND period = {placeholder}",
                        (new_documents, new_questions, user_id, period),
                    )
                else:
                    cursor.execute(
                        f"INSERT INTO ai_usage (user_id, period, documents, questions) "
                        f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})",
                        (user_id, period, new_documents, new_questions),
                    )
        return AiUsage(period, new_documents, new_questions)

    def refund_ai_usage(
        self,
        user_id: str,
        *,
        documents: int = 0,
        questions: int = 0,
        timestamp: int | None = None,
    ) -> AiUsage:
        """Release quota reserved for an AI request that did not complete."""
        period = self._usage_period(timestamp)
        placeholder = self._placeholder
        with self._usage_lock:
            with self._connect() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    f"SELECT documents, questions FROM ai_usage "
                    f"WHERE user_id = {placeholder} AND period = {placeholder}",
                    (user_id, period),
                )
                row = cursor.fetchone()
                used_documents, used_questions = (int(row[0]), int(row[1])) if row else (0, 0)
                new_documents = max(0, used_documents - max(0, documents))
                new_questions = max(0, used_questions - max(0, questions))
                if row:
                    cursor.execute(
                        f"UPDATE ai_usage SET documents = {placeholder}, questions = {placeholder} "
                        f"WHERE user_id = {placeholder} AND period = {placeholder}",
                        (new_documents, new_questions, user_id, period),
                    )
        return AiUsage(period, new_documents, new_questions)
