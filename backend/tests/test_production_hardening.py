# =========================================================
# MeetIQ - Production Hardening & Security Tests
# =========================================================

import pytest
import os
import sys
import io
import yaml
from pathlib import Path
from bson.objectid import ObjectId
import mongomock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app


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


def test_health_and_readiness_endpoints(client):
    """Verify GET /health and GET /ready endpoints return safe status metadata."""
    res_health = client.get("/health")
    assert res_health.status_code in [200, 503]
    health_data = res_health.get_json()
    assert "status" in health_data
    assert "service" in health_data

    res_ready = client.get("/ready")
    assert res_ready.status_code in [200, 503]
    ready_data = res_ready.get_json()
    assert "status" in ready_data
    assert "database" in ready_data


def test_unsupported_audio_extension_rejection(client, mock_db):
    """Verify uploaded file with invalid extension (e.g. .exe or .sh) is rejected with HTTP 400."""
    meetings_col = mock_db["meetings"]
    meeting_id = str(ObjectId())
    participant_id = "p_test@example.com"

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin@example.com",
        "invited_participants": [participant_id],
        "status": "live"
    })

    with client.session_transaction() as sess:
        sess["user_id"] = participant_id
        sess["role"] = "participant"

    malicious_file = (io.BytesIO(b"malicious script"), "payload.exe")
    res = client.post(
        "/api/meeting-audio/realtime",
        data={"meeting_id": meeting_id, "audio": malicious_file},
        content_type="multipart/form-data"
    )

    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False
    assert "Unsupported audio file format" in data["message"]


def test_max_content_length_config():
    """Verify MAX_CONTENT_LENGTH is set to 50MB in Flask app config."""
    assert app.config.get("MAX_CONTENT_LENGTH") == 50 * 1024 * 1024


def test_docker_compose_file_validity():
    """Verify docker-compose.yml exists, is non-empty, and parses as valid YAML."""
    compose_path = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"
    assert compose_path.exists()

    with open(compose_path, "r") as f:
        data = yaml.safe_load(f)

    assert "services" in data
    assert "web" in data["services"]
    assert "celery_worker" in data["services"]
    assert "redis" in data["services"]
    assert "mongodb" in data["services"]


def test_dockerfile_exists_and_secure():
    """Verify Dockerfile exists and contains non-root user instructions."""
    dockerfile_path = Path(__file__).resolve().parent.parent.parent / "Dockerfile"
    assert dockerfile_path.exists()

    content = dockerfile_path.read_text()
    assert "FROM python:" in content
    assert "USER meetiq" in content
    assert "HEALTHCHECK" in content
