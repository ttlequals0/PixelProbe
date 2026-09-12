FROM node:22.22.2-bookworm-slim AS frontend

WORKDIR /frontend
COPY package.json package-lock.json webpack.config.js ./
RUN npm ci
COPY static ./static
RUN npm run build

FROM ubuntu:26.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Ubuntu 26.04 includes:
# - FFmpeg 8.0.1 (vs 6.1.1 on 24.04)
# - ImageMagick 7.1.2 (Q16; 'magick' CLI, 'convert' kept via alternatives)
# - Python 3.14 by default; the app runs on 3.12 from deadsnakes (matches the
#   tested CI matrix and available psycopg2-binary wheels)
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
    # Python 3.12 (deadsnakes)
    python3.12 \
    python3.12-venv \
    # Core utilities \
    ffmpeg \
    libmagic1 \
    curl \
    wget \
    # ImageMagick 7 (Q16, non-HDRI) and extra codecs \
    imagemagick \
    imagemagick-7.q16 \
    libmagickcore-7.q16-10-extra \
    libmagickwand-7.q16-10 \
    # Image format libraries \
    libjpeg-turbo8 \
    libpng16-16t64 \
    libtiff6 \
    libwebp7 \
    libwebpmux3 \
    libwebpdemux2 \
    webp \
    libopenjp2-7 \
    librsvg2-2 \
    libraw23 \
    libheif1 \
    ghostscript \
    # Additional libraries for better support \
    libexif12 \
    liblcms2-2 \
    libfftw3-double3 \
    libfreetype6 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/* \
    # pebble ships in the ubuntu:26.04 OCI rootfs (not dpkg-owned); unused
    # here and its embedded Go deps carry unfixed HIGH CVEs, so drop it
    && rm -rf /usr/bin/pebble /var/lib/pebble

# All app Python runs from this venv (python/pip/gunicorn/celery resolve here).
# A venv avoids pip 26+ conflicts with the system 3.14 dist-packages.
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip==26.2.1

# Configure ImageMagick 7: raise resource limits for large media. The 26.04
# default policy only sets disk=2GiB (no PDF/HEIC coder blocks to lift).
RUN sed -i 's|<policy domain="resource" name="disk" value=".*"/>|<policy domain="resource" name="disk" value="8GiB"/>|' /etc/ImageMagick-7/policy.xml && \
    sed -i 's|</policymap>|  <policy domain="resource" name="memory" value="2GiB"/>\n  <policy domain="resource" name="map" value="4GiB"/>\n</policymap>|' /etc/ImageMagick-7/policy.xml && \
    # Fail the build if the seds silently matched nothing (policy format drift)
    grep -q '"disk" value="8GiB"' /etc/ImageMagick-7/policy.xml && \
    grep -q '"memory" value="2GiB"' /etc/ImageMagick-7/policy.xml && \
    grep -q '"map" value="4GiB"' /etc/ImageMagick-7/policy.xml

# Configure libpng and ImageMagick to handle benign PNG errors better
# These environment variables tell libpng to be less strict
ENV PNG_SKIP_SETJMP_CHECK=1
ENV PNG_IGNORE_ADLER32=1

ENV MAGICK_CONFIGURE_PATH=/etc/ImageMagick-7

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

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" pixelprobe && \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin pixelprobe

# Verify FFmpeg and ImageMagick installations
RUN ffmpeg -version && \
    ffmpeg -decoders 2>/dev/null | grep -E "(hevc|h264|h265|av1|vp9)" && \
    ffmpeg -encoders 2>/dev/null | grep -E "(libx264|libx265|libvpx)" && \
    echo "=== ImageMagick Version and Delegates ===" && \
    magick -version && \
    echo "=== ImageMagick Delegate Libraries ===" && \
    magick -list delegate | head -30 && \
    echo "=== ImageMagick Supported Formats ===" && \
    magick identify -list format | grep -E "(JPEG|JPG|PNG|WEBP|GIF|TIFF|HEIC)" && \
    echo "=== Testing ImageMagick with sample images ===" && \
    # Test JPEG support \
    magick -size 100x100 xc:white /tmp/test.jpg && \
    magick identify -verbose /tmp/test.jpg | head -5 && \
    # Test PNG support \
    magick -size 100x100 xc:white /tmp/test.png && \
    magick identify -verbose /tmp/test.png | head -5 && \
    # Test WebP support \
    magick -size 100x100 xc:white /tmp/test.webp && \
    magick identify -verbose /tmp/test.webp | head -5 && \
    # Clean up test files \
    rm -f /tmp/test.jpg /tmp/test.png /tmp/test.webp && \
    echo "=== All image format tests passed ==="

COPY requirements.txt .
# After install, remove chardet pulled in by reportlab. Its 7.x version fails
# requests' version check (requires <6.0.0). Our app uses charset_normalizer instead.
RUN pip install --no-cache-dir -r requirements.txt \
    && (pip uninstall -y chardet 2>/dev/null || true) \
    && python -m pip uninstall -y pip \
    && test ! -e /opt/venv/bin/pip

# Keep build headers out of the runtime image. All Python dependencies above use
# wheels, so the runtime does not need libc6-dev or linux-libc-dev.
RUN set -eux; \
    for package in linux-libc-dev libc6-dev; do \
        if dpkg -s "$package" >/dev/null 2>&1; then \
            echo "Unexpected build header package: $package" >&2; \
            exit 1; \
        fi; \
    done; \
    rm -rf /var/lib/apt/lists/* /root/.cache

COPY . .
COPY --from=frontend /frontend/static/dist ./static/dist

# Ensure the pixelprobe package is properly installed
RUN mkdir -p /app/instance && \
    chmod -R 755 /app && \
    find /app -type f -name "*.py" -exec chmod 644 {} \; && \
    mkdir -p /app/instance /app/runtime /app/logs && \
    chown -R "${APP_UID}:${APP_GID}" /app/instance /app/runtime /app/logs /tmp

# Set Python path to include the app directory
ENV PYTHONPATH=/app
# Ensure Python output is unbuffered for proper logging
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

USER pixelprobe

# Don't set APP_VERSION here - let version.py be the single source of truth
# The app will read the version from version.py directly

# Bind/workers/timeout/logging live in gunicorn.conf.py (GUNICORN_* env overrides)
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
