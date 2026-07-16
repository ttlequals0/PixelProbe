# PixelProbe Troubleshooting Guide

## Table of Contents

- [Quick Diagnostics](#quick-diagnostics)
- [Installation Issues](#installation-issues)
- [Database Issues](#database-issues)
- [Scanning Issues](#scanning-issues)
- [Performance Issues](#performance-issues)
- [Authentication Issues](#authentication-issues)
- [Docker Issues](#docker-issues)
- [Worker Issues](#worker-issues)
- [Debug Mode](#debug-mode)
- [Log Locations](#log-locations)
- [Getting Help](#getting-help)

## Quick Diagnostics

Run these commands first to identify the problem area:

### Docker Installation

```bash
# Check container status
docker-compose ps

# Check all logs
docker-compose logs --tail=50

# Check specific service
docker-compose logs pixelprobe --tail=100
docker-compose logs celery-worker --tail=100
docker-compose logs postgres --tail=50
docker-compose logs redis --tail=50

# Check health status (unauthenticated liveness probe)
curl http://localhost:5000/healthz

# Authenticated health check
curl http://localhost:5000/health -H "Authorization: Bearer your-token-here"
```

### Manual Installation

```bash
# Check services
systemctl status postgresql
systemctl status redis

# Check Python process
ps aux | grep python

# Check application logs
tail -f /path/to/logs/pixelprobe.log

# Test database connection
psql -U pixelprobe -d pixelprobe -h localhost
```

## Installation Issues

### FFmpeg Not Found

**Symptom:** `FFmpeg not found in PATH` error

**Solutions:**

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install ffmpeg
ffmpeg -version
```

**macOS:**
```bash
brew install ffmpeg
ffmpeg -version
```

**Docker:** FFmpeg is included in the image, but if you built a custom image:
```dockerfile
RUN apt-get install -y ffmpeg
```

**Verify Installation:**
```bash
# Docker
docker exec pixelprobe-app ffmpeg -version

# Manual
ffmpeg -version
```

### ImageMagick Not Found

**Symptom:** `ImageMagick not found` or `convert: command not found`

**Solutions:**

**Ubuntu/Debian:**
```bash
sudo apt-get install imagemagick libmagickcore-6.q16hdri-7-extra
identify -version
```

**macOS:**
```bash
brew install imagemagick
identify -version
```

**Verify Installation:**
```bash
# Docker
docker exec pixelprobe-app identify -version

# Manual
identify -version
```

### Python Dependencies Installation Failed

**Symptom:** `pip install -r requirements.txt` fails

**Solutions:**

1. **Update pip:**
```bash
pip install --upgrade pip
```

2. **Install system dependencies first:**
```bash
# Ubuntu/Debian
sudo apt-get install python3-dev build-essential libpq-dev

# macOS
brew install postgresql
```

3. **Use virtual environment:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Database Issues

### Database Connection Failed

**Symptom:** `could not connect to server` or `connection refused`

**Diagnosis:**
```bash
# Check if PostgreSQL is running
docker-compose ps postgres        # Docker
systemctl status postgresql        # Manual

# Test connection
psql -U pixelprobe -d pixelprobe -h localhost
```

**Solutions:**

1. **Check PostgreSQL is running:**
```bash
# Docker
docker-compose up -d postgres

# Manual
sudo systemctl start postgresql
```

2. **Verify credentials in .env:**
```bash
POSTGRES_HOST=postgres    # or localhost for manual
POSTGRES_PORT=5432
POSTGRES_DB=pixelprobe
POSTGRES_USER=pixelprobe
POSTGRES_PASSWORD=your-password-here
```

3. **Check network connectivity:**
```bash
# Docker containers should be on same network
docker network ls
docker network inspect pixelprobe-network
```

4. **Check PostgreSQL logs:**
```bash
# Docker
docker-compose logs postgres

# Manual
sudo tail -f /var/log/postgresql/postgresql-18-main.log
```

### Database Table Missing

**Symptom:** `no such table: scan_results` or `relation does not exist`

**Solution:**

Tables are created automatically on first run. If missing:

```bash
# Docker
docker exec pixelprobe-app python tools/fix_database_schema.py

# Manual
python tools/fix_database_schema.py
```

### Connection Pool Exhausted

**Symptom:** `TimeoutError: QueuePool limit` or `no more connections available`

**Diagnosis:**
```sql
-- Check active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'pixelprobe';

-- Check max connections
SHOW max_connections;
```

**Solutions:**

1. **Reduce MAX_WORKERS:**
```bash
# Total connections = 60 + MAX_WORKERS
# Keep under PostgreSQL max_connections (default: 100)
MAX_WORKERS=10  # Reduce from higher value
```

2. **Increase PostgreSQL max_connections:**
```sql
ALTER SYSTEM SET max_connections = 150;
SELECT pg_reload_conf();
```

3. **Check for connection leaks:**
```bash
# View long-running connections
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT pid, age(clock_timestamp(), query_start), state, query
   FROM pg_stat_activity
   WHERE state != 'idle'
   ORDER BY query_start;"
```

### Database Growth Too Large

**Symptom:** Database size growing rapidly

**Diagnosis:**
```bash
# Check database size
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT pg_size_pretty(pg_database_size('pixelprobe'));"

# Check table sizes
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
   FROM pg_stat_user_tables
   ORDER BY pg_total_relation_size(relid) DESC;"
```

**Solutions:**

1. **Enable data retention (runs daily automatically):**
```bash
# Verify in .env
REPORT_RETENTION_DAYS=90
SCAN_STATE_RETENTION_DAYS=7
```

2. **Run manual cleanup:**
```bash
docker exec pixelprobe-app python tools/data_retention.py
```

3. **Vacuum database:**
```bash
docker exec pixelprobe-postgres vacuumdb -U pixelprobe -d pixelprobe --analyze
```

## Scanning Issues

### No Files Found During Scan

**Symptom:** `No valid files provided for scanning` or `0 files discovered`

**Diagnosis:**
```bash
# Check if path exists
docker exec pixelprobe-app ls -la /media

# Check permissions
docker exec pixelprobe-app id
ls -la $MEDIA_PATH  # On host
```

**Solutions:**

1. **Verify SCAN_PATHS matches mounted volumes:**
```yaml
# docker-compose.yml
volumes:
  - /path/on/host:/media:ro
environment:
  SCAN_PATHS: /media  # Must match container path
```

2. **Fix user permissions (CRITICAL):**
```yaml
# Both services MUST run as same user
pixelprobe:
  user: "1000:1000"

celery-worker:
  user: "1000:1000"  # Must match pixelprobe
```

3. **Check file ownership on host:**
```bash
# Files should be readable by user 1000
ls -la /path/to/media
chown -R 1000:1000 /path/to/media  # If needed
```

4. **Verify path not excluded:**
```bash
# Check exclusions in web UI: Tools > Exclusions
# Or check environment:
echo $EXCLUDED_PATHS
```

### Scan Stuck or Not Progressing

**Symptom:** Scan progress doesn't update or stays at 0%

**Diagnosis:**
```bash
# Check worker logs
docker-compose logs celery-worker --tail=100

# Check Redis queue
docker exec pixelprobe-redis redis-cli LLEN celery

# Check active tasks
docker exec pixelprobe-celery-worker celery -A celery_config inspect active
```

**Solutions:**

1. **Restart Celery worker:**
```bash
docker-compose restart celery-worker
```

2. **Check Redis is running:**
```bash
docker-compose ps redis
docker exec pixelprobe-redis redis-cli ping  # Should return PONG
```

3. **Clear stuck tasks:**
```bash
# Flush Redis queue (WARNING: cancels all tasks)
docker exec pixelprobe-redis redis-cli FLUSHDB
```

4. **Check database locks:**
```sql
SELECT * FROM pg_locks WHERE NOT granted;
```

### High False Positive Rate

**Symptom:** Many valid files marked as corrupted

**Possible Causes:**

1. **Transient network issues** (for network storage)
2. **Corrupt media files** (actually corrupt)
3. **ImageMagick policy restrictions**

**Solutions:**

1. **Check ImageMagick policies:**
```bash
docker exec pixelprobe-app cat /etc/ImageMagick-6/policy.xml | grep -A2 PDF
```

2. **Rescan specific files:**
- Select files in web UI
- Click "Rescan"

3. **Enable deep scan for verification:**
- Tools > Deep Analysis

4. **Mark false positives as good:**
- Select files
- Click "Mark as Good"

### Scan Running but No Results

**Symptom:** Scan shows progress but no results in dashboard

**Diagnosis:**
```bash
# Check database for results
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT COUNT(*) FROM scan_results;"

# Check scan state
curl http://localhost:5000/api/scan-status
```

**Solutions:**

1. **Refresh browser** (cache issue)

2. **Check database connection:**
```bash
docker-compose logs pixelprobe | grep -i database
```

3. **Verify write permissions:**
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "INSERT INTO scan_results (file_path, is_corrupted)
   VALUES ('/test', false);"
```

## Performance Issues

### Slow Scanning Speed

**Symptom:** Scans taking too long to complete

**Diagnosis:**
```bash
# Check CPU and memory usage
docker stats

# Check disk I/O
iostat -x 5

# Check active workers
docker-compose logs celery-worker | grep "concurrent"
```

**Solutions:**

1. **Increase MAX_WORKERS:**
```bash
MAX_WORKERS=16  # Increase from 10
```

2. **Increase CELERY_CONCURRENCY:**
```bash
CELERY_CONCURRENCY=6  # Increase from 4
```

3. **Use SSD for database:**
```yaml
volumes:
  - /mnt/ssd/postgres_data:/var/lib/postgresql/data
```

4. **Increase batch size:**
```bash
BATCH_SIZE=200  # Increase from 100
```

5. **Allocate more resources:**
```yaml
celery-worker:
  deploy:
    resources:
      limits:
        cpus: '8'
        memory: 8G
```

### High Memory Usage

**Symptom:** Out of memory errors or system slowdown

**Diagnosis:**
```bash
# Check memory usage by container
docker stats --no-stream

# Check PostgreSQL memory
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SHOW shared_buffers;"
```

**Solutions:**

1. **Reduce MAX_WORKERS:**
```bash
MAX_WORKERS=8  # Reduce from higher value
```

2. **Enable output rotation:**
```bash
OUTPUT_ROTATION_ENABLED=true
MAX_OUTPUT_SIZE=10000
```

3. **Reduce CELERY_CONCURRENCY:**
```bash
CELERY_CONCURRENCY=3  # Reduce from higher value
```

4. **Set memory limits:**
```yaml
pixelprobe:
  deploy:
    resources:
      limits:
        memory: 4G
```

5. **Increase Redis max memory:**
```bash
REDIS_MAX_MEMORY=4gb  # Increase from 2gb
```

### Database Queries Slow

**Symptom:** Web interface slow to load or timeouts

**Diagnosis:**
```sql
-- Check slow queries
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```

**Solutions:**

1. **Create indexes** (should be automatic):
```bash
docker exec pixelprobe-app python scripts/create_indexes.py
```

2. **Vacuum and analyze:**
```bash
docker exec pixelprobe-postgres vacuumdb -U pixelprobe -d pixelprobe -z
```

3. **Increase PostgreSQL shared buffers:**
```sql
ALTER SYSTEM SET shared_buffers = '2GB';
SELECT pg_reload_conf();
```

## Authentication Issues

### Cannot Create Admin Account

**Symptom:** Setup endpoint returns error or admin already exists

**Solutions:**

1. **Check if admin exists:**
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT username, is_admin FROM users WHERE is_admin = true;"
```

2. **Reset admin password** (if locked out):

First-run setup only works before any user exists. If a user already exists,
generate a bcrypt hash and update the users table directly:
```bash
# Generate a bcrypt hash for the new password
docker exec pixelprobe-app python -c \
  "import bcrypt; print(bcrypt.hashpw(b'new-password', bcrypt.gensalt()).decode())"

# Set it on the locked-out account
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c \
  "UPDATE users SET password_hash = 'paste-hash-here' WHERE username = 'admin';"
```

3. **Check SECRET_KEY is set:**
```bash
docker-compose logs pixelprobe | grep SECRET_KEY
```

### API Token Not Working

**Symptom:** `401 Unauthorized` with Bearer token

**Solutions:**

1. **Verify token format:**
```bash
curl http://localhost:5000/api/stats \
  -H "Authorization: Bearer your-token-here"
```

2. **Check token expiration:**
- Navigate to Account > API Tokens in web UI
- Check expiration date

3. **Generate new token:**
- Navigate to Account > API Tokens
- Click "Generate Token"

### Session Expired Too Quickly

**Symptom:** Logged out after short time

**Solution:**

Sessions last 24 hours, with an additional 30-minute inactivity timeout. Being logged out after 30 minutes of inactivity is expected behavior. If sessions expire faster than that:

1. **Check browser cookies enabled**
2. **Check SECRET_KEY hasn't changed** (invalidates sessions)
3. **Clear browser cookies and re-login**

## Docker Issues

### Containers Won't Start

**Symptom:** `docker-compose up` fails or containers exit

**Diagnosis:**
```bash
# Check container status
docker-compose ps

# Check logs for errors
docker-compose logs
```

**Solutions:**

1. **Check .env file exists:**
```bash
ls -la .env
cat .env  # Verify SECRET_KEY and POSTGRES_PASSWORD are set
```

2. **Check port conflicts:**
```bash
# Check if ports are already in use
sudo netstat -tlnp | grep :5001
sudo netstat -tlnp | grep :5432
sudo netstat -tlnp | grep :6379
```

3. **Remove old containers:**
```bash
docker-compose down
docker-compose up -d
```

4. **Check disk space:**
```bash
df -h
docker system df
```

### Permission Denied Errors

**Symptom:** `Permission denied` accessing media files

**Solutions:**

1. **Set user in docker-compose.yml:**
```yaml
pixelprobe:
  user: "${PUID:-1000}:${PGID:-1000}"

celery-worker:
  user: "${PUID:-1000}:${PGID:-1000}"
```

2. **Check file permissions on host:**
```bash
ls -la /path/to/media
# Files should be readable by user 1000
```

3. **Fix ownership if needed:**
```bash
sudo chown -R 1000:1000 /path/to/media
```

### Volume Mount Issues

**Symptom:** Files not visible in container

**Diagnosis:**
```bash
# Check volume mounts
docker inspect pixelprobe-app | grep -A5 Mounts

# Check inside container
docker exec pixelprobe-app ls -la /media
```

**Solutions:**

1. **Verify MEDIA_PATH exists on host:**
```bash
ls -la $MEDIA_PATH
```

2. **Use absolute paths:**
```yaml
volumes:
  - /absolute/path/to/media:/media:ro  # Not ~/media or ./media
```

3. **Check SELinux (if applicable):**
```bash
# Add :z flag for SELinux
volumes:
  - /path/to/media:/media:ro,z
```

## Worker Issues

### Worker Not Processing Tasks

**Symptom:** Scans queued but not executing

**Diagnosis:**
```bash
# Check worker is running
docker-compose ps celery-worker

# Check worker logs
docker-compose logs celery-worker

# Check registered tasks
docker exec pixelprobe-celery-worker celery -A celery_config inspect registered
```

**Solutions:**

1. **Restart worker:**
```bash
docker-compose restart celery-worker
```

2. **Check Redis connection:**
```bash
docker exec pixelprobe-celery-worker redis-cli -h redis ping
```

3. **Verify CELERY_BROKER_URL:**
```yaml
celery-worker:
  environment:
    CELERY_BROKER_URL: redis://redis:6379/0  # Must match pixelprobe
```

### Worker Crashing

**Symptom:** `celery-worker exited with code 1` or constant restarts

**Diagnosis:**
```bash
# Check worker logs for errors
docker-compose logs celery-worker --tail=200

# Check resource limits
docker stats pixelprobe-celery-worker
```

**Solutions:**

1. **Check for out-of-memory:**
```yaml
celery-worker:
  deploy:
    resources:
      limits:
        memory: 8G  # Increase limit
```

2. **Reduce concurrency:**
```bash
CELERY_CONCURRENCY=2  # Reduce from higher value
```

3. **Check database connection:**
```bash
docker-compose logs celery-worker | grep -i database
```

## Debug Mode

### Enable Debug Logging

**Docker:**
```yaml
pixelprobe:
  environment:
    FLASK_ENV: development
    DATABASE_ECHO: true

celery-worker:
  environment:
    CELERY_LOG_LEVEL: DEBUG
```

**Manual:**
```bash
export FLASK_ENV=development
export DATABASE_ECHO=true
export CELERY_LOG_LEVEL=DEBUG
```

### View Detailed Logs

```bash
# All logs with timestamps
docker-compose logs -f --timestamps

# Filter for errors
docker-compose logs | grep -i error

# Filter for warnings
docker-compose logs | grep -i warning

# Search for specific message
docker-compose logs | grep "connection refused"
```

### SQL Query Logging

Enable in .env:
```bash
DATABASE_ECHO=true
```

View queries:
```bash
docker-compose logs pixelprobe | grep SELECT
```

## Log Locations

### Docker Logs

```bash
# All containers
docker-compose logs

# Specific container
docker logs pixelprobe-app
docker logs pixelprobe-celery-worker
docker logs pixelprobe-postgres
docker logs pixelprobe-redis

# Save logs to file
docker-compose logs > pixelprobe-logs.txt
```

### Manual Installation Logs

**Application logs:**
```bash
# If using systemd
journalctl -u pixelprobe -f

# If running directly
# Logs go to stdout/stderr
```

**PostgreSQL logs:**
```bash
sudo tail -f /var/log/postgresql/postgresql-18-main.log
```

**Redis logs:**
```bash
sudo tail -f /var/log/redis/redis-server.log
```

### Log Retention

Docker logs can grow large. Configure log rotation:

```yaml
# docker-compose.yml
services:
  pixelprobe:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## Getting Help

If you've tried the solutions above and still have issues:

### 1. Gather Information

Collect this information before asking for help:

```bash
# System information
uname -a
docker --version
docker-compose --version

# PixelProbe version
curl http://localhost:5000/healthz
# or
docker exec pixelprobe-app cat pixelprobe/version.py

# Container status
docker-compose ps

# Recent logs (last 100 lines)
docker-compose logs --tail=100 > logs.txt

# Health check (unauthenticated liveness probe)
curl http://localhost:5000/healthz

# Database status
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT version();"
```

### 2. Search Existing Issues

Search GitHub issues: https://github.com/ttlequals0/PixelProbe/issues

Common search terms:
- Your error message
- Feature you're having trouble with
- "database connection"
- "permission denied"
- "scan stuck"

### 3. Create New Issue

If no existing issue matches your problem:

1. Go to https://github.com/ttlequals0/PixelProbe/issues/new
2. Include:
   - PixelProbe version
   - Docker/OS information
   - Steps to reproduce
   - Error messages and logs
   - What you've already tried

### 4. Community Support

- GitHub Discussions: https://github.com/ttlequals0/PixelProbe/discussions
- Check README.md for additional resources
- Review documentation: INSTALLATION.md, CONFIGURATION.md

## Additional Resources

- [INSTALLATION.md](INSTALLATION.md) - Installation guide
- [CONFIGURATION.md](CONFIGURATION.md) - Configuration reference
- [README.md](README.md) - General documentation
- [docs/DOCKER_SETUP.md](docs/DOCKER_SETUP.md) - Docker architecture
- [docs/PERFORMANCE_TUNING.md](docs/PERFORMANCE_TUNING.md) - Performance guide
