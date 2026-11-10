# TOV4R MUSIC — Python 3.13, FFmpeg and the matched bgutil Node runtime.
FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2 AS pot_runtime
FROM python:3.13-slim-bookworm
COPY --from=pot_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=pot_runtime /app /opt/bgutil
ENV YOUTUBE_POT_SERVER_HOME=/opt/bgutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates libstdc++6 && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY bot.py server_setup.py source_probe.py .
# Fail the build, rather than crash after login, if a runtime module is missing.
RUN python -c "import bot; import server_setup"
ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
