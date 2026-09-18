"""Small password/session authentication store for the PDFaspect web app.

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


@dataclass(frozen=True)
class BillingRecord:
    user_id: str
    stripe_customer_id: str | None
    stripe_subscription_id: str | None


@dataclass(frozen=True)
class SubscriptionRecord:
    user_id: str
    plan: str
    current_period_end: int | None
    cancel_at_period_end: bool
    status: str


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
                    created_at BIGINT NOT NULL,
                    email_verified_at BIGINT
                )
                """
            )
            if self.postgres:
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at BIGINT"
                )
            else:
                cursor.execute("PRAGMA table_info(users)")
                user_columns = {row[1] for row in cursor.fetchall()}
                if "email_verified_at" not in user_columns:
                    cursor.execute(
                        "ALTER TABLE users ADD COLUMN email_verified_at BIGINT"
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
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    used_at BIGINT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS password_reset_expires_idx "
                "ON password_reset_tokens(expires_at)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS email_verification_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    used_at BIGINT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS email_verification_expires_idx "
                "ON email_verification_tokens(expires_at)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL,
                    current_period_end BIGINT,
                    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
                    status TEXT NOT NULL DEFAULT 'free',
                    updated_at BIGINT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            if self.postgres:
                cursor.execute(
                    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS "
                    "cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE"
                )
                cursor.execute(
                    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS "
                    "status TEXT NOT NULL DEFAULT 'free'"
                )
            else:
                cursor.execute("PRAGMA table_info(subscriptions)")
                subscription_columns = {row[1] for row in cursor.fetchall()}
                if "cancel_at_period_end" not in subscription_columns:
                    cursor.execute(
                        "ALTER TABLE subscriptions ADD COLUMN "
                        "cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                if "status" not in subscription_columns:
                    cursor.execute(
                        "ALTER TABLE subscriptions ADD COLUMN "
                        "status TEXT NOT NULL DEFAULT 'free'"
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS billing_customers (
                    user_id TEXT PRIMARY KEY,
                    stripe_customer_id TEXT UNIQUE,
                    stripe_subscription_id TEXT UNIQUE,
                    updated_at BIGINT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    processed_at BIGINT NOT NULL
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

    def create_email_verification(self, user_id: str, ttl_seconds: int = 24 * 60 * 60) -> str:
        """Create a single-use email-verification token for a user."""
        placeholder = self._placeholder
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"DELETE FROM email_verification_tokens "
                f"WHERE user_id = {placeholder} OR expires_at <= {placeholder}",
                (user_id, now),
            )
            cursor.execute(
                f"INSERT INTO email_verification_tokens "
                f"(token_hash, user_id, created_at, expires_at, used_at) "
                f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, NULL)",
                (token_hash, user_id, now, now + max(300, int(ttl_seconds))),
            )
        return token

    def verify_email(self, token: str) -> AuthUser:
        """Consume an email-verification token and mark the address verified."""
        if not token:
            raise AuthError("This email verification link is invalid or has expired.")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        placeholder = self._placeholder
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT t.user_id, t.expires_at, t.used_at, u.email "
                f"FROM email_verification_tokens t "
                f"JOIN users u ON u.id = t.user_id "
                f"WHERE t.token_hash = {placeholder}",
                (token_hash,),
            )
            row = cursor.fetchone()
            if not row or row[2] is not None or int(row[1]) <= now:
                raise AuthError("This email verification link is invalid or has expired.")
            user_id, _, _, email = row
            cursor.execute(
                f"UPDATE users SET email_verified_at = {placeholder} WHERE id = {placeholder}",
                (now, user_id),
            )
            cursor.execute(
                f"UPDATE email_verification_tokens SET used_at = {placeholder} "
                f"WHERE token_hash = {placeholder}",
                (now, token_hash),
            )
            cursor.execute(
                f"DELETE FROM email_verification_tokens "
                f"WHERE user_id = {placeholder} AND token_hash <> {placeholder}",
                (user_id, token_hash),
            )
        return AuthUser(id=user_id, email=email)

    def email_is_verified(self, user_id: str) -> bool:
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT email_verified_at FROM users WHERE id = {placeholder}",
                (user_id,),
            )
            row = cursor.fetchone()
        return bool(row and row[0] is not None)

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

    def create_password_reset(self, email_value: str, ttl_seconds: int = 3600) -> str | None:
        """Create a single-use reset token for an existing account.

        Returns None when the email does not exist so callers can keep responses
        enumeration-safe. Only a SHA-256 hash is stored in the database.
        """
        email = normalize_email(email_value)
        placeholder = self._placeholder
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT id FROM users WHERE email = {placeholder}",
                (email,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            user_id = row[0]
            cursor.execute(
                f"DELETE FROM password_reset_tokens "
                f"WHERE user_id = {placeholder} OR expires_at <= {placeholder}",
                (user_id, now),
            )
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            cursor.execute(
                f"INSERT INTO password_reset_tokens "
                f"(token_hash, user_id, created_at, expires_at, used_at) "
                f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, NULL)",
                (token_hash, user_id, now, now + max(300, int(ttl_seconds))),
            )
        return token

    def reset_password(self, token: str, new_password: str) -> None:
        """Consume a valid reset token, replace the password, and sign out sessions."""
        if not token:
            raise AuthError("This password reset link is invalid or has expired.")
        password_hash = hash_password(new_password)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        placeholder = self._placeholder
        now = int(time.time())

        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT user_id, expires_at, used_at FROM password_reset_tokens "
                f"WHERE token_hash = {placeholder}",
                (token_hash,),
            )
            row = cursor.fetchone()
            if not row or row[2] is not None or int(row[1]) <= now:
                raise AuthError("This password reset link is invalid or has expired.")
            user_id = row[0]
            cursor.execute(
                f"UPDATE users SET password_hash = {placeholder} WHERE id = {placeholder}",
                (password_hash, user_id),
            )
            cursor.execute(
                f"UPDATE password_reset_tokens SET used_at = {placeholder} "
                f"WHERE token_hash = {placeholder}",
                (now, token_hash),
            )
            cursor.execute(
                f"DELETE FROM sessions WHERE user_id = {placeholder}",
                (user_id,),
            )
            cursor.execute(
                f"DELETE FROM password_reset_tokens "
                f"WHERE user_id = {placeholder} AND token_hash <> {placeholder}",
                (user_id, token_hash),
            )

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

    def user_for_id(self, user_id: str) -> AuthUser | None:
        """Return a user by internal ID without exposing authentication data."""
        if not user_id:
            return None
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT id, email FROM users WHERE id = {placeholder}",
                (user_id,),
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
        *,
        cancel_at_period_end: bool = False,
        status: str | None = None,
    ) -> None:
        """Upsert the authoritative subscription state received from Stripe."""
        if plan not in {"free", "monthly", "yearly", "pro"}:
            raise AuthError("Unknown subscription plan.")
        stripe_status = (status or ("active" if plan != "free" else "canceled")).strip()
        placeholder = self._placeholder
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.cursor()
            if self.postgres:
                cursor.execute(
                    """
                    INSERT INTO subscriptions
                        (user_id, plan, current_period_end, cancel_at_period_end, status, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        plan = EXCLUDED.plan,
                        current_period_end = EXCLUDED.current_period_end,
                        cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        user_id,
                        plan,
                        current_period_end,
                        cancel_at_period_end,
                        stripe_status,
                        now,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO subscriptions
                        (user_id, plan, current_period_end, cancel_at_period_end, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        plan = excluded.plan,
                        current_period_end = excluded.current_period_end,
                        cancel_at_period_end = excluded.cancel_at_period_end,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        user_id,
                        plan,
                        current_period_end,
                        int(cancel_at_period_end),
                        stripe_status,
                        now,
                    ),
                )

    def subscription_for_user(self, user_id: str) -> SubscriptionRecord | None:
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT user_id, plan, current_period_end, "
                f"cancel_at_period_end, status FROM subscriptions "
                f"WHERE user_id = {placeholder}",
                (user_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return SubscriptionRecord(
            user_id=row[0],
            plan=row[1],
            current_period_end=int(row[2]) if row[2] is not None else None,
            cancel_at_period_end=bool(row[3]),
            status=row[4],
        )

    def stripe_event_processed(self, event_id: str) -> bool:
        if not event_id:
            return False
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT 1 FROM stripe_webhook_events WHERE event_id = {placeholder}",
                (event_id,),
            )
            return cursor.fetchone() is not None

    def mark_stripe_event_processed(self, event_id: str, event_type: str) -> None:
        if not event_id:
            return
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            if self.postgres:
                cursor.execute(
                    """
                    INSERT INTO stripe_webhook_events (event_id, event_type, processed_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (event_id, event_type, int(time.time())),
                )
            else:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO stripe_webhook_events
                        (event_id, event_type, processed_at)
                    VALUES (?, ?, ?)
                    """,
                    (event_id, event_type, int(time.time())),
                )
    def set_billing_customer(
        self,
        user_id: str,
        *,
        customer_id: str | None,
        subscription_id: str | None,
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.cursor()
            if self.postgres:
                cursor.execute(
                    """
                    INSERT INTO billing_customers
                        (user_id, stripe_customer_id, stripe_subscription_id, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        stripe_customer_id = COALESCE(EXCLUDED.stripe_customer_id, billing_customers.stripe_customer_id),
                        stripe_subscription_id = COALESCE(EXCLUDED.stripe_subscription_id, billing_customers.stripe_subscription_id),
                        updated_at = EXCLUDED.updated_at
                    """,
                    (user_id, customer_id, subscription_id, now),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO billing_customers
                        (user_id, stripe_customer_id, stripe_subscription_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        stripe_customer_id = COALESCE(excluded.stripe_customer_id, billing_customers.stripe_customer_id),
                        stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, billing_customers.stripe_subscription_id),
                        updated_at = excluded.updated_at
                    """,
                    (user_id, customer_id, subscription_id, now),
                )

    def billing_for_user(self, user_id: str) -> BillingRecord | None:
        placeholder = self._placeholder
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT user_id, stripe_customer_id, stripe_subscription_id "
                f"FROM billing_customers WHERE user_id = {placeholder}",
                (user_id,),
            )
            row = cursor.fetchone()
        return BillingRecord(row[0], row[1], row[2]) if row else None

    def user_id_for_billing(
        self,
        *,
        customer_id: str | None = None,
        subscription_id: str | None = None,
    ) -> str | None:
        if not customer_id and not subscription_id:
            return None
        placeholder = self._placeholder
        clauses: list[str] = []
        values: list[str] = []
        if customer_id:
            clauses.append(f"stripe_customer_id = {placeholder}")
            values.append(customer_id)
        if subscription_id:
            clauses.append(f"stripe_subscription_id = {placeholder}")
            values.append(subscription_id)
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT user_id FROM billing_customers WHERE " + " OR ".join(clauses),
                tuple(values),
            )
            row = cursor.fetchone()
        return row[0] if row else None

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
