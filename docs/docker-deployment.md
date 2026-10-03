# MEETIQ Docker Deployment & Containerization Guide

This document describes the multi-container Docker setup for the **MEETIQ** platform using Docker and Docker Compose.

---

## 1. Architecture

```text
               ┌──────────────────────────────────────────────┐
               │           Client Web Browsers                │
               └──────────────────────┬───────────────────────┘
                                      │
                                      ▼
               ┌──────────────────────────────────────────────┐
               │  MeetIQ Web Container (Flask + Gunicorn)     │
               │                   [Port 5000]                │
               └──────────┬───────────────────┬───────────────┘
                          │                   │
                          ▼                   ▼
┌───────────────────────────────────┐   ┌───────────────────────────────┐
│ MongoDB Container (mongo:6-jammy) │   │ Redis Container (redis:7)     │
│             [Port 27017]          │   │         [Port 6379]           │
└───────────────────────────────────┘   └───────────────┬───────────────┘
                                                        │
                                                        ▼
                                        ┌───────────────────────────────┐
                                        │ Celery AI Worker Container    │
                                        │ (tasks.async_generate_summary)│
                                        └───────────────────────────────┘
```

---

## 2. Services

| Service Name | Container Name | Base Image / Build | Port | Function |
| :--- | :--- | :--- | :--- | :--- |
| `web` | `meetiq_web` | `Dockerfile` | `5000` | Flask WSGI application server (Gunicorn) |
| `celery_worker` | `meetiq_celery_worker` | `Dockerfile` | N/A | Celery background AI task worker |
| `redis` | `meetiq_redis` | `redis:7-alpine` | `6379` | Task broker and result store |
| `mongodb` | `meetiq_mongodb` | `mongo:6-jammy` | `27017` | MongoDB database engine |

---

## 3. Environment Variables

Runtime configuration is passed via environment variables (or `.env` file):

```env
FLASK_ENV=production
SECRET_KEY=your_production_secure_secret_key
MONGODB_URI=mongodb://mongodb:27017/meetiq
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
AI_ASYNC_ENABLED=True
OLLAMA_API_URL=http://host.docker.internal:11434
CORS_ALLOWED_ORIGINS=*
TURN_ENABLED=False
```

---

## 4. Building Images

Build the production Docker image locally:

```bash
docker build -t meetiq:latest .
```

---

## 5. Starting the Stack

Orchestrate all services with Docker Compose:

```bash
docker compose up -d
```

---

## 6. Stopping the Stack

Stop all container services:

```bash
docker compose down
```

To remove persistent volumes:

```bash
docker compose down -v
```

---

## 7. Logs

Inspect service logs:

```bash
docker compose logs -f web
docker compose logs -f celery_worker
```

---

## 8. Health Checks

- **Web Service**: `GET /health` and `GET /ready` on `http://localhost:5000/health`.
- **Redis Service**: `redis-cli ping`
- **MongoDB Service**: `mongosh --eval "db.adminCommand('ping')"`

---

## 9. Celery Worker

The background worker runs separately from web workers:
```bash
python -m celery -A backend.celery_app.celery worker --loglevel=info
```

---

## 10. MongoDB

Data is stored persistently in named volume `mongodb_data`.

---

## 11. Redis

Provides high-speed message brokering between Flask web handlers and Celery workers.

---

## 12. Ollama

Local Ollama instance runs on host machine (`http://host.docker.internal:11434`) or external server, preventing heavy LLM model weights from inflating container sizes.

---

## 13. TURN

Configurable TURN credentials (`TURN_ENABLED`, `TURN_SERVER_URL`, `TURN_USERNAME`, `TURN_PASSWORD`) allow NAT traversal across corporate firewalls when Coturn is provisioned.

---

## 14. Production Security

- Non-root user (`meetiq`) inside Docker containers.
- Hardened HTTP security headers and SameSite Lax cookies.
- Input file validation and path traversal protection for uploaded recordings.
- Health and readiness probes without credential leakage.

---

## 15. Troubleshooting

- **MongoDB Connection Refused**: Verify container network linkage and `MONGODB_URI`.
- **Celery Worker Idle**: Verify Redis health check (`docker compose exec redis redis-cli ping`).
