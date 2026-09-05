FROM ghcr.io/astral-sh/uv:0.12.10 AS uv
FROM python:3.13-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
RUN groupadd --gid 10001 gtnh && useradd --uid 10001 --gid 10001 --no-create-home gtnh
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["gtnh-mcp"]
