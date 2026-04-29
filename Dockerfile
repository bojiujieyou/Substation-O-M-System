# Dockerfile — 变电站图像监控运维平台
ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 复制启动脚本并设置权限
RUN chmod +x /app/docker-entrypoint.sh

# 创建数据目录和非 root 用户
RUN mkdir -p /app/data /app/photos /app/source_docs && \
    useradd --no-create-home --system --uid 1000 appuser && \
    chown -R appuser:appuser /app/data /app/photos /app/source_docs

# 暴露端口
EXPOSE 5000

# 环境变量
ENV FLASK_APP=app.py
ENV FLASK_DEBUG=False

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# 切换到非 root 用户
USER appuser

# 使用 entrypoint 脚本，默认以 gunicorn 启动
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
