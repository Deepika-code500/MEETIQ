import pytest
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
import mongomock

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, finalize_attendance_session, finalize_meeting_attendance


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


def test_authenticated_participant_can_join(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "status": "live",
        "invited_participants": ["part_123"],
        "attendance": []
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["user_name"] = "Alice"
        sess["role"] = "participant"

    res = client.post(f"/api/attendance/{meeting_id}/join")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["already_recorded"] is False

    meeting = meetings_col.find_one({"_id": ObjectId(meeting_id)})
    assert len(meeting["attendance"]) == 1
    assert meeting["attendance"][0]["participant_id"] == "part_123"
    assert meeting["attendance"][0]["status"] == "active"
    assert meeting["attendance"][0]["leave_time"] is None


def test_unauthorized_user_cannot_join(client, mock_db):
    meeting_id = str(ObjectId())

    # No session
    res = client.post(f"/api/attendance/{meeting_id}/join")
    assert res.status_code == 401

    # Admin role instead of participant
    with client.session_transaction() as sess:
        sess["user_id"] = "admin_1"
        sess["role"] = "admin"

    res = client.post(f"/api/attendance/{meeting_id}/join")
    assert res.status_code == 401


def test_participant_cannot_join_nonexistent_meeting(client, mock_db):
    nonexistent_id = str(ObjectId())

    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["role"] = "participant"

    res = client.post(f"/api/attendance/{nonexistent_id}/join")
    assert res.status_code == 404

    # Invalid ObjectId string
    res = client.post("/api/attendance/invalid_id/join")
    assert res.status_code == 404


def test_participant_cannot_join_non_live_meeting(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "status": "upcoming",
        "invited_participants": ["part_123"],
        "attendance": []
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["role"] = "participant"

    res = client.post(f"/api/attendance/{meeting_id}/join")
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False


def test_duplicate_join_does_not_create_duplicate_active_sessions(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "status": "live",
        "invited_participants": ["part_123"],
        "attendance": []
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["role"] = "participant"

    res1 = client.post(f"/api/attendance/{meeting_id}/join")
    assert res1.status_code == 200

    res2 = client.post(f"/api/attendance/{meeting_id}/join")
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2["success"] is True
    assert data2["already_recorded"] is True

    meeting = meetings_col.find_one({"_id": ObjectId(meeting_id)})
    assert len(meeting["attendance"]) == 1


def test_participant_can_leave_and_calculates_duration(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())
    join_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "status": "live",
        "invited_participants": ["part_123"],
        "attendance": [{
            "session_id": "sess_1",
            "participant_id": "part_123",
            "join_time": join_time,
            "leave_time": None,
            "status": "active"
        }]
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["role"] = "participant"

    res = client.post(f"/api/attendance/{meeting_id}/leave")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["recorded"] is True
    assert data["duration_seconds"] >= 119

    meeting = meetings_col.find_one({"_id": ObjectId(meeting_id)})
    rec = meeting["attendance"][0]
    assert rec["status"] == "completed"
    assert rec["leave_time"] is not None
    assert rec["duration_seconds"] >= 119
    assert rec["duration_minutes"] >= 1.9


def test_duplicate_leave_is_safely_handled(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())
    join_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)
    leave_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "status": "live",
        "invited_participants": ["part_123"],
        "attendance": [{
            "session_id": "sess_1",
            "participant_id": "part_123",
            "join_time": join_time,
            "leave_time": leave_time,
            "duration_seconds": 50,
            "duration_minutes": 0.83,
            "status": "completed"
        }]
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["role"] = "participant"

    res = client.post(f"/api/attendance/{meeting_id}/leave")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["recorded"] is False

    meeting = meetings_col.find_one({"_id": ObjectId(meeting_id)})
    rec = meeting["attendance"][0]
    assert rec["duration_seconds"] == 50
    assert abs((rec["leave_time"] - leave_time).total_seconds()) < 1


def test_admin_ending_meeting_closes_active_attendance_sessions(client, mock_db):
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())
    join_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=300)

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "status": "live",
        "created_by_email": "admin@example.com",
        "invited_participants": ["part_1", "part_2"],
        "attendance": [
            {
                "session_id": "sess_1",
                "participant_id": "part_1",
                "join_time": join_time,
                "leave_time": None,
                "status": "active"
            },
            {
                "session_id": "sess_2",
                "participant_id": "part_2",
                "join_time": join_time,
                "leave_time": join_time + timedelta(seconds=100),
                "duration_seconds": 100,
                "duration_minutes": 1.67,
                "status": "completed"
            }
        ]
    })

    ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
    finalized_count = finalize_meeting_attendance(meeting_id, ended_at)

    assert finalized_count == 2
    meeting = meetings_col.find_one({"_id": ObjectId(meeting_id)})
    rec1 = meeting["attendance"][0]
    rec2 = meeting["attendance"][1]

    assert rec1["status"] == "completed"
    assert abs((rec1["leave_time"] - ended_at).total_seconds()) < 1
    assert rec1["duration_seconds"] >= 299

    assert rec2["status"] == "completed"
    assert rec2["duration_seconds"] == 100
