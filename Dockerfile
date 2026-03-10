FROM python:3.14-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create default directories
RUN mkdir -p /data /music

ENV MUSIC_LIBRARY_DIR=/music
ENV DATA_DIR=/data
ENV WEB_PORT=5023
ENV CHECK_INTERVAL_HOURS=6
ENV LOOKBACK_DAYS=3
ENV LOG_LEVEL=INFO

EXPOSE 5023

CMD ["python", "main.py"]
