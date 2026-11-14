# Image for the live services (worker, API, replayer). P14; the static site does not use it.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY . .
# Editable, so core.config.ROOT is /app and config.yaml, data/ and docs/ are found.
RUN pip install -e ".[live]"
