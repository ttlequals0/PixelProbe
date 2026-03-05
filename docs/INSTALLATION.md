# PixelProbe Installation Guide

This guide provides complete installation instructions for PixelProbe, covering both Docker (recommended) and manual installation methods.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start with Docker](#quick-start-with-docker)
- [Manual Installation](#manual-installation)
- [First-Time Setup](#first-time-setup)
- [Verification](#verification)
- [Next Steps](#next-steps)

## Prerequisites

### For Docker Installation (Recommended)

- **Docker**: Version 20.10 or higher
- **Docker Compose**: Version 2.0 or higher
- **System Requirements**:
  - 2 CPU cores minimum (4+ recommended)
  - 4 GB RAM minimum (8 GB recommended for large libraries)
  - 10 GB free disk space for database
  - Additional space for media files

### For Manual Installation

- **Operating System**: Ubuntu 20.04+, Debian 11+, macOS 11+, or Windows 10+ (WSL2)
- **Python**: 3.9 or higher
- **PostgreSQL**: 15 or higher
- **Redis**: 7.0 or higher
- **System Tools**:
  - FFmpeg: 4.4 or higher
  - ImageMagick: 6.9.10+ or 7.0+
  - Git: 2.25 or higher

#### Installing System Dependencies

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y \
    python3 python3-dev python3-venv python3-pip \
    ffmpeg imagemagick \
    postgresql-15 redis-server \
    git curl wget
```

**macOS:**
```bash
brew install python@3.11 ffmpeg imagemagick postgresql@15 redis git
brew services start postgresql@15
brew services start redis
```

**Windows (WSL2):**
```bash
# Install WSL2 with Ubuntu 22.04, then follow Ubuntu instructions above
```

## Quick Start with Docker

This is the recommended installation method for most users.

### 1. Clone the Repository

```bash
git clone https://github.com/ttlequals0/PixelProbe.git
cd PixelProbe
```

### 2. Configure Environment Variables

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

Edit `.env` with your preferred text editor and set the required variables:

```bash
# Generate a secure secret key
python3 -c "import secrets; print(secrets.token_hex(32))"

# Edit .env file
nano .env  # or vim, code, etc.
```

**Required settings in `.env`:**
```bash
# Security (use the generated secret key from above)
SECRET_KEY=your-generated-64-character-hex-key-here

# Database password (choose a secure password)
POSTGRES_PASSWORD=your-secure-database-password

# Media path on host system (absolute path)
MEDIA_PATH=/path/to/your/media/directory

# Scan paths inside container (usually /media)
SCAN_PATHS=/media
```

**Optional settings:**
```bash
# Timezone (default: UTC)
TZ=America/New_York

# Performance tuning (see CONFIGURATION.md for details)
MAX_WORKERS=10
CELERY_CONCURRENCY=4
BATCH_SIZE=100

# Port (default: 5000)
PORT=5000
```

### 3. Start the Application

```bash
docker-compose up -d
```

This will:
1. Pull the latest PixelProbe image from Docker Hub
2. Start PostgreSQL database
3. Start Redis message broker
4. Start PixelProbe web application
5. Start Celery worker for background processing

### 4. Verify Containers are Running

```bash
docker-compose ps
```

You should see all containers in "Up" state:
```
NAME                      STATUS
pixelprobe-postgres       Up (healthy)
pixelprobe-redis          Up (healthy)
pixelprobe-app            Up (healthy)
pixelprobe-celery-worker  Up
```

### 5. View Logs

```bash
# All containers
docker-compose logs -f

# Specific container
docker-compose logs -f pixelprobe-app
docker-compose logs -f pixelprobe-celery-worker
```

## Manual Installation

For advanced users who prefer manual installation without Docker.

### 1. Clone the Repository

```bash
git clone https://github.com/ttlequals0/PixelProbe.git
cd PixelProbe
```

### 2. Set Up PostgreSQL Database

```bash
# Create database user and database
sudo -u postgres psql << EOF
CREATE USER pixelprobe WITH PASSWORD 'your-secure-password';
CREATE DATABASE pixelprobe OWNER pixelprobe;
GRANT ALL PRIVILEGES ON DATABASE pixelprobe TO pixelprobe;
\q
EOF
```

### 3. Set Up Redis

Redis should already be running if installed via package manager. Verify:

```bash
redis-cli ping
# Should return: PONG
```

### 4. Create Python Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/macOS
# or
.\venv\Scripts\activate  # Windows
```

### 5. Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Configure Environment Variables

```bash
cp .env.example .env
nano .env  # Edit configuration
```

Set the following in `.env`:
```bash
SECRET_KEY=your-generated-secret-key
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=pixelprobe
POSTGRES_USER=pixelprobe
POSTGRES_PASSWORD=your-secure-password
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
SCAN_PATHS=/path/to/your/media
```

### 7. Initialize Database

```bash
# The database tables will be created automatically on first run
python app.py
```

Press Ctrl+C after seeing "Running on http://127.0.0.1:5000".

### 8. Start Celery Worker

In a new terminal (with venv activated):

```bash
source venv/bin/activate
python celery_worker.py
```

### 9. Start Celery Beat (Optional - for scheduled scans)

In a new terminal (with venv activated):

```bash
source venv/bin/activate
celery -A celery_config beat --loglevel=info
```

### 10. Start Web Application

In a new terminal (with venv activated):

```bash
source venv/bin/activate
python app.py
```

Or for production with Gunicorn:

```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## First-Time Setup

After installation, you must create the admin account.

### Option 1: Web Setup Wizard

1. Open your browser and navigate to http://localhost:5000

2. You should be redirected to the first-run setup page
3. Create your admin account with a secure password (minimum 8 characters)
4. Click "Create Admin Account"
5. You'll be automatically logged in

### Option 2: API Setup

Create the admin account via API:

```bash
curl -X POST http://localhost:5000/api/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"password":"YourSecurePassword123"}'
```

**Security Note:** No default admin account exists. You must explicitly create it on first run. The setup endpoint is only available when no admin account exists.

## Verification

### 1. Check Web Interface

Open your browser at http://localhost:5000

You should see the login page. Log in with:
- Username: `admin`
- Password: (the password you created during setup)

### 2. Check API Health

```bash
curl http://localhost:5000/health
```

Should return:
```json
{"status": "healthy", "database": "connected", "redis": "connected"}
```

### 3. Check Celery Worker

```bash
# Docker
docker logs pixelprobe-celery-worker

# Manual (check the terminal where celery_worker.py is running)
```

You should see:
```
[tasks]
  . pixelprobe.tasks.cleanup_database_task
  . pixelprobe.tasks.file_changes_scan_task
  . pixelprobe.tasks.integrity_check_task
  . pixelprobe.tasks.scan_directory_task
```

### 4. Test a Simple Scan

1. Log in to the web interface
2. Click "Tools" in the sidebar
3. Click "Start Scan"
4. The scan should begin and show progress
5. Check the Dashboard for results

## Next Steps

After successful installation:

1. **Configure Scan Paths**: See [CONFIGURATION.md](CONFIGURATION.md) for details on setting up multiple scan paths
2. **Performance Tuning**: Adjust MAX_WORKERS and other settings based on your system
3. **Schedule Automated Scans**: Set up periodic scans in the web interface under Tools > Schedules
4. **Configure Exclusions**: Add paths and extensions to exclude under Tools > Exclusions
5. **Review API Documentation**: Access Swagger docs at /api/v1/docs (after login)

## Upgrade Process

### Docker Installation

```bash
# Stop containers
docker-compose down

# Pull latest image
docker-compose pull

# Start containers
docker-compose up -d

# Check logs
docker-compose logs -f
```

### Manual Installation

```bash
# Activate virtual environment
source venv/bin/activate

# Pull latest code
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Restart all services
# (Stop and restart app.py, celery_worker.py, celery beat)
```

## Troubleshooting

If you encounter issues during installation, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for solutions to common problems.

Common installation issues:
- FFmpeg/ImageMagick not found: Ensure they're installed and in PATH
- Database connection errors: Check PostgreSQL is running and credentials are correct
- Permission errors: Ensure user has read access to media directories (Docker: use `user:` directive)
- Port conflicts: Change PORT in .env if 5000 is already in use

## Getting Help

1. Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues
2. Review logs: `docker-compose logs` or check terminal output
3. Search existing issues: https://github.com/ttlequals0/PixelProbe/issues
4. Create new issue with logs and system info
