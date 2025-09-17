# PixelProbe

<div align="center">
  <img src="static/images/pixelprobe-logo.png" alt="PixelProbe Logo" width="200" height="200">
</div>

## Overview

PixelProbe is a comprehensive media file corruption detection tool with a modern web interface. It helps you identify and manage corrupted video, image, and audio files across your media libraries.

**Version 2.3.3** - Critical scheduler reliability fixes and stuck scan prevention.

### Why PixelProbe?

- **Protect Your Media**: Automatically detect corrupted files before they cause playback issues
- **Save Time**: Batch scan entire media libraries instead of checking files individually  
- **Prevent Data Loss**: Identify failing drives by detecting corruption patterns
- **Professional Grade**: Uses industry-standard tools (FFmpeg, ImageMagick) for accurate detection
- **Set and Forget**: Schedule automated scans to continuously monitor your media health

## 🚀 What's New in Version 2.3.3

### 🎯 Scheduler Reliability Overhaul
- **Fixed Next Run Updates**: Scheduled tasks now properly update their next execution time after running
- **Single Scheduler Instance**: Implemented file-based locking to prevent multiple scheduler instances in production
- **No More Duplicate Executions**: Eliminates duplicate scheduled scans that were running simultaneously

### 🛡️ Stuck Scan Prevention & Recovery
- **Automatic Detection**: Scans stuck for 30+ minutes are automatically detected and marked as crashed
- **Large File Support**: Extended timeout accounts for 50GB+ files that can take 26+ minutes to scan
- **Periodic Health Checks**: System checks for stuck scans every 5 minutes
- **Clean Recovery**: Prevents indefinite blocking of the scan system

### 🔧 Technical Improvements
- **File-based Locking**: Uses fcntl exclusive lock on `/tmp/pixelprobe_scheduler.lock`
- **Extended API Timeouts**: Increased scheduler->API timeouts from 30s to 60s
- **Test Suite Fixes**: Fixed SQLite concurrency issues in test suite
- **All Scan Types Working**: Normal, orphan, and file_changes scans all work reliably as scheduled

## ✨ Features

### Media Support
- Comprehensive video format support (MP4, MKV, AVI, MOV, WebM, FLV, etc.)
- Image format detection (JPEG, PNG, GIF, BMP, TIFF, WebP, etc.)
- Audio file validation (MP3, FLAC, WAV, AAC, OGG, etc.)
- Large file support (tested with 50GB+ Bluray remux files)

### Detection Capabilities
- FFmpeg-based deep video analysis
- ImageMagick and PIL image validation
- Smart warning system for minor issues vs critical corruption
- Multi-stage detection with configurable thresholds
- Automatic retry logic for transient failures

### Scanning Features
- Parallel multi-threaded scanning with real-time progress
- Phase-based scanning (discovery → database → validation)
- Multiple scan types (normal, orphan cleanup, file changes)
- Scheduled automated scans with cron or interval support
- Configurable path and extension exclusions
- Bulk operations for rescanning and deep analysis

### Web Interface
- Modern responsive design with dark/light theme support
- Real-time scan progress with WebSocket updates
- Advanced filtering and search capabilities
- Bulk file selection and management
- Mobile-optimized touch interface
- Detailed file corruption reports

### System Features
- PostgreSQL database for reliable persistence
- Redis-backed task queue for background processing
- Celery worker pool for distributed scanning
- Docker deployment with multi-container architecture
- Comprehensive API for automation
- Detailed system statistics and monitoring

## 📸 Screenshots

### Desktop Interface

#### Light Mode
![Desktop Light Mode](docs/screenshots/desktop-light.png)

The modern desktop interface features:
- Modern design with clean, professional aesthetics
- Sidebar navigation for easy access to all features
- Real-time statistics dashboard showing file health status
- Advanced filtering and search capabilities
- Bulk action support for managing multiple files

#### Dark Mode
![Desktop Dark Mode](docs/screenshots/desktop-dark.png)

PixelProbe includes a sophisticated dark mode:
- High contrast design optimized for low-light environments
- Consistent color scheme across all UI elements
- Smooth theme transitions
- Automatic theme persistence

### Mobile Interface

<div align="center">
  <img src="docs/screenshots/mobile-light-dashboard.png" alt="Mobile Light Dashboard" width="300" style="margin: 10px">
  <img src="docs/screenshots/mobile-dark-dashboard.png" alt="Mobile Dark Dashboard" width="300" style="margin: 10px">
</div>

The mobile interface is fully responsive and touch-optimized:
- Adaptive layout that works on all screen sizes
- Touch-friendly buttons and controls
- Collapsible sidebar navigation
- Card-based design for scan results on mobile

### Advanced Features

#### Scan Reports
![Scan Reports](docs/screenshots/features/scan-reports.png)

Comprehensive scan reporting with history and analytics:
- View all past scan operations with detailed statistics
- Filter by scan type (full scan, rescan, deep scan, cleanup, file changes)
- Export reports as JSON for data analysis or PDF for documentation

#### Scheduled Scanning
![Scan Schedules](docs/screenshots/features/scan-schedules.png)

Create and manage automated scan schedules:
- Support for both cron expressions and simple intervals
- Multiple scan types: Normal Scan, Orphan Cleanup, File Changes
- View next run times and last execution status

## 🚀 Quick Start

### Using Docker (Recommended)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ttlequals0/PixelProbe.git
   cd PixelProbe
   ```

2. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set required variables:
   ```bash
   # Generate a secure secret key
   python -c "import secrets; print(secrets.token_hex(32))"
   
   # Edit .env file with your values
   SECRET_KEY=your-generated-secret-key-here
   MEDIA_PATH=/path/to/your/actual/media/directory
   SCAN_PATHS=/media
   ```

3. **Start the application**:
   ```bash
   docker-compose up -d
   ```

4. **Access the web interface**:
   Open http://localhost:5001 in your browser

5. **Start scanning**:
   Click "Scan All Files" to begin analyzing your media library

### Docker Image Versions

PixelProbe is available on Docker Hub as `ttlequals0/pixelprobe`:

- **`ttlequals0/pixelprobe:latest`** - Latest stable release (v2.3.3)
- **`ttlequals0/pixelprobe:2.3.3`** - Critical scheduler fixes and stuck scan prevention
- **`ttlequals0/pixelprobe:2.2.87`** - Ubuntu 24.04 with modern media tools

## 📦 Requirements

**Important**: PixelProbe requires PostgreSQL. SQLite is no longer supported.

### Quick Migration from SQLite

1. **Backup your data**:
   ```bash
   cp /path/to/instance/pixelprobe.db /path/to/instance/pixelprobe.db.backup
   ```

2. **Update Docker Compose** - Add PostgreSQL and Redis services:
   ```yaml
   services:
     postgres:
       image: postgres:15-alpine
       environment:
         POSTGRES_DB: pixelprobe
         POSTGRES_USER: pixelprobe
         POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
       volumes:
         - postgres_data:/var/lib/postgresql/data
   
     mediachecker:
       image: ttlequals0/pixelprobe:2.3.3
       environment:
         POSTGRES_HOST: postgres
         POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
         # ... other settings
   ```

3. **Run migration**:
   ```bash
   docker-compose up -d postgres
   sleep 15
   
   # Migrate existing SQLite data
   docker run --rm \
     --network pixelprobe_pixelprobe-network \
     -v "/path/to/instance/pixelprobe.db:/app/pixelprobe.db:ro" \
     -e POSTGRES_HOST=postgres \
     -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
     ttlequals0/pixelprobe:2.3.3 \
     python migrate_to_postgres.py --sqlite-path /app/pixelprobe.db
   ```

For detailed migration instructions, see [MIGRATION_v2.2.0.md](MIGRATION_v2.2.0.md).

## 📚 Documentation

### Quick Links
- **[Docker Setup Guide](docs/DOCKER_SETUP.md)** - Complete Docker Compose setup with container explanations
- **[System Architecture](docs/SYSTEM_ARCHITECTURE.md)** - Container architecture, Celery queues, and data flow
- **[API Documentation](docs/api/README.md)** - Complete REST API reference  
- **[Architecture Overview](docs/ARCHITECTURE.md)** - Application layers and design
- **[Performance Tuning](docs/PERFORMANCE_TUNING.md)** - Optimization strategies
- **[Developer Guide](docs/developer/README.md)** - Development setup and guidelines

### API Client Examples
- **[Python Client](docs/examples/python-client.py)** - Full-featured Python client with CLI
- **[Node.js Client](docs/examples/nodejs-client.js)** - JavaScript/Node.js client implementation
- **[Bash Client](docs/examples/bash-client.sh)** - Shell script client using curl and jq

## Configuration

### Environment Variables

PixelProbe uses environment variables for all configuration. Copy `.env.example` to `.env` and customize:

**Required Variables:**
- `SECRET_KEY` - Secure secret key for Flask sessions
- `MEDIA_PATH` - Host path to your media files (for Docker volume mounting)

**Optional Variables:**
- `DATABASE_URL` - Database connection string (default: PostgreSQL)
- `SCAN_PATHS` - Comma-separated directories to monitor inside container (default: `/media`)
- `TZ` - Timezone (default: UTC)
- `MAX_FILES_TO_SCAN` - Performance limit (default: 100)
- `MAX_SCAN_WORKERS` - Parallel scanning threads (default: 4)
- `PERIODIC_SCAN_SCHEDULE` - Automated scanning schedule
- `CLEANUP_SCHEDULE` - Automated cleanup schedule
- `EXCLUDED_PATHS` - Paths to ignore during scanning
- `EXCLUDED_EXTENSIONS` - File extensions to ignore

See `.env.example` for complete configuration options with examples.

### Multiple Scan Paths

You can configure multiple directories to scan:

**Method 1: Docker Compose with Multiple Volumes**
```yaml
environment:
  - SCAN_PATHS=/movies,/tv-shows,/backup
volumes:
  - /mnt/movies:/movies
  - /mnt/tv-shows:/tv-shows  
  - /mnt/backup:/backup
```

**Method 2: Single Volume with Subdirectories**
```bash
export MEDIA_PATH=/mnt/all-media  # Contains subdirs: movies/, tv/, backup/
# docker-compose.yml uses: SCAN_PATHS=/media/movies,/media/tv,/media/backup
```

## Usage

### Web Interface

1. **Access the Dashboard**: Navigate to http://localhost:5001
2. **Start a Scan**: Click "Scan All Files" to begin scanning your media directories
3. **View Results**: Results appear in the table below with corruption status
4. **Filter Results**: Use the filter buttons to show only corrupted or healthy files
5. **File Actions**: 
   - **Rescan**: Re-examine a specific file
   - **Download**: Download the file to your local machine
6. **Schedules**: Manage automated scan schedules with multiple scan types
7. **Exclusions**: Interactive management of paths and extensions to exclude

### API Endpoints

The application provides REST API endpoints for automation:

- `GET /api/stats` - Get scanning statistics
- `GET /api/scan-results` - Get paginated scan results
- `POST /api/scan-all` - Start a full scan
- `POST /api/scan-file` - Scan a specific file
- `GET /api/schedules` - List all scan schedules
- `POST /api/schedules` - Create a new scan schedule
- `GET /api/exclusions` - Get current exclusions
- `PUT /api/exclusions` - Update exclusions

See [API Documentation](docs/api/README.md) for complete reference.

### Command Line Usage

```python
from media_checker import PixelProbe

checker = PixelProbe()

# Scan a single file
result = checker.scan_file('/path/to/media/file.mp4')
print(f"Corrupted: {result['is_corrupted']}")

# Scan multiple directories
results = checker.scan_directories(['/path/to/media1', '/path/to/media2'])
for result in results:
    if result['is_corrupted']:
        print(f"Corrupted file: {result['file_path']}")
```

## Supported File Formats

### Video Formats
- **Common**: MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V
- **HEVC/H.265**: HEVC, H265 
- **Professional**: ProRes, MXF, DNxHD, DNxHR
- **Broadcast**: MTS, M2TS, AVCHD
- **Legacy**: MPG, MPEG, VOB, RM, RMVB

### Image Formats
- **Common**: JPEG, PNG, GIF, BMP, TIFF, WebP
- **Apple**: HEIC, HEIF
- **RAW Formats**: CR2, CR3, NEF, NRW, ARW, DNG, ORF, RW2, PEF, RAF

### Audio Formats
- **Lossy**: MP3, AAC, M4A, WMA, OGG, OGA, Opus
- **Lossless**: FLAC, WAV, AIFF, APE, WV
- **High-Resolution**: DSF, DFF (DSD)
- **Dolby/DTS**: AC3, DTS

## Development

### Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ttlequals0/PixelProbe.git
   cd PixelProbe
   ```

2. **Use development compose file**:
   ```bash
   docker-compose -f docker-compose.dev.yml up -d
   ```

### Testing

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest

# Run with coverage report
pytest --cov=pixelprobe --cov-report=html

# Run specific test categories
pytest tests/unit/           # Unit tests only
pytest tests/integration/    # Integration tests only
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Troubleshooting

### Database Errors After Updates

If you encounter **"no such table: scan_results"** errors after upgrading:

```bash
# Quick fix
docker exec pixelprobe python tools/fix_database_schema.py
```

### Common Issues

**FFmpeg/ImageMagick not found**:
- Ensure FFmpeg and ImageMagick are installed and in PATH
- On Ubuntu/Debian: `sudo apt-get install ffmpeg imagemagick`
- On macOS: `brew install ffmpeg imagemagick`

**Permission errors**:
- Ensure the application has read access to your media directories
- Check file permissions and ownership

**Performance issues with large libraries**:
- Increase `MAX_SCAN_WORKERS` (default: 4, try 8-16 for powerful systems)
- Monitor system resources during scanning
- Use SSD storage for the database if possible

### Getting Help

1. **Check logs first**: `docker logs pixelprobe` 
2. **Search existing issues**: [GitHub Issues](https://github.com/ttlequals0/PixelProbe/issues)
3. **Create new issue**: Include logs and system info

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [FFmpeg](https://ffmpeg.org/) for video analysis
- [ImageMagick](https://imagemagick.org/) for image processing
- [PIL/Pillow](https://pillow.readthedocs.io/) for Python image handling
- Inspired by media integrity checkers and corruption detectors

## Support

For issues, questions, or contributions, please visit the [GitHub repository](https://github.com/ttlequals0/PixelProbe/issues).