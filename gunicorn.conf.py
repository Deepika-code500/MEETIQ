# =========================================================
# MeetIQ Gunicorn Staging/Production Configuration
# =========================================================


bind = "0.0.0.0:5000"
workers = 1
worker_class = "eventlet"
keepalive = 5
timeout = 120
graceful_timeout = 30
loglevel = "info"
accesslog = "-"
errorlog = "-"
