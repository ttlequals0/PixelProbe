# Docker setup guide

The Docker Compose setup for PixelProbe and what each container does.

## Container overview

The Compose stack has four containers:

| Container | Purpose | Ports | Dependencies |
|-----------|---------|-------|--------------|
| **postgres** | Database storage | 5432 (internal only, not published) | None |
| **redis** | Message queue | 6379 (internal only, not published) | None |
| **pixelprobe** | Web UI & API | 5000 | postgres, redis |
| **celery-worker** | Background processing | None | postgres, redis |

Only the web container publishes a port to the host. postgres and redis are reachable solely on the internal compose network.

## Effective Docker Compose file

[`docker-compose.yml`](../docker-compose.yml) in the repository root is the only supported deployment file. It defines the current image tag, non-root identity, read-only media bind, writable runtime paths, resource limits, cookie settings, and scheduler ownership. Do not copy older Compose examples.

Use a small override file only for local changes. It must preserve the same `PUID:PGID` and read-only media mount for both application services.

```yaml
services:
  pixelprobe:
    environment:
      GUNICORN_WORKERS: 2
  celery-worker:
    environment:
      CELERY_CONCURRENCY: 2
```

## Environment variables

The project `.env` file supplies values for substitutions in Compose. It does not automatically add every value to the container environment. The root Compose file forwards only the variables listed under each service's `environment` section. If a service also has `env_file`, entries under `environment` take precedence. Removing a default-valued `environment` entry can expose a value with the same name in `env_file`; check for conflicts before dropping defaults. See the [Compose services reference](https://docs.docker.com/reference/compose-file/services/).

After changing a value used by Compose, run `docker compose up -d` to recreate containers whose configuration changed. `docker compose restart` restarts existing containers and does not apply new environment or Compose settings, as described in the [`docker compose restart` reference](https://docs.docker.com/reference/cli/docker/compose/restart/).

The restart policy handles a stopped process. A failed healthcheck marks a running container unhealthy; it does not trigger the restart policy or stop an active scan by itself. Healthchecks are status signals for operators and dependent services. See Docker's [restart policy documentation](https://docs.docker.com/engine/containers/start-containers-automatically/).

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

Note: remove the `container_name:` line before using `replicas` or `docker compose up --scale`. Container names must be unique, so a fixed name prevents more than one instance from starting.

Or increase concurrency in a single container:

```yaml
environment:
  CELERY_CONCURRENCY: 8  # Increase from the default 4 concurrent tasks
```

### Performance tuning

Adjust these settings based on your system. They are operator starting points, not measured throughput or memory guarantees. Validate them against the media, storage, and database used by the deployment.

| Setting | Default | Description | Recommendation |
|---------|---------|-------------|----------------|
| CELERY_CONCURRENCY | 4 | Concurrent Celery tasks per container | Budget roughly 1 CPU and 2 GB RAM per slot (the worker recycles children at --max-memory-per-child, about 1.9 GiB); raise only if the container has matching cpus/memory limits |
| MAX_WORKERS | 10 | Threads for selected-file rescans only (Scan Selected); does not affect directory scans, which scale with CELERY_CONCURRENCY | Each thread holds one PostgreSQL connection. Leave at 10; raise toward 16-24 only for large hand-picked rescans with max_connections headroom. Not a function of CPU cores |
| BATCH_SIZE | 100 | Legacy media-checker discovery lookup batch | Leave at 100. It does not control parallel discovery inserts or scan chunk commits. |
| REDIS_MAX_MEMORY | 2gb | Task queue memory | 1-4gb for large libraries |

### PostgreSQL tuning

There is no PixelProbe environment variable for PostgreSQL memory; tune the database on the `postgres` service itself, for example:

```yaml
postgres:
  image: postgres:18-alpine
  command: postgres -c shared_buffers=1GB -c effective_cache_size=3GB -c work_mem=16MB -c max_connections=150
  deploy:
    resources:
      limits:
        memory: 4G
```

Rule of thumb: set `shared_buffers` to about 25% of the container's memory limit and `effective_cache_size` to about 75%. [configuration.md](configuration.md) shows an `ALTER SYSTEM` variant for a running database.

## Volume mounts

### Media directories

For a Portainer stack based on the root Compose file, add the six host path variables in the stack's environment settings. Replace the existing `/media` bind under both application services with these mounts, then set the matching `SCAN_PATHS` values. Keep the web service's `./instance` volume and the rest of the root service configuration.

```yaml
x-media-films: &media-films
  type: bind
  source: ${MEDIA_FILMS_PATH:?Set MEDIA_FILMS_PATH to an existing host directory}
  target: /library/films
  read_only: true
  bind:
    create_host_path: false
x-media-series: &media-series
  type: bind
  source: ${MEDIA_SERIES_PATH:?Set MEDIA_SERIES_PATH to an existing host directory}
  target: /library/series
  read_only: true
  bind:
    create_host_path: false
x-media-photos: &media-photos
  type: bind
  source: ${MEDIA_PHOTOS_PATH:?Set MEDIA_PHOTOS_PATH to an existing host directory}
  target: /library/photos
  read_only: true
  bind:
    create_host_path: false
x-media-music: &media-music
  type: bind
  source: ${MEDIA_MUSIC_PATH:?Set MEDIA_MUSIC_PATH to an existing host directory}
  target: /library/music
  read_only: true
  bind:
    create_host_path: false
x-media-home-video: &media-home-video
  type: bind
  source: ${MEDIA_HOME_VIDEO_PATH:?Set MEDIA_HOME_VIDEO_PATH to an existing host directory}
  target: /library/home-video
  read_only: true
  bind:
    create_host_path: false
x-media-misc: &media-misc
  type: bind
  source: ${MEDIA_MISC_PATH:?Set MEDIA_MISC_PATH to an existing host directory}
  target: /library/misc
  read_only: true
  bind:
    create_host_path: false

services:
  pixelprobe:
    environment:
      SCAN_PATHS: /library/films,/library/series,/library/photos,/library/music,/library/home-video,/library/misc
    volumes:
      - *media-films
      - *media-series
      - *media-photos
      - *media-music
      - *media-home-video
      - *media-misc
      - ./instance:/app/instance

  celery-worker:
    environment:
      SCAN_PATHS: /library/films,/library/series,/library/photos,/library/music,/library/home-video,/library/misc
    volumes:
      - *media-films
      - *media-series
      - *media-photos
      - *media-music
      - *media-home-video
      - *media-misc
```

The root Compose file runs both application services as the same configured user. Keep those identities and the web service's existing `instance` volume when adding media mounts:

`SCAN_PATHS` is synced into the database at web startup, and both services use it for path authorization. Recreate both services after changing it. Existing active database paths remain in use, so disable old roots in the UI if they should no longer be scanned.

Files such as `.mount-ok` used by host healthchecks are not read by the scanner and do not guard scan roots. For PixelProbe's mount identity check, set `require_mount` on a scan-path configuration; see [Required mount policy](operational-evidence.md#required-mount-policy).

```yaml
services:
  pixelprobe:
    # ... other settings ...
    user: "${PUID:-10001}:${PGID:-10001}"

  celery-worker:
    # ... other settings ...
    user: "${PUID:-10001}:${PGID:-10001}"
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

If the web app and Celery worker run as different users, the worker may not read the mounted media directories. The root Compose file also uses `create_host_path: false`, so a missing host directory fails at startup instead of being created as a directory.

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
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop all containers
docker compose down
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
1. Check Redis is running: `docker compose ps redis`
2. Check worker logs: `docker compose logs celery-worker`
3. Verify queue: `docker exec pixelprobe-redis valkey-cli LLEN pixelprobe`

### Database connection issues
1. Check PostgreSQL is healthy: `docker compose ps postgres`
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
5. Review published versions periodically and update image tags intentionally

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
2. Stop containers: `docker compose down`
3. Update image version in `docker-compose.yml`
4. Pull new image: `docker compose pull`
5. Start containers: `docker compose up -d`
6. Check logs: `docker compose logs -f`

## PostgreSQL 15 to 18 migration (required for v2.7.0+)

Starting with v2.7.0 the compose file defaults to `postgres:18-alpine`. PostgreSQL data directories are not portable across major versions. A `postgres_data` volume created by PostgreSQL 15 will refuse to start on the 18 image and the container will crash-loop with a version mismatch error. Migrate before switching to the new compose file. If you are not ready to migrate, pin `image: postgres:15-alpine`; the app works with both versions.

The postgres:18+ Docker images changed the expected volume mount from `/var/lib/postgresql/data` to `/var/lib/postgresql`. Data now lives in a major-version subdirectory so future upgrades can use `pg_upgrade --link`. The 18 image refuses a volume mounted at the old `/data` path. The bundled docker-compose.yml already uses the new mount. If you maintain your own compose file, update the postgres volume line to `- postgres_data:/var/lib/postgresql`.

Downtime for the migration is roughly the dump plus restore time (a few minutes for typical libraries).

```bash
# 1. Dump while the OLD stack is still running
DUMP=pixelprobe_pg15_$(date +%Y%m%d).sql.gz
docker exec pixelprobe-postgres pg_dump -U pixelprobe pixelprobe | gzip > "$DUMP"

# 2. Stop the stack
docker compose down

# 3. Keep the old volume as a fallback (rename instead of delete)
docker volume create pixelprobe_postgres_data_pg15_backup
docker run --rm -v pixelprobe_postgres_data:/from -v pixelprobe_postgres_data_pg15_backup:/to alpine sh -c "cp -a /from/. /to/"
docker volume rm pixelprobe_postgres_data

# 4. NOW switch to the v2.7.0 compose file (postgres:18 image and the new
#    /var/lib/postgresql volume mount) - e.g. git pull or edit your copy

# 5. Start ONLY postgres on the new compose file (creates a fresh v18 volume)
docker compose up -d postgres
# wait for: docker exec pixelprobe-postgres pg_isready -U pixelprobe

# 6. Restore the dump
gunzip < "$DUMP" | docker exec -i pixelprobe-postgres psql -U pixelprobe pixelprobe

# 7. Refresh collation metadata (avoids "collation version mismatch" warnings)
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "ALTER DATABASE pixelprobe REFRESH COLLATION VERSION;"
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "REINDEX DATABASE pixelprobe;"

# 8. Start the rest of the stack and verify
docker compose up -d
# sanity check: row counts should match your pre-migration numbers
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "SELECT COUNT(*) FROM scan_results;"
```

Note: your compose project name may prefix the volume (e.g. `pixelprobe_postgres_data`); check with `docker volume ls`. Once you have verified the app against PostgreSQL 18, the `_pg15_backup` volume and the dump file can be deleted.
