FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8765 \
    MUSIC_DIR=/music \
    SONORA_NO_BROWSER=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 sonora \
    && mkdir /music \
    && chown sonora:sonora /music

WORKDIR /app
COPY --chown=sonora:sonora app.py ./app.py
COPY --chown=sonora:sonora static ./static
USER sonora
EXPOSE 8765
VOLUME ["/music"]
CMD ["python", "app.py"]
