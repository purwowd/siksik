FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV SADT_API_HOST=0.0.0.0
ENV SADT_LAB_DEMO_MODE=0
ENV PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin satria \
    && mkdir -p /app/data \
    && chown -R satria:satria /app

USER satria

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
