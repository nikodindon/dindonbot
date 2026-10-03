FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY dindon/ ./dindon/

RUN pip install --no-cache-dir ".[dev]" \
    && groupadd --system --gid 10001 sandbox \
    && useradd --system --uid 10001 --gid sandbox --create-home sandbox

USER sandbox
ENTRYPOINT ["dindon-sandbox"]
