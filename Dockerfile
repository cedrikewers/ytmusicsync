FROM python:3.14-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates unzip && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deno.land/install.sh | sh


WORKDIR /app

RUN python -m pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync

COPY . .

# Create default directories
RUN mkdir -p /data /music

ENV MUSIC_LIBRARY_DIR=/music
ENV DATA_DIR=/data
ENV WEB_PORT=5023
ENV CHECK_INTERVAL_HOURS=6
ENV LOOKBACK_DAYS=3
ENV LOG_LEVEL=INFO
ENV URL_PREFIX=/ytdl
ENV DENO_INSTALL=/root/.deno/bin

EXPOSE 5023

CMD ["uv", "run", "python", "main.py"]
