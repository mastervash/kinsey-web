FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install --no-install-recommends -y build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY maigret ./maigret
RUN pip install --no-cache-dir .
COPY --from=web /web/dist ./web/dist
ENV KW_HOST=0.0.0.0 KW_PORT=7580 KW_DIST=/app/web/dist KW_DATA_DIR=/data \
    KW_SITES_DB=/app/maigret/resources/data.json
VOLUME /data
EXPOSE 7580
CMD ["kinsey-web"]
