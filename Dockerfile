# 多阶段构建：编译层装依赖，运行层装 Chromium 系统库 + 应用
# 最终镜像约 1.2GB（Python + Chromium + 应用代码），可在 1核2G 服务器正常跑

FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.11-slim AS runtime

# Chromium 系统依赖（playwright install --with-deps 会帮我们装一遍，这里先补齐 apt 源）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
        libcairo2 libasound2 libx11-xcb1 libxcb-dri3-0 libxshmfence1 \
        fonts-noto-cjk ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# 时区
ENV TZ=UTC

WORKDIR /app

# 从 builder 拿 Python 包
COPY --from=builder /usr/local /usr/local

# 先装 Chromium（缓存利用：依赖没改就不用重装）
RUN playwright install chromium --with-deps

# 应用代码
COPY listing_gen ./listing_gen
COPY web ./web
COPY .env.example .env.example

# 数据目录（缓存、输出）
RUN mkdir -p /app/data/cache /app/output

ENV HOST=0.0.0.0 PORT=8000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000", "--access-log"]
