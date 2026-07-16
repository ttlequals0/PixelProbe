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
    python3.12-dev \
    python3.12-venv \
    # Node.js for frontend build \
    nodejs \
    npm \
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
    libjpeg-dev \
    libpng16-16t64 \
    libpng-dev \
    libtiff6 \
    libtiff-dev \
    libwebp7 \
    libwebp-dev \
    libwebpmux3 \
    libwebpdemux2 \
    webp \
    libopenjp2-7 \
    libopenjp2-7-dev \
    librsvg2-2 \
    librsvg2-dev \
    libraw23 \
    libraw-dev \
    libheif1 \
    libheif-dev \
    ghostscript \
    # Additional libraries for better support \
    libexif12 \
    libexif-dev \
    liblcms2-2 \
    liblcms2-dev \
    libfftw3-double3 \
    libfftw3-dev \
    libfreetype6 \
    libfreetype6-dev \
    libfontconfig1 \
    libfontconfig1-dev \
    && rm -rf /var/lib/apt/lists/* \
    # pebble ships in the ubuntu:26.04 OCI rootfs (not dpkg-owned); unused
    # here and its embedded Go deps carry unfixed HIGH CVEs, so drop it
    && rm -rf /usr/bin/pebble /var/lib/pebble

# All app Python runs from this venv (python/pip/gunicorn/celery resolve here).
# A venv avoids pip 26+ conflicts with the system 3.14 dist-packages.
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

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
# After install, remove chardet (pulled in by reportlab) -- its 7.x version fails
# requests' version check (requires <6.0.0). Our app uses charset_normalizer instead.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y chardet 2>/dev/null; true

# Build headers were only needed for pip C-extension builds above; linux-libc-dev
# otherwise ships a stream of unfixed kernel-header CVEs the runtime never touches.
RUN apt-get purge -y linux-libc-dev python3.12-dev 2>/dev/null; \
    apt-get autoremove -y 2>/dev/null; \
    rm -rf /var/lib/apt/lists/* /root/.cache

COPY package.json webpack.config.js ./
RUN npm install

COPY . .

# Build frontend assets, then drop the node toolchain. Webpack and its
# transitive dev-only dependencies (picomatch, serialize-javascript, svgo,
# etc.) are not needed at runtime and otherwise ship as CVEs in the image.
RUN npm run build && \
    rm -rf node_modules package-lock.json

# Ensure the pixelprobe package is properly installed
RUN mkdir -p /app/instance && \
    chmod -R 755 /app && \
    find /app -type f -name "*.py" -exec chmod 644 {} \;

# Set Python path to include the app directory
ENV PYTHONPATH=/app
# Ensure Python output is unbuffered for proper logging
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Don't set APP_VERSION here - let version.py be the single source of truth
# The app will read the version from version.py directly

# Bind/workers/timeout/logging live in gunicorn.conf.py (GUNICORN_* env overrides)
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
