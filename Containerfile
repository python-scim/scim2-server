FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY scim2_server ./scim2_server
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER nobody
EXPOSE 8080
# Werkzeug stops gracefully on KeyboardInterrupt, which lets --dump-resources be written.
STOPSIGNAL SIGINT
ENTRYPOINT ["scim2-server", "--hostname", "0.0.0.0"]
CMD ["--port", "8080"]
