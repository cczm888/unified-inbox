FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /srv

# pip 源可覆盖：国内直连 pypi.org 常常几十秒超时，构建会卡住
# 想用官方源：docker compose build --build-arg PIP_INDEX=https://pypi.org/simple
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX" -r requirements.txt

COPY app ./app
COPY sql ./sql
COPY tests ./tests

EXPOSE 8000

# 单容器内只跑 API + 适配器 + outbox worker（都在 lifespan 里启动）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
