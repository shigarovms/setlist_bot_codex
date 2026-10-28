FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12-slim
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY --chown=10001:10001 pyproject.toml uv.lock ./
COPY --chown=10001:10001 src ./src
RUN --mount=type=secret,id=ca_bundle \
    if [ -f /run/secrets/ca_bundle ]; then \
      export SSL_CERT_FILE=/run/secrets/ca_bundle UV_SYSTEM_CERTS=true; \
    fi; \
    uv sync --frozen --no-dev
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations ./migrations
ARG APP_REVISION=unknown
RUN printf '%s' "$APP_REVISION" > /app/.build-revision \
    && useradd --create-home --uid 10001 bot
USER bot
CMD [".venv/bin/setlist-bot"]
