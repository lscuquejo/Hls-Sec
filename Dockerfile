FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bin ./bin
RUN chmod +x /app/bin/*.sh /app/bin/*.py \
    && mkdir -p /app/exports /app/out/downloads /app/.playwright-profile

ENV APP_ROOT=/app
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_PROFILE=/app/.playwright-profile
# Docker Compose defaults: talk to the sibling browser service
ENV BRAVE_CDP=auto
ENV CDP_CANDIDATES=http://host.docker.internal:9222,http://127.0.0.1:9222,ws://browser:3000
ENV EXTRACT_MODE=cdp

EXPOSE 8080
VOLUME ["/app/exports", "/app/out", "/app/.playwright-profile"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
