# PixelProbe installation guide

How to install PixelProbe, either with Docker (recommended) or manually.

## Table of contents

- [Prerequisites](#prerequisites)
- [Quick start with Docker](#quick-start-with-docker)
- [Manual installation](#manual-installation)
- [First-time setup](#first-time-setup)
- [Verification](#verification)
- [Next steps](#next-steps)

## Prerequisites

### For Docker installation (recommended)

- **Docker**: Version 20.10 or higher
- **Docker Compose**: Version 2.0 or higher
- **System Requirements**:
  - 2 CPU cores minimum (4+ recommended)
  - 4 GB RAM minimum (8 GB recommended for large libraries). With only 4 GB,
    lower CELERY_CONCURRENCY to 2 - the default of 4 slots budgets roughly
    2 GB per worker child
  - 10 GB free disk space for database
  - Additional space for media files

### For manual installation

- **Operating System**: Ubuntu 20.04+, Debian 11+, macOS 11+, or Windows 10+ (WSL2)
- **Python**: 3.10-3.12 (3.12 recommended; the Docker image ships 3.12)
- **PostgreSQL**: 15 or higher
- **Redis**: 7.0 or higher, or Valkey (the Docker stack uses valkey/valkey:9-alpine)
- **System Tools**:
  - FFmpeg: 4.4 or higher (the Docker image ships 8.0.1)
  - ImageMagick: 6.9.10+ or 7.0+
  - Git: 2.25 or higher

#### Installing system dependencies

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y \
    python3 python3-dev python3-venv python3-pip \
    ffmpeg imagemagick \
    postgresql redis-server \
    git curl wget
```

PixelProbe supports PostgreSQL 15 through 18; the distro default is fine. For
PostgreSQL 18 on releases that ship an older version, use the
[PGDG apt repository](https://www.postgresql.org/download/linux/ubuntu/).

**macOS:**
```bash
brew install python@3.12 ffmpeg imagemagick postgresql@18 redis git
brew services start postgresql@18
brew services start redis
```

**Windows (WSL2):**
```bash
# Install WSL2 with Ubuntu 22.04, then follow Ubuntu instructions above
```

## Quick start with Docker

Recommended for most users.

### 1. Clone the repository

```bash
git clone https://github.com/ttlequals0/PixelProbe.git
cd PixelProbe
```

### 2. Configure environment variables

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

# Performance tuning (see configuration.md for details)
MAX_WORKERS=10
CELERY_CONCURRENCY=4
BATCH_SIZE=100

# Port (default: 5000)
PORT=5000
```

### 3. Start the application

```bash
docker-compose up -d
```

This will:
1. Pull the latest PixelProbe image from Docker Hub
2. Start PostgreSQL database
3. Start Redis message broker
4. Start PixelProbe web application
5. Start Celery worker for background processing

### 4. Verify containers are running

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

### 5. View logs

```bash
# All containers
docker-compose logs -f

# Specific container
docker-compose logs -f pixelprobe-app
docker-compose logs -f pixelprobe-celery-worker
```

## Manual installation

For running PixelProbe without Docker.

### 1. Clone the repository

```bash
git clone https://github.com/ttlequals0/PixelProbe.git
cd PixelProbe
```

### 2. Set up PostgreSQL database

```bash
# Create database user and database
sudo -u postgres psql << EOF
CREATE USER pixelprobe WITH PASSWORD 'your-secure-password';
CREATE DATABASE pixelprobe OWNER pixelprobe;
GRANT ALL PRIVILEGES ON DATABASE pixelprobe TO pixelprobe;
\q
EOF
```

### 3. Set up Redis

Redis should already be running if installed via package manager. Verify:

```bash
redis-cli ping
# Should return: PONG
```

### 4. Create Python virtual environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/macOS
# or
.\venv\Scripts\activate  # Windows
```

### 5. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Configure environment variables

```bash
cp .env.example .env
nano .env  # Edit configuration
```

Set the following in `.env`:
```bash
SECRET_KEY=your-generated-secret-key
FLASK_ENV=production
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=pixelprobe
POSTGRES_USER=pixelprobe
POSTGRES_PASSWORD=your-secure-password
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
SCAN_PATHS=/path/to/your/media
```

Notes:
- `FLASK_ENV=production` is required for a real deployment; the code default
  is `development` (the Docker image sets production for you).
- If you serve the app over plain HTTP on a LAN (no TLS), also set
  `SESSION_COOKIE_SECURE=false` or the browser will refuse to send the
  session cookie and login will not stick.

### 7. Initialize database

```bash
# The database tables will be created automatically on first run
python app.py
```

Press Ctrl+C after seeing "Running on http://127.0.0.1:5000".

### 8. Start Celery worker

In a new terminal (with venv activated):

```bash
source venv/bin/activate
python celery_worker.py
```

### 9. Start web application

In a new terminal (with venv activated):

```bash
source venv/bin/activate
python app.py
```

Or for production with Gunicorn:

```bash
gunicorn -c gunicorn.conf.py app:app
```

Use the bundled `gunicorn.conf.py` rather than bare flags like `-w 4` - the
config file also sets the 300-second worker timeout that long scan requests
need.

## First-time setup

After installation, you must create the admin account.

### Option 1: web setup wizard

1. Open your browser and navigate to http://localhost:5000

2. You should be redirected to the first-run setup page
3. Create your admin account with a secure password (minimum 8 characters)
4. Click "Create Admin Account"
5. You'll be automatically logged in

### Option 2: API setup

Create the admin account via API:

```bash
curl -X POST http://localhost:5000/api/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"password":"YourSecurePassword123"}'
```

**Security note:** No default admin account exists. You must explicitly create it on first run. The setup endpoint is only available when no admin account exists.

## Verification

### 1. Check web interface

Open your browser at http://localhost:5000

You should see the login page. Log in with:
- Username: `admin`
- Password: (the password you created during setup)

### 2. Check API health

```bash
curl http://localhost:5000/healthz
```

Should return:
```json
{"status": "ok", "version": "2.8.0"}
```

Note: `/health` also exists but requires authentication; `/healthz` is the
unauthenticated liveness probe used by the container healthcheck.

### 3. Check Celery worker

```bash
# Docker
docker logs pixelprobe-celery-worker

# Manual (check the terminal where celery_worker.py is running)
```

You should see:
```
[tasks]
  . pixelprobe.tasks.calculate_file_hash_task
  . pixelprobe.tasks.check_file_exists_task
  . pixelprobe.tasks.health_check_task
  . pixelprobe.tasks.reload_schedules_task
  . pixelprobe.tasks.run_retention_cleanup
  . pixelprobe.tasks.scan_files_task
  . pixelprobe.tasks.scan_media_task
  . pixelprobe.tasks_parallel.discover_directory_task
  . pixelprobe.tasks_parallel.parallel_scan_orchestrator
  . pixelprobe.tasks_parallel.process_chunk_task
```

### 4. Test a simple scan

1. Log in to the web interface
2. Click "Tools" in the sidebar
3. Click "Start Scan"
4. The scan should begin and show progress
5. Check the Dashboard for results

## Next steps

After installation:

1. Set up scan paths; [configuration.md](configuration.md) covers multiple scan paths
2. Adjust MAX_WORKERS and other performance settings for your system
3. Set up periodic scans in the web interface under Tools > Schedules
4. Add paths and extensions to exclude under Tools > Exclusions
5. Read the API docs at /api-docs (after login)

## Upgrade process

### Docker installation

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

### Manual installation

```bash
# Activate virtual environment
source venv/bin/activate

# Pull latest code
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Restart all services
# (Stop and restart app.py and celery_worker.py)
```

## Troubleshooting

If you encounter issues during installation, see [troubleshooting.md](troubleshooting.md) for solutions to common problems.

Common installation issues:
- FFmpeg/ImageMagick not found: Ensure they're installed and in PATH
- Database connection errors: Check PostgreSQL is running and credentials are correct
- Permission errors: Ensure user has read access to media directories (Docker: use `user:` directive)
- Port conflicts: Change PORT in .env if 5000 is already in use

## Getting help

1. Check [troubleshooting.md](troubleshooting.md) for common issues
2. Review logs: `docker-compose logs` or check terminal output
3. Search existing issues: https://github.com/ttlequals0/PixelProbe/issues
4. Create new issue with logs and system info
