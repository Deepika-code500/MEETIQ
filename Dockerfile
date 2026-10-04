# =========================================================
# MeetIQ - Multi-Stage Production Dockerfile
# =========================================================

FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app \
    FLASK_ENV=production

# Install system dependencies (ffmpeg for Whisper, libgl1 for OpenCV)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python packages
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy application source code
COPY . /app

# Create unprivileged user for security hardening
RUN useradd -m -u 1000 meetiq && \
    mkdir -p /app/ai-services/speech-to-text/recordings && \
    chown -R meetiq:meetiq /app

USER meetiq

EXPOSE 5000

# Container Health Check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${PORT:-5000}/health || exit 1

# Production WSGI Server Startup Command
CMD ["gunicorn", "-c", "gunicorn.conf.py", "backend.app:app"]
