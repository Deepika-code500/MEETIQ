# =========================================================
# MeetIQ Gunicorn Staging/Production Configuration
# =========================================================


import os

port = os.getenv("PORT", "5000")
bind = f"0.0.0.0:{port}" if ":" not in port else port
workers = 1
worker_class = "eventlet"
keepalive = 5
timeout = 120
graceful_timeout = 30
loglevel = "info"
accesslog = "-"
errorlog = "-"
