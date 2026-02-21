FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts/export_campgrounds.py ./scripts/export_campgrounds.py
COPY container-entrypoint.sh /usr/local/bin/container-entrypoint.sh
COPY monitor.json /data/monitor.json
COPY campgrounds.json /data/campgrounds.json

RUN pip install --no-cache-dir .
RUN chmod +x /usr/local/bin/container-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/container-entrypoint.sh"]
