ARG BUILD_FROM=python:3.11-slim-bookworm
FROM ${BUILD_FROM}

# OCI labels (build-time args populated by CI)
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION
LABEL org.opencontainers.image.title="rca-browser" \
      org.opencontainers.image.description="On-demand headless browser microservice for AIDA RCA policy checks via nodriver" \
      org.opencontainers.image.url="https://github.com/emanuelbesliu/homeassistant-rca" \
      org.opencontainers.image.source="https://github.com/emanuelbesliu/homeassistant-rca" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="emanuelbesliu"

# Install Chromium and dependencies (using default Debian repos)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-sandbox \
    xvfb \
    fonts-liberation \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-ron \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    aiohttp \
    nodriver \
    Pillow \
    pytesseract \
    pydub \
    SpeechRecognition \
    xvfbwrapper

WORKDIR /app
COPY server.py .

# Environment
ENV HEADLESS=true
ENV HOST=0.0.0.0
ENV PORT=8194
ENV LOG_LEVEL=INFO

EXPOSE 8194

CMD ["python", "server.py"]
