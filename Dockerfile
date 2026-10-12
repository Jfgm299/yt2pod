FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY yt2pod ./yt2pod
RUN pip install --no-cache-dir .
ENV YT2POD_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["yt2pod", "-c", "/config/config.yaml"]
CMD ["run"]
