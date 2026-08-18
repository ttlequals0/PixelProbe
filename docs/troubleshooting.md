# PixelProbe troubleshooting guide

## Table of contents

- [Quick diagnostics](#quick-diagnostics)
- [Installation issues](#installation-issues)
- [Database issues](#database-issues)
- [Scanning issues](#scanning-issues)
- [Performance issues](#performance-issues)
- [Authentication issues](#authentication-issues)
- [Docker issues](#docker-issues)
- [Worker issues](#worker-issues)
- [Debug mode](#debug-mode)
- [Log locations](#log-locations)
- [Getting help](#getting-help)
- [Additional resources](#additional-resources)

## Quick diagnostics

Run these commands first to identify the problem area:

### Docker installation

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

### Manual installation

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

## Installation issues

### FFmpeg not found

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

**Verify installation:**
```bash
# Docker
docker exec pixelprobe-app ffmpeg -version

# Manual
ffmpeg -version
```

### ImageMagick not found

**Symptom:** `ImageMagick not found` or `magick: command not found` (or `convert: command not found` with legacy ImageMagick 6)

**Solutions:**

**Ubuntu/Debian:**
```bash
sudo apt-get install imagemagick libmagickcore-7.q16-10-extra
magick -version
```

**macOS:**
```bash
brew install imagemagick
magick -version
```

**Verify installation:**
```bash
# Docker
docker exec pixelprobe-app magick -version

# Manual
magick -version
```

Note: PixelProbe images ship ImageMagick 7. The `magick` command is the primary CLI; `convert` and `identify` remain available as legacy aliases.

### Python dependencies installation failed

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

## Database issues

### Database connection failed

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

### Database table missing

**Symptom:** `no such table: scan_results` or `relation does not exist`

**Solution:**

Tables are created automatically on first run, and schema migrations run automatically at startup. If tables are still missing:

```bash
# Docker
docker exec pixelprobe-app python tools/fix_database_schema.py

# Manual
python tools/fix_database_schema.py
```

Note: `tools/fix_database_schema.py` only repairs missing tables. Its attempt to re-run migrations silently no-ops (the functions it imports from `app` no longer exist), which is harmless because migrations already run at every startup.

### Connection pool exhausted

**Symptom:** `TimeoutError: QueuePool limit` or `no more connections available`

**Diagnosis:**
```sql
-- Check active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'pixelprobe';

-- Check max connections
SHOW max_connections;
```

**Solutions:**

1. **Reduce pool settings:**
```bash
# Each process holds up to DB_POOL_SIZE + DB_MAX_OVERFLOW connections
# (defaults: 5 + 10 = 15). With 4 gunicorn workers that is
# 4 x (5 + 10) = 60 connections, plus one pool per Celery worker child.
# Keep the total under PostgreSQL max_connections (default: 100).
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
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

### Database growth too large

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
# Via the API
curl -X POST http://localhost:5000/api/maintenance/vacuum \
  -H "Authorization: Bearer $TOKEN"

# Or directly against PostgreSQL
docker exec pixelprobe-postgres vacuumdb -U pixelprobe -d pixelprobe --analyze
```

## Scanning issues

### No files found during scan

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
# Check exclusions in web UI: Exclusions (sidebar)
# Or check environment:
echo $EXCLUDED_PATHS
```

### Scan stuck or not progressing

**Symptom:** Scan progress doesn't update or stays at 0%

**Triage first: is it actually stuck?**

```bash
curl http://localhost:5000/api/scan-status \
  -H "Authorization: Bearer $TOKEN"
```

Since v2.7.3, `last_update` is a liveness heartbeat: it advances every ~120 seconds while workers are alive, even if `files_processed` is flat. A single large movie can take 30-60 minutes to validate, so a flat `files_processed` with an advancing `last_update` is normal, not a stuck scan. Only treat the scan as stuck when `last_update` itself stops moving.

**Diagnosis:**
```bash
# Check worker logs
docker-compose logs celery-worker --tail=100

# Check Redis queue depth (the queue is named "pixelprobe")
docker exec pixelprobe-redis valkey-cli LLEN pixelprobe

# Check active tasks
docker exec pixelprobe-celery-worker celery -A app.celery inspect active
```

**Solutions:**

1. **Restart Celery worker:**
```bash
docker-compose restart celery-worker
```
Restarting a worker mid-scan is safe: revival is automatic. A sweeper runs every 5 minutes and re-dispatches a scan's chunks when its heartbeat is older than `CHUNK_REVIVE_STALENESS_SECS` (default 600 seconds), up to 3 attempts. Wait 10-15 minutes before intervening further.

2. **Check Redis is running** (the redis container ships Valkey, so use `valkey-cli`):
```bash
docker-compose ps redis
docker exec pixelprobe-redis valkey-cli ping  # Should return PONG
```

3. **Run scan recovery:**
```bash
# Cleans up stuck scan state without destroying data
curl -X POST http://localhost:5000/api/scan/recovery \
  -H "Authorization: Bearer $TOKEN"
# (alias: POST /api/force-cleanup-scan)
```
To cancel the scan outright instead:
```bash
curl -X POST http://localhost:5000/api/cancel-scan \
  -H "Authorization: Bearer $TOKEN"
```

4. **Check database locks:**
```sql
SELECT * FROM pg_locks WHERE NOT granted;
```

5. **Flush the Redis queue (last resort):**
```bash
# WARNING: destroys all queued chunks and progress keys.
# Only use if /api/scan/recovery did not help.
docker exec pixelprobe-redis valkey-cli FLUSHDB
```

### Stuck scans, revival, and recovery

A sweeper job runs every 5 minutes and handles stalled scans automatically. Its branches, in order:

1. **Backstop finalize:** if all chunks completed but the finalize step died, the sweeper finishes the scan.
2. **Revival:** if the heartbeat is older than `CHUNK_REVIVE_STALENESS_SECS` (default 600 seconds) and the scan still has active chunk rows, the sweeper re-dispatches the orphaned chunks instead of crashing the scan (up to 3 attempts per scan). Discovery-phase scans are not revivable.
3. **Crash:** the scan is marked crashed when any of these hold:
   - no update for more than 30 minutes, or
   - no update for more than 5 minutes and the Celery task is gone, or
   - the scan started more than 30 minutes ago with no updates at all.
4. **Orphaned-row reclaim:** when no scan is active, files left in `scanning` state by a dead worker are reclaimed to `pending`.

Log lines to grep for:

```bash
docker-compose logs celery-worker pixelprobe | grep -E "Revived scan|Marking stuck scan|Reclaimed"
```

At startup, active scans are given `STUCK_SCAN_STARTUP_GRACE_SECS` (default 1800 seconds) before being marked crashed, so a scan that was healthy just before a restart is left running for the sweeper to revive.

### High false positive rate

**Symptom:** Many valid files marked as corrupted

**Possible causes:**

1. **Transient network issues** (for network storage)
2. **Corrupt media files** (actually corrupt)
3. **ImageMagick policy restrictions**

**Solutions:**

1. **Check ImageMagick policies:**
```bash
docker exec pixelprobe-app cat /etc/ImageMagick-7/policy.xml | grep -A2 PDF
```

2. **List files with errors:**
```bash
curl http://localhost:5000/api/error-files \
  -H "Authorization: Bearer $TOKEN"
```

3. **Rescan specific files:**
- Select files in web UI and click "Rescan", or via the API:
```bash
# Reset selected files for rescan (by file IDs; type can also be
# "corrupted", "error", or "all")
curl -X POST http://localhost:5000/api/reset-for-rescan \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type": "selected", "file_ids": [1, 2, 3]}'

# Reset by exact file path(s)
curl -X POST http://localhost:5000/api/reset-files-by-path \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"file_paths": ["/media/movies/example.mkv"]}'
```

4. **Run a deeper verification:**
- Integrity Check (sidebar)

5. **Mark false positives as good:**
- Select files
- Click "Mark as Good"

### Scan running but no results

**Symptom:** Scan shows progress but no results in dashboard

**Diagnosis:**
```bash
# Check database for results
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT COUNT(*) FROM scan_results;"

# Check scan state
curl http://localhost:5000/api/scan-status \
  -H "Authorization: Bearer $TOKEN"

# Check why files are still pending
curl http://localhost:5000/api/diagnose-pending-files \
  -H "Authorization: Bearer $TOKEN"
```

**Solutions:**

1. **Refresh browser** (cache issue)

2. **Check database connection:**
```bash
docker-compose logs pixelprobe | grep -i database
```

3. **Verify the database is writable:**
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "SELECT pg_is_in_recovery();"
# Should return f (false). Then confirm writes work without touching real data:
docker exec pixelprobe-postgres psql -U pixelprobe -c \
  "CREATE TEMP TABLE _probe(x int); DROP TABLE _probe;"
```

### Files show healthy with N/A scan details

**Symptom:** Files display as healthy in the UI but show "N/A" for the tool details and scan date.

**Cause:** The database row was created when the file was discovered but the file was never actually scanned. Historically this came from a pre-v2.2.59 chunk query bug; today it means a worker was killed mid-chunk or a scan crashed before those files were processed.

**Diagnosis:**
```bash
curl http://localhost:5000/api/diagnose-incomplete-scans \
  -H "Authorization: Bearer $TOKEN"
```
Or run the read-only queries in `scripts/find_incomplete_scans.sql` against PostgreSQL.

**Fix:**
```bash
# Reset the affected rows to pending
curl -X POST http://localhost:5000/api/reset-incomplete-scans \
  -H "Authorization: Bearer $TOKEN"

# Then scan the pending files
curl -X POST http://localhost:5000/api/force-scan-pending \
  -H "Authorization: Bearer $TOKEN"
```

**SQL fallback** (only if the API is unavailable):
```sql
UPDATE scan_results
SET scan_status = 'pending',
    is_corrupted = NULL,
    marked_as_good = false,
    scan_output = NULL
WHERE scan_status = 'completed' AND scan_date IS NULL;
```

## Performance issues

### Slow scanning speed

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
Note: `BATCH_SIZE`, `MAX_OUTPUT_SIZE`, and `OUTPUT_ROTATION_ENABLED` must be set on the celery-worker service to affect scans; setting them only on the pixelprobe service has no effect on scanning.

5. **Allocate more resources:**
```yaml
celery-worker:
  deploy:
    resources:
      limits:
        cpus: '8'
        memory: 8G
```

### High memory usage

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

2. **Enable output rotation** (set on the celery-worker service, not just pixelprobe):
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

### Database queries slow

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

1. **Verify indexes exist** (they are created automatically at startup by the migration runner):
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -c "\di"
```
If an index is genuinely missing, create it manually without blocking writes:
```sql
CREATE INDEX CONCURRENTLY idx_scan_results_scan_status
  ON scan_results (scan_status);
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

## Authentication issues

### Cannot create admin account

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

### API token not working

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

### Session expired too quickly

**Symptom:** Logged out after short time

**Solution:**

Sessions last 24 hours, with an additional 30-minute inactivity timeout. Being logged out after 30 minutes of inactivity is expected behavior. If sessions expire faster than that:

1. **Check browser cookies enabled**
2. **Check SECRET_KEY hasn't changed** (invalidates sessions)
3. **Clear browser cookies and re-login**

## Docker issues

### Containers won't start

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
# (the app listens on 5000 by default; override with the PORT env variable)
sudo netstat -tlnp | grep :5000
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

### Permission denied errors

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

### Volume mount issues

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

## Worker issues

### Worker not processing tasks

**Symptom:** Scans queued but not executing

**Diagnosis:**
```bash
# Check worker is running
docker-compose ps celery-worker

# Check worker logs
docker-compose logs celery-worker

# Check registered tasks
docker exec pixelprobe-celery-worker celery -A app.celery inspect registered

# Check worker status via the API
curl http://localhost:5000/api/worker-status \
  -H "Authorization: Bearer $TOKEN"
```

**Solutions:**

1. **Restart worker:**
```bash
docker-compose restart celery-worker
```

2. **Check Redis connection from the worker** (the app image has no redis-cli):
```bash
docker exec pixelprobe-celery-worker python -c \
  "import redis; print(redis.Redis.from_url('redis://redis:6379/0').ping())"
```

3. **Verify CELERY_BROKER_URL:**
```yaml
celery-worker:
  environment:
    CELERY_BROKER_URL: redis://redis:6379/0  # Must match pixelprobe
```

### Worker crashing

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

## Debug mode

### Enable debug logging

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

### View detailed logs

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

### SQL query logging

Enable in .env:
```bash
DATABASE_ECHO=true
```

View queries:
```bash
docker-compose logs pixelprobe | grep SELECT
```

## Log locations

### Docker logs

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

### Manual installation logs

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

### Log retention

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

## Getting help

If you've tried the solutions above and still have issues:

### 1. Gather information

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

### 2. Search existing issues

Search GitHub issues: https://github.com/ttlequals0/PixelProbe/issues

Common search terms:
- Your error message
- Feature you're having trouble with
- "database connection"
- "permission denied"
- "scan stuck"

### 3. Create new issue

If no existing issue matches your problem:

1. Go to https://github.com/ttlequals0/PixelProbe/issues/new
2. Include:
   - PixelProbe version
   - Docker/OS information
   - Steps to reproduce
   - Error messages and logs
   - What you've already tried

### 4. Community support

- GitHub Discussions: https://github.com/ttlequals0/PixelProbe/discussions
- Check the README for additional resources
- Review documentation: [installation.md](installation.md), [configuration.md](configuration.md)

## Additional resources

- [installation.md](installation.md) - Installation guide
- [configuration.md](configuration.md) - Configuration reference
- [docker-setup.md](docker-setup.md) - Docker architecture
- [performance-tuning.md](performance-tuning.md) - Performance guide
- [glossary.md](glossary.md) - Terminology reference
- [README](../README.md) - General documentation
