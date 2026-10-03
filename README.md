# MeetIQ – AI Virtual Meeting Platform

MeetIQ is a real-time AI-powered virtual meeting platform built with Flask, Socket.IO, WebRTC, MongoDB, Celery, Redis, and integrated AI services for automated speech-to-text transcription, summary generation, participant attention tracking, and content moderation.

---

## 🏗️ System Architecture

```text
       ┌─────────────────────────────────────────────────────────┐
       │                Browser Clients (WebRTC / Socket.IO)     │
       └───────────────────────────┬─────────────────────────────┘
                                   │
                                   ▼
       ┌─────────────────────────────────────────────────────────┐
       │             Flask App & Socket.IO Backend Server         │
       └─────┬─────────────────────┬─────────────────────┬───────┘
             │                     │                     │
             ▼                     ▼                     ▼
┌─────────────────────────┐ ┌───────────────┐ ┌──────────────────────┐
│ MongoDB Database        │ │ Celery/Redis  │ │ AI Subsystems        │
│ • User Accounts         │ │ Task Queue    │ │ • Whisper (STT)      │
│ • Meetings & Attendance │ │ • Summaries   │ │ • Ollama (Summaries) │
│ • Toxicity & Jobs       │ │ • Whisper STT │ │ • MediaPipe (Gaze)   │
└─────────────────────────┘ └───────────────┘ │ • Detoxify (Toxicity)│
                                              └──────────────────────┘
```

---

## 🛠️ Prerequisites

- **Python**: 3.10+
- **MongoDB**: 6.0+ (running locally or remote cluster via connection string)
- **Redis**: 7.0+ (for Celery asynchronous task queue)
- **Docker & Docker Compose**: (Optional, for containerized local/staging deployment)
- **Ollama**: (Optional, for local LLM meeting summarization with `llama3`)

---

## 🔑 Environment Variables Configuration

Copy `.env.example` to `.env` and fill in your environment settings:

```bash
cp .env.example .env
```

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `FLASK_ENV` | Application runtime environment | `development` or `production` |
| `SECRET_KEY` | Flask session secret key | Safe random string in production |
| `MONGODB_URI` | MongoDB database connection URI | `mongodb://localhost:27017/meetiq` |
| `CELERY_BROKER_URL` | Redis broker URL for Celery | `redis://localhost:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis result store URL | `redis://localhost:6379/0` |
| `AI_ASYNC_ENABLED` | Enable asynchronous Celery AI queue | `True` |
| `CORS_ALLOWED_ORIGINS` | Configurable allowed CORS origins | `*` (or `http://localhost:5000`) |
| `OLLAMA_API_URL` | Local Ollama endpoint | `http://localhost:11434` |
| `SUMMARIZER_MODEL` | Ollama model name | `llama3` |
| `TURN_ENABLED` | Enable TURN relay configuration | `False` |
| `TURN_SERVER_URL` | Configurable TURN server URL | `turn:turn.meetiq.com:3478` |

---

## 🚀 Local Setup & Run Commands

1. **Clone & Install Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

2. **Start Infrastructure (MongoDB & Redis)**:
   Ensure MongoDB and Redis services are running locally, or start using Docker Compose:
   ```bash
   docker compose up -d mongodb redis
   ```

3. **Run Celery Background AI Worker**:
   ```bash
   python -m celery -A backend.celery_app.celery worker --loglevel=info
   ```

4. **Run Backend Application Server**:
   ```bash
   python backend/app.py
   ```
   The application will start on `http://localhost:5000`.

---

## 🐳 Docker Orchestration

Run the complete MeetIQ stack (Web, Celery Worker, Redis, MongoDB) in containers:

```bash
docker compose up -d
```

Check health status:
```bash
curl http://localhost:5000/health
```

---

## 🧪 Testing

Run complete unit, integration, and security test suite:

```bash
python -m pytest backend/tests -v
```

---

## 📹 WebRTC & STUN / TURN Configuration

MeetIQ uses WebRTC peer-to-peer audio/video connections with Socket.IO signaling.

- **STUN Setup**: Default Google public STUN server (`stun:stun.l.google.com:19302`).
- **TURN Relay Setup**: For deployments across restrictive corporate networks or symmetric NATs, enable TURN in `.env`:
  ```env
  TURN_ENABLED=True
  TURN_SERVER_URL=turn:turn.meetiq.com:3478?transport=udp
  TURN_USERNAME=meetiq_user
  TURN_PASSWORD=meetiq_secret
  ```

---

## ⚙️ CI/CD Pipeline

Automated build and test workflows run on GitHub Actions (`.github/workflows/ci.yml`) on every push/PR:
1. Pytest suite execution (`python -m pytest backend/tests -v`).
2. Docker compilation check (`docker build`).
