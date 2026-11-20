from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from estoubem import main
from estoubem.db import initialize_database


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ESTOUBEM_DB_PATH", str(tmp_path / "test.sqlite3"))
    initialize_database()
    with TestClient(main.app) as test_client:
        yield test_client


def test_preferences_can_be_changed_without_changing_existing_attempt(client):
    checkin = client.post("/api/v1/checkins").json()

    updated = client.patch(
        "/api/v1/customers/me/checkin-preferences",
        json={"preferred_checkin_channel": "whatsapp_message"},
    )

    assert updated.status_code == 200
    assert updated.json()["preferred_checkin_channel"] == "whatsapp_message"
    assert checkin["attempts"][0]["channel"] == "telephone"
    assert checkin["attempts"][0]["provider"] == "simulator"


def test_starting_with_same_idempotency_key_does_not_duplicate_checkin(client):
    headers = {"Idempotency-Key": "demo-occurrence-1"}
    first = client.post("/api/v1/checkins", headers=headers)
    repeated = client.post("/api/v1/checkins", headers=headers)

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert len(client.get("/api/v1/checkins").json()["items"]) == 1


def test_only_explicit_positive_response_marks_checkin_ok(client):
    checkin = client.post("/api/v1/checkins").json()

    response = client.post(
        f"/api/v1/checkins/{checkin['id']}/response",
        json={"response": "checkin_ok"},
    )

    assert response.json()["final_outcome"] == "OK"
    assert response.json()["response_value"] == "checkin_ok"


def test_cannot_start_a_second_checkin_while_one_is_awaiting_response(client):
    client.post("/api/v1/checkins")

    response = client.post("/api/v1/checkins")

    assert response.status_code == 409
    assert len(client.get("/api/v1/checkins").json()["items"]) == 1


def test_help_request_is_recorded_once_and_never_claims_to_notify_caregiver(client):
    checkin = client.post("/api/v1/checkins").json()

    first = client.post(
        f"/api/v1/checkins/{checkin['id']}/response",
        json={"response": "checkin_help"},
    )
    repeated = client.post(
        f"/api/v1/checkins/{checkin['id']}/response",
        json={"response": "checkin_help"},
    )
    notifications = client.get("/api/v1/checkins").json()["notifications"]

    assert first.json()["final_outcome"] == "HELP_REQUESTED"
    assert repeated.json()["final_outcome"] == "HELP_REQUESTED"
    assert len(notifications) == 1
    assert notifications[0]["status"] == "recorded_locally"


def test_postponing_schedules_a_followup_without_marking_ok(client):
    checkin = client.post("/api/v1/checkins").json()

    response = client.post(
        f"/api/v1/checkins/{checkin['id']}/response",
        json={"response": "checkin_postpone"},
    )
    items = client.get("/api/v1/checkins").json()["items"]
    followup = next(item for item in items if item["retry_of_checkin_id"] == checkin["id"])

    assert response.json()["final_outcome"] == "POSTPONED"
    assert followup["retry_count"] == 1
    assert followup["started_at"] is None


def test_expired_response_window_becomes_unanswered(client, monkeypatch):
    current_time = main.utc_now()
    monkeypatch.setattr(main, "utc_now", lambda: current_time)
    checkin = client.post("/api/v1/checkins").json()
    monkeypatch.setattr(
        main,
        "utc_now",
        lambda: current_time + timedelta(seconds=61),
    )

    refreshed = client.get("/api/v1/checkins").json()["items"][0]
    late_response = client.post(
        f"/api/v1/checkins/{checkin['id']}/response",
        json={"response": "checkin_ok"},
    )

    assert refreshed["final_outcome"] == "UNANSWERED"
    assert late_response.json()["final_outcome"] == "UNANSWERED"

