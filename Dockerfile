# Dockerfile — 变电站图像监控运维平台
ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

# 设置工作目录
WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY config.py .
COPY app.py .
COPY admin.py .
COPY auth.py .
COPY utils.py .
COPY photo_indexer.py .
COPY init_db.py .
COPY init_admin.py .
COPY parse_excel.py .
COPY import_excel.py .
COPY precheck_excel.py .
COPY templates/ ./templates/
COPY static/ ./static/

# 复制启动脚本并设置权限
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 5000

# 环境变量
ENV FLASK_APP=app.py
ENV FLASK_DEBUG=False
ENV API_TOKEN=
ENV DATABASE_PATH=/app/data/station_monitor.db
ENV PHOTO_ROOT_PATH=/app/photos
ENV DATA_SOURCE_PATH=/app/source_docs

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# 使用entrypoint脚本
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "app.py"]
