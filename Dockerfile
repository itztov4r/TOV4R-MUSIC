FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m app
COPY . .
RUN mkdir -p /app/data && chown -R app:app /app
CMD ["python", "bot.py"]
