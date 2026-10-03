FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY dindon/ ./dindon/

RUN pip install --no-cache-dir . \
    && groupadd --system --gid 10001 dindon \
    && useradd --system --uid 10001 --gid dindon --create-home dindon \
    && mkdir -p /data \
    && chown dindon:dindon /data

USER dindon
VOLUME ["/data"]

ENTRYPOINT ["dindon"]
CMD ["--help"]
