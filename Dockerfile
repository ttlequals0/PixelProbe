FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the pixelprobe package is properly installed
RUN mkdir -p /app/instance

# Set Python path to include the app directory
ENV PYTHONPATH=/app:$PYTHONPATH

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Set version as build argument
ARG APP_VERSION=2.0.85
ENV APP_VERSION=$APP_VERSION

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "300", "app:app"]