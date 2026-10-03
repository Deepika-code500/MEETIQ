# MEETIQ Participant Management

This document details the architecture, data structures, bulk import rules, filtering mechanics, authorization controls, and API contracts for Participant Management and Bulk Invitations in MEETIQ.

---

## 1. User Data Model

Participants are stored in MongoDB in the `participants` collection (`meetiq_db.participants`).

```json
{
  "_id": ObjectId("..."),
  "name": "Jane Doe",
  "email": "jane.doe@university.edu",
  "department": "CSE",
  "team": "Alpha",
  "year": "3",
  "user_id": "jane_doe",
  "eligible": true,
  "password": "<bcrypt_hash>"
}
```

### Key Attributes
- **`name`**: Full name of participant.
- **`email`**: Normalized lower-case email address (unique identifier across system).
- **`department`**: Academic or administrative department (e.g. `CSE`, `ECE`, `MECH`). Defaults to `General`.
- **`team`**: Sub-group, cohort, or team designation (e.g. `Team-A`, `Alpha`). Defaults to `General`.
- **`year`**: Academic year or level (e.g. `1`, `2`, `3`, `4`).
- **`user_id`**: Unique login handle/ID (derived from email username if unspecified).
- **`eligible`**: Boolean flag (`true` or `false`). Ineligible users (`false`) cannot be invited to meetings.

---

## 2. Excel/CSV Format

MEETIQ supports bulk participant imports via standard `.csv` and `.xlsx` (Excel) files.

### Expected Header Columns
| Column Name | Required | Description | Default / Normalization |
|---|---|---|---|
| `name` | **Yes** | Participant full name | Required |
| `email` | **Yes** | Primary email address | Lowercased & whitespace stripped |
| `department` | No | Department / Division | `General` |
| `team` | No | Team / Cohort | `General` |
| `year` | No | Year / Grade level | `1` |
| `user_id` | No | Custom User ID | Derived from email prefix |
| `eligible` | No | Eligibility flag | `true` (unless set to false/0/no) |

---

## 3. Bulk Import Workflow

1. **File Selection & Upload**: Admin selects a `.csv` or `.xlsx` file (up to 10MB) via the Admin Participants dashboard modal.
2. **In-Memory Streaming & Parsing**: The server reads the stream using `io.BytesIO` and parses CSV using `csv.DictReader` or Excel using `openpyxl.load_workbook`.
3. **Validation & Preview (`POST /api/admin/participants/import/preview`)**:
   - Checks file extension & mime type.
   - Enforces 10MB size limit.
   - Validates required fields (`name`, `email`).
   - Normalizes email addresses.
   - Identifies row-level syntax/format errors.
   - Detects duplicate rows within the uploaded file.
   - Detects duplicate users already registered in MongoDB.
   - Returns a structured preview JSON without saving to MongoDB.
4. **Admin Confirmation (`POST /api/admin/participants/import/confirm`)**: Admin reviews valid rows, selects/deselects candidates, and confirms import. Valid entries are persisted to MongoDB.

---

## 4. Validation Rules

- **Extension Check**: Rejects extensions other than `.csv`, `.xlsx`, `.xls` with `HTTP 400`.
- **File Size**: Enforces `10 * 1024 * 1024` byte limit; returns `HTTP 400` if exceeded.
- **Syntax & Email Formatting**: Validates email structure against standard regex `^[^@\s]+@[^@\s]+\.[^@\s]+$`.
- **Duplicate Prevention**:
  - Duplicate emails inside the upload stream are grouped under `duplicate_rows`.
  - Existing `email` or `user_id` entries already in MongoDB are categorized as system duplicates.
- **Atomic Preview**: Uploaded files are processed strictly in-memory and temporary buffers are garbage-collected immediately.

---

## 5. Department Filtering

Server-side department filtering is supported across both UI view routes and JSON API endpoints.

- **Query Parameter**: `department=CSE`
- **Regex Query**: Matches department case-insensitively (`^CSE$`).
- **Endpoint**: `GET /api/admin/participants/filter?department=CSE`

---

## 6. Team Filtering

Server-side team filtering operates independently or combined with department filters.

- **Query Parameter**: `team=Team-A`
- **Combination Support**:
  - `department=CSE`
  - `team=Team-A`
  - Combined: `department=CSE&team=Team-A`
- **Endpoint**: `GET /api/admin/participants/filter?department=CSE&team=Team-A`

---

## 7. Eligibility Requirement

- **Rule**: Explicitly ineligible participants (`eligible: false`) are barred from meeting invitations.
- **UI Enforce**: Ineligible participants are marked with a red `Ineligible` badge and their selection checkbox is disabled on the invitation interface.
- **Backend Enforce**: The bulk-invite API (`POST /api/admin/meetings/<meeting_id>/participants/bulk-invite`) filters out any selected candidate where `eligible == False`.

---

## 8. Participant Selection

Admins can select participants through multiple modes:
1. **Manual Select**: Checkboxes on the participant management dashboard or meeting invitation list.
2. **Bulk Filter Select**: Applying department, team, and eligibility filters, then choosing **Select All Visible**.
3. **Excel/CSV Bulk Import**: Importing structured candidate lists.

---

## 9. Meeting Invitations

When participants are invited to a meeting:
- Meeting document's `invited_participants` array is updated via MongoDB `$addToSet` (preventing duplicates).
- System notifications are created in `meetiq_db.notifications`.
- Email invitations containing details and meeting access link are dispatched.
- Invited participants gain authorization to join live WebRTC sessions; uninvited users are blocked (`HTTP 403`).

---

## 10. Authorization & Security

- **Admin Requirement**: All participant management routes (`/admin/participants`, `/api/admin/participants/*`, `/api/admin/meetings/<meeting_id>/participants/bulk-invite`) enforce `session["role"] == "admin"`.
- **Unauthenticated Access**: Unauthenticated requests return `HTTP 401 Unauthorized`.
- **Unauthorized Participant Access**: Non-admin roles attempting admin endpoints receive `HTTP 401` / `HTTP 403 Forbidden`.
- **File System Safety**: File paths are sanitized using `secure_filename`. Temporary upload streams are processed in-memory.

---

## 11. Error Handling & API Responses

| Endpoint | Method | Status | Description |
|---|---|---|---|
| `/api/admin/participants/filter` | GET | 200 | Returns filtered participant list |
| `/api/admin/participants/filter` | GET | 401 | Unauthenticated or non-admin request |
| `/api/admin/participants/import/preview` | POST | 200 | Returns import preview stats & valid/invalid rows |
| `/api/admin/participants/import/preview` | POST | 400 | Invalid file format, size >10MB, or malformed data |
| `/api/admin/participants/import/confirm` | POST | 200 | Confirms and persists valid rows to DB |
| `/api/admin/meetings/<id>/participants/bulk-invite` | POST | 200 | Bulk invites eligible participants |

---

## 12. Usage Examples

### Preview CSV Import
```bash
curl -X POST http://localhost:5000/api/admin/participants/import/preview \
  -b "session=YOUR_ADMIN_SESSION" \
  -F "file=@participants.csv"
```

### Filter Participants by Department & Team
```bash
curl -X GET "http://localhost:5000/api/admin/participants/filter?department=CSE&team=Team-A&eligible_only=true" \
  -b "session=YOUR_ADMIN_SESSION"
```

---

## 13. Known Limitations

- **Live TURN Relay**: NAT traversal is supported via STUN/TURN configuration, but live TURN relay servers are unverified in automated CI without external TURN infrastructure.
- **Real Browser E2E**: End-to-end multi-browser video streams are verified via unit & API integration suites; browser UI rendering relies on standard DOM testing.
