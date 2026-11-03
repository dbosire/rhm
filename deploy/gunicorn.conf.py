"""Gunicorn configuration for RHM Extractor (used by rhm-gunicorn.service)."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8001")
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 9)))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))  # large Excel generation can take a while
graceful_timeout = 30
keepalive = 5
max_requests = 1000          # recycle workers to keep memory flat
max_requests_jitter = 100
accesslog = "-"              # -> journald
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'
forwarded_allow_ips = "127.0.0.1"
proxy_allow_ips = "127.0.0.1"
