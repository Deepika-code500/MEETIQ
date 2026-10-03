# MEETIQ Staging Deployment

This document provides the complete staging deployment guide for the **MEETIQ** platform. It details deployment architecture, environment configurations, WSGI process management, database connections, AI service dependencies, and verification procedures.

---

## 1. Deployment Architecture

```text
       ┌──────────────────────────────────────────────────────────┐
       │             Client Browsers (WebRTC / Socket.IO)          │
       └────────────────────────────┬─────────────────────────────┘
                                    │
                                    ▼
       ┌──────────────────────────────────────────────────────────┐
       │  Reverse Proxy (Nginx / Cloud Load Balancer) [Port 80/443]│
       └────────────────────────────┬─────────────────────────────┘
                                    │ (Proxy Pass WSS & HTTP)
                                    ▼
       ┌──────────────────────────────────────────────────────────┐
       │         Gunicorn WSGI Server + Eventlet Worker           │
       │                   [Port 5000]                            │
       │        (gunicorn -c gunicorn.conf.py backend.app:app)    │
       └─────┬──────────────────────┬──────────────────────┬──────┘
             │                      │                      │
             ▼                      ▼                      ▼
┌──────────────────────────┐ ┌──────────────┐ ┌────────────────────────┐
│ Staging MongoDB Cluster  │ │ SMTP Mail    │ │ AI Subsystem Drivers   │
│ • MongoDB Atlas / URI    │ │ Server       │ │ • Whisper (STT)        │
│ • PyMongo 4.0+ Driver    │ └──────────────┘ │ • Ollama API (:11434)  │
└──────────────────────────┘                  │ • MediaPipe Landmarker │
                                              │ • Detoxify & Librosa   │
                                              └────────────────────────┘
```

---

## 2. Required Environment Variables

The staging application loads runtime settings via environment variables (or local `.env` file via `python-dotenv`). Never commit plain `.env` files with credentials to repository history.

| Environment Variable | Description | Staging / Production Example |
| :--- | :--- | :--- |
| `FLASK_ENV` | Application environment state | `production` or `staging` |
| `SECRET_KEY` | Flask session secret key | `str` (Generate via `openssl rand -hex 32`) |
| `SESSION_COOKIE_SECURE` | Enable secure HTTPS-only cookies | `True` (when SSL/TLS active) |
| `SESSION_COOKIE_SAMESITE` | SameSite cookie policy | `Lax` or `Strict` |
| `SESSION_COOKIE_HTTPONLY` | Prevent client-side JS cookie access | `True` |
| `MONGODB_URI` | Staging MongoDB connection string | `mongodb+srv://user:pass@cluster.mongodb.net/meetiq` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed HTTP/WSS origins | `https://staging.meetiq.com,http://localhost:5000` |
| `MAIL_SERVER` | SMTP host for email invitations | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USE_TLS` | Enable TLS encryption | `True` |
| `MAIL_USERNAME` | SMTP account email | `notifications@meetiq.com` |
| `MAIL_PASSWORD` | SMTP app password | `[EXTERNALIZED_APP_PASSWORD]` |
| `MAIL_DEFAULT_SENDER` | Default sender email address | `MeetIQ <notifications@meetiq.com>` |
| `OLLAMA_API_URL` | Ollama service endpoint URL | `http://localhost:11434` or remote Ollama IP |
| `SUMMARIZER_MODEL` | Ollama model identifier | `llama3.2:3b` or `llama3` |

---

## 3. MongoDB Configuration

1. **Connection String**: The backend connects using `MONGODB_URI` via PyMongo (`pymongo.MongoClient`).
2. **Database Ping**: Connection health is checked on startup (`client.admin.command('ping')`) with a 2000ms server selection timeout.
3. **Automatic Index Creation**: Safe index creation runs on application startup:
   - `db.admins.create_index("email", unique=True)`
   - `db.participants.create_index("email", unique=True)`
   - `db.meetings.create_index("created_by_email")`
   - `db.meetings.create_index("status")`
   - `db.attendance.create_index([("meeting_id", 1), ("participant_id", 1)])`
4. **Fault Tolerance**: Database disconnects are caught by application error boundaries returning clean JSON 503 error responses without exposing raw database connection strings or credentials.

---

## 4. AI Service Requirements

| AI Component | Service / Package | Resource Requirements | Staging Fallback / Degradation Behavior |
| :--- | :--- | :--- | :--- |
| **Speech-to-Text** | OpenAI Whisper (`base`) | 2GB+ VRAM / RAM, `ffmpeg` | If Whisper library or audio drivers fail, real-time chunk API returns status message without interrupting meeting call. |
| **Summarization** | Local Ollama API | 4GB+ RAM, Ollama service on `:11434` | If Ollama is offline, summary endpoints return `{"success": False, "message": "AI summary service unavailable"}` without fabricating output. |
| **Toxicity Moderation**| Detoxify & Librosa | PyTorch / Librosa CPU dependencies | If toxicity module is missing, chat and audio stream proceed cleanly with toxicity score set to `0.0`. |
| **Attention Tracking** | MediaPipe Landmarker | `face_landmarker_v2_with_blendshapes.task` | Standalone script degrades gracefully if webcam/video input is unreadable. |
| **Task Queue & Broker**| Redis + Celery Worker | Redis server (port 6379), Celery 5.3+ | Staging server requires running Redis broker (`CELERY_BROKER_URL`) and Celery worker process (`celery -A backend.celery_app.celery worker --loglevel=info`) alongside Flask/Gunicorn. If Redis is down, Flask safely falls back to synchronous AI processing or 503 response. |

---

## 5. Gunicorn Configuration

The project includes a production/staging Gunicorn configuration file [`gunicorn.conf.py`](file:///c:/Users/DELL/OneDrive/Desktop/major_project/MEETIQ/gunicorn.conf.py):

```python
bind = "0.0.0.0:5000"
workers = 1
worker_class = "eventlet"
keepalive = 5
timeout = 120
graceful_timeout = 30
loglevel = "info"
accesslog = "-"
errorlog = "-"
```

*Note: `worker_class = "eventlet"` is required for Flask-SocketIO asynchronous WebSocket connection management. Running single worker process (`workers = 1`) ensures Socket.IO in-memory room registries remain synchronized unless a Redis message queue adapter is added.*

---

## 6. Startup Command

- **Staging / Production WSGI Server**:
  ```bash
  gunicorn -c gunicorn.conf.py backend.app:app
  ```
- **PaaS Deployment (Procfile)**:
  ```text
  web: gunicorn -c gunicorn.conf.py backend.app:app
  ```

---

## 7. Health Check

MeetIQ provides a dedicated health monitoring endpoint:

- **Endpoint**: `GET /health`
- **Successful Response (200 OK)**:
  ```json
  {
    "status": "healthy",
    "service": "MeetIQ Virtual Meeting Platform",
    "database": "connected",
    "timestamp": "2026-10-01T19:28:56.159647"
  }
  ```
- **Degraded Response (503 Service Unavailable)**:
  ```json
  {
    "status": "degraded",
    "service": "MeetIQ Virtual Meeting Platform",
    "database": "disconnected",
    "timestamp": "2026-10-01T19:28:56.159647"
  }
  ```

---

## 8. CORS Configuration

HTTP API CORS headers and Socket.IO WebSocket origins are controlled via `CORS_ALLOWED_ORIGINS`:

- **Dynamic CORS Middleware**: Sets `Access-Control-Allow-Origin` dynamically to match requested origin if present in allowed origins list.
- **Socket.IO Scoping**: `cors_allowed_origins` parameter on `SocketIO(app)` parses `CORS_ALLOWED_ORIGINS` to enforce origin restriction on WebSocket handshakes.

---

## 9. WebRTC STUN & TURN Configuration

- **STUN Configuration**: Default Google STUN server (`stun:stun.l.google.com:19302`) is automatically provided.
- **TURN Configuration**: Dynamic TURN relay support is integrated via `GET /api/webrtc/ice-servers?meeting_id=<id>`. Staging environments can enable TURN relay by setting `TURN_ENABLED=True` and populating `TURN_SERVER_URL`, `TURN_USERNAME`, and `TURN_PASSWORD` in `.env`.

---

## 10. Deployment Steps

1. **Provision Staging Environment**: Linux virtual machine or PaaS instance with Python 3.10+ installed.
2. **Install Codebase & Dependencies**:
   ```bash
   git clone <repository_url>
   cd MEETIQ
   pip install -r backend/requirements.txt
   ```
3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   # Edit .env with staging MongoDB URI, SECRET_KEY, CORS_ALLOWED_ORIGINS, CELERY_BROKER_URL, and TURN server settings
   ```
4. **Start Application & Worker Services**:
   ```bash
   # Terminal 1: Gunicorn WSGI Web Server
   gunicorn -c gunicorn.conf.py backend.app:app

   # Terminal 2: Celery Async AI Worker
   python -m celery -A backend.celery_app.celery worker --loglevel=info
   ```
5. **Verify Health Endpoint**:
   ```bash
   curl http://localhost:5000/health
   ```

---

## 11. Verification Checklist

- [x] Backend unit & integration test suite passes 36/36 (`python -m pytest backend/tests -v`).
- [x] `/health` endpoint operational.
- [x] Celery + Redis asynchronous AI task queue operational.
- [x] WebRTC TURN server credential endpoint (`/api/webrtc/ice-servers`) operational and secured.
- [x] Environment secrets loaded strictly from environment variables.
- [x] Flask session cookie security enabled (`HTTPONLY=True`, `SAMESITE=Lax`).
- [x] Socket.IO room isolation (`validate_same_meeting_sockets`) verified.
- [x] MongoDB database connection timeouts & index initialization operational.

---

## 12. Known Limitations

- **Live TURN Relay Verification**: Live TURN relay routing across symmetric NATs requires Coturn server infrastructure with public IP routing.
- **Real Browser E2E**: Automation driver dependency restricted on runner environment.


---

## 13. Troubleshooting

- **Socket.IO Worker Mismatch**: Ensure Gunicorn runs with `-w 1` and `--worker-class eventlet` unless an external Redis message queue adapter is configured.
- **Database Connection Timeout**: Verify staging MongoDB URI host permissions and IP access rules.
- **CORS Rejected Handshake**: Verify the client domain matches `CORS_ALLOWED_ORIGINS` in `.env`.
