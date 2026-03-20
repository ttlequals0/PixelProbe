# PixelProbe Configuration Guide

Complete reference for all PixelProbe configuration options, environment variables, and performance tuning.

## Table of Contents

- [Environment Variables](#environment-variables)
- [Docker Compose Configuration](#docker-compose-configuration)
- [Database Configuration](#database-configuration)
- [Performance Tuning](#performance-tuning)
- [Scanning Configuration](#scanning-configuration)
- [Celery Configuration](#celery-configuration)
- [Data Retention Configuration](#data-retention-configuration)
- [Security Configuration](#security-configuration)
- [Resource Recommendations](#resource-recommendations)

## Environment Variables

All configuration is done via environment variables, either in `.env` file or directly in `docker-compose.yml`.

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret key (64 chars) | Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_PASSWORD` | PostgreSQL database password | `your-secure-password` |
| `MEDIA_PATH` | Host path to media files (Docker only) | `/mnt/media` |

### Database Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | PostgreSQL server hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL server port |
| `POSTGRES_DB` | `pixelprobe` | Database name |
| `POSTGRES_USER` | `pixelprobe` | Database username |
| `POSTGRES_PASSWORD` | (required) | Database password |
| `DATABASE_ECHO` | `false` | Enable SQL query logging (debug) |

### Application Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `production` | Flask environment (`production`, `development`, `testing`) |
| `SCAN_PATHS` | `/media` | Comma-separated paths to scan inside container |
| `TZ` | `UTC` | Timezone for timestamps (e.g., `America/New_York`) |
| `PORT` | `5000` | Web interface port |

### Performance Variables

| Variable | Default | Description | Recommendations |
|----------|---------|-------------|-----------------|
| `MAX_WORKERS` | `10` | Parallel file scanning workers per task | 10-24 for most systems |
| `BATCH_SIZE` | `100` | Files per batch during discovery | 50-200 based on file sizes |
| `MAX_OUTPUT_SIZE` | `10000` | Max output characters before rotation | 10000-50000 |
| `OUTPUT_ROTATION_ENABLED` | `true` | Enable output truncation | `true` for large scans |

**Performance Notes:**
- `MAX_WORKERS` controls parallelism within each scan task
- Each worker creates 1 database connection
- Total connections = 60 (main app pool) + MAX_WORKERS
- Keep under PostgreSQL max_connections (default: 100)

### Celery Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL for task queue |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Redis URL for results |
| `CELERY_CONCURRENCY` | `4` | Number of concurrent Celery tasks |
| `CELERY_LOG_LEVEL` | `INFO` | Celery log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

**Celery Notes:**
- `CELERY_CONCURRENCY` controls how many scan tasks run simultaneously
- Independent from `MAX_WORKERS` (which controls parallelism within each task)
- Recommended: 4-8 for most systems

### Redis Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_MAX_MEMORY` | `2gb` | Maximum Redis memory for task queue |

**Redis Notes:**
- For large libraries (1M+ files), increase to 4gb
- Redis stores task queue and results temporarily
- Uses `noeviction` policy to prevent task loss

### Scanning Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXCLUDED_PATHS` | (empty) | Comma-separated paths to exclude from scanning |
| `EXCLUDED_EXTENSIONS` | `.txt,.log,.md` | Comma-separated file extensions to exclude |
| `PERIODIC_SCAN_SCHEDULE` | (empty) | Automated scan schedule (cron or interval format) |
| `CLEANUP_SCHEDULE` | (empty) | Automated cleanup schedule (cron or interval format) |

**Schedule Format Examples:**
```bash
# Cron format (standard cron syntax)
PERIODIC_SCAN_SCHEDULE=cron:0 2 * * *        # Daily at 2 AM
CLEANUP_SCHEDULE=cron:0 3 * * 0              # Weekly on Sunday at 3 AM

# Interval format
PERIODIC_SCAN_SCHEDULE=interval:hours:6      # Every 6 hours
CLEANUP_SCHEDULE=interval:days:7             # Every 7 days
```

### Data Retention Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCAN_OUTPUT_RETENTION_DAYS` | `30` | Days before archiving scan outputs (currently disabled) |
| `REPORT_RETENTION_DAYS` | `90` | Days before deleting old reports |
| `SCAN_STATE_RETENTION_DAYS` | `7` | Days before deleting completed scan states |
| `LOG_RETENTION_DAYS` | `30` | Days before deleting old log entries (configurable via UI) |

**Data Retention Notes:**
- Automated cleanup runs daily via Celery Beat
- `SCAN_OUTPUT_RETENTION_DAYS` is currently not used (scan results kept forever)
- Configurable via environment variables for future flexibility
- `LOG_RETENTION_DAYS` default is stored in the `app_configs` database table and can be changed via the UI (System > View Logs) or API (`PUT /api/logs/retention`)

### Monitoring Variables (Future)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_MONITORING` | `false` | Enable Prometheus metrics endpoint |
| `METRICS_PORT` | `9090` | Metrics endpoint port |

## Docker Compose Configuration

### Basic Configuration

Minimal `docker-compose.yml` for production:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: pixelprobe-postgres
    environment:
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pixelprobe"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: pixelprobe-redis
    command: redis-server --maxmemory ${REDIS_MAX_MEMORY:-2gb} --maxmemory-policy noeviction
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  pixelprobe:
    image: ttlequals0/pixelprobe:latest
    container_name: pixelprobe-app
    environment:
      SECRET_KEY: ${SECRET_KEY}
      POSTGRES_HOST: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      SCAN_PATHS: ${SCAN_PATHS:-/media}
      MAX_WORKERS: ${MAX_WORKERS:-10}
      TZ: ${TZ:-UTC}
    volumes:
      - ${MEDIA_PATH}:/media:ro
    ports:
      - "${PORT:-5000}:5000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  celery-worker:
    image: ttlequals0/pixelprobe:latest
    container_name: pixelprobe-celery-worker
    command: python celery_worker.py
    environment:
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      POSTGRES_HOST: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      SECRET_KEY: ${SECRET_KEY}
      MAX_WORKERS: ${MAX_WORKERS:-10}
      CELERY_CONCURRENCY: ${CELERY_CONCURRENCY:-4}
    volumes:
      - ${MEDIA_PATH}:/media:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  postgres_data:
```

### Multiple Scan Paths

To scan multiple directories:

**Method 1: Multiple Volume Mounts**
```yaml
pixelprobe:
  environment:
    SCAN_PATHS: /movies,/tv-shows,/photos
  volumes:
    - /mnt/movies:/movies:ro
    - /mnt/tv-shows:/tv-shows:ro
    - /mnt/photos:/photos:ro

celery-worker:
  environment:
    SCAN_PATHS: /movies,/tv-shows,/photos
  volumes:
    - /mnt/movies:/movies:ro
    - /mnt/tv-shows:/tv-shows:ro
    - /mnt/photos:/photos:ro
```

**Method 2: Single Parent Volume**
```yaml
pixelprobe:
  environment:
    SCAN_PATHS: /media/movies,/media/tv,/media/photos
  volumes:
    - /mnt/all-media:/media:ro
```

### User Permissions (Important)

Both `pixelprobe` and `celery-worker` MUST run as the same user to access media files:

```yaml
pixelprobe:
  user: "${PUID:-1000}:${PGID:-1000}"
  volumes:
    - ${MEDIA_PATH}:/media:ro

celery-worker:
  user: "${PUID:-1000}:${PGID:-1000}"  # MUST match pixelprobe
  volumes:
    - ${MEDIA_PATH}:/media:ro
```

Find your UID/GID:
```bash
id -u  # Shows UID (typically 1000)
id -g  # Shows GID (typically 1000)
```

## Database Configuration

### Connection Pool Settings

Configured in `config.py`:

```python
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_size': 20,           # Base connection pool size
    'pool_pre_ping': True,     # Test connections before use
    'pool_recycle': 3600,      # Recycle connections after 1 hour
    'max_overflow': 40,        # Additional connections when pool exhausted
    'pool_timeout': 30,        # Timeout waiting for connection
}
```

**Total Connections:** 20 (base) + 40 (overflow) + MAX_WORKERS = 60 + MAX_WORKERS

**PostgreSQL max_connections:**
- Default: 100 connections
- Recommended: 150+ for production
- Set in PostgreSQL: `ALTER SYSTEM SET max_connections = 150;`

### Database Performance

For PostgreSQL optimization:

```sql
-- Increase shared buffers (25% of RAM)
ALTER SYSTEM SET shared_buffers = '2GB';

-- Increase work memory for sorts
ALTER SYSTEM SET work_mem = '16MB';

-- Enable parallel queries
ALTER SYSTEM SET max_parallel_workers_per_gather = 4;

-- Restart PostgreSQL
SELECT pg_reload_conf();
```

## Performance Tuning

### Recommended Settings by System Size

#### Small Library (< 10,000 files)
```bash
MAX_WORKERS=4
CELERY_CONCURRENCY=2
BATCH_SIZE=50
REDIS_MAX_MEMORY=512mb
```

#### Medium Library (10,000 - 100,000 files)
```bash
MAX_WORKERS=10
CELERY_CONCURRENCY=4
BATCH_SIZE=100
REDIS_MAX_MEMORY=1gb
```

#### Large Library (100,000 - 1,000,000 files)
```bash
MAX_WORKERS=16
CELERY_CONCURRENCY=6
BATCH_SIZE=200
REDIS_MAX_MEMORY=2gb
```

#### Extra Large Library (1,000,000+ files)
```bash
MAX_WORKERS=24
CELERY_CONCURRENCY=8
BATCH_SIZE=200
REDIS_MAX_MEMORY=4gb
```

### Resource Allocation

Docker resource limits for large libraries:

```yaml
celery-worker:
  deploy:
    resources:
      limits:
        cpus: '8'          # Limit CPU cores
        memory: 8G         # Limit RAM
      reservations:
        cpus: '4'          # Guaranteed CPU cores
        memory: 4G         # Guaranteed RAM
```

### Storage Performance

For best performance:

1. **Database Storage**: SSD strongly recommended
2. **Media Storage**: Can be HDD, but SSD improves scan speed
3. **Temp Files**: Use tmpfs for temporary files (optional)

```yaml
pixelprobe:
  volumes:
    - /mnt/ssd/postgres_data:/var/lib/postgresql/data  # SSD for database
    - /mnt/hdd/media:/media:ro                          # HDD OK for media
  tmpfs:
    - /tmp:size=1G                                      # tmpfs for temp files
```

## Scanning Configuration

### Exclusion Configuration

Exclude specific paths or file types from scanning:

**Via Environment Variables:**
```bash
EXCLUDED_PATHS=/media/temp,/media/incomplete,/media/.cache
EXCLUDED_EXTENSIONS=.tmp,.partial,.!qB,.part,.crdownload
```

**Via Web Interface:**
1. Navigate to Tools > Exclusions
2. Add paths or extensions
3. Click Save

### Schedule Configuration

Schedule automated scans:

**Via Environment Variables:**
```bash
# Daily full scan at 2 AM
PERIODIC_SCAN_SCHEDULE=cron:0 2 * * *

# Weekly cleanup on Sunday at 3 AM
CLEANUP_SCHEDULE=cron:0 3 * * 0
```

**Via Web Interface:**
1. Navigate to Tools > Schedules
2. Click "Create Schedule"
3. Configure schedule type, frequency, and scan type
4. Click Save

## Celery Configuration

### Worker Concurrency

Number of concurrent scan tasks:

```bash
# Low concurrency (memory-constrained systems)
CELERY_CONCURRENCY=2

# Medium concurrency (typical systems)
CELERY_CONCURRENCY=4

# High concurrency (powerful systems)
CELERY_CONCURRENCY=8
```

### Task Prioritization

Celery queues and priorities are automatically configured:

- **Default Queue**: Normal scans (priority 5)
- **Integrity Queue**: Integrity checks (priority 7)
- **Cleanup Queue**: Database cleanup (priority 6)
- **Retention Queue**: Data retention (priority 9)

### Beat Schedule (Automated Tasks)

Celery Beat runs scheduled tasks daily:

```python
# Runs at 2 AM daily
'data-retention-cleanup': {
    'task': 'pixelprobe.tasks.run_retention_cleanup',
    'schedule': crontab(hour=2, minute=0),
    'options': {'queue': 'retention', 'priority': 9}
}
```

## Data Retention Configuration

### Retention Policies

Configure how long data is retained:

```bash
# Archive scan outputs after 30 days (currently disabled)
SCAN_OUTPUT_RETENTION_DAYS=30

# Delete old reports after 90 days
REPORT_RETENTION_DAYS=90

# Delete completed scan states after 7 days
SCAN_STATE_RETENTION_DAYS=7
```

### Manual Cleanup

Run data retention manually:

```bash
# Docker
docker exec pixelprobe-app python tools/data_retention.py

# Manual installation
python tools/data_retention.py
```

Preview what would be cleaned:

```bash
python tools/data_retention.py --dry-run
```

## Security Configuration

### SSRF Trusted Hosts

PixelProbe includes SSRF protection that blocks outbound requests to private/reserved IP ranges. If you use internal services for healthchecks, notifications (ntfy, webhooks), or similar integrations that resolve to private IPs, you can allowlist them:

| Variable | Default | Description |
|----------|---------|-------------|
| `TRUSTED_INTERNAL_HOSTS` | (empty) | Comma-separated hostnames and/or CIDR ranges that bypass SSRF private-IP blocking |

**Examples:**
```bash
# Single hostname
TRUSTED_INTERNAL_HOSTS=healthcheck.internal.local

# Hostname + subnet
TRUSTED_INTERNAL_HOSTS=healthcheck.internal.local,192.168.5.0/24

# Multiple entries
TRUSTED_INTERNAL_HOSTS=healthcheck.internal.local,ntfy.internal.local,10.0.0.0/8
```

**Notes:**
- Hostname matching is case-insensitive
- CIDR ranges apply to resolved IPs regardless of hostname
- A bare IP (e.g., `10.0.0.5`) is treated as a `/32` single-host range
- Must be set in both `pixelprobe` and `celery-worker` containers (or via shared `.env`)
- Public IPs are always allowed; this setting only affects private/reserved ranges

### Secret Key Generation

Generate a secure secret key:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output to `SECRET_KEY` in `.env`.

### Session Configuration

Session settings are configured in Flask:

```python
# Session timeout (default: 30 days)
PERMANENT_SESSION_LIFETIME = timedelta(days=30)

# Session cookie settings
SESSION_COOKIE_SECURE = True   # HTTPS only (production)
SESSION_COOKIE_HTTPONLY = True # Prevent JavaScript access
SESSION_COOKIE_SAMESITE = 'Lax' # CSRF protection
```

### API Token Authentication

Users can generate API tokens via:
1. Web UI: Account > API Tokens
2. API: `POST /api/auth/tokens`

Tokens support optional expiration dates.

## Resource Recommendations

### CPU Requirements

| Library Size | Minimum CPUs | Recommended CPUs |
|--------------|-------------|------------------|
| < 10K files | 2 cores | 4 cores |
| 10K-100K files | 4 cores | 8 cores |
| 100K-1M files | 8 cores | 16 cores |
| 1M+ files | 16 cores | 32 cores |

### Memory Requirements

| Library Size | Minimum RAM | Recommended RAM |
|--------------|------------|-----------------|
| < 10K files | 2 GB | 4 GB |
| 10K-100K files | 4 GB | 8 GB |
| 100K-1M files | 8 GB | 16 GB |
| 1M+ files | 16 GB | 32 GB |

### Disk Requirements

- **Database**: 100 MB per 10,000 files (estimated)
- **Logs**: 1-10 GB (depending on retention)
- **Reports**: 100 MB per 1,000 reports
- **Temp Files**: 1-2 GB during scans

### Network Requirements

- **Bandwidth**: Minimal (local file access)
- **Latency**: Low latency to database required
- **Ports**: 5000 (web), 5432 (postgres), 6379 (redis)

## Configuration Best Practices

1. **Start Conservative**: Begin with default settings and increase gradually
2. **Monitor Resources**: Use `docker stats` to monitor CPU/memory usage
3. **Test Changes**: Test configuration changes on a subset of files first
4. **Document Settings**: Keep notes on what works for your environment
5. **Regular Backups**: Backup database and configuration regularly
6. **Security First**: Use strong passwords and keep SECRET_KEY secure
7. **Update Regularly**: Pull latest images for bug fixes and improvements

## Examples

### Home Media Server (20K files)
```bash
MAX_WORKERS=8
CELERY_CONCURRENCY=3
BATCH_SIZE=100
REDIS_MAX_MEMORY=1gb
POSTGRES_PASSWORD=strong-password-here
SCAN_PATHS=/movies,/tv
```

### Professional Archive (500K files)
```bash
MAX_WORKERS=20
CELERY_CONCURRENCY=6
BATCH_SIZE=200
REDIS_MAX_MEMORY=4gb
POSTGRES_PASSWORD=very-strong-password
SCAN_PATHS=/archive/video,/archive/images
OUTPUT_ROTATION_ENABLED=true
MAX_OUTPUT_SIZE=50000
```

### Multi-User Production (2M files)
```bash
MAX_WORKERS=24
CELERY_CONCURRENCY=8
BATCH_SIZE=200
REDIS_MAX_MEMORY=8gb
POSTGRES_PASSWORD=enterprise-strength-password
SCAN_PATHS=/storage/media1,/storage/media2,/storage/media3
ENABLE_MONITORING=true
```

## Troubleshooting Configuration

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for solutions to common configuration issues.
