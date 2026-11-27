# 多阶段构建：编译层装依赖，运行层装 Chromium 系统库 + 应用
# 最终镜像约 1.2GB（Python + Chromium + 应用代码），可在 1核2G 服务器正常跑

FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .

# 国内 PyPI 镜像（如需换回官方源，删掉 -i 参数即可）
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt


FROM python:3.11-slim AS runtime

# Debian apt 源换阿里云镜像 + Chromium 系统依赖
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
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

# Chromium 浏览器二进制走 npmmirror 镜像，避免从 playwright.azureedge.net 下载卡死
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 先装 Chromium（系统库上面已装好，这里不再 --with-deps，减少一次 apt 往返）
RUN playwright install chromium

# 应用代码
COPY listing_gen ./listing_gen
COPY web ./web
COPY .env.example .env.example

# 数据目录（缓存、输出）
RUN mkdir -p /app/listing_gen/data/cache /app/output

ENV HOST=0.0.0.0 PORT=8000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000", "--access-log"]
