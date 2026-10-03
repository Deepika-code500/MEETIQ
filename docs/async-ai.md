# MEETIQ Asynchronous AI Processing

This document describes the asynchronous AI task queue architecture implemented for the **MEETIQ** platform using Celery and Redis.

---

## 1. Problem Being Solved

Heavy AI operations (such as multi-second Ollama LLM transcript summarization and OpenAI Whisper audio transcription) previously executed synchronously inside Flask HTTP request handler threads. This caused HTTP timeouts, delayed response times for web clients, and risked blocking concurrent Socket.IO meeting communication.

---

## 2. Current Synchronous Flow (Legacy / Fallback)

```text
HTTP Request (POST /api/meeting-summary/<id>)
   │
   ▼
Flask Request Handler Thread
   │
   ├── (Blocking) Ollama HTTP Request / Whisper Inference
   │
   ▼
Flask Sends HTTP 200 Response
```

---

## 3. New Architecture

```text
HTTP Request (POST /api/meeting-summary/<id>)
   │
   ▼
Flask Request Handler
   ├── Creates Job Record in MongoDB (ai_jobs: status = PENDING)
   ├── Enqueues Task to Redis Broker
   └── Returns HTTP 202 Accepted {"job_id": "job_123", "status": "PENDING"}
   │
   ▼
Redis Broker (redis://localhost:6379/0)
   │
   ▼
Celery Worker Process (celery -A backend.celery_app.celery worker)
   ├── Status -> PROCESSING
   ├── Performs Ollama LLM / Whisper AI Inference
   └── Saves Summary to MongoDB & Updates Job Status -> COMPLETED / FAILED
   │
   ▼
Client Polls Job Status (GET /api/ai/jobs/<job_id>)
```

---

## 4. Celery Configuration

Centralized in [`backend/celery_app.py`](file:///c:/Users/DELL/OneDrive/Desktop/major_project/MEETIQ/backend/celery_app.py):

- **Broker**: `CELERY_BROKER_URL` (default `redis://localhost:6379/0`)
- **Result Backend**: `CELERY_RESULT_BACKEND` (default `redis://localhost:6379/0`)
- **Serializer**: `json`
- **Timezone**: `UTC`

---

## 5. Redis Configuration

Configured via environment variables in `.env`:

```env
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
AI_ASYNC_ENABLED=True
```

---

## 6. Whisper Worker

Heavy audio transcription is offloaded to the Celery worker task (`async_transcribe_audio_task`). Audio chunk files saved to `ai-services/speech-to-text/recordings/` are processed asynchronously without blocking web requests or WebRTC room signaling.

---

## 7. Ollama Worker

Meeting summarization is offloaded to the Celery task (`async_generate_summary_task`). The worker reads meeting transcript segments from MongoDB, calls the local Ollama endpoint (`http://localhost:11434`), validates the JSON output format, saves the summary to the meeting document, and updates the job record.

---

## 8. Toxicity Processing

Real-time chat moderation retains immediate, low-latency text evaluation. Voice toxicity analysis and transcript segment processing run in background Celery tasks, emitting warning/mute events to participant browser sockets via Socket.IO.

---

## 9. Job Lifecycle

```text
    PENDING ──► PROCESSING ──► COMPLETED
                   │
                   └──► FAILED
```

- **PENDING**: Job enqueued by Flask HTTP request.
- **PROCESSING**: Celery worker picks up job and begins AI inference.
- **COMPLETED**: AI inference finished; output saved to MongoDB meeting record.
- **FAILED**: Exception or AI model error occurred; error message stored in job record.

---

## 10. API Endpoints

### Enqueue Summary Job
- **Endpoint**: `POST /api/meeting-summary/<meeting_id>`
- **Response (202 Accepted)**:
  ```json
  {
    "success": true,
    "async": true,
    "job_id": "job_a1b2c3d4",
    "status": "PENDING",
    "message": "AI summary generation enqueued successfully."
  }
  ```

### Query Job Status
- **Endpoint**: `GET /api/ai/jobs/<job_id>`
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "job_id": "job_a1b2c3d4",
    "meeting_id": "67a1b2c3d4e5f67890123456",
    "type": "summary",
    "status": "COMPLETED",
    "result": {
      "discussion_points": ["Point 1", "Point 2"],
      "decisions": ["Decision 1"],
      "action_items": ["Action 1"]
    },
    "error": null,
    "created_at": "2026-10-01T19:30:00.000000",
    "completed_at": "2026-10-01T19:30:05.123456"
  }
  ```

---

## 11. Worker Startup

Run Celery worker process:

```bash
celery -A backend.celery_app.celery worker --loglevel=info
```

---

## 12. Local Development

1. Start Redis server on `localhost:6379`.
2. Run Celery worker: `celery -A backend.celery_app.celery worker --loglevel=info`
3. Run Flask backend: `python backend/app.py` or `gunicorn -c gunicorn.conf.py backend.app:app`

---

## 13. Failure Handling

- **Redis Offline / Enqueue Failure**: If Redis is unreachable during HTTP request, Flask logs a warning and falls back cleanly to synchronous generation or returns an HTTP 503 error without crashing.
- **Ollama Offline**: Celery worker catches HTTP error and marks job status as `FAILED` with descriptive error message.
- **No Fake Summaries**: Never fabricates fake summaries or fake scores when AI services fail.

---

## 14. Security

- **Authentication**: `GET /api/ai/jobs/<job_id>` requires valid admin or participant session.
- **Job Authorization**: Verifies user is admin owner or invited participant of the associated meeting before returning job details. Cross-user or cross-meeting job polling is blocked with HTTP 403 Forbidden.

---

## 15. Known Limitations

- **Celery Windows Worker Limit**: Celery worker processes on Windows OS require pool type `--pool=solo` or `--pool=threads` if multiprocessing fork is unsupported by current Python version.
