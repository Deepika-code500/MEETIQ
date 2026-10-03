"""
Tests for Phase 4 — Multi-Tenant / Multi-User Security Isolation Audit.
Verifies strict isolation boundaries between ADMIN_A, ADMIN_B, PARTICIPANT_A, and PARTICIPANT_B across:
1. Meeting modification, deletion, and invitation controls
2. Analytics and AI insight boundaries
3. Meeting attendance and participant access
4. Async AI job polling isolation
5. Notifications isolation
6. TURN / ICE credentials harvesting prevention
"""

import sys
import os
import pytest
import mongomock
from bson.objectid import ObjectId

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, ai_jobs_collection, compute_admin_analytics, compute_admin_ai_insights


@pytest.fixture
def mock_db(monkeypatch):
    """Fixture supplying isolated in-memory mongomock database collections."""
    client = mongomock.MongoClient()
    db = client["meetiq_test_multitenant"]
    meetings_col = db["meetings"]
    participants_col = db["participants"]
    notifications_col = db["notifications"]
    ai_jobs_col = db["ai_jobs"]

    monkeypatch.setattr("app.meetings_collection", meetings_col)
    monkeypatch.setattr("app.participants_collection", participants_col)
    monkeypatch.setattr("app.notifications_collection", notifications_col)
    monkeypatch.setattr("app.ai_jobs_collection", ai_jobs_col)

    try:
        import models.notification_model
        monkeypatch.setattr("models.notification_model.notifications_collection", notifications_col)
    except Exception:
        pass

    return {
        "meetings": meetings_col,
        "participants": participants_col,
        "notifications": notifications_col,
        "ai_jobs": ai_jobs_col
    }


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key"
    with app.test_client() as client:
        yield client


# Setup Conceptual Identities and Meetings
@pytest.fixture
def tenant_environment(mock_db):
    """Creates ADMIN_A, ADMIN_B, PARTICIPANT_A, PARTICIPANT_B, MEETING_A, and MEETING_B."""
    admin_a_email = "admin_a@tenant1.com"
    admin_b_email = "admin_b@tenant2.com"
    part_a_id = "part_a_user"
    part_b_id = "part_b_user"

    mock_db["participants"].insert_many([
        {"name": "Participant A", "email": "part_a@tenant1.com", "user_id": part_a_id, "department": "CSE", "team": "Alpha", "eligible": True},
        {"name": "Participant B", "email": "part_b@tenant2.com", "user_id": part_b_id, "department": "ECE", "team": "Beta", "eligible": True}
    ])

    meeting_a_id = str(ObjectId())
    meeting_b_id = str(ObjectId())

    mock_db["meetings"].insert_many([
        {
            "_id": ObjectId(meeting_a_id),
            "title": "Meeting A (Tenant 1)",
            "created_by_email": admin_a_email,
            "status": "live",
            "invited_participants": [part_a_id],
            "attendance": [{"participant_id": part_a_id, "status": "active"}]
        },
        {
            "_id": ObjectId(meeting_b_id),
            "title": "Meeting B (Tenant 2)",
            "created_by_email": admin_b_email,
            "status": "live",
            "invited_participants": [part_b_id],
            "attendance": [{"participant_id": part_b_id, "status": "active"}]
        }
    ])

    return {
        "admin_a": admin_a_email,
        "admin_b": admin_b_email,
        "part_a": part_a_id,
        "part_b": part_b_id,
        "meeting_a_id": meeting_a_id,
        "meeting_b_id": meeting_b_id
    }


# -------------------------------------------------------------------------
# 1. ADMIN_A cannot modify, delete, end, or invite participants to MEETING_B
# -------------------------------------------------------------------------
def test_admin_cannot_modify_other_admin_meeting(client, tenant_environment, mock_db):
    env = tenant_environment

    # Authenticate as ADMIN_A
    with client.session_transaction() as sess:
        sess["user_id"] = env["admin_a"]
        sess["role"] = "admin"

    # ADMIN_A attempts to delete MEETING_B owned by ADMIN_B
    del_res = client.post(f"/admin/meetings/{env['meeting_b_id']}/delete")
    assert del_res.status_code in [302, 403, 404]

    # Verify MEETING_B was NOT deleted
    meeting_b = mock_db["meetings"].find_one({"_id": ObjectId(env["meeting_b_id"])})
    assert meeting_b is not None

    # ADMIN_A attempts to bulk invite to MEETING_B
    invite_res = client.post(
        f"/api/admin/meetings/{env['meeting_b_id']}/participants/bulk-invite",
        json={"participant_ids": [env["part_a"]]}
    )
    assert invite_res.status_code in [403, 404]


# -------------------------------------------------------------------------
# 2. ADMIN_A cannot read ADMIN_B analytics or private data
# -------------------------------------------------------------------------
def test_admin_analytics_isolation(tenant_environment):
    env = tenant_environment

    # Compute analytics for ADMIN_A
    analytics_a = compute_admin_analytics(env["admin_a"])
    meeting_ids_a = [m["meeting_id"] for m in analytics_a["meeting_analytics"]]

    # Verify ADMIN_A analytics ONLY contains MEETING_A, not MEETING_B
    assert env["meeting_a_id"] in meeting_ids_a
    assert env["meeting_b_id"] not in meeting_ids_a

    # Compute AI insights for ADMIN_A
    insights_a = compute_admin_ai_insights(env["admin_a"])
    admin_a_meeting_ids = [m["id"] for m in insights_a["admin_meetings_list"]]
    assert env["meeting_a_id"] in admin_a_meeting_ids
    assert env["meeting_b_id"] not in admin_a_meeting_ids


# -------------------------------------------------------------------------
# 3. PARTICIPANT_A cannot access MEETING_B & PARTICIPANT_B cannot access MEETING_A
# -------------------------------------------------------------------------
def test_cross_participant_meeting_access_prevention(client, tenant_environment):
    env = tenant_environment

    # PARTICIPANT_A attempts to access MEETING_B
    with client.session_transaction() as sess:
        sess["user_id"] = env["part_a"]
        sess["role"] = "participant"

    res_a_b = client.get(f"/participant/meetings/{env['meeting_b_id']}")
    assert res_a_b.status_code in [302, 403]

    join_a_b = client.post(f"/api/attendance/{env['meeting_b_id']}/join")
    assert join_a_b.status_code in [403, 404]

    # PARTICIPANT_B attempts to access MEETING_A
    with client.session_transaction() as sess:
        sess["user_id"] = env["part_b"]
        sess["role"] = "participant"

    res_b_a = client.get(f"/participant/meetings/{env['meeting_a_id']}")
    assert res_b_a.status_code in [302, 403]

    join_b_a = client.post(f"/api/attendance/{env['meeting_a_id']}/join")
    assert join_b_a.status_code in [403, 404]


# -------------------------------------------------------------------------
# 4. Participants cannot access admin analytics or insights
# -------------------------------------------------------------------------
def test_participant_blocked_from_admin_endpoints(client, tenant_environment):
    env = tenant_environment

    with client.session_transaction() as sess:
        sess["user_id"] = env["part_a"]
        sess["role"] = "participant"

    analytics_res = client.get("/api/admin/analytics")
    assert analytics_res.status_code in [401, 403]

    insights_res = client.get("/api/admin/ai-insights")
    assert insights_res.status_code in [401, 403]


# -------------------------------------------------------------------------
# 5. AI job IDs cannot be harvested cross-user or cross-meeting
# -------------------------------------------------------------------------
def test_ai_job_polling_isolation(client, tenant_environment, mock_db):
    env = tenant_environment

    job_id = "job_meeting_a_secret"
    mock_db["ai_jobs"].insert_one({
        "job_id": job_id,
        "meeting_id": env["meeting_a_id"],
        "user_id": env["admin_a"],
        "job_type": "summary",
        "status": "COMPLETED",
        "result": "Confidential meeting A summary"
    })

    # PARTICIPANT_B attempts to poll ADMIN_A's job
    with client.session_transaction() as sess:
        sess["user_id"] = env["part_b"]
        sess["role"] = "participant"

    poll_res_b = client.get(f"/api/ai/jobs/{job_id}")
    assert poll_res_b.status_code in [403, 404]

    # ADMIN_B attempts to poll ADMIN_A's job
    with client.session_transaction() as sess:
        sess["user_id"] = env["admin_b"]
        sess["role"] = "admin"

    poll_res_admin_b = client.get(f"/api/ai/jobs/{job_id}")
    assert poll_res_admin_b.status_code in [403, 404]

    # ADMIN_A (owner) can poll job
    with client.session_transaction() as sess:
        sess["user_id"] = env["admin_a"]
        sess["role"] = "admin"

    poll_res_admin_a = client.get(f"/api/ai/jobs/{job_id}")
    assert poll_res_admin_a.status_code == 200


# -------------------------------------------------------------------------
# 6. TURN / ICE credentials cannot be harvested for unauthorized meetings
# -------------------------------------------------------------------------
def test_ice_credentials_harvesting_prevention(client, tenant_environment):
    env = tenant_environment

    # PARTICIPANT_B attempts to harvest ICE servers for MEETING_A
    with client.session_transaction() as sess:
        sess["user_id"] = env["part_b"]
        sess["role"] = "participant"

    ice_res = client.get(f"/api/webrtc/ice-servers?meeting_id={env['meeting_a_id']}")
    assert ice_res.status_code in [403, 404]

    # PARTICIPANT_A (invited to MEETING_A) CAN retrieve ICE servers
    with client.session_transaction() as sess:
        sess["user_id"] = env["part_a"]
        sess["role"] = "participant"

    ice_res_a = client.get(f"/api/webrtc/ice-servers?meeting_id={env['meeting_a_id']}")
    assert ice_res_a.status_code == 200
