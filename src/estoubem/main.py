import asyncio
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from estoubem.db import connect, initialize_database


class Channel(StrEnum):
    telephone = "telephone"
    whatsapp_call = "whatsapp_call"
    whatsapp_message = "whatsapp_message"


class ResponseValue(StrEnum):
    ok = "checkin_ok"
    help = "checkin_help"
    postpone = "checkin_postpone"


CHANNEL_LABELS = {
    Channel.telephone: "Telefone (simulado)",
    Channel.whatsapp_call: "Chamada de voz pelo WhatsApp (simulada)",
    Channel.whatsapp_message: "Mensagem pelo WhatsApp (simulada)",
}
RESPONSE_OUTCOMES = {
    ResponseValue.ok: "OK",
    ResponseValue.help: "HELP_REQUESTED",
    ResponseValue.postpone: "POSTPONED",
}
STATIC_DIR = Path(__file__).parent / "static"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return str(uuid.uuid4())


def create_attempt(
    connection: sqlite3.Connection,
    checkin_id: str,
    channel: str,
    started_at: str,
) -> str:
    attempt_id = new_id()
    connection.execute(
        """
        INSERT INTO checkin_attempts
            (id, checkin_id, attempt_number, channel, status, started_at)
        VALUES (?, ?, 1, ?, 'awaiting_response', ?)
        """,
        (attempt_id, checkin_id, channel, started_at),
    )
    connection.execute(
        """
        INSERT INTO checkin_events
            (id, checkin_id, attempt_id, source, event_type, occurred_at,
             external_event_key)
        VALUES (?, ?, ?, 'simulator', 'awaiting_response', ?, ?)
        """,
        (new_id(), checkin_id, attempt_id, started_at, f"{attempt_id}:started"),
    )
    return attempt_id


def advance_checkins() -> None:
    now = timestamp(utc_now())
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE checkin_attempts
            SET status = 'completed', response_outcome = 'UNANSWERED'
            WHERE status = 'awaiting_response'
              AND checkin_id IN (
                  SELECT id FROM checkins
                  WHERE completed_at IS NULL
                    AND response_deadline_at IS NOT NULL
                    AND response_deadline_at <= ?
              )
            """,
            (now,),
        )
        connection.execute(
            """
            UPDATE checkins
            SET final_outcome = 'UNANSWERED', completed_at = ?
            WHERE completed_at IS NULL
              AND response_deadline_at IS NOT NULL
              AND response_deadline_at <= ?
            """,
            (now, now),
        )
        due_checkins = connection.execute(
            """
            SELECT checkins.id, customers.preferred_checkin_channel,
                   customers.response_window_seconds
            FROM checkins
            JOIN customers ON customers.id = checkins.customer_id
            WHERE checkins.completed_at IS NULL
              AND checkins.started_at IS NULL
              AND checkins.scheduled_for <= ?
              AND NOT EXISTS (
                  SELECT 1 FROM checkins active
                  WHERE active.customer_id = customers.id
                    AND active.started_at IS NOT NULL
                    AND active.completed_at IS NULL
              )
            ORDER BY checkins.scheduled_for
            LIMIT 1
            """,
            (now,),
        ).fetchall()
        for checkin in due_checkins:
            deadline = timestamp(
                utc_now() + timedelta(seconds=checkin["response_window_seconds"])
            )
            connection.execute(
                """
                UPDATE checkins
                SET started_at = ?, response_deadline_at = ?,
                    final_channel = ?
                WHERE id = ? AND started_at IS NULL
                """,
                (now, deadline, checkin["preferred_checkin_channel"], checkin["id"]),
            )
            create_attempt(
                connection,
                checkin["id"],
                checkin["preferred_checkin_channel"],
                now,
            )


def serialize_checkin(connection: sqlite3.Connection, checkin: sqlite3.Row) -> dict:
    attempts = connection.execute(
        """
        SELECT id, attempt_number, channel, provider, status, response_outcome,
               started_at, response_at
        FROM checkin_attempts
        WHERE checkin_id = ?
        ORDER BY attempt_number
        """,
        (checkin["id"],),
    ).fetchall()
    return {
        "id": checkin["id"],
        "scheduled_for": checkin["scheduled_for"],
        "started_at": checkin["started_at"],
        "response_deadline_at": checkin["response_deadline_at"],
        "completed_at": checkin["completed_at"],
        "final_outcome": checkin["final_outcome"],
        "final_channel": checkin["final_channel"],
        "response_at": checkin["response_at"],
        "response_value": checkin["response_value"],
        "retry_count": checkin["retry_count"],
        "retry_of_checkin_id": checkin["retry_of_checkin_id"],
        "attempts": [dict(attempt) for attempt in attempts],
    }


def fetch_checkin(checkin_id: str) -> dict:
    with connect() as connection:
        checkin = connection.execute(
            "SELECT * FROM checkins WHERE id = ? AND customer_id = 'demo'",
            (checkin_id,),
        ).fetchone()
        if checkin is None:
            raise HTTPException(status_code=404, detail="Check-in não encontrado.")
        return serialize_checkin(connection, checkin)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Estou Bem — demonstração local",
    description="MVP local de check-ins. Nenhum canal externo é contatado.",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/v1/customers/me/checkin-preferences")
def get_preferences() -> dict:
    with connect() as connection:
        customer = connection.execute(
            """
            SELECT preferred_checkin_channel, response_window_seconds,
                   postpone_retry_after_seconds, timezone
            FROM customers WHERE id = 'demo'
            """
        ).fetchone()
    return {
        **dict(customer),
        "preferred_checkin_channel_label": CHANNEL_LABELS[
            Channel(customer["preferred_checkin_channel"])
        ],
        "channels": [
            {"id": channel.value, "label": CHANNEL_LABELS[channel]}
            for channel in Channel
        ],
        "simulated_only": True,
    }


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferred_checkin_channel: Channel


@app.patch("/api/v1/customers/me/checkin-preferences")
def update_preferences(preferences: PreferencesUpdate) -> dict:
    with connect() as connection:
        connection.execute(
            """
            UPDATE customers
            SET preferred_checkin_channel = ?, updated_at = ?
            WHERE id = 'demo'
            """,
            (preferences.preferred_checkin_channel.value, timestamp(utc_now())),
        )
    return get_preferences()


@app.get("/api/v1/checkins")
def list_checkins() -> dict:
    advance_checkins()
    with connect() as connection:
        checkins = connection.execute(
            """
            SELECT * FROM checkins
            WHERE customer_id = 'demo'
            ORDER BY scheduled_for DESC
            LIMIT 50
            """
        ).fetchall()
        notifications = connection.execute(
            """
            SELECT checkin_id, reason, status, created_at
            FROM caregiver_notifications
            WHERE caregiver_id = 'demo-caregiver'
            ORDER BY created_at DESC
            """
        ).fetchall()
        return {
            "items": [serialize_checkin(connection, checkin) for checkin in checkins],
            "notifications": [dict(item) for item in notifications],
        }


@app.post("/api/v1/checkins", status_code=201)
def start_checkin(
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    advance_checkins()
    key = idempotency_key or new_id()
    if len(key) > 200:
        raise HTTPException(status_code=422, detail="Chave de idempotência muito longa.")
    now = utc_now()
    started_at = timestamp(now)
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM checkins WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing is not None:
            response.status_code = 200
            return serialize_checkin(connection, existing)
        active = connection.execute(
            """
            SELECT id FROM checkins
            WHERE customer_id = 'demo'
              AND started_at IS NOT NULL
              AND completed_at IS NULL
            LIMIT 1
            """
        ).fetchone()
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail="Já existe um check-in aguardando resposta.",
            )
        customer = connection.execute(
            """
            SELECT preferred_checkin_channel, response_window_seconds
            FROM customers WHERE id = 'demo'
            """
        ).fetchone()
        checkin_id = new_id()
        channel = customer["preferred_checkin_channel"]
        connection.execute(
            """
            INSERT INTO checkins
                (id, customer_id, scheduled_for, started_at,
                 response_deadline_at, final_channel, idempotency_key, created_at)
            VALUES (?, 'demo', ?, ?, ?, ?, ?, ?)
            """,
            (
                checkin_id,
                started_at,
                started_at,
                timestamp(now + timedelta(seconds=customer["response_window_seconds"])),
                channel,
                key,
                started_at,
            ),
        )
        create_attempt(connection, checkin_id, channel, started_at)
        created = connection.execute(
            "SELECT * FROM checkins WHERE id = ?", (checkin_id,)
        ).fetchone()
        return serialize_checkin(connection, created)


class CheckinResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response: ResponseValue


@app.post("/api/v1/checkins/{checkin_id}/response")
def respond_to_checkin(checkin_id: str, payload: CheckinResponse) -> dict:
    advance_checkins()
    now = timestamp(utc_now())
    outcome = RESPONSE_OUTCOMES[payload.response]
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        checkin = connection.execute(
            "SELECT * FROM checkins WHERE id = ? AND customer_id = 'demo'",
            (checkin_id,),
        ).fetchone()
        if checkin is None:
            raise HTTPException(status_code=404, detail="Check-in não encontrado.")
        if checkin["completed_at"] is not None:
            if (
                checkin["response_value"] == payload.response.value
                or checkin["final_outcome"] == "UNANSWERED"
            ):
                return serialize_checkin(connection, checkin)
            raise HTTPException(
                status_code=409,
                detail="Este check-in já foi concluído com outra resposta.",
            )
        attempt = connection.execute(
            """
            SELECT id FROM checkin_attempts
            WHERE checkin_id = ? AND status = 'awaiting_response'
            """,
            (checkin_id,),
        ).fetchone()
        if attempt is None:
            raise HTTPException(status_code=409, detail="Não há tentativa ativa.")
        connection.execute(
            """
            UPDATE checkins
            SET final_outcome = ?, completed_at = ?, response_at = ?,
                response_value = ?
            WHERE id = ? AND completed_at IS NULL
            """,
            (outcome, now, now, payload.response.value, checkin_id),
        )
        connection.execute(
            """
            UPDATE checkin_attempts
            SET status = 'completed', response_outcome = ?, response_at = ?
            WHERE id = ? AND status = 'awaiting_response'
            """,
            (outcome, now, attempt["id"]),
        )
        connection.execute(
            """
            INSERT INTO checkin_events
                (id, checkin_id, attempt_id, source, event_type, occurred_at,
                 external_event_key)
            VALUES (?, ?, ?, 'simulator', ?, ?, ?)
            """,
            (
                new_id(),
                checkin_id,
                attempt["id"],
                outcome,
                now,
                f"{checkin_id}:{payload.response.value}",
            ),
        )
        if payload.response == ResponseValue.help:
            connection.execute(
                """
                INSERT OR IGNORE INTO caregiver_notifications
                    (id, checkin_id, caregiver_id, reason, created_at)
                VALUES (?, ?, 'demo-caregiver', 'HELP_REQUESTED', ?)
                """,
                (new_id(), checkin_id, now),
            )
        elif payload.response == ResponseValue.postpone:
            customer = connection.execute(
                """
                SELECT preferred_checkin_channel, postpone_retry_after_seconds
                FROM customers WHERE id = 'demo'
                """
            ).fetchone()
            retry_at = timestamp(
                utc_now() + timedelta(seconds=customer["postpone_retry_after_seconds"])
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO checkins
                    (id, customer_id, scheduled_for, retry_count,
                     retry_of_checkin_id, idempotency_key, created_at)
                VALUES (?, 'demo', ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    retry_at,
                    checkin["retry_count"] + 1,
                    checkin_id,
                    f"postpone:{checkin_id}",
                    now,
                ),
            )
        updated = connection.execute(
            "SELECT * FROM checkins WHERE id = ?", (checkin_id,)
        ).fetchone()
        return serialize_checkin(connection, updated)


@app.get("/api/v1/checkins/{checkin_id}")
def get_checkin(checkin_id: str) -> dict:
    advance_checkins()
    return fetch_checkin(checkin_id)

