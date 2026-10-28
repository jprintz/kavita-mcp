# Remote MCP server (Streamable HTTP on :8000/mcp). Dependencies come from
# uv.lock; pandoc is the static binary from pandoc/minimal.
FROM docker.io/library/python:3.14-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
COPY --from=docker.io/pandoc/minimal:3.11.0-static /usr/local/bin/pandoc /usr/local/bin/pandoc

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    KAVITA_MCP_TRANSPORT=streamable-http \
    KAVITA_MCP_HOST=0.0.0.0 \
    KAVITA_MCP_PORT=8000

WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache

# pandoc writes into a temporary directory: give a read-only container a /tmp
# (Podman's ReadOnly=true mounts one; with Docker use --tmpfs /tmp).
USER 65532:65532
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
  CMD ["/opt/venv/bin/python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), 2).close()"]
CMD ["kavita-mcp"]
