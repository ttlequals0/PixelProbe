# Docker setup guide

The Docker Compose setup for PixelProbe and what each container does.

## Container overview

PixelProbe uses 4 main containers:

| Container | Purpose | Ports | Dependencies |
|-----------|---------|-------|--------------|
| **postgres** | Database storage | 5432 (internal only, not published) | None |
| **redis** | Message queue | 6379 (internal only, not published) | None |
| **pixelprobe** | Web UI & API | 5000 | postgres, redis |
| **celery-worker** | Background processing | None | postgres, redis |

Only the web container publishes a port to the host. postgres and redis are
reachable solely on the internal compose network.

## Effective Docker Compose file

[`docker-compose.yml`](../docker-compose.yml) in the repository root is the
only supported deployment file. It defines the current image tag, non-root
identity, read-only media bind, writable runtime paths, resource limits,
cookie settings, and scheduler ownership. Do not copy older Compose examples.

Use a small override file only for local changes. It must preserve the same
`PUID:PGID` and read-only media mount for both application services.

```yaml
services:
  pixelprobe:
    environment:
      GUNICORN_WORKERS: 2
  celery-worker:
    environment:
      CELERY_CONCURRENCY: 2
```

## Environment variables (.env file)

Create a `.env` file in the same directory as your `docker-compose.yml`:

```bash
# Required secrets
POSTGRES_PASSWORD=your-secure-database-password-here
SECRET_KEY=your-secret-key-for-sessions-here

# Optional settings
TZ=America/New_York
CELERY_CONCURRENCY=4
MAX_WORKERS=10
BATCH_SIZE=100
REDIS_MAX_MEMORY=2gb
```

## Container responsibilities

### PostgreSQL container

Stores all persistent data: scan results and metadata, file corruption status, user configurations, scan history, and schedule settings.

### Redis container

Carries the Celery task queue: task messages, worker coordination, result caching, and progress updates.

### Web application container

Serves the dashboard and REST API on port 5000, handles user authentication, reports real-time scan progress, and manages scheduled scans.

### Celery worker container

Does the actual scanning: corruption detection, parallel file discovery, batch processing, and cleanup. It runs FFmpeg for video/audio, and ImageMagick plus Python PIL for images.

## Scaling options

### Adding more workers

To increase scanning speed, you can run multiple worker containers:

```yaml
celery-worker:
  # ... existing configuration ...
  deploy:
    replicas: 3  # Run 3 worker containers
```

Note: remove the `container_name:` line from the service before using
`replicas` or `docker compose up --scale` - container names must be unique,
so a fixed name prevents starting more than one instance.

Or increase concurrency in a single container:

```yaml
environment:
  CELERY_CONCURRENCY: 8  # Increase from the default 4 concurrent tasks
```

### Performance tuning

Adjust these settings based on your system. They are operator starting points,
not measured throughput or memory guarantees. Validate them against the media,
storage, and database used by the deployment.

| Setting | Default | Description | Recommendation |
|---------|---------|-------------|----------------|
| CELERY_CONCURRENCY | 4 | Concurrent Celery tasks per container | Budget roughly 1 CPU and 2 GB RAM per slot (the worker recycles children at --max-memory-per-child, about 1.9 GiB); raise only if the container has matching cpus/memory limits |
| MAX_WORKERS | 10 | Threads for selected-file rescans only (Scan Selected); does not affect directory scans, which scale with CELERY_CONCURRENCY | Each thread holds one PostgreSQL connection. Leave at 10; raise toward 16-24 only for large hand-picked rescans with max_connections headroom. Not a function of CPU cores |
| BATCH_SIZE | 100 | Legacy media-checker discovery lookup batch | Leave at 100. It does not control parallel discovery inserts or scan chunk commits. |
| REDIS_MAX_MEMORY | 2gb | Task queue memory | 1-4gb for large libraries |

### PostgreSQL tuning

There is no PixelProbe environment variable for PostgreSQL memory; tune the
database on the `postgres` service itself, for example:

```yaml
postgres:
  image: postgres:18-alpine
  command: postgres -c shared_buffers=1GB -c effective_cache_size=3GB -c work_mem=16MB -c max_connections=150
  deploy:
    resources:
      limits:
        memory: 4G
```

Rule of thumb: `shared_buffers` about 25% of the container's memory limit,
`effective_cache_size` about 75%. [configuration.md](configuration.md) shows
an `ALTER SYSTEM` variant that changes the same settings on a running
database.

## Volume mounts

### Media directories

Mount your media directories as **read-only** to prevent accidental modifications:

```yaml
volumes:
  - /media/movies:/movies:ro
  - /media/tv:/tv:ro
  - /media/photos:/photos:ro
  - /media/music:/music:ro
```

**Important:** the root Compose file already runs both application services as
the same configured user. Keep that setting in any override:

```yaml
services:
  pixelprobe:
    # ... other settings ...
    user: "${PUID:-10001}:${PGID:-10001}"
    volumes:
      - /media/movies:/movies:ro

  celery-worker:
    # ... other settings ...
    user: "${PUID:-10001}:${PGID:-10001}"  # MUST match pixelprobe user
    volumes:
      - /media/movies:/movies:ro
```

To find your user's UID and GID on the host:
```bash
id -u  # Shows UID (typically 1000)
id -g  # Shows GID (typically 1000)
```

Or use environment variables:
```yaml
user: "${PUID:-10001}:${PGID:-10001}"
```

If the web app and Celery worker run as different users, the worker gets "No valid files provided" errors even though files exist, because it can't read the mounted media directories.

### Database persistence

The PostgreSQL data is stored in a named volume:

```yaml
volumes:
  postgres_data:  # Persists across container restarts
```

To backup:
```bash
docker exec pixelprobe-postgres pg_dump -U pixelprobe pixelprobe > backup.sql
```

## Network communication

All containers communicate on an internal Docker network:

```
pixelprobe:5000 <-> redis:6379 <-> celery-worker
       |                               |
       +------> postgres:5432 <--------+
```

- Web app submits tasks to Redis
- Workers pull tasks from Redis
- Both web and workers write to PostgreSQL

## Starting the system

1. Create your `.env` file with passwords
2. Update volume mounts to your media paths
3. Start the system:

```bash
# Start all containers
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f

# Stop all containers
docker-compose down
```

## Monitoring

### Check worker status
```bash
docker exec pixelprobe-celery-worker celery -A pixelprobe.celery_config inspect active
```

### View queue length
```bash
docker exec pixelprobe-redis valkey-cli LLEN pixelprobe
```

### Database connections
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -c "SELECT count(*) FROM pg_stat_activity;"
```

## Troubleshooting

### Workers not processing
1. Check Redis is running: `docker-compose ps redis`
2. Check worker logs: `docker-compose logs celery-worker`
3. Verify queue: `docker exec pixelprobe-redis valkey-cli LLEN pixelprobe`

### Database connection issues
1. Check PostgreSQL is healthy: `docker-compose ps postgres`
2. Test connection: `docker exec pixelprobe-postgres pg_isready`
3. Check password in `.env` file

### High memory usage
1. Reduce CELERY_CONCURRENCY
2. Confirm OUTPUT_ROTATION_ENABLED is not disabled (it defaults to true)
3. Lower REDIS_MAX_MEMORY
4. BATCH_SIZE has no real memory effect - it only batches database lookups during file discovery

## Security considerations

1. Use strong passwords in the `.env` file
2. Mount media as read-only (`:ro` flag)
3. Don't expose ports unless needed (remove `ports:` sections)
4. Use a firewall if exposing ports
5. Pull latest images periodically

## Backup strategy

### Database backup
```bash
# Backup
docker exec pixelprobe-postgres pg_dump -U pixelprobe pixelprobe | gzip > backup_$(date +%Y%m%d).sql.gz

# Restore
gunzip < backup_20250823.sql.gz | docker exec -i pixelprobe-postgres psql -U pixelprobe pixelprobe
```

### Configuration backup
```bash
# Save docker-compose and env
tar -czf pixelprobe_config_$(date +%Y%m%d).tar.gz docker-compose.yml .env
```

## Upgrade process

1. Backup database
2. Stop containers: `docker-compose down`
3. Update image version in `docker-compose.yml`
4. Pull new image: `docker-compose pull`
5. Start containers: `docker-compose up -d`
6. Check logs: `docker-compose logs -f`

## PostgreSQL 15 to 18 migration (required for v2.7.0+)

Starting with v2.7.0 the compose file defaults to `postgres:18-alpine`.
PostgreSQL data directories are NOT portable across major versions: an
existing `postgres_data` volume created by PostgreSQL 15 will refuse to start
on the 18 image (the container crash-loops with a version mismatch error).
Migrate BEFORE switching to the new compose file. If you are not ready to
migrate, pin `image: postgres:15-alpine` in your compose file - the app works
with both versions.

Also note: the postgres:18+ Docker images changed the expected volume mount
point from `/var/lib/postgresql/data` to `/var/lib/postgresql` (data now lives
in a major-version subdirectory so future upgrades can use `pg_upgrade
--link`). The 18 image refuses to start with a volume mounted at the old
`/data` path. The bundled docker-compose.yml already uses the new mount; if
you maintain your own compose file, update the postgres volume line to
`- postgres_data:/var/lib/postgresql`.

Downtime for the migration is roughly the dump plus restore time (a few
minutes for typical libraries).

```bash
# 1. Dump while the OLD stack is still running
DUMP=pixelprobe_pg15_$(date +%Y%m%d).sql.gz
docker exec pixelprobe-postgres pg_dump -U pixelprobe pixelprobe | gzip > "$DUMP"

# 2. Stop the stack
docker-compose down

# 3. Keep the old volume as a fallback (rename instead of delete)
docker volume create pixelprobe_postgres_data_pg15_backup
docker run --rm -v pixelprobe_postgres_data:/from -v pixelprobe_postgres_data_pg15_backup:/to alpine sh -c "cp -a /from/. /to/"
docker volume rm pixelprobe_postgres_data

# 4. NOW switch to the v2.7.0 compose file (postgres:18 image and the new
#    /var/lib/postgresql volume mount) - e.g. git pull or edit your copy

# 5. Start ONLY postgres on the new compose file (creates a fresh v18 volume)
docker-compose up -d postgres
# wait for: docker exec pixelprobe-postgres pg_isready -U pixelprobe

# 6. Restore the dump
gunzip < "$DUMP" | docker exec -i pixelprobe-postgres psql -U pixelprobe pixelprobe

# 7. Refresh collation metadata (avoids "collation version mismatch" warnings)
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "ALTER DATABASE pixelprobe REFRESH COLLATION VERSION;"
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "REINDEX DATABASE pixelprobe;"

# 8. Start the rest of the stack and verify
docker-compose up -d
# sanity check: row counts should match your pre-migration numbers
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "SELECT COUNT(*) FROM scan_results;"
```

Note: your compose project name may prefix the volume (e.g.
`pixelprobe_postgres_data`); check with `docker volume ls`. Once you have
verified the app against PostgreSQL 18, the `_pg15_backup` volume and the
dump file can be deleted.
