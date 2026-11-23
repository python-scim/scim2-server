FROM docker.io/astral/uv:0.13.7-python3.14-trixie-slim@sha256:e80c05a310400cfc90af00b5b7271ef9ad24816e3265e1162af8a678e1aad099 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --extra precis --extra cursor --extra oauth --no-install-project
COPY scim2_server ./scim2_server
RUN uv sync --locked --no-dev --extra precis --extra cursor --extra oauth --no-editable

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER nobody
EXPOSE 8080
# The server stops gracefully on KeyboardInterrupt, which lets --dump-resources be written.
STOPSIGNAL SIGINT
ENTRYPOINT ["scim2-server", "--hostname", "0.0.0.0"]
CMD ["--port", "8080"]
