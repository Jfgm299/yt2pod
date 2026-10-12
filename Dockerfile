FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY yt2pod ./yt2pod
# yt-dlp needs a JavaScript runtime (deno) and its solver scripts (yt-dlp[default]) to read YouTube.
RUN pip install --no-cache-dir . "yt-dlp[default]" deno
ENV YT2POD_DATA_DIR=/data YT2POD_PORT=8765
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=1m --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz')"
# YouTube changes often; start each container with the latest yt-dlp.
CMD ["sh", "-c", "pip install -q --no-cache-dir -U "yt-dlp[default]" >/dev/null 2>&1; exec yt2pod serve"]
