# =========================================================
# MeetIQ - WebRTC ICE & TURN Server Tests
# =========================================================

import pytest
import os
import sys
from bson.objectid import ObjectId
import mongomock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, get_ice_servers_config


@pytest.fixture
def mock_db(monkeypatch):
    client = mongomock.MongoClient()
    db = client["meetiq_test"]
    meetings_col = db["meetings"]
    participants_col = db["participants"]

    monkeypatch.setattr("app.meetings_collection", meetings_col)
    monkeypatch.setattr("app.participants_collection", participants_col)

    return {
        "meetings": meetings_col,
        "participants": participants_col
    }


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key"
    with app.test_client() as client:
        yield client


def test_unauthenticated_ice_servers_access(client):
    """Verify unauthenticated users receive HTTP 401 Unauthorized."""
    meeting_id = str(ObjectId())
    res = client.get(f"/api/webrtc/ice-servers?meeting_id={meeting_id}")
    assert res.status_code == 401
    data = res.get_json()
    assert data["success"] is False


def test_missing_meeting_id_ice_servers(client):
    """Verify request without meeting_id parameter returns HTTP 400 Bad Request."""
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    res = client.get("/api/webrtc/ice-servers")
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False


def test_meeting_not_found_ice_servers(client, mock_db):
    """Verify request for non-existent meeting returns HTTP 404 Not Found."""
    meeting_id = str(ObjectId())

    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    res = client.get(f"/api/webrtc/ice-servers?meeting_id={meeting_id}")
    assert res.status_code == 404
    data = res.get_json()
    assert data["success"] is False


def test_unauthorized_participant_ice_servers_access(client, mock_db):
    """Verify uninvited participant receives HTTP 403 Forbidden."""
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin@example.com",
        "invited_participants": ["invited_p@example.com"]
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "uninvited_p@example.com"
        sess["role"] = "participant"

    res = client.get(f"/api/webrtc/ice-servers?meeting_id={meeting_id}")
    assert res.status_code == 403
    data = res.get_json()
    assert data["success"] is False


def test_unauthorized_admin_ice_servers_access(client, mock_db):
    """Verify admin of Meeting A cannot retrieve ICE servers for Meeting B."""
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin_owner@example.com",
        "invited_participants": []
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "admin_other@example.com"
        sess["role"] = "admin"

    res = client.get(f"/api/webrtc/ice-servers?meeting_id={meeting_id}")
    assert res.status_code == 403
    data = res.get_json()
    assert data["success"] is False


def test_authorized_participant_and_admin_ice_servers_access(client, mock_db):
    """Verify authorized admin and invited participant can successfully retrieve ICE configuration."""
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin_owner@example.com",
        "invited_participants": ["participant_invited@example.com"]
    })

    # Authorized Admin Access
    with client.session_transaction() as sess:
        sess["user_id"] = "admin_owner@example.com"
        sess["role"] = "admin"

    res_admin = client.get(f"/api/webrtc/ice-servers?meeting_id={meeting_id}")
    assert res_admin.status_code == 200
    data_admin = res_admin.get_json()
    assert data_admin["success"] is True
    assert "ice_servers" in data_admin
    assert any("stun:stun.l.google.com:19302" in s.get("urls", []) for s in data_admin["ice_servers"])

    # Authorized Participant Access
    with client.session_transaction() as sess:
        sess["user_id"] = "participant_invited@example.com"
        sess["role"] = "participant"

    res_part = client.get(f"/api/webrtc/ice-servers?meeting_id={meeting_id}")
    assert res_part.status_code == 200
    data_part = res_part.get_json()
    assert data_part["success"] is True
    assert "ice_servers" in data_part


def test_turn_disabled_ice_servers(monkeypatch):
    """Verify TURN_ENABLED=False returns STUN server only without TURN credentials."""
    monkeypatch.setenv("TURN_ENABLED", "False")
    monkeypatch.setenv("TURN_SERVER_URL", "turn:turn.meetiq.com:3478")
    monkeypatch.setenv("TURN_USERNAME", "secret_user")
    monkeypatch.setenv("TURN_PASSWORD", "secret_pass")

    servers = get_ice_servers_config()
    assert len(servers) == 1
    assert servers[0]["urls"] == ["stun:stun.l.google.com:19302"]


def test_turn_enabled_ice_servers(monkeypatch):
    """Verify TURN_ENABLED=True correctly includes STUN and TURN server configuration."""
    monkeypatch.setenv("TURN_ENABLED", "True")
    monkeypatch.setenv("TURN_SERVER_URL", "turn:turn.meetiq.com:3478?transport=udp,turn:turn.meetiq.com:3478?transport=tcp")
    monkeypatch.setenv("TURN_USERNAME", "meetiq_test_user")
    monkeypatch.setenv("TURN_PASSWORD", "meetiq_test_pass")

    servers = get_ice_servers_config()
    assert len(servers) == 2
    # STUN is retained
    assert servers[0]["urls"] == ["stun:stun.l.google.com:19302"]
    # TURN is appended
    assert servers[1]["urls"] == ["turn:turn.meetiq.com:3478?transport=udp", "turn:turn.meetiq.com:3478?transport=tcp"]
    assert servers[1]["username"] == "meetiq_test_user"
    assert servers[1]["credential"] == "meetiq_test_pass"


def test_stun_always_retained(monkeypatch):
    """Verify STUN server is always present even if TURN config is empty or invalid."""
    monkeypatch.setenv("TURN_ENABLED", "True")
    monkeypatch.setenv("TURN_SERVER_URL", "")

    servers = get_ice_servers_config()
    assert len(servers) >= 1
    assert "stun:stun.l.google.com:19302" in servers[0]["urls"]
