FROM docker.io/astral/uv:0.13.1-python3.14-trixie-slim@sha256:112f89760bd54b63c84d140151ea5f3d849278e54f6f939f2c40a57f3e33d895 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --extra precis --extra cursor --no-install-project
COPY scim2_server ./scim2_server
RUN uv sync --locked --no-dev --extra precis --extra cursor --no-editable

FROM python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER nobody
EXPOSE 8080
# The server stops gracefully on KeyboardInterrupt, which lets --dump-resources be written.
STOPSIGNAL SIGINT
ENTRYPOINT ["scim2-server", "--hostname", "0.0.0.0"]
CMD ["--port", "8080"]
