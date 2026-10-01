import pytest
from datetime import datetime
from bson.objectid import ObjectId
import mongomock

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import (
    app,
    generate_meeting_ai_summary,
    validate_same_meeting_sockets,
    socket_meeting_rooms,
    socket_users
)


@pytest.fixture
def mock_db(monkeypatch):
    client = mongomock.MongoClient()
    db = client["meetiq_test"]
    meetings_col = db["meetings"]
    participants_col = db["participants"]

    monkeypatch.setattr("app.meetings_collection", meetings_col)
    monkeypatch.setattr("app.participants_collection", participants_col)

    return {"meetings": meetings_col, "participants": participants_col}


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key"
    with app.test_client() as client:
        yield client


def test_unauthorized_admin_analytics_access(client, mock_db):
    # No session
    res = client.get("/api/admin/analytics")
    assert res.status_code == 401

    # Participant session trying to access admin route
    with client.session_transaction() as sess:
        sess["user_id"] = "part_1"
        sess["role"] = "participant"

    res = client.get("/api/admin/analytics")
    assert res.status_code == 401

    res_insights = client.get("/api/admin/ai-insights")
    assert res_insights.status_code == 401


def test_cross_user_meeting_access_prevention(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "title": "Private Admin Meeting",
        "created_by_email": "admin1@example.com",
        "status": "live",
        "invited_participants": ["part_invited"]
    })

    # Participant NOT invited trying to join attendance
    with client.session_transaction() as sess:
        sess["user_id"] = "part_uninvited"
        sess["role"] = "participant"

    res = client.post(f"/api/attendance/{meeting_id}/join")
    assert res.status_code == 403
    data = res.get_json()
    assert data["success"] is False


def test_invalid_meeting_ids_handling(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "part_1"
        sess["role"] = "participant"

    # Non-hex string
    res1 = client.post("/api/attendance/not_an_object_id/join")
    assert res1.status_code == 404

    # Hex string but non-existent
    non_existent = str(ObjectId())
    res2 = client.post(f"/api/attendance/{non_existent}/join")
    assert res2.status_code == 404


def test_cross_meeting_socketio_signaling_prevention(client, mock_db, monkeypatch):
    socket_meeting_rooms.clear()
    socket_users.clear()

    meeting_1 = "m_111"
    meeting_2 = "m_222"

    sid_user1 = "sid_111"
    sid_user2 = "sid_222"

    socket_users["user1"] = sid_user1
    socket_users["user2"] = sid_user2

    socket_meeting_rooms[sid_user1] = {
        "meeting_id": meeting_1,
        "participant_id": "user1",
        "role": "participant"
    }
    socket_meeting_rooms[sid_user2] = {
        "meeting_id": meeting_2,
        "participant_id": "user2",
        "role": "participant"
    }

    with app.test_request_context("/"):
        monkeypatch.setattr("app.request", type("Req", (), {"sid": sid_user1}))

        valid_socket = validate_same_meeting_sockets(meeting_1, "user2")
        assert valid_socket is None  # Blocked cross-meeting attempt!

        # Same meeting check
        socket_meeting_rooms[sid_user2]["meeting_id"] = meeting_1
        valid_socket_same = validate_same_meeting_sockets(meeting_1, "user2")
        assert valid_socket_same == sid_user2  # Allowed in same meeting!


def test_ai_service_failure_handling_graceful_degradation(client, mock_db, monkeypatch):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "title": "Meeting with AI Failure",
        "status": "live",
        "created_by_email": "admin@example.com",
        "invited_participants": ["p1"],
        "transcript_segments": [
            {"participant_name": "p1", "text": "Hello world"}
        ]
    })

    def mock_broken_summary(transcript):
        raise RuntimeError("Ollama AI Service Offline")

    monkeypatch.setattr("app.generate_summary", mock_broken_summary)

    result = generate_meeting_ai_summary(meeting_id)
    assert result["success"] is False
    assert "Ollama AI Service Offline" in result["message"]

    meeting = meetings_col.find_one({"_id": ObjectId(meeting_id)})
    assert meeting is not None
    assert meeting["status"] == "live"
