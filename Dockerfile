# TOV4R MUSIC — Railway/Docker image: Python 3.13, FFmpeg and Node.js 22
# Node.js 22 is required by current yt-dlp EJS challenge support.
FROM node:22-bookworm-slim AS node_runtime
FROM python:3.13-slim-bookworm
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates libstdc++6 && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY bot.py .
ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
