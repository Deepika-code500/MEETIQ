"""
Tests for Milestone 5 — Participant Management & Bulk Invitations.
Covering Excel/CSV parsing, file validation, department/team/eligibility filtering,
bulk import confirmation, security authorization, and meeting invitations.
"""

import io
import csv
import sys
import os
import pytest
import openpyxl
import mongomock
from bson.objectid import ObjectId

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, create_participant


@pytest.fixture
def mock_db(monkeypatch):
    """Fixture supplying isolated in-memory mongomock database collections."""
    client = mongomock.MongoClient()
    db = client["meetiq_test_pm"]
    meetings_col = db["meetings"]
    participants_col = db["participants"]
    notifications_col = db["notifications"]

    monkeypatch.setattr("app.meetings_collection", meetings_col)
    monkeypatch.setattr("app.participants_collection", participants_col)
    monkeypatch.setattr("app.notifications_collection", notifications_col)
    try:
        import models.notification_model
        monkeypatch.setattr("models.notification_model.notifications_collection", notifications_col)
    except Exception:
        pass

    return {
        "meetings": meetings_col,
        "participants": participants_col,
        "notifications": notifications_col
    }


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key"
    with app.test_client() as client:
        yield client


def create_csv_bytes(rows, headers=None):
    """Helper to create UTF-8 encoded CSV bytes."""
    if headers is None:
        headers = ["name", "email", "department", "team", "year", "user_id", "eligible"]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def create_xlsx_bytes(rows, headers=None):
    """Helper to create in-memory .xlsx file bytes."""
    if headers is None:
        headers = ["name", "email", "department", "team", "year", "user_id", "eligible"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


# -------------------------------------------------------------------------
# 1. Admin can import valid CSV
# -------------------------------------------------------------------------
def test_import_valid_csv(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    csv_data = create_csv_bytes([
        {"name": "Alice Smith", "email": "alice_csv@example.com", "department": "CSE", "team": "Team-A", "year": "2", "user_id": "alice_csv", "eligible": "true"}
    ])

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(csv_data), "valid_participants.csv")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["preview"]["valid_count"] == 1
    assert data["preview"]["valid_rows"][0]["email"] == "alice_csv@example.com"


# -------------------------------------------------------------------------
# 2. Admin can import valid XLSX
# -------------------------------------------------------------------------
def test_import_valid_xlsx(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    xlsx_data = create_xlsx_bytes([
        {"name": "Bob Jones", "email": "bob_xlsx@example.com", "department": "ECE", "team": "Team-B", "year": "3", "user_id": "bob_xlsx", "eligible": "true"}
    ])

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(xlsx_data), "valid_participants.xlsx")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["preview"]["valid_count"] == 1
    assert data["preview"]["valid_rows"][0]["email"] == "bob_xlsx@example.com"


# -------------------------------------------------------------------------
# 3. Invalid file type rejected
# -------------------------------------------------------------------------
def test_import_invalid_file_type(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    txt_data = b"Some random text content"

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(txt_data), "participants.txt")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "Unsupported file format" in data["message"]


# -------------------------------------------------------------------------
# 4. Oversized file rejected
# -------------------------------------------------------------------------
def test_import_oversized_file(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    huge_data = b"name,email\n" + (b"User,user@example.com\n" * 500000)

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(huge_data), "huge.csv")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "exceeds the 10MB size limit" in data["message"]


# -------------------------------------------------------------------------
# 5. Missing required column rejected
# -------------------------------------------------------------------------
def test_import_missing_required_column(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    csv_data = create_csv_bytes(
        [{"name": "No Email Person", "email": "", "department": "CSE"}],
        headers=["name", "email", "department"]
    )

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(csv_data), "missing_email.csv")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["preview"]["invalid_count"] == 1
    assert "Missing required field 'email'" in data["preview"]["invalid_rows"][0]["errors"][0]


# -------------------------------------------------------------------------
# 6. Invalid email rejected
# -------------------------------------------------------------------------
def test_import_invalid_email_format(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    csv_data = create_csv_bytes([
        {"name": "Bad Email User", "email": "invalid-email-address", "department": "CSE"}
    ])

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(csv_data), "bad_email.csv")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["preview"]["invalid_count"] == 1
    assert "Invalid email format" in data["preview"]["invalid_rows"][0]["errors"][0]


# -------------------------------------------------------------------------
# 7. Duplicate rows in file handled
# -------------------------------------------------------------------------
def test_import_duplicate_rows_in_file(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    csv_data = create_csv_bytes([
        {"name": "User One", "email": "dupe@example.com", "department": "CSE"},
        {"name": "User One Copy", "email": "dupe@example.com", "department": "CSE"}
    ])

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(csv_data), "dupes.csv")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["preview"]["valid_count"] == 1
    assert data["preview"]["duplicate_count"] == 1


# -------------------------------------------------------------------------
# 8. Existing users in system handled
# -------------------------------------------------------------------------
def test_import_existing_system_user_handled(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    mock_db["participants"].insert_one({
        "name": "Existing Person",
        "email": "existing@example.com",
        "user_id": "existing_user"
    })

    csv_data = create_csv_bytes([
        {"name": "Existing Person", "email": "existing@example.com", "department": "CSE", "user_id": "existing_user"}
    ])

    response = client.post(
        "/api/admin/participants/import/preview",
        data={"file": (io.BytesIO(csv_data), "existing.csv")},
        content_type="multipart/form-data"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["preview"]["valid_count"] == 0
    assert data["preview"]["duplicate_count"] == 1
    assert "already registered" in data["preview"]["duplicate_rows"][0]["reason"]


# -------------------------------------------------------------------------
# 9. Department filtering
# -------------------------------------------------------------------------
def test_department_filtering(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    mock_db["participants"].insert_many([
        {"name": "Dev 1", "email": "dev1@example.com", "department": "CSE", "team": "Alpha", "user_id": "p1", "eligible": True},
        {"name": "Dev 2", "email": "dev2@example.com", "department": "ECE", "team": "Beta", "user_id": "p2", "eligible": True}
    ])

    response = client.get("/api/admin/participants/filter?department=CSE")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1
    assert data["participants"][0]["user_id"] == "p1"


# -------------------------------------------------------------------------
# 10. Team filtering
# -------------------------------------------------------------------------
def test_team_filtering(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    mock_db["participants"].insert_many([
        {"name": "Dev 1", "email": "dev1@example.com", "department": "CSE", "team": "Alpha", "user_id": "p1", "eligible": True},
        {"name": "Dev 2", "email": "dev2@example.com", "department": "CSE", "team": "Beta", "user_id": "p2", "eligible": True}
    ])

    response = client.get("/api/admin/participants/filter?team=Beta")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1
    assert data["participants"][0]["user_id"] == "p2"


# -------------------------------------------------------------------------
# 11. Department + Team filtering combined
# -------------------------------------------------------------------------
def test_department_and_team_filtering_combined(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    mock_db["participants"].insert_many([
        {"name": "Dev 1", "email": "d1@example.com", "department": "CSE", "team": "Alpha", "user_id": "p1", "eligible": True},
        {"name": "Dev 2", "email": "d2@example.com", "department": "CSE", "team": "Beta", "user_id": "p2", "eligible": True},
        {"name": "Dev 3", "email": "d3@example.com", "department": "ECE", "team": "Alpha", "user_id": "p3", "eligible": True}
    ])

    response = client.get("/api/admin/participants/filter?department=CSE&team=Alpha")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1
    assert data["participants"][0]["user_id"] == "p1"


# -------------------------------------------------------------------------
# 12. Eligibility filtering
# -------------------------------------------------------------------------
def test_eligibility_filtering(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    mock_db["participants"].insert_many([
        {"name": "Eligible User", "email": "el@example.com", "department": "CSE", "team": "Alpha", "user_id": "p1", "eligible": True},
        {"name": "Ineligible User", "email": "inel@example.com", "department": "CSE", "team": "Alpha", "user_id": "p2", "eligible": False}
    ])

    response = client.get("/api/admin/participants/filter?eligible_only=true")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1
    assert data["participants"][0]["user_id"] == "p1"


# -------------------------------------------------------------------------
# 13. Unauthorized participant blocked
# -------------------------------------------------------------------------
def test_unauthorized_participant_blocked(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "participant@example.com"
        sess["role"] = "participant"

    response = client.get("/api/admin/participants/filter")
    assert response.status_code in [401, 403]

    response_import = client.post("/api/admin/participants/import/confirm", json={"participants": []})
    assert response_import.status_code in [401, 403]


# -------------------------------------------------------------------------
# 14. Unauthenticated request blocked
# -------------------------------------------------------------------------
def test_unauthenticated_request_blocked(client, mock_db):
    response = client.get("/api/admin/participants/filter")
    assert response.status_code == 401

    response_import = client.post("/api/admin/participants/import/preview")
    assert response_import.status_code == 401


# -------------------------------------------------------------------------
# 15. Invitation creation
# -------------------------------------------------------------------------
def test_invitation_creation(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    meeting_obj_id = ObjectId()
    mock_db["meetings"].insert_one({
        "_id": meeting_obj_id,
        "title": "Test Meeting",
        "created_by_email": "admin@example.com",
        "invited_participants": []
    })

    mock_db["participants"].insert_one({
        "name": "Invited User",
        "email": "invited@example.com",
        "user_id": "p_invite_1",
        "eligible": True
    })

    response = client.post(
        f"/api/admin/meetings/{str(meeting_obj_id)}/participants/bulk-invite",
        json={"participant_ids": ["p_invite_1"]}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["invited_added"] == 1

    updated_meeting = mock_db["meetings"].find_one({"_id": meeting_obj_id})
    assert "p_invite_1" in updated_meeting["invited_participants"]


# -------------------------------------------------------------------------
# 16. Duplicate invitation prevention
# -------------------------------------------------------------------------
def test_duplicate_invitation_prevention(client, mock_db):
    with client.session_transaction() as sess:
        sess["user_id"] = "admin@example.com"
        sess["role"] = "admin"

    meeting_obj_id = ObjectId()
    mock_db["meetings"].insert_one({
        "_id": meeting_obj_id,
        "title": "Test Meeting",
        "created_by_email": "admin@example.com",
        "invited_participants": ["p_already_invited"]
    })

    mock_db["participants"].insert_one({
        "name": "Already Invited User",
        "email": "already@example.com",
        "user_id": "p_already_invited",
        "eligible": True
    })

    response = client.post(
        f"/api/admin/meetings/{str(meeting_obj_id)}/participants/bulk-invite",
        json={"participant_ids": ["p_already_invited"]}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["invited_added"] == 0

    updated_meeting = mock_db["meetings"].find_one({"_id": meeting_obj_id})
    assert updated_meeting["invited_participants"].count("p_already_invited") == 1


# -------------------------------------------------------------------------
# 17. Invited participant access allowed
# -------------------------------------------------------------------------
def test_invited_participant_access_allowed(client, mock_db):
    meeting_obj_id = ObjectId()
    mock_db["meetings"].insert_one({
        "_id": meeting_obj_id,
        "title": "Restricted Meeting",
        "status": "live",
        "created_by_email": "admin@example.com",
        "invited_participants": ["invited_p_123"]
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "invited_p_123"
        sess["role"] = "participant"

    response = client.get(f"/participant/meetings/{str(meeting_obj_id)}")
    assert response.status_code == 200


# -------------------------------------------------------------------------
# 18. Non-invited participant rejection
# -------------------------------------------------------------------------
def test_non_invited_participant_rejection(client, mock_db):
    meeting_obj_id = ObjectId()
    mock_db["meetings"].insert_one({
        "_id": meeting_obj_id,
        "title": "Private Meeting",
        "status": "live",
        "created_by_email": "admin@example.com",
        "invited_participants": ["some_other_user"]
    })

    with client.session_transaction() as sess:
        sess["user_id"] = "uninvited_p_456"
        sess["role"] = "participant"

    response = client.get(f"/participant/meetings/{str(meeting_obj_id)}")
    assert response.status_code in [302, 403]
