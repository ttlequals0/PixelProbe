FROM ubuntu:24.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install Python and latest media tools
# Ubuntu 24.04 includes:
# - FFmpeg 6.1.1 (much newer than 22.04's 4.4.2)
# - ImageMagick 6.9.13 (newer than 22.04's 6.9.11)
# - Python 3.12 by default
RUN apt-get update && \
    apt-get install -y \
    # Python and pip
    python3 \
    python3-dev \
    python3-venv \
    python3-pip \
    # Node.js for frontend build \
    nodejs \
    npm \
    # Core utilities \
    ffmpeg \
    libmagic1 \
    curl \
    wget \
    # ImageMagick and dependencies (24.04 uses ImageMagick 7) \
    imagemagick \
    libmagickcore-6.q16hdri-7-extra \
    libmagickwand-6.q16hdri-7 \
    # Image format libraries (updated versions in 24.04) \
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
    && rm -rf /var/lib/apt/lists/*

# Python 3.12 is already default in Ubuntu 24.04
# Just create python symlink for compatibility
RUN ln -s /usr/bin/python3 /usr/bin/python

# Configure ImageMagick to allow all file formats and increase resource limits
# Ubuntu 22.04 uses ImageMagick 6
RUN sed -i 's/<policy domain="coder" rights="none" pattern="PDF" \/>/<policy domain="coder" rights="read|write" pattern="PDF" \/>/g' /etc/ImageMagick-6/policy.xml && \
    sed -i 's/<policy domain="coder" rights="none" pattern="HEIC" \/>/<policy domain="coder" rights="read|write" pattern="HEIC" \/>/g' /etc/ImageMagick-6/policy.xml && \
    sed -i 's/<policy domain="coder" rights="none" pattern="HEIF" \/>/<policy domain="coder" rights="read|write" pattern="HEIF" \/>/g' /etc/ImageMagick-6/policy.xml && \
    sed -i 's/<policy domain="resource" name="memory" value=".*"\/>/<policy domain="resource" name="memory" value="2GiB"\/>/g' /etc/ImageMagick-6/policy.xml && \
    sed -i 's/<policy domain="resource" name="map" value=".*"\/>/<policy domain="resource" name="map" value="4GiB"\/>/g' /etc/ImageMagick-6/policy.xml && \
    sed -i 's/<policy domain="resource" name="disk" value=".*"\/>/<policy domain="resource" name="disk" value="8GiB"\/>/g' /etc/ImageMagick-6/policy.xml

# Configure libpng and ImageMagick to handle benign PNG errors better
# These environment variables tell libpng to be less strict
ENV PNG_SKIP_SETJMP_CHECK=1
ENV PNG_IGNORE_ADLER32=1

# Set ImageMagick to be less verbose about warnings
ENV MAGICK_CONFIGURE_PATH=/etc/ImageMagick-6

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
    convert -version && \
    echo "=== ImageMagick Delegate Libraries ===" && \
    convert -list delegate | head -30 && \
    echo "=== ImageMagick Supported Formats ===" && \
    identify -list format | grep -E "(JPEG|JPG|PNG|WEBP|GIF|TIFF|HEIC)" && \
    echo "=== Testing ImageMagick with sample images ===" && \
    # Test JPEG support \
    convert -size 100x100 xc:white /tmp/test.jpg && \
    identify -verbose /tmp/test.jpg | head -5 && \
    # Test PNG support \
    convert -size 100x100 xc:white /tmp/test.png && \
    identify -verbose /tmp/test.png | head -5 && \
    # Test WebP support \
    convert -size 100x100 xc:white /tmp/test.webp && \
    identify -verbose /tmp/test.webp | head -5 && \
    # Clean up test files \
    rm -f /tmp/test.jpg /tmp/test.png /tmp/test.webp && \
    echo "=== All image format tests passed ==="

COPY requirements.txt .
# Ubuntu 24.04 requires --break-system-packages for pip install in Docker
# After install, remove chardet (pulled in by reportlab) -- its 7.x version fails
# requests' version check (requires <6.0.0). Our app uses charset_normalizer instead.
RUN pip install --no-cache-dir --break-system-packages --ignore-installed -r requirements.txt \
    && pip uninstall -y chardet --break-system-packages 2>/dev/null; true

COPY package.json webpack.config.js ./
RUN npm install

COPY . .

# Build frontend assets
RUN npm run build

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

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "app:app"]