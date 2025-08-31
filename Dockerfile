FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    libmagic1 \
    curl \
    # ImageMagick delegates for full format support \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libwebp-dev \
    libopenjp2-7-dev \
    librsvg2-dev \
    ghostscript \
    # Additional libraries for better format support \
    libexif-dev \
    liblcms2-dev \
    libfftw3-dev \
    webp \
    && rm -rf /var/lib/apt/lists/*

# Configure ImageMagick to allow all file formats and increase resource limits
# Remove restrictive policies that might block certain formats
RUN sed -i 's/<policy domain="coder" rights="none" pattern="PDF" \/>/<policy domain="coder" rights="read|write" pattern="PDF" \/>/g' /etc/ImageMagick-6/policy.xml || true && \
    sed -i 's/<policy domain="coder" rights="none" pattern="HEIC" \/>/<policy domain="coder" rights="read|write" pattern="HEIC" \/>/g' /etc/ImageMagick-6/policy.xml || true && \
    sed -i 's/<policy domain="coder" rights="none" pattern="HEIF" \/>/<policy domain="coder" rights="read|write" pattern="HEIF" \/>/g' /etc/ImageMagick-6/policy.xml || true && \
    # Increase resource limits for large images
    sed -i 's/<policy domain="resource" name="memory" value=".*"\/>/<policy domain="resource" name="memory" value="2GiB"\/>/g' /etc/ImageMagick-6/policy.xml || true && \
    sed -i 's/<policy domain="resource" name="map" value=".*"\/>/<policy domain="resource" name="map" value="4GiB"\/>/g' /etc/ImageMagick-6/policy.xml || true && \
    sed -i 's/<policy domain="resource" name="disk" value=".*"\/>/<policy domain="resource" name="disk" value="8GiB"\/>/g' /etc/ImageMagick-6/policy.xml || true

# Configure FFmpeg for optimal performance and compatibility
# Set thread count for better performance
ENV FFMPEG_THREADS=0
# Enable all decoder/encoder features
ENV FFMPEG_STRICT=-2
# Set higher analyzeduration and probesize for better format detection
ENV FFMPEG_ANALYZEDURATION=100M
ENV FFMPEG_PROBESIZE=100M
# Disable interactive mode
ENV FFMPEG_HIDE_BANNER=1
# Set VA-API device for hardware acceleration (if available)
ENV LIBVA_DRIVER_NAME=iHD
ENV LIBVA_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri
# Increase network timeout for streaming sources
ENV FFMPEG_HTTP_TIMEOUT=30000000

WORKDIR /app

# Verify FFmpeg and ImageMagick installations
RUN ffmpeg -version && \
    ffmpeg -decoders 2>/dev/null | grep -E "(hevc|h264|h265|av1|vp9)" && \
    ffmpeg -encoders 2>/dev/null | grep -E "(libx264|libx265|libvpx)" && \
    convert -version && \
    identify -list format | grep -E "(JPEG|PNG|WEBP|HEIC)" || true

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