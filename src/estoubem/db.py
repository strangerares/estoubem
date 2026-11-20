from contextlib import contextmanager
from datetime import datetime, timezone
import os
import sqlite3
from pathlib import Path
from typing import Iterator


def database_path() -> Path:
    return Path(os.environ.get("ESTOUBEM_DB_PATH", "data/estoubem.sqlite3"))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        if connection.in_transaction:
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
                preferred_checkin_channel TEXT NOT NULL DEFAULT 'telephone'
                    CHECK (preferred_checkin_channel IN (
                        'telephone', 'whatsapp_call', 'whatsapp_message'
                    )),
                response_window_seconds INTEGER NOT NULL DEFAULT 60
                    CHECK (response_window_seconds BETWEEN 10 AND 3600),
                postpone_retry_after_seconds INTEGER NOT NULL DEFAULT 3600
                    CHECK (postpone_retry_after_seconds BETWEEN 60 AND 604800),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS checkins (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(id),
                scheduled_for TEXT NOT NULL,
                started_at TEXT,
                response_deadline_at TEXT,
                completed_at TEXT,
                final_outcome TEXT CHECK (final_outcome IN (
                    'OK', 'HELP_REQUESTED', 'POSTPONED', 'UNANSWERED'
                )),
                final_channel TEXT,
                response_at TEXT,
                response_value TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                retry_of_checkin_id TEXT REFERENCES checkins(id),
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS checkin_attempts (
                id TEXT PRIMARY KEY,
                checkin_id TEXT NOT NULL REFERENCES checkins(id),
                attempt_number INTEGER NOT NULL,
                channel TEXT NOT NULL CHECK (channel IN (
                    'telephone', 'whatsapp_call', 'whatsapp_message'
                )),
                provider TEXT NOT NULL DEFAULT 'simulator',
                status TEXT NOT NULL CHECK (status IN (
                    'awaiting_response', 'completed'
                )),
                response_outcome TEXT,
                started_at TEXT NOT NULL,
                response_at TEXT,
                UNIQUE (checkin_id, attempt_number)
            );

            CREATE TABLE IF NOT EXISTS checkin_events (
                id TEXT PRIMARY KEY,
                checkin_id TEXT NOT NULL REFERENCES checkins(id),
                attempt_id TEXT REFERENCES checkin_attempts(id),
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                external_event_key TEXT NOT NULL,
                UNIQUE (source, external_event_key)
            );

            CREATE TABLE IF NOT EXISTS caregiver_notifications (
                id TEXT PRIMARY KEY,
                checkin_id TEXT NOT NULL REFERENCES checkins(id),
                caregiver_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'recorded_locally',
                created_at TEXT NOT NULL,
                UNIQUE (checkin_id, caregiver_id, reason)
            );

            CREATE INDEX IF NOT EXISTS checkins_customer_schedule
                ON checkins(customer_id, scheduled_for DESC);
            """
        )
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT OR IGNORE INTO customers (id, created_at, updated_at)
            VALUES ('demo', ?, ?)
            """,
            (now, now),
        )

