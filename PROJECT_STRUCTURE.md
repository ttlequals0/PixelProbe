# PixelProbe Project Structure

## Overview
This document describes the organization of the PixelProbe codebase after the v2.0 UI overhaul.

## Directory Structure

```
PixelProbe/
├── app.py                    # Main Flask application
├── media_checker.py          # Core media scanning logic
├── models.py                 # SQLAlchemy database models
├── version.py                # Version information
├── requirements.txt          # Python dependencies
├── Dockerfile                # Production Docker image
├── docker-compose.yml        # Production Docker Compose
├── docker-compose.simple.yml # Simplified Docker Compose
│
├── static/                   # Static assets
│   ├── css/
│   │   ├── desktop.css      # Desktop styles (Hulu-inspired)
│   │   ├── mobile.css       # Mobile-specific styles
│   │   └── logo-styles.css  # Logo animations
│   ├── js/
│   │   └── app.js           # Modern ES6+ JavaScript application
│   └── images/
│       ├── pixelprobe-logo.png
│       └── favicon files
│
├── templates/                # Jinja2 templates
│   ├── index_modern.html    # Modern UI template (v2.0+)
│   ├── index.html           # Legacy UI template
│   └── api_docs.html        # API documentation page
│
├── docker/                   # Docker development files
│   ├── Dockerfile.modern
│   ├── Dockerfile.modern-simple
│   ├── docker-compose.modern.yml
│   └── docker-compose.dev.yml
│
├── scripts/                  # Utility scripts
│   ├── docker-run-modern.sh
│   ├── run_modern_ui.sh
│   ├── run_test_ui.sh
│   ├── setup_and_run_local.sh
│   ├── setup_test_env.sh
│   ├── create_test_database.py
│   ├── test_database.py
│   ├── check_db_integrity.py
│   └── create_indexes.py
│
├── tools/                    # Database maintenance tools
│   ├── analyze_*.py         # Various analysis scripts
│   ├── fix_*.py            # Database fix scripts
│   └── README.md           # Tools documentation
│
├── docs/                     # Documentation
│   └── screenshots/         # UI screenshots for README
│       ├── desktop-*.png
│       ├── mobile-*.png
│       └── *-modal.png
│
├── instance/                 # Flask instance folder (gitignored)
│   └── media_checker.db     # SQLite database
│
└── venv/                     # Python virtual environment (gitignored)
```

## Key Files

### Application Core
- `app.py` - Flask routes and API endpoints
- `media_checker.py` - Media file scanning implementation
- `models.py` - Database schema definitions

### Modern UI (v2.0+)
- `static/js/app.js` - Modular JavaScript with classes for:
  - ThemeManager - Dark/light mode handling
  - SidebarManager - Mobile navigation
  - APIClient - Centralized API communication
  - ProgressManager - 3-phase progress tracking
  - TableManager - Advanced data display
  - PixelProbeApp - Main application controller

- `static/css/desktop.css` - Hulu-inspired desktop styles
- `static/css/mobile.css` - Responsive mobile overrides
- `templates/index_modern.html` - Modern UI template

### Configuration
- `docker-compose.yml` - Production deployment
- `docker-compose.simple.yml` - Simplified deployment
- `Dockerfile` - Production container image

## Development Notes

### UI Architecture
The v2.0 UI uses a modern, modular JavaScript architecture with:
- ES6+ classes for organization
- CSS variables for theming
- Separate desktop/mobile stylesheets
- Pi-hole style sidebar navigation
- Hulu-inspired color scheme (#1ce783)

### API Endpoints
All API endpoints are prefixed with `/api/`:
- `/api/scan-results` - Get/search scan results
- `/api/scan-all` - Start full scan
- `/api/scan-status` - Get current scan status
- `/api/stats` - Get system statistics
- `/api/system-info` - Get detailed system information
- `/api/export-csv` - Export results to CSV

### Docker Deployment
- Production: `docker-compose up -d`
- Development: `docker-compose -f docker/docker-compose.dev.yml up`
- Simple mode: `docker-compose -f docker-compose.simple.yml up -d`