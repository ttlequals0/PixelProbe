# PixelProbe

<div align="center">
  <img src="static/images/pixelprobe-logo.png" alt="PixelProbe Logo" width="200" height="200">
</div>

PixelProbe is a comprehensive media file corruption detection tool with a modern web interface. It helps you identify and manage corrupted video and image files across your media libraries.

**Version 2.0** introduces a completely redesigned UI with Hulu-inspired aesthetics, Pi-hole style navigation, and full mobile responsiveness.

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
- **`ttlequals0/pixelprobe:latest`** - Latest stable release (v1.26)
- **`ttlequals0/pixelprobe:1.26`** - UI improvements and ImageMagick UTF-8 fixes
- **`ttlequals0/pixelprobe:1.25`** - Database resilience for long-running scans
- **`ttlequals0/pixelprobe:1.24`** - UI color visibility fixes
- **`ttlequals0/pixelprobe:1.23`** - SQLite WAL mode and connection improvements

You can specify a specific version in your `docker-compose.yml`:
```yaml
services:
  pixelprobe:
    image: ttlequals0/pixelprobe:1.26  # or :latest for newest
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
```

### Docker Compose Configuration

For Docker deployment, you can also configure paths in `docker-compose.yml`:

```yaml
services:
  pixelprobe:
    image: ttlequals0/pixelprobe:1.03  # Specify version
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

1. **Access the Dashboard**: Navigate to http://localhost:5000
2. **Start a Scan**: Click "Scan All Files" to begin scanning your media directories
3. **View Results**: Results appear in the table below with corruption status
4. **Filter Results**: Use the filter buttons to show only corrupted or healthy files
5. **File Actions**: 
   - **Rescan**: Re-examine a specific file
   - **Download**: Download the file to your local machine

### API Endpoints

The application provides REST API endpoints:

- `GET /api/stats` - Get scanning statistics
- `GET /api/scan-results` - Get paginated scan results
- `POST /api/scan-all` - Start a full scan of all configured directories
- `POST /api/scan-file` - Scan a specific file
- `GET /api/download/<id>` - Download a file

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

### Image Files
- **PIL Verification**: Uses Python Imaging Library for basic corruption detection
- **ImageMagick**: Advanced image analysis and validation
- **Dimension Checks**: Validates image dimensions and properties
- **Format Validation**: Ensures files match their declared format

### Detection Accuracy
- **High Confidence**: 100% detection of files with broken headers, truncated files, and I/O errors
- **Moderate Confidence**: ~85% detection of random corruption patterns
- **Low Confidence**: ~50% detection of zero-byte overwrites

## Supported File Formats

### Video Formats
- MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V

### Image Formats
- JPEG, PNG, GIF, BMP, TIFF, WebP

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
├── templates/
│   └── index.html        # Web interface template
├── tools/                # Utility scripts for maintenance
│   ├── README.md        # Documentation for tools
│   └── *.py             # Various fix and migration scripts
├── screenshots/         # UI screenshots for documentation
├── requirements.txt      # Python dependencies
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