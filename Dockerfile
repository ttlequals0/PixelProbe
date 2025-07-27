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
# Ensure Python output is unbuffered for proper logging
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Don't set APP_VERSION here - let version.py be the single source of truth
# The app will read the version from version.py directly

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "app:app"]