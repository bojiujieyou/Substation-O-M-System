# Dockerfile — 变电站图像监控运维平台
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY config.py .
COPY app.py .
COPY admin.py .
COPY init_db.py .
COPY parse_excel.py .
COPY import_excel.py .
COPY precheck_excel.py .
COPY templates/ ./templates/
COPY static/ ./static/

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 5000

# 环境变量
ENV FLASK_APP=app.py
ENV FLASK_DEBUG=False
ENV API_TOKEN=

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# 启动命令
CMD ["python", "app.py"]
