"""First-party, privacy-conscious aggregate analytics for PDFaspect."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import time


class AnalyticsStore:
    """Store aggregate traffic metrics without raw IPs or user-agent strings."""

    def __init__(self, database_url: str | None = None, sqlite_path: Path | None = None):
        self.database_url = (database_url or "").strip()
        self.sqlite_path = sqlite_path or Path("data/papermint.sqlite3")
        self.postgres = self.database_url.startswith(("postgres://", "postgresql://"))
        if not self.postgres:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connect(self):
        if self.postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise RuntimeError("PostgreSQL analytics requires psycopg.") from exc
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

    @staticmethod
    def day(timestamp: int | float | None = None) -> str:
        return datetime.fromtimestamp(timestamp or time.time(), timezone.utc).date().isoformat()

    def initialize(self) -> None:
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_daily_visitors (
                    day TEXT NOT NULL,
                    visitor_hash TEXT NOT NULL,
                    first_seen BIGINT NOT NULL,
                    PRIMARY KEY(day, visitor_hash)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_pageviews (
                    day TEXT NOT NULL,
                    path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, path, source)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_events (
                    day TEXT NOT NULL,
                    event TEXT NOT NULL,
                    tool_id TEXT NOT NULL DEFAULT '',
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, event, tool_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS analytics_visitors_day_idx "
                "ON analytics_daily_visitors(day)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS analytics_pageviews_day_idx "
                "ON analytics_pageviews(day)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS analytics_events_day_idx "
                "ON analytics_events(day)"
            )

    def _increment(self, cursor, table: str, columns: tuple[str, ...], values: tuple) -> None:
        placeholders = ", ".join(self._placeholder for _ in values)
        names = ", ".join((*columns, "count" if table == "analytics_events" else "views"))
        conflict = ", ".join(columns)
        metric = "count" if table == "analytics_events" else "views"
        if self.postgres:
            cursor.execute(
                f"INSERT INTO {table} ({names}) VALUES ({placeholders}, 1) "
                f"ON CONFLICT ({conflict}) DO UPDATE SET {metric} = {table}.{metric} + 1",
                values,
            )
        else:
            cursor.execute(
                f"INSERT INTO {table} ({names}) VALUES ({placeholders}, 1) "
                f"ON CONFLICT({conflict}) DO UPDATE SET {metric} = {metric} + 1",
                values,
            )

    def record(
        self,
        event: str,
        *,
        visitor_hash: str = "",
        path: str = "/",
        source: str = "direct",
        tool_id: str = "",
        timestamp: int | float | None = None,
    ) -> None:
        day = self.day(timestamp)
        now = int(timestamp or time.time())
        with self._connect() as connection:
            cursor = connection.cursor()
            if event == "page_view":
                if visitor_hash:
                    if self.postgres:
                        cursor.execute(
                            "INSERT INTO analytics_daily_visitors (day, visitor_hash, first_seen) "
                            "VALUES (%s, %s, %s) ON CONFLICT (day, visitor_hash) DO NOTHING",
                            (day, visitor_hash, now),
                        )
                    else:
                        cursor.execute(
                            "INSERT OR IGNORE INTO analytics_daily_visitors "
                            "(day, visitor_hash, first_seen) VALUES (?, ?, ?)",
                            (day, visitor_hash, now),
                        )
                self._increment(
                    cursor,
                    "analytics_pageviews",
                    ("day", "path", "source"),
                    (day, path, source),
                )
            self._increment(
                cursor,
                "analytics_events",
                ("day", "event", "tool_id"),
                (day, event, tool_id),
            )

        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=45)).isoformat()
        with self._connect() as connection:
            connection.cursor().execute(
                f"DELETE FROM analytics_daily_visitors WHERE day < {self._placeholder}",
                (cutoff,),
            )

    def summary(self, days: int = 30) -> dict:
        days = min(365, max(1, int(days)))
        today = datetime.now(timezone.utc).date()
        start = (today - timedelta(days=days - 1)).isoformat()
        day_labels = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
        placeholder = self._placeholder

        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT day, COUNT(*) FROM analytics_daily_visitors "
                f"WHERE day >= {placeholder} GROUP BY day",
                (start,),
            )
            visitors = {row[0]: int(row[1]) for row in cursor.fetchall()}
            cursor.execute(
                f"SELECT day, SUM(views) FROM analytics_pageviews "
                f"WHERE day >= {placeholder} GROUP BY day",
                (start,),
            )
            pageviews = {row[0]: int(row[1]) for row in cursor.fetchall()}
            cursor.execute(
                f"SELECT path, SUM(views) AS total FROM analytics_pageviews "
                f"WHERE day >= {placeholder} GROUP BY path ORDER BY total DESC LIMIT 10",
                (start,),
            )
            top_pages = [{"name": row[0], "count": int(row[1])} for row in cursor.fetchall()]
            cursor.execute(
                f"SELECT source, SUM(views) AS total FROM analytics_pageviews "
                f"WHERE day >= {placeholder} GROUP BY source ORDER BY total DESC LIMIT 10",
                (start,),
            )
            sources = [{"name": row[0], "count": int(row[1])} for row in cursor.fetchall()]
            cursor.execute(
                f"SELECT tool_id, SUM(count) AS total FROM analytics_events "
                f"WHERE day >= {placeholder} AND event = {placeholder} AND tool_id <> '' "
                f"GROUP BY tool_id ORDER BY total DESC LIMIT 10",
                (start, "tool_open"),
            )
            tools = [{"name": row[0], "count": int(row[1])} for row in cursor.fetchall()]
            cursor.execute(
                f"SELECT event, SUM(count) FROM analytics_events "
                f"WHERE day >= {placeholder} AND event IN "
                f"({placeholder}, {placeholder}, {placeholder}, {placeholder}) GROUP BY event",
                (start, "tool_submit", "registration", "checkout_started", "subscription_started"),
            )
            funnel = {row[0]: int(row[1]) for row in cursor.fetchall()}

        daily = [
            {"day": day, "visitors": visitors.get(day, 0), "pageviews": pageviews.get(day, 0)}
            for day in day_labels
        ]
        return {
            "days": days,
            "totals": {
                "visitors": sum(visitors.values()),
                "pageviews": sum(pageviews.values()),
                "tool_submits": funnel.get("tool_submit", 0),
                "registrations": funnel.get("registration", 0),
                "checkouts": funnel.get("checkout_started", 0),
                "subscriptions": funnel.get("subscription_started", 0),
            },
            "daily": daily,
            "sources": sources,
            "top_pages": top_pages,
            "top_tools": tools,
        }
