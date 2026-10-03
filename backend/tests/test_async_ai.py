import pytest
import os
import sys
import io
from bson.objectid import ObjectId
import mongomock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, ai_jobs_collection
from celery_app import celery, async_generate_summary_task


@pytest.fixture
def mock_db(monkeypatch):
    client = mongomock.MongoClient()
    db = client["meetiq_test"]
    meetings_col = db["meetings"]
    participants_col = db["participants"]
    ai_jobs_col = db["ai_jobs"]

    monkeypatch.setattr("app.meetings_collection", meetings_col)
    monkeypatch.setattr("app.participants_collection", participants_col)
    monkeypatch.setattr("app.ai_jobs_collection", ai_jobs_col)

    return {
        "meetings": meetings_col,
        "participants": participants_col,
        "ai_jobs": ai_jobs_col
    }


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key"
    with app.test_client() as client:
        yield client


def test_celery_initialization():
    """Verify Celery app instance is created with expected configuration."""
    assert celery is not None
    assert celery.main == "meetiq"


def test_async_summary_job_creation_and_polling(client, mock_db, monkeypatch):
    """Verify HTTP summary endpoint creates job and GET /api/ai/jobs/<id> returns status."""
    meetings_col = mock_db["meetings"]
    ai_jobs_col = mock_db["ai_jobs"]

    meeting_id = str(ObjectId())
    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "title": "Async Test Meeting",
        "created_by_email": "admin@example.com",
        "invited_participants": ["p1@example.com"],
        "transcript_segments": [{"participant_name": "p1", "text": "Discussion topic"}]
    })

    # Mock Celery delay to avoid requiring live Redis broker in unit test
    def mock_delay(job_id, meeting_id_arg):
        ai_jobs_col.update_one(
            {"job_id": job_id},
            {"$set": {"status": "COMPLETED", "result": {"discussion_points": ["Discussion topic"]}}}
        )
        return True

    monkeypatch.setattr("celery_app.async_generate_summary_task.delay", mock_delay)

    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    # 1. Enqueue job
    res = client.post(f"/api/meeting-summary/{meeting_id}")
    assert res.status_code in [200, 202]
    data = res.get_json()
    assert data["success"] is True
    job_id = data["job_id"]

    # 2. Poll job status
    res_status = client.get(f"/api/ai/jobs/{job_id}")
    assert res_status.status_code == 200
    status_data = res_status.get_json()
    assert status_data["success"] is True
    assert status_data["job_id"] == job_id
    assert status_data["status"] == "COMPLETED"
    assert "discussion_points" in status_data["result"]


def test_job_status_unauthorized_and_cross_user_protection(client, mock_db):
    """Verify unauthorized users or non-invited participants cannot access job status."""
    meetings_col = mock_db["meetings"]
    ai_jobs_col = mock_db["ai_jobs"]

    meeting_id = str(ObjectId())
    job_id = "job_private_123"

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin_owner@example.com",
        "invited_participants": ["participant_invited@example.com"]
    })

    ai_jobs_col.insert_one({
        "job_id": job_id,
        "meeting_id": meeting_id,
        "type": "summary",
        "status": "COMPLETED",
        "created_by": "admin_owner@example.com"
    })

    # Unauthenticated check
    res_unauth = client.get(f"/api/ai/jobs/{job_id}")
    assert res_unauth.status_code == 401

    # Unauthorized participant check
    with client.session_transaction() as sess:
        sess["user_id"] = "participant_uninvited@example.com"
        sess["role"] = "participant"

    res_cross = client.get(f"/api/ai/jobs/{job_id}")
    assert res_cross.status_code == 403


def test_ai_failure_handling_and_fallback(client, mock_db, monkeypatch):
    """Verify worker and API handle AI service failure without crashing or returning fake summary."""
    meetings_col = mock_db["meetings"]
    ai_jobs_col = mock_db["ai_jobs"]

    meeting_id = str(ObjectId())
    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin@example.com",
        "invited_participants": [],
        "transcript_segments": [{"participant_name": "admin", "text": "Fail test"}]
    })

    def mock_broken_summary(transcript):
        raise RuntimeError("Ollama Timeout")

    monkeypatch.setattr("app.generate_summary", mock_broken_summary)
    monkeypatch.setenv("AI_ASYNC_ENABLED", "False")

    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    res = client.post(f"/api/meeting-summary/{meeting_id}")
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False
    assert "Ollama Timeout" in data["message"]


def test_async_whisper_job_creation_and_polling(client, mock_db, monkeypatch, tmp_path):
    """Verify audio upload creates a Whisper job and returns PENDING status in async mode."""
    meetings_col = mock_db["meetings"]
    ai_jobs_col = mock_db["ai_jobs"]

    meeting_id = str(ObjectId())
    participant_id = "p_async@example.com"

    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "title": "Whisper Async Test",
        "created_by_email": "admin@example.com",
        "invited_participants": [participant_id],
        "status": "live",
        "transcript_segments": []
    })

    def mock_whisper_delay(job_id, meeting_id_arg, participant_id_arg, chunk_path_arg, participant_name_arg):
        ai_jobs_col.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": "COMPLETED",
                    "result": {
                        "success": True,
                        "transcript": "Test audio transcript",
                        "toxicity_score": 0.0,
                        "action": "none",
                        "message": "Processed"
                    }
                }
            }
        )
        return True

    monkeypatch.setattr("celery_app.async_transcribe_audio_task.delay", mock_whisper_delay)

    with client.session_transaction() as sess:
        sess["user_id"] = participant_id
        sess["user_name"] = "Async Participant"
        sess["role"] = "participant"

    audio_file = (io.BytesIO(b"fake audio data"), "test.webm")
    res = client.post(
        "/api/meeting-audio/realtime",
        data={"meeting_id": meeting_id, "audio": audio_file},
        content_type="multipart/form-data"
    )

    assert res.status_code == 202
    data = res.get_json()
    assert data["success"] is True
    assert data["async"] is True
    job_id = data["job_id"]

    # Check status endpoint
    res_status = client.get(f"/api/ai/jobs/{job_id}")
    assert res_status.status_code == 200
    status_data = res_status.get_json()
    assert status_data["success"] is True
    assert status_data["job_id"] == job_id
    assert status_data["status"] == "COMPLETED"
    assert status_data["result"]["transcript"] == "Test audio transcript"


def test_redis_unavailable_fallback(client, mock_db, monkeypatch):
    """Verify application falls back to synchronous processing if Redis is unreachable when enqueuing."""
    meetings_col = mock_db["meetings"]

    meeting_id = str(ObjectId())
    meetings_col.insert_one({
        "_id": ObjectId(meeting_id),
        "created_by_email": "admin@example.com",
        "invited_participants": [],
        "transcript_segments": [{"participant_name": "admin", "text": "Sync Fallback"}]
    })

    def mock_failing_delay(job_id, meeting_id_arg):
        raise ConnectionError("Redis connection refused")

    monkeypatch.setattr("celery_app.async_generate_summary_task.delay", mock_failing_delay)
    monkeypatch.setattr("app.generate_summary", lambda text: {"discussion_points": ["Sync Fallback"], "decisions": [], "action_items": []})

    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    res = client.post(f"/api/meeting-summary/{meeting_id}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["status"] == "COMPLETED"


def test_job_status_cross_meeting_protection(client, mock_db):
    """Verify admin or participant of Meeting A cannot view job status of Meeting B."""
    meetings_col = mock_db["meetings"]
    ai_jobs_col = mock_db["ai_jobs"]

    meeting_a_id = str(ObjectId())
    meeting_b_id = str(ObjectId())
    job_b_id = "job_meeting_b_999"

    meetings_col.insert_one({
        "_id": ObjectId(meeting_a_id),
        "created_by_email": "admin_a@example.com",
        "invited_participants": ["participant_a@example.com"]
    })

    meetings_col.insert_one({
        "_id": ObjectId(meeting_b_id),
        "created_by_email": "admin_b@example.com",
        "invited_participants": ["participant_b@example.com"]
    })

    ai_jobs_col.insert_one({
        "job_id": job_b_id,
        "meeting_id": meeting_b_id,
        "type": "summary",
        "status": "COMPLETED",
        "created_by": "admin_b@example.com"
    })

    # Admin A attempts to access Meeting B's job
    with client.session_transaction() as sess:
        sess["user_id"] = "admin_a@example.com"
        sess["role"] = "admin"

    res_admin_a = client.get(f"/api/ai/jobs/{job_b_id}")
    assert res_admin_a.status_code == 403

    # Participant A attempts to access Meeting B's job
    with client.session_transaction() as sess:
        sess["user_id"] = "participant_a@example.com"
        sess["role"] = "participant"

    res_part_a = client.get(f"/api/ai/jobs/{job_b_id}")
    assert res_part_a.status_code == 403

