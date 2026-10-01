# MeetIQ – AI Virtual Meeting Platform

MeetIQ is a real-time AI-powered virtual meeting platform built with Flask, Socket.IO, WebRTC, MongoDB, and integrated AI services for automated speech-to-text transcription, summary generation, participant attention tracking, and content moderation.

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
│ MongoDB Database        │ │ Mail Server   │ │ AI Subsystems        │
│ • User Accounts         │ │ • Invites     │ │ • Whisper (STT)      │
│ • Meetings & Attendance │ │ • Reports     │ │ • Ollama (Summaries) │
│ • Toxicity & Attention  │ └───────────────┘ │ • MediaPipe (Gaze)   │
└─────────────────────────┘                   │ • Detoxify (Toxicity)│
                                              └──────────────────────┘
```

---

## 🛠️ Prerequisites

- **Python**: 3.10+
- **MongoDB**: 6.0+ (running locally or remote cluster via connection string)
- **Node.js / npm**: (Optional, for asset processing if applicable)
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
| `CORS_ALLOWED_ORIGINS` | Configurable allowed CORS origins | `*` (or `http://localhost:5000`) |
| `MAIL_SERVER` | SMTP mail server host | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP server port | `587` |
| `MAIL_USE_TLS` | Enable TLS encryption | `True` |
| `MAIL_USERNAME` | Mail account username | `your_email@example.com` |
| `MAIL_PASSWORD` | Mail account app password | `your_email_app_password` |
| `MAIL_DEFAULT_SENDER` | Default sender address | `your_email@example.com` |
| `OLLAMA_API_URL` | Local Ollama endpoint | `http://localhost:11434` |
| `SUMMARIZER_MODEL` | Ollama model name | `llama3` |

---

## 🚀 Local Setup & Run Commands

1. **Clone & Install Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

2. **Start MongoDB**:
   Ensure MongoDB service is running on `mongodb://localhost:27017` or update `MONGODB_URI` in `.env`.

3. **Run Backend Application**:
   ```bash
   python backend/app.py
   ```
   The application will start on `http://localhost:5000`.

---

## 🧪 Testing

Run backend unit & security hardening test suite:

```bash
python -m pytest backend/tests -v
```

---

## 📹 WebRTC & STUN / TURN Configuration

MeetIQ uses WebRTC peer-to-peer audio/video connections with Socket.IO signaling.

- **Current Setup**: STUN-only configuration using Google public STUN server (`stun:stun.l.google.com:19302`).
- **Production Requirement**: For deployments across restrictive corporate networks or symmetric NATs, a TURN server (e.g. Coturn) must be configured with environment variables:
  ```env
  TURN_SERVER_URL=turn:turn.meetiq.com:3478
  TURN_USERNAME=meetiq_user
  TURN_CREDENTIAL=meetiq_secret
  ```

---

## ⚠️ Known Limitations & Deployment Dependencies

1. **STUN-Only NAT Traversal**: Direct P2P video works on LAN and open networks; NAT traversal across isolated subnets requires a TURN relay server.
2. **AI Subsystem Dependencies**: If optional AI services (Whisper, Detoxify, Ollama) are offline or missing hardware dependencies, MeetIQ degrades gracefully by keeping meeting calls and attendance functional without crashing.
