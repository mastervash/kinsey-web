FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS wheels
WORKDIR /src
RUN apt-get update && apt-get install --no-install-recommends -y build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY maigret ./maigret
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/mastervash/kinsey-web" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
RUN apt-get update && apt-get install --no-install-recommends -y libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --home /app kinsey \
    && mkdir /data && chown kinsey /data
COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY --from=web /web/dist ./web/dist
COPY maigret/resources/data.json /app/seed/data.json
# Site DB is edited at runtime (Sites page), so it lives on the volume; seeded on first start.
ENV KW_HOST=0.0.0.0 KW_PORT=7580 KW_DIST=/app/web/dist KW_DATA_DIR=/data \
    KW_SITES_DB=/data/data.json PYTHONUNBUFFERED=1
USER kinsey
VOLUME /data
EXPOSE 7580
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['KW_PORT']+'/api/health',timeout=4)"
CMD ["sh", "-c", "[ -f \"$KW_SITES_DB\" ] || cp /app/seed/data.json \"$KW_SITES_DB\"; exec kinsey-web"]
