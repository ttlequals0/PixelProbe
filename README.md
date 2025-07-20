# PixelProbe

<div align="center">
  <img src="static/images/pixelprobe-logo.png" alt="PixelProbe Logo" width="200" height="200">
</div>

PixelProbe is a comprehensive media file corruption detection tool with a modern web interface. It helps you identify and manage corrupted video and image files across your media libraries.

**Version 2.0.53** fixes file-changes scanning progress tracking with smooth per-file updates and proper async database writes.

## ✨ Features

- **🎬 Comprehensive Media Support**: Detects corruption in videos (MP4, MKV, AVI, MOV, etc.) and images (JPEG, PNG, GIF, etc.)
- **🔍 Advanced Detection**: Uses FFmpeg, ImageMagick, and PIL for thorough corruption analysis
- **⚠️ Warning System**: Differentiates between corrupted files and files with minor issues
- **🌐 Modern Web Interface**: Clean, responsive dark/light mode UI for viewing and managing scan results
- **💾 Persistent Storage**: SQLite database stores scan results across application restarts
- **📁 File Management**: Download, view, mark as good, and manage files directly from the web interface
- **🐳 Docker Support**: Easy deployment with Docker and docker-compose
- **⚙️ Configurable**: Environment variable configuration for scan directories and behavior
- **⚡ Parallel Scanning**: Multi-threaded scanning for improved performance with real-time progress
- **📊 System Statistics**: Detailed system statistics with monitored paths and file tracking
- **🔄 Bulk Actions**: Select multiple files for rescanning, deep scanning, or marking as good
- **📈 Phase-Based Progress**: Clear scanning phases showing discovery, database addition, and scanning stages
- **📅 Scheduled Scanning**: Automated scans with cron or interval-based scheduling
- **🚫 Path & Extension Exclusions**: Configure paths and file types to exclude from scanning
- **🔍 Multiple Scan Types**: Normal scan, orphan cleanup, and file changes detection

## 📸 Screenshots

### Desktop Interface

#### Light Mode
![Desktop Light Mode](docs/screenshots/desktop-light.png)

The modern desktop interface features:
- Hulu-inspired design with clean, professional aesthetics
- Pi-hole style sidebar navigation for easy access to all features
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

<div align="center">
  <img src="docs/screenshots/mobile-light-menu.png" alt="Mobile Menu Light" width="300" style="margin: 10px">
  <img src="docs/screenshots/mobile-dark-results.png" alt="Mobile Results Dark" width="300" style="margin: 10px">
</div>

### Feature Highlights

#### System Statistics Modal
![System Statistics](docs/screenshots/system-stats-modal.png)

Comprehensive system overview showing:
- Database statistics with file counts by status
- Monitored paths with accessibility status
- Scan performance metrics
- File system completion percentages

#### Scan Output Details
![Scan Output Modal](docs/screenshots/scan-output-modal.png)

Detailed scan results viewer:
- Shows specific corruption or warning details
- Displays which tool detected the issue
- Provides full scan output for debugging

### Advanced Features

#### Scheduled Scanning
![Scan Schedules](docs/screenshots/features/scan-schedules.png)

Create and manage automated scan schedules:
- Support for both cron expressions and simple intervals
- Multiple scan types: Normal Scan, Orphan Cleanup, File Changes
- View next run times and last execution status
- Enable/disable schedules with a single click

![Create Schedule](docs/screenshots/features/create-schedule.png)

Flexible scheduling options:
- Name your schedules for easy identification
- Choose between cron expressions for advanced users or simple intervals
- Select scan type to automate different maintenance tasks
- Optionally specify custom scan paths

#### Exclusion Management
![Exclusions Management](docs/screenshots/features/exclusions-management.png)

Interactive exclusion management with modern UI:
- Add exclusions individually with dedicated input fields
- Remove specific exclusions with one-click delete buttons
- See all exclusions at a glance in a clean list format
- Press Enter to quickly add new exclusions
- Separate management for paths and file extensions
- Real-time updates with no page refresh needed

## 🚀 Quick Start

### Using Docker (Recommended)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ttlequals0/PixelProbe.git
   cd PixelProbe
   ```

2. **Set your media path**:
   ```bash
   export MEDIA_PATH=/path/to/your/actual/media/directory
   ```

3. **Start the application**:
   ```bash
   docker-compose up -d
   ```

4. **Access the web interface**:
   Open http://localhost:5001 in your browser

5. **Start scanning**:
   Click "Scan All Files" to begin analyzing your media library

**Note**: The `MEDIA_PATH` environment variable is only needed for Docker volume mounting. The application scans paths defined in `SCAN_PATHS` inside the container.

### Docker Image Versions

PixelProbe is available on Docker Hub as `ttlequals0/pixelprobe`. Check the [Docker Hub page](https://hub.docker.com/r/ttlequals0/pixelprobe/tags) for all available versions.

**Current stable versions:**
- **`ttlequals0/pixelprobe:latest`** - Latest stable release (v2.0.55)
- **`ttlequals0/pixelprobe:2.0.55`** - Major refactoring for code quality, added comprehensive audio/video/image format support
- **`ttlequals0/pixelprobe:2.0.53`** - Fixed file-changes progress tracking with async updates

You can specify a specific version in your `docker-compose.yml`:
```yaml
services:
  pixelprobe:
    image: ttlequals0/pixelprobe:2.0.55  # or :latest for newest
```

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

### Manual Installation

1. **Install dependencies**:
   ```bash
   # System dependencies
   sudo apt-get update
   sudo apt-get install ffmpeg imagemagick libmagic1
   
   # Python dependencies
   pip install -r requirements.txt
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. **Run the application**:
   ```bash
   python app.py
   ```

## Configuration

### Environment Variables

Configure the application by editing the `.env` file:

```env
# Comma-separated list of directories to scan
SCAN_PATHS=/path/to/your/media,/another/path/to/media

# Database configuration
DATABASE_URL=sqlite:///media_checker.db

# Secret key for Flask sessions
SECRET_KEY=your-very-secret-key-here

# Optional: Set to development for debugging
FLASK_ENV=production

# Scheduled Scanning (optional)
# Cron format: "cron:minute hour day month day_of_week"
# Interval format: "interval:unit:value" (unit: hours/days/weeks)
PERIODIC_SCAN_SCHEDULE=cron:0 2 * * *  # Daily at 2 AM
# PERIODIC_SCAN_SCHEDULE=interval:hours:6  # Every 6 hours

# Scheduled Cleanup (optional)
CLEANUP_SCHEDULE=cron:0 3 * * 0  # Weekly on Sunday at 3 AM
# CLEANUP_SCHEDULE=interval:days:7  # Every 7 days

# Path and Extension Exclusions (optional)
EXCLUDED_PATHS=/media/temp,/media/cache
EXCLUDED_EXTENSIONS=.tmp,.temp,.cache
```

### Docker Compose Configuration

For Docker deployment, you can also configure paths in `docker-compose.yml`:

```yaml
services:
  pixelprobe:
    image: ttlequals0/pixelprobe:2.0.53  # Specify version
    environment:
      - SCAN_PATHS=/media
      - DATABASE_URL=sqlite:///media_checker.db
    volumes:
      - ${MEDIA_PATH}:/media
```

**Recommended**: Always specify a version tag instead of using `:latest` to ensure consistent deployments.

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

**For Docker**: Set the `MEDIA_PATH` environment variable for volume mounting:
```bash
export MEDIA_PATH=/path/to/your/actual/media
docker-compose up -d
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
6. **Schedules**: Manage automated scan schedules with multiple scan types (v2.0.44+)
   - Create schedules for normal scans, orphan cleanup, or file changes detection
   - Use cron expressions or simple intervals
   - Enable/disable schedules on demand
7. **Exclusions**: Interactive management of paths and extensions to exclude (v2.0.44+)
   - Add exclusions individually with dedicated input fields
   - Remove specific exclusions with one click
   - Press Enter to quickly add new exclusions

### API Endpoints

The application provides REST API endpoints:

- `GET /api/stats` - Get scanning statistics
- `GET /api/scan-results` - Get paginated scan results
- `POST /api/scan-all` - Start a full scan of all configured directories
- `POST /api/scan-file` - Scan a specific file
- `GET /api/download/<id>` - Download a file
- `GET /api/schedules` - List all scan schedules (v2.0.41+)
- `POST /api/schedules` - Create a new scan schedule (v2.0.41+)
- `PUT /api/schedules/<id>` - Update a scan schedule (v2.0.41+)
- `DELETE /api/schedules/<id>` - Delete a scan schedule (v2.0.41+)
- `GET /api/exclusions` - Get current exclusions (v2.0.41+)
- `PUT /api/exclusions` - Update exclusions (v2.0.41+)
- `POST /api/exclusions/<type>/<item>` - Add individual exclusion (v2.0.44+)
- `DELETE /api/exclusions/<type>/<item>` - Remove individual exclusion (v2.0.44+)

### Command Line Usage

You can also use the PixelProbe class directly in Python:

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

## Corruption Detection

PixelProbe uses multiple methods to detect file corruption:

### Video Files
- **FFmpeg Analysis**: Deep analysis of video streams and metadata
- **Frame Validation**: Attempts to decode video frames to detect corruption
- **Quick Scan**: Fast check of first 10 seconds for immediate feedback
- **Stream Validation**: Verifies video and audio stream integrity
- **HEVC/ProRes Support**: Specialized detection for modern codecs

### Image Files
- **PIL Verification**: Uses Python Imaging Library for basic corruption detection
- **ImageMagick**: Advanced image analysis and validation
- **Dimension Checks**: Validates image dimensions and properties
- **Format Validation**: Ensures files match their declared format
- **RAW/HEIC Support**: Handles camera RAW files and Apple's HEIC format

### Audio Files
- **FFmpeg Audio Analysis**: Comprehensive audio stream validation
- **Decode Testing**: Attempts to decode audio to detect corruption
- **Header Validation**: Checks for missing or corrupted headers
- **Format-Specific Tests**: 
  - FLAC: CRC validation and built-in integrity checking
  - MP3: Frame header validation
  - Lossless formats: Bit-perfect verification
- **Deep Scan Mode**: Full file analysis for timestamp and packet errors

### Detection Accuracy
- **High Confidence**: 100% detection of files with broken headers, truncated files, and I/O errors
- **Moderate Confidence**: ~85% detection of random corruption patterns
- **Low Confidence**: ~50% detection of zero-byte overwrites

## Supported File Formats

### Video Formats
- **Common**: MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V
- **HEVC/H.265**: HEVC, H265 
- **Professional**: ProRes, MXF, DNxHD, DNxHR
- **Broadcast**: MTS, M2TS, AVCHD
- **Legacy**: MPG, MPEG, VOB, RM, RMVB
- **Other**: 3GP, 3G2, F4V, F4P, OGV, ASF, AMV, M2V, SVI

### Image Formats
- **Common**: JPEG, PNG, GIF, BMP, TIFF, WebP
- **Apple**: HEIC, HEIF
- **RAW Formats**: 
  - Canon: CR2, CR3
  - Nikon: NEF, NRW
  - Sony: ARW, SRF, SR2
  - Adobe: DNG
  - Others: ORF (Olympus), RW2 (Panasonic), PEF/PTX (Pentax), RAF (Fujifilm), X3F (Sigma), DCR/KDC (Kodak), MOS (Leaf)
- **Professional**: PSD, EXR, HDR, SVG
- **Other**: ICO, PBM, PGM, PPM, PNM, FITS

### Audio Formats (NEW!)
- **Lossy**: MP3, AAC, M4A, WMA, OGG, OGA, Opus, AMR
- **Lossless**: FLAC, WAV, AIFF, APE, WV (WavPack), TTA, CAF
- **Uncompressed**: WAV, AIFF, AU, SND, VOC
- **High-Resolution**: DSF, DFF (DSD)
- **Dolby/DTS**: AC3, DTS
- **Container**: MKA (Matroska Audio), M4B (Audiobook)
- **Legacy**: RA, RAM (RealAudio), GSM, MIDI

## Performance Considerations

- **Scan Duration**: Video scanning can take 2-20 minutes per file depending on size
- **Resource Usage**: CPU-intensive during scanning (80-100% utilization)
- **Memory Usage**: Minimal memory footprint, processes files individually
- **Disk I/O**: Sequential read access to media files

## Architecture

```
PixelProbe/
├── app.py                 # Flask web application
├── media_checker.py       # Core corruption detection logic
├── models.py             # SQLAlchemy database models
├── version.py            # Version information
├── templates/
│   ├── index.html        # Legacy web interface
│   ├── index_modern.html # Modern responsive UI
│   └── api_docs.html     # API documentation
├── static/
│   ├── css/             # Stylesheets
│   │   ├── desktop.css  # Desktop responsive styles
│   │   ├── mobile.css   # Mobile responsive styles
│   │   └── logo-styles.css # Logo styling
│   ├── js/              # JavaScript
│   │   └── app.js       # Main application logic
│   └── images/          # Images and icons
├── tools/               # Utility scripts for maintenance
│   ├── README.md        # Documentation for tools
│   └── *.py             # Various fix and migration scripts
├── docs/                # Documentation
│   └── screenshots/     # UI screenshots
├── scripts/             # Development and deployment scripts
├── requirements.txt     # Python dependencies
├── Dockerfile           # Docker container configuration
├── docker-compose.yml   # Docker Compose setup
└── README.md           # This file
```

## 🛠️ Utility Tools

The `tools/` directory contains utility scripts for database maintenance and migration tasks. These are useful for:

- Fixing false positives from older versions
- Adding new database columns
- Resetting files for rescanning with updated logic

See [tools/README.md](tools/README.md) for detailed documentation on each tool.

## Development

### Running in Development Mode

```bash
export FLASK_ENV=development
python app.py
```

### Adding New File Formats

To add support for new file formats:

1. Update `supported_formats` in `PixelProbe.__init__()`
2. Add detection logic in `_check_*_corruption()` methods
3. Update the documentation

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Troubleshooting

### Common Issues

**FFmpeg/ImageMagick not found**:
- Ensure FFmpeg and ImageMagick are installed and in PATH
- On Ubuntu/Debian: `sudo apt-get install ffmpeg imagemagick`
- On macOS: `brew install ffmpeg imagemagick`

**Permission errors**:
- Ensure the application has read access to your media directories
- Check file permissions and ownership

**Database errors**:
- Delete the database file to reset: `rm media_checker.db`
- Ensure write permissions in the application directory

**Memory issues with large files**:
- The application processes files individually to minimize memory usage
- For very large files, consider increasing system swap space

### Logs and Debugging

Enable debug logging by setting `FLASK_ENV=development` in your `.env` file.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [FFmpeg](https://ffmpeg.org/) for video analysis
- [ImageMagick](https://imagemagick.org/) for image processing
- [PIL/Pillow](https://pillow.readthedocs.io/) for Python image handling
- Inspired by [check-media-integrity](https://github.com/ftarlao/check-media-integrity)
- Reference implementations from [broken-video-file-detector](https://github.com/EuropaYou/broken-video-file-detector) and [CorruptVideoFileInspector](https://github.com/nhershy/CorruptVideoFileInspector)

## Support

For issues, questions, or contributions, please visit the [GitHub repository](https://github.com/ttlequals0/PixelProbe/issues).