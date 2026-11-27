FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 espeak-ng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m app
COPY . .
RUN espeak-ng -v ar -s 145 -w /tmp/tov4r-welcome.wav 'مَرْحَبَا بِيك فِي سِيرْفَر تُوفَار! نْتْمَنَّى لِيك وَقْت زْوِين.' && ffmpeg -hide_banner -loglevel error -y -i /tmp/tov4r-welcome.wav -codec:a libmp3lame -q:a 5 /app/welcome-offline.mp3 && rm /tmp/tov4r-welcome.wav && test -s /app/welcome-offline.mp3
RUN mkdir -p /app/data && chown -R app:app /app
CMD ["python", "bot.py"]
