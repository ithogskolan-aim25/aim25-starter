FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv /uv /bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY api/ ./api/

CMD [".venv/bin/uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]