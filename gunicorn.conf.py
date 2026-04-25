"""Gunicorn 配置 — 变电站图像监控运维平台"""
import os

# 绑定地址
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")

# worker 数量
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))

# worker 类
worker_class = "sync"

# 超时
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))

# 最大请求数（防止内存泄漏累积）
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "5000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "500"))

# 日志
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# 进程命名
proc_name = "station_monitor"

# 优雅重启超时
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
