import pytest
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
import mongomock

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, compute_admin_analytics, compute_admin_ai_insights


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


def test_empty_database_analytics_does_not_crash(client, mock_db):
    analytics = compute_admin_analytics("admin@example.com")
    assert analytics["total_meetings"] == 0
    assert analytics["completed_meetings"] == 0
    assert analytics["overall_attendance_rate"] == 0.0
    assert analytics["avg_meeting_duration_minutes"] == 0.0

    insights = compute_admin_ai_insights("admin@example.com")
    assert insights["total_meetings"] == 0
    assert insights["meetings_with_summary"] == 0
    assert insights["total_action_items"] == 0
    assert insights["moderation_summary"]["total_events"] == 0
    assert insights["attention_summary"]["total_alerts"] == 0


def test_admin_can_retrieve_analytics(client, mock_db):
    meetings_col = mock_db["meetings"]
    meetings_col.insert_one({
        "_id": ObjectId(),
        "created_by_email": "admin@example.com",
        "title": "Strategy Sync",
        "status": "completed",
        "date": "2026-09-29",
        "time": "10:00 AM",
        "invited_participants": ["part_1", "part_2"],
        "attendance": [
            {
                "session_id": "s1",
                "participant_id": "part_1",
                "duration_seconds": 600,
                "status": "completed"
            }
        ]
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    res = client.get("/admin/analytics")
    assert res.status_code == 200

    api_res = client.get("/api/admin/analytics")
    assert api_res.status_code == 200
    data = api_res.get_json()
    assert data["success"] is True
    assert data["analytics"]["total_meetings"] == 1
    assert data["analytics"]["completed_meetings"] == 1
    assert data["analytics"]["total_participants"] == 2
    assert data["analytics"]["unique_attended_participants"] == 1


def test_non_admin_cannot_retrieve_analytics(client, mock_db):
    # Unauthenticated
    res = client.get("/api/admin/analytics")
    assert res.status_code == 401

    res_page = client.get("/admin/analytics")
    assert res_page.status_code == 302  # Redirect to admin_login

    # Participant role
    with client.session_transaction() as sess:
        sess["user_id"] = "part_123"
        sess["role"] = "participant"

    res = client.get("/api/admin/analytics")
    assert res.status_code == 401


def test_attendance_and_meeting_duration_statistics(client, mock_db):
    meetings_col = mock_db["meetings"]
    start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    end_time = datetime.now(timezone.utc).replace(tzinfo=None)

    meetings_col.insert_one({
        "_id": ObjectId(),
        "created_by_email": "admin@example.com",
        "title": "All Hands",
        "status": "completed",
        "started_at": start_time,
        "ended_at": end_time,
        "invited_participants": ["p1", "p2", "p3"],
        "attendance": [
            {"participant_id": "p1", "duration_seconds": 1800, "status": "completed"},
            {"participant_id": "p2", "duration_seconds": 1200, "status": "completed"}
        ]
    })

    analytics = compute_admin_analytics("admin@example.com")
    assert analytics["completed_meetings"] == 1
    assert analytics["avg_meeting_duration_minutes"] == 30.0
    assert analytics["avg_attendance_duration_minutes"] == 25.0
    assert analytics["max_session_duration_minutes"] == 30.0
    assert analytics["min_session_duration_minutes"] == 20.0
    assert analytics["overall_attendance_rate"] == 66.7


def test_ai_insight_aggregation(client, mock_db):
    meetings_col = mock_db["meetings"]
    meetings_col.insert_one({
        "_id": ObjectId(),
        "created_by_email": "admin@example.com",
        "title": "Q3 Planning",
        "status": "completed",
        "ai_summary": {
            "discussion_points": ["Budget allocation", "Timeline review"],
            "decisions": ["Approved $50k budget"],
            "action_items": [
                {"task": "Prepare slides", "owner": "Alice"},
                "Send email updates"
            ]
        }
    })

    insights = compute_admin_ai_insights("admin@example.com")
    assert insights["meetings_with_summary"] == 1
    assert insights["total_discussion_points"] == 2
    assert insights["total_decisions"] == 1
    assert insights["total_action_items"] == 2
    assert insights["action_items"][0]["owner"] == "Alice"
    assert insights["action_items"][1]["owner"] == "Unassigned"


def test_moderation_aggregation_works(client, mock_db):
    meetings_col = mock_db["meetings"]
    meetings_col.insert_one({
        "_id": ObjectId(),
        "created_by_email": "admin@example.com",
        "title": "Moderated Call",
        "status": "completed",
        "toxicity_events": [
            {
                "participant_id": "p1",
                "toxicity_score": 0.85,
                "action": "warning",
                "message": "Measured model toxicity signal",
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None)
            }
        ]
    })

    insights = compute_admin_ai_insights("admin@example.com")
    mod = insights["moderation_summary"]
    assert mod["total_events"] == 1
    assert mod["meetings_with_moderation"] == 1
    assert mod["avg_toxicity_score"] == 0.85
    assert mod["highest_toxicity_score"] == 0.85
    assert mod["events"][0]["participant_id"] == "p1"


def test_attention_aggregation_works(client, mock_db):
    meetings_col = mock_db["meetings"]
    meetings_col.insert_one({
        "_id": ObjectId(),
        "created_by_email": "admin@example.com",
        "title": "Attention Monitored Call",
        "status": "completed",
        "attention_alerts": [
            {
                "participant_id": "p1",
                "participant_name": "Bob",
                "score": 75,
                "status": "Partially Attentive",
                "time": "10:15:00"
            }
        ]
    })

    insights = compute_admin_ai_insights("admin@example.com")
    att = insights["attention_summary"]
    assert att["total_alerts"] == 1
    assert att["meetings_with_attention"] == 1
    assert att["monitored_participants"] == 1
    assert att["avg_attention_score"] == 75.0
    assert att["signals"][0]["participant_name"] == "Bob"
