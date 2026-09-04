FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bin ./bin
RUN chmod +x /app/bin/*.sh \
    && mkdir -p /app/exports /app/out/downloads

ENV APP_ROOT=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
VOLUME ["/app/exports", "/app/out"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
