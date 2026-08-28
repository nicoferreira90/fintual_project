import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("WEB_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.environ.get("WEB_TIMEOUT", "30"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
