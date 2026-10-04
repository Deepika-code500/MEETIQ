# MEETIQ Render Deployment Guide

This guide describes how to deploy the **MEETIQ** platform to **Render** as a public HTTPS web service for staging and demo purposes, while maintaining compatibility with the existing local Docker Compose production architecture.

> [!WARNING]
> Render Free Tier web services spin down automatically after 15 minutes of inactivity and have strict CPU/memory limits (512MB RAM). This setup is intended for public demonstration and staging review, **not as high-capacity production infrastructure**.

---

## 1. Public Web Service

The MEETIQ application is deployed on Render as a **Web Service** running the official Docker container.
- **Protocol**: HTTPS / WSS (managed automatically by Render SSL certificates)
- **Port Binding**: Dynamic `$PORT` assigned by Render runtime environment (listens on `0.0.0.0:$PORT`)
- **WSGI Server**: Gunicorn with `eventlet` worker class for Flask-SocketIO support

---

## 2. GitHub Repository

To deploy MEETIQ on Render:
1. Ensure your latest code (including `Dockerfile` and `render.yaml`) is pushed to your GitHub repository on `main` or a staging branch.
2. Connect your GitHub account to the Render Dashboard.
3. Select the MEETIQ repository when creating a new Web Service or Blueprint deployment.

---

## 3. Docker Configuration

MEETIQ uses the multi-stage `Dockerfile` located at the root of the repository:
- **Base Image**: `python:3.10-slim`
- **System Dependencies**: `ffmpeg`, `libgl1`, `libglib2.0-0`, `curl`, `build-essential`
- **Non-root Security User**: `meetiq` (UID 1000)
- **Startup Command**: `gunicorn -c gunicorn.conf.py backend.app:app`
- **Health Check**: `curl -f http://localhost:${PORT:-5000}/health`

---

## 4. Environment Variables

Set the following environment variables in the Render Dashboard (or via `render.yaml` Blueprint):

| Variable | Recommended Render Value | Notes / Description |
| :--- | :--- | :--- |
| `FLASK_ENV` | `production` | Enables secure session cookies and hardened headers |
| `SECRET_KEY` | *(Auto-generated random string)* | Flask session signing secret |
| `MONGODB_URI` | `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/meetiq?retryWrites=true&w=majority` | Connection string to MongoDB Atlas |
| `CORS_ALLOWED_ORIGINS` | `https://<your-render-app>.onrender.com` | Strict CORS policy set to public Render domain |
| `SESSION_COOKIE_HTTPONLY` | `True` | Mitigates XSS session hijacking |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Cross-site request policy |
| `SESSION_COOKIE_SECURE` | `True` | Forces HTTPS-only cookies |
| `AI_ASYNC_ENABLED` | `False` (Free-tier) or `True` (with Redis) | Controls background Celery queueing vs sync execution |
| `CELERY_BROKER_URL` | `redis://<user>:<pass>@<redis-host>:6379/0` | External Redis connection URI (if Celery used) |
| `CELERY_RESULT_BACKEND` | `redis://<user>:<pass>@<redis-host>:6379/0` | External Redis result backend URI (if Celery used) |
| `OLLAMA_API_URL` | `http://<your-ai-host>:11434` | Endpoint for Ollama LLM service |
| `SUMMARIZER_MODEL` | `llama3` | Model name for meeting summarization |
| `TURN_ENABLED` | `False` or `True` | Enables WebRTC TURN relay server integration |
| `TURN_SERVER_URL` | `turn:<coturn-host>:3478?transport=udp` | STUN/TURN server URL |
| `TURN_USERNAME` | `<turn-username>` | TURN credentials |
| `TURN_PASSWORD` | `<turn-password>` | TURN secret |

---

## 5. MongoDB Atlas

The public Render Web Service requires a cloud-accessible database:
1. Create a free **MongoDB Atlas M0** cluster at [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas).
2. Under **Network Access**, add `0.0.0.0/0` (Allow access from anywhere) so Render container IPs can connect.
3. Under **Database Access**, create a database user with read/write permissions to the `meetiq` database.
4. Copy the connection string and set `MONGODB_URI` in Render Environment Variables.
5. MEETIQ automatically initializes required collections and indexes on startup without schema modifications.

---

## 6. CORS

To protect API endpoints and Socket.IO connections in public HTTPS deployments:
- Configure `CORS_ALLOWED_ORIGINS` to your exact Render HTTPS URL (e.g., `https://meetiq-web.onrender.com`).
- Do **NOT** use `*` for production deployments.
- Multiple domains can be comma-separated if custom domains are mapped.

---

## 7. Health Check

Render uses HTTP health checks to verify service availability before routing traffic:
- **Path**: `/health`
- **Method**: `GET`
- **Expected Status**: `200 OK`
- **Payload**: `{"database": "connected", "service": "MeetIQ Virtual Meeting Platform", "status": "healthy", "timestamp": "..."}`
- **Security**: The health endpoint explicitly omits database URIs, secrets, Redis credentials, or stack traces.

---

## 8. WebSocket / Socket.IO

MEETIQ relies on Socket.IO for WebRTC signaling, chat, participant roster events, and live meeting state:
- Render Web Services natively support WebSockets over standard HTTPS (port 443).
- Eventlet WSGI handles HTTP long-polling upgrade to WebSockets automatically.
- Room isolation and socket authentication remain enforced (`validate_same_meeting_sockets`).

---

## 9. Celery / Redis

In full Docker Compose production:
- Redis runs as a dedicated container.
- Celery worker executes background AI transcription and summarization tasks.

In Render Free Web Service deployment:
- Background workers (`type: worker`) require separate hosting or paid Render instance types.
- If Redis is provisioned externally (e.g. Upstash Redis or Render Managed Redis), set `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` and set `AI_ASYNC_ENABLED=True`.
- If Redis is unavailable, setting `AI_ASYNC_ENABLED=False` ensures request handlers process tasks cleanly or return existing graceful error responses without crashing.

---

## 10. Whisper

OpenAI Whisper speech recognition (`base.pt`) requires significant CPU/RAM resources (~1GB+ RAM and ffmpeg):
- On Render Free Tier (512MB RAM Limit), running Whisper in-process can cause Out-Of-Memory (OOM) container kills.
- For full live transcription on public staging, host the Celery worker with Whisper on a machine/VPS with >= 2GB RAM.

---

## 11. Ollama

Ollama LLM summarization (`llama3`) requires local model weights (4GB+):
- Render container instances do not package heavy LLM weights.
- Configure `OLLAMA_API_URL` to point to a remote Ollama server or GPU host.
- If Ollama is unreachable, MEETIQ returns a structured error message (`"AI summarization service unavailable"`) without failing the web process.

---

## 12. TURN

WebRTC audio/video peer connections across public networks require NAT traversal:
- Default setup uses public Google STUN server (`stun:stun.l.google.com:19302`).
- For clients behind strict corporate firewalls or symmetric NATs, provision a Coturn TURN server and configure `TURN_ENABLED=True`, `TURN_SERVER_URL`, `TURN_USERNAME`, and `TURN_PASSWORD`.

---

## 13. Free-Tier Limitations

When deploying MEETIQ on Render Free Tier:
1. **Sleep / Spin Down**: Free web services automatically sleep after 15 minutes of inactivity. The first incoming request will experience a cold-start delay of 30–50 seconds.
2. **Resource Constraints**: 512 MB RAM limit. In-memory heavy AI models (Whisper/Ollama) will exceed memory limits if invoked directly on the free web service.
3. **Bandwidth**: 100 GB free outbound bandwidth per month.
4. **Ephemerality**: Local filesystem writes in containers are ephemeral. Uploaded audio chunks or temporary files are deleted when the container restarts. Persistent data is stored in MongoDB Atlas.

---

## 14. Deployment Steps

### Option A: Using Render Blueprint (`render.yaml`)
1. Push `render.yaml` to your GitHub repository.
2. Log in to [Render Dashboard](https://dashboard.render.com).
3. Click **New +** -> **Blueprint**.
4. Connect your GitHub repository. Render will automatically detect `render.yaml`.
5. Fill in required `sync: false` environment variables (`MONGODB_URI`, `CORS_ALLOWED_ORIGINS`).
6. Click **Apply**.

### Option B: Manual Web Service Creation
1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** -> **Web Service**.
3. Select **Build and deploy from a Git repository**.
4. Choose repository and branch `main`.
5. Environment: **Docker**.
6. Docker Command: (Leave default to use `Dockerfile`).
7. Health Check Path: `/health`.
8. Add Environment Variables (listed in Section 4).
9. Click **Create Web Service**.

---

## 15. Verification Checklist

- [ ] `GET /health` returns HTTP `200` with `{"status": "healthy", "database": "connected"}`.
- [ ] `GET /ready` returns HTTP `200` with `{"status": "ready"}`.
- [ ] User authentication (Login / Signup) succeeds against MongoDB Atlas.
- [ ] Admin meeting creation and participant roster import work correctly.
- [ ] WebRTC audio/video stream exchange succeeds across two browser sessions.
- [ ] Socket.IO chat and signaling messages operate without CORS warnings in browser developer console.
