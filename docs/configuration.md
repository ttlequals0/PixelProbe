# PixelProbe configuration guide

Reference for deployment environment variables, saved scanner settings, and performance tuning.

## Table of contents

- [Environment variables](#environment-variables)
- [Docker Compose configuration](#docker-compose-configuration)
- [Database configuration](#database-configuration)
- [Performance tuning](#performance-tuning)
- [Scanning configuration](#scanning-configuration)
- [Celery configuration](#celery-configuration)
- [Data retention configuration](#data-retention-configuration)
- [Notification providers](#notification-providers)
- [Security configuration](#security-configuration)
- [Resource recommendations](#resource-recommendations)

## Environment variables

Deployment settings come from the process environment. Scanner tunables and scan paths can also be saved in the database through the UI or API. Their source and update behavior differ, as summarized below.

### Configuration sources and when changes take effect

| Setting | Source and precedence | When a change takes effect |
|---|---|---|
| Compose substitutions such as `MEDIA_PATH` and `POSTGRES_PASSWORD` | Host shell, then the project `.env` file; `.env` values are used by Compose for interpolation and are not automatically copied into containers. Only variables listed under a service's `environment` or `env_file` reach that container. If both are set, `environment` wins. Removing an environment entry can expose a same-name value in `env_file`, so check for conflicts before dropping defaults. | Recreate the affected container after changing values. `docker compose restart` restarts the existing container with its old configuration. |
| Scanner tunables such as freeze detection and decode limits | Saved `AppConfig` rows take priority. At startup, legacy environment variables seed any missing rows. A saved row wins while present; if a row is reset while its legacy variable remains set, the next startup can seed it again. | Workers refresh cached values within 60 seconds. Selected-file scans snapshot settings for each submitted file; other checks use cached values as they reach each setting lookup. An edit is not guaranteed to change a file already in progress. |
| Scan paths | At startup, `SCAN_PATHS` is inserted into the active database path list if those rows do not already exist. Active database paths drive scan selection; the environment is a fallback when none are active. Environment paths also remain authorization roots in both services. | Database path edits affect new requests. Changing `SCAN_PATHS` requires recreating both services; the web service runs startup sync. |
| Scan capacity | `CELERY_CONCURRENCY` sets Celery process count at worker start. `MAX_WORKERS` caps selected-file scan threads; that API defaults to 4 threads. | Recreate the worker after changing concurrency. Recreate both app and worker after changing the selected-scan cap. |
| Exclusions | On first startup, the v2.10.3 migration imports `exclusions.json` and legacy `EXCLUDED_PATHS` / `EXCLUDED_EXTENSIONS` values into PostgreSQL. Existing rows, including inactive rows, take precedence for an exact type/value pair. The migration marker prevents later imports. Active database exclusions are used by scanners and the scheduler. | The next file checker and scheduled scan load current active rules. An in-progress checker, discovery task, or scheduled scan keeps its captured rules; chunk checkers load current rules. |
| Stored scan output | `MAX_OUTPUT_SIZE` and `OUTPUT_ROTATION_ENABLED` are read when the model module imports. | Recreate app and worker processes after changing either value. |
| Library and framework settings | `PILLOW_BLOCKS_MAX` is read by Pillow; the bundled Compose value is `256`. `FLASK_ENV` defaults to `production` in Compose and remains configurable. | Recreate affected containers after changing either value. |
| Legacy discovery batch | `BATCH_SIZE` controls lookup batch size in the legacy media-checker discovery path. It does not set parallel discovery inserts or scan chunk commits. | Recreate the affected process after changing its environment. |
| Schedules | Saved UI/API schedules are loaded from PostgreSQL and reloaded when their definitions change. `PERIODIC_SCAN_SCHEDULE` and `CLEANUP_SCHEDULE` add process-level default jobs. | Database schedule edits are synchronized by the scheduler. Environment schedule changes require recreating the worker. |
| Startup settings such as database credentials, broker URLs, `SECRET_KEY`, and `TRUSTED_INTERNAL_HOSTS` | Process environment. Trusted hosts are cached after they are first read. | Recreate the affected container. |

## Effective Compose deployment

The root `docker-compose.yml` is the deployment source of truth. It starts PostgreSQL 18, Valkey 9, the `pixelprobe` web service, and `celery-worker`.

| Service | Scheduler | Media mount | Identity | Writable paths |
|---|---|---|---|---|
| `pixelprobe` | Disabled in bundled Compose | `${MEDIA_PATH}:/media:ro` | `${PUID:-10001}:${PGID:-10001}` | `./instance`, `/app/runtime`, `/tmp` |
| `celery-worker` | Enabled in bundled Compose by the application default | `${MEDIA_PATH}:/media:ro` | `${PUID:-10001}:${PGID:-10001}` | `/app/runtime`, `/tmp` |

The services share the same UID/GID and read-only media view. Do not change only one service identity or mount path. Both host media and `instance` paths must exist before startup. Compose rejects a missing media path instead of creating it. Give `instance` to the configured identity, for example `sudo chown 10001:10001 instance` when using defaults.

The web service publishes `${PORT:-5000}:5000`. PostgreSQL and Valkey are internal to the Compose network. The scheduler lease prevents duplicate ownership, but the normal deployment deliberately enables scheduler work only in `celery-worker`.

### Required variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret key (64 chars) | Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_PASSWORD` | PostgreSQL database password | Set a private value in `.env` |
| `MEDIA_PATH` | Host path to media files (Docker only) | `/mnt/media` |

### Database variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | PostgreSQL server hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL server port |
| `POSTGRES_DB` | `pixelprobe` | Database name |
| `POSTGRES_USER` | `pixelprobe` | Database username |
| `POSTGRES_PASSWORD` | (required) | Database password |
| `DB_POOL_SIZE` | `5` | SQLAlchemy base connection pool size per process |
| `DB_MAX_OVERFLOW` | `10` | Extra SQLAlchemy connections when the pool is exhausted |
| `DATABASE_ECHO` | `false` | Enable SQL query logging (debug) |

`DATABASE_URL` remains accepted as a deprecated compatibility override and takes precedence when explicitly set. Do not set it alongside `POSTGRES_*` unless you intend it to select that database. The bundled Compose file does not pass it to the application.

### Application variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `production` in the bundled Compose file | Flask environment (`production`, `development`, `testing`). The code default is `development`; the image and Compose deployment default to `production`. |
| `SCAN_PATHS` | (empty) | Comma-separated paths to scan inside container. The code default is empty; the bundled compose file sets `/media` |
| `TZ` | `UTC` | Timezone for timestamps (e.g., `America/New_York`) |
| `PORT` | `5000` | Host port published by the compose file; gunicorn always binds 5000 inside the container |
| `GUNICORN_BIND` | `0.0.0.0:5000` | Gunicorn listen address; accepts a comma-separated list for multiple sockets. For dual-stack IPv4+IPv6 (e.g. rootless podman) set `[::]:5000`; one IPv6 wildcard also accepts IPv4 when the kernel keeps `net.ipv6.bindv6only=0` (the Linux default). Do not list both `0.0.0.0:5000` and `[::]:5000` on such kernels; the second bind fails with `EADDRINUSE`. |
| `GUNICORN_WORKERS` | `4` | Gunicorn worker process count |
| `GUNICORN_TIMEOUT` | `300` | Gunicorn per-request timeout in seconds |
| `GUNICORN_LOG_LEVEL` | `info` | Gunicorn log level |
| `SCHEDULER_ENABLED` | `true` | Whether this process may run the scan scheduler. In multi-container deployments set `false` on the web container so only the Celery worker competes for the scheduler lock. Leave `true` in single-container setups. |

### Performance variables

| Variable | Default | Description | Recommendations |
|----------|---------|-------------|-----------------|
| `MAX_WORKERS` | `10` | Upper bound on thread workers accepted by the selected-file scan API. The API request defaults to 4 workers and clamps larger values to this limit. | Raise only for selected rescans and with PostgreSQL connection headroom. |
| `BATCH_SIZE` | `100` | Legacy media-checker discovery lookup batch. It does not set parallel discovery inserts or scan chunk commits. | Leave at `100` unless diagnosing that legacy path. |
| `MAX_OUTPUT_SIZE` | `10000` | Maximum stored scan-output characters before model-level rotation truncates the stored text. | Keep the default unless operators accept less retained diagnostic output. |
| `OUTPUT_ROTATION_ENABLED` | `true` | Enable model-level stored-output rotation. | Keep `true` for bounded stored output. |
| `CHUNK_HEARTBEAT_INTERVAL_SECS` | `120` | How often a running chunk task bumps the scan's liveness timestamp. Keeps a scan busy on one long movie from being falsely marked crashed by the 30-minute stuck-scan rule | Leave at default unless debugging |
| `CHUNK_REVIVE_STALENESS_SECS` | `600` | How stale the scan liveness timestamp must be before the stuck-scan sweeper treats the chunk workers as gone and re-queues their chunks (recovers scans interrupted by container restarts) | Must exceed several heartbeat intervals |

**Performance notes:**
- `MAX_WORKERS` caps selected-file scan threads; it does not set Celery process count.
- `CELERY_CONCURRENCY` sets the number of Celery worker processes.
- Worst-case connections = web app 4 gunicorn workers x (5 base + 10 overflow) = 60, plus CELERY_CONCURRENCY x (5 + 10) from worker children, plus selected-scan connections.
- Keep the total under PostgreSQL max_connections (default: 100). The extra large profile below exceeds 100 and needs `max_connections` raised to 150+

### Celery variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL for task queue |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Redis URL for results |
| `CELERY_CONCURRENCY` | `4` | Number of concurrent Celery tasks |
| `CELERY_LOG_LEVEL` | `INFO` | Celery log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

**Celery notes:**
- `CELERY_CONCURRENCY` controls how many scan tasks run simultaneously
- Independent from `MAX_WORKERS` (which caps selected-file scan threads)
- Recommended: 4-8 for most systems

### Redis variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_MAX_MEMORY` | `2gb` | Maximum Redis memory for task queue |

**Redis notes:**
- For large libraries (1M+ files), increase to 4gb
- Redis stores task queue and results temporarily
- Uses `noeviction` policy to prevent task loss

### Scanning variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERIODIC_SCAN_SCHEDULE` | (empty) | Automated scan schedule (cron or interval format) |
| `CLEANUP_SCHEDULE` | (empty) | Automated cleanup schedule (cron or interval format) |

**Schedule format examples:**
```bash
# Cron format (standard cron syntax)
PERIODIC_SCAN_SCHEDULE=cron:0 2 * * *        # Daily at 2 AM
CLEANUP_SCHEDULE=cron:0 3 * * 0              # Weekly on Sunday at 3 AM

# Interval format
PERIODIC_SCAN_SCHEDULE=interval:hours:6      # Every 6 hours
CLEANUP_SCHEDULE=interval:days:7             # Every 7 days
```

### Data retention variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCAN_OUTPUT_RETENTION_DAYS` | `30` | Days before archiving scan outputs (currently disabled) |
| `REPORT_RETENTION_DAYS` | `90` | Days before deleting old reports |
| `SCAN_STATE_RETENTION_DAYS` | `7` | Days before deleting completed scan states |

**Data retention notes:**
- Automated cleanup runs daily via the built-in MediaScheduler (APScheduler): data retention at 04:00, log retention at 03:00
- `SCAN_OUTPUT_RETENTION_DAYS` is currently not used (scan results kept forever)
- Log retention days are stored in the `app_configs` database table, not an environment variable. The default is 30 days. Change it through System > View Logs or `PUT /api/logs/retention`.

### Advanced variables

Rarely-changed knobs with sensible defaults.

**Scan and task lifecycle:**

| Variable | Default | Description |
|----------|---------|-------------|
| `STUCK_SCAN_STARTUP_GRACE_SECS` | `1800` | Grace period after startup before a scan with no liveness signal is treated as stuck |
| `DISCOVERY_TASK_TIMEOUT_SECS` | `3600` | Soft time limit for a single directory discovery task |
| `DISCOVERY_RESULT_TIMEOUT_SECS` | `7200` | How long the orchestrator waits for discovery results |
| `CELERY_MAX_TASKS_PER_CHILD` | `1000` | Tasks a Celery prefork child processes before being recycled |
| `INTEGRITY_BATCH_SIZE` | `10000` | Files per batch when queueing integrity (file-changes) checks |
| `INTEGRITY_TASK_TIMEOUT_SECS` | `10800` | Deadline before a stuck integrity check task is abandoned |
| `CLEANUP_TASK_TIMEOUT_SECS` | `600` | Deadline before a stuck existence-check (cleanup) task is abandoned |
| `MAX_CONCURRENT_SMALL` | `5000` | Max in-flight hash tasks for small files |
| `MAX_CONCURRENT_MEDIUM` | `500` | Max in-flight hash tasks for medium files |
| `MAX_CONCURRENT_LARGE` | `50` | Max in-flight hash tasks for large files |
| `MAX_CONCURRENT_HUGE` | `5` | Max in-flight hash tasks for huge files |
| `BITROT_STABLE_CHECKS_TO_EXPIRE` | `2` | Stable integrity checks before a bitrot suspicion expires |
| `ORPHAN_CLEANUP_ABORT_FLOOR` | `100` | Minimum count of kept records before the run's message calls out a possible storage outage |
| `ORPHAN_CLEANUP_MAX_DELETE_FRACTION` | `0.5` | Call out a possible outage when this fraction of the files checked could not be confirmed as deleted |

Orphan cleanup confirms each missing file before deleting its record, by reading the file's own directory and finding other files in it. A nonempty listing is evidence that the directory is available and supports treating the file as absent.

A folder holding one film has nothing left to say once you delete it. The question moves up a level: the parent lists folders, and one must hold a file PixelProbe recorded. That is evidence that the library is available, not proof of mount identity. Enable `require_mount=true` to compare the filesystem type, source, and mount root with the administrator-approved baseline.

Nothing above answers for a tree that has gone entirely, and those records are kept and counted. Files that have become readable again since the sweep are left alone.

Confirming that kept records were deleted is your decision. The UI offers it after a run that kept records, and the API takes `trust_unreadable_dirs: true` on `POST /api/cleanup-orphaned`; it is never set for a scheduled cleanup. A confirmed run that finds more unreadable records than the previous run kept stops without deleting, since your answer cannot cover storage that went offline in between. The two variables above only decide when a run's message calls the kept records a likely outage.

**Startup and migrations:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MIGRATION_LOCK_TIMEOUT_MS` | `10000` | PostgreSQL lock_timeout during startup migrations |
| `MIGRATION_STATEMENT_TIMEOUT_MS` | `300000` | PostgreSQL statement_timeout during startup migrations |

**Web security and networking:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_COOKIE_SECURE` | `true` | Send the session cookie over HTTPS only. Set `false` for plain-HTTP LAN deployments or login will not stick |
| `CORS_ORIGINS` | (empty) | Comma-separated origins allowed for cross-origin API requests |
| `RATELIMIT_STORAGE_URI` | (derived) | Storage for rate-limit counters. Defaults to the Celery broker's Redis on database 1; falls back to per-process memory if Redis is unavailable |

**Image processing:**

`PILLOW_BLOCKS_MAX` (set to `256` in the bundled Compose file) is read by Pillow, not PixelProbe. The installed Pillow default is `0`, so keep `256` in custom stacks unless you have measured a reason to change it.

### Monitoring variables (future)

| Variable | Default | Description |
|----------|---------|-------------|

## Docker Compose configuration

### Basic configuration

The root [`docker-compose.yml`](../docker-compose.yml) is the authoritative deployment configuration. It supplies the current image tag, exact web and worker environment forwarding, non-root identity, read-only bind behavior, resource limits, and scheduler ownership. Do not copy a second full Compose file from documentation.

For a local override, retain the root file's `PUID`, `PGID`, mount mode, and service names. For example:

```yaml
services:
  pixelprobe:
    environment:
      GUNICORN_WORKERS: 2
  celery-worker:
    environment:
      CELERY_CONCURRENCY: 2
```

See [Docker setup: media directories](docker-setup.md#media-directories) for a Portainer-ready example with six read-only mounts shared by the web and worker services.

### User permissions (important)

Both `pixelprobe` and `celery-worker` must run as the same user to access media files:

```yaml
pixelprobe:
  user: "${PUID:-10001}:${PGID:-10001}"
  volumes:
    - type: bind
      source: ${MEDIA_PATH:?Set MEDIA_PATH to an existing host directory}
      target: /media
      read_only: true
      bind:
        create_host_path: false

celery-worker:
  user: "${PUID:-10001}:${PGID:-10001}"  # MUST match pixelprobe
  volumes:
    - type: bind
      source: ${MEDIA_PATH:?Set MEDIA_PATH to an existing host directory}
      target: /media
      read_only: true
      bind:
        create_host_path: false
```

Find your UID/GID:
```bash
id -u  # Shows UID (typically 1000)
id -g  # Shows GID (typically 1000)
```

## Database configuration

### Connection pool settings

Configured in `config.py`:

```python
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_size': 5,            # Base pool size per process (DB_POOL_SIZE)
    'pool_pre_ping': True,     # Test connections before use
    'pool_recycle': 3600,      # Recycle connections after 1 hour
    'max_overflow': 10,        # Extra connections when pool exhausted (DB_MAX_OVERFLOW)
    'pool_timeout': 30,        # Timeout waiting for connection
    'connect_args': {'options': '-c timezone=UTC'},  # Pin the PostgreSQL session timezone to UTC
}
```

**Total connections (worst case):** Four Gunicorn workers use 60 connections: 4 x (5 base + 10 overflow). Add `CELERY_CONCURRENCY` x (5 + 10) for Celery prefork children, plus about `MAX_WORKERS` checker connections.

**PostgreSQL max_connections:**
- Default: 100 connections
- Recommended: 150+ for production
- Set in PostgreSQL: `ALTER SYSTEM SET max_connections = 150;`

### Database performance

For PostgreSQL optimization:

```sql
-- Increase shared buffers (25% of RAM) - requires a full restart
ALTER SYSTEM SET shared_buffers = '2GB';

-- Increase work memory for sorts - picked up by a config reload
ALTER SYSTEM SET work_mem = '16MB';

-- Enable parallel queries
ALTER SYSTEM SET max_parallel_workers_per_gather = 4;
```

`work_mem` and `max_parallel_workers_per_gather` take effect after `SELECT pg_reload_conf();`. `shared_buffers` and `max_connections` require a full PostgreSQL restart with `docker compose restart postgres`. A reload is not enough. The same settings can instead be passed as `command:` flags on the postgres service. See [docker-setup.md](docker-setup.md).

## Performance tuning

The values in this section are operator starting points, not benchmark results. `CELERY_CONCURRENCY` controls directory-scan throughput. `MAX_WORKERS` only caps thread count for selected-file scans. Measure CPU, memory, database connections, and media storage before increasing concurrency.

### Recommended settings by system size

`BATCH_SIZE` is omitted from these profiles because it only tunes a legacy discovery lookup. Leave `MAX_WORKERS` at its default unless selected-file scans need a higher cap.

#### Small library (< 10,000 files)
```bash
CELERY_CONCURRENCY=2
REDIS_MAX_MEMORY=512mb
```

#### Medium library (10,000 - 100,000 files)
```bash
CELERY_CONCURRENCY=4
REDIS_MAX_MEMORY=1gb
```

#### Large library (100,000 - 1,000,000 files)
```bash
CELERY_CONCURRENCY=6
REDIS_MAX_MEMORY=2gb
```

#### Extra large library (1,000,000+ files)
```bash
CELERY_CONCURRENCY=8
REDIS_MAX_MEMORY=4gb
```

### Resource allocation

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

### Storage performance

For best performance:

1. Database storage: SSD strongly recommended
2. Media storage: can be HDD, but SSD improves scan speed
3. Temp files: use tmpfs for temporary files (optional)

```yaml
postgres:
  volumes:
    - /mnt/ssd/postgres_data:/var/lib/postgresql  # SSD for database

pixelprobe:
  volumes:
    - /mnt/hdd/media:/media:ro                    # HDD OK for media
  tmpfs:
    - /tmp:size=1G                                # tmpfs for temp files
```

## Scanning configuration

### Exclusion behavior

The Tools > Exclusions UI stores paths and extensions in PostgreSQL. The API also supports filename patterns. Scanners and the scheduler use active database rules. This makes imported `EXCLUDED_EXTENSIONS` apply to all scanner paths, including selected-file corruption rescans. Removing configured rules does not remove built-in filename patterns: `._*`, `.DS_Store`, `Thumbs.db`, `.gitkeep`, and `.placeholder` always remain excluded.

At startup, the v2.10.3 migration imports legacy settings once. It reads `paths`, `extensions`, and `filename_patterns` from a root-level `exclusions.json`, plus `EXCLUDED_PATHS` and `EXCLUDED_EXTENSIONS` from the process environment. JSON and environment values are combined; environment extensions are lowercased, while JSON values keep their spelling. Existing database rows for an exact type and value win, including inactive rows. The `legacy_exclusions_imported_v1` marker prevents a later startup from importing those sources again, even after configured rules are removed. JSON may be absent; malformed JSON, non-list fields, empty/non-string entries, or entries longer than 500 characters stop startup without importing rows or setting the marker.

Extension matching uses lowercase file suffixes. Use lowercase `exclusions.json` extensions; environment imports and API writes normalize extensions to lowercase.

Filename patterns use Python `fnmatch` matching; matching is case-sensitive in the Linux containers.

After import, environment variables do not override database rules at runtime. Edit paths and extensions in Tools > Exclusions or manage all three types through the API. A `PUT /api/exclusions` request replaces the `paths` and `extensions` lists; an omitted key or an empty list clears that configured type. It changes `filename_patterns` only when that key is supplied; an empty list clears configured patterns. The single-rule API supports `filename_pattern`. The GET endpoint lists configured active rules; built-in filename patterns remain active regardless.

Each file checker uses the active rules it loaded when it was created. An in-progress checker keeps that snapshot. Parallel discovery carries its captured policy into its discovery task, while chunk checkers load current rules. The scheduler reloads exclusion paths before each scheduled scan; a scan already in progress keeps the root list it started with. Excluded files are skipped before file reads, hashing, decoding, or cache writes. Directory-scoped cleanup and integrity checks apply paths, extensions, and filename patterns they can translate to SQL. That maintenance filter supports `*` and `?`; bracket character classes are not applied. Exact-file maintenance requests and unscoped maintenance do not filter candidates by exclusion rules.

### Schedule configuration

Schedule automated scans:

**Via environment variables:**
```bash
# Daily full scan at 2 AM
PERIODIC_SCAN_SCHEDULE=cron:0 2 * * *

# Weekly cleanup on Sunday at 3 AM
CLEANUP_SCHEDULE=cron:0 3 * * 0
```

**Via web interface:**
1. Navigate to Tools > Schedules
2. Click "Create Schedule"
3. Configure schedule type, frequency, and scan type
4. Click Save

## Celery configuration

### Worker concurrency

Number of concurrent scan tasks:

```bash
# Low concurrency (memory-constrained systems)
CELERY_CONCURRENCY=2

# Medium concurrency (typical systems)
CELERY_CONCURRENCY=4

# High concurrency (powerful systems)
CELERY_CONCURRENCY=8
```

### Task prioritization

All tasks run on a single queue named `pixelprobe`. Priorities (0 = highest, 9 = lowest) are set per task and bucketed by `priority_steps` `[0, 3, 6, 9]`:

- Scan tasks: priority 3 (high)
- Default: tasks without an explicit priority (priority 5)
- check_file_exists: priority 6 (quick cleanup checks)
- calculate_file_hash: priority 7 (background maintenance)
- Retention cleanup: priority 9 (lowest)

### Scheduled maintenance (automated tasks)

There is no Celery Beat process. Scheduled maintenance runs in the built-in MediaScheduler (APScheduler, inside the Celery worker container by default):

- Log retention cleanup: daily at 03:00
- Data retention cleanup: daily at 04:00

## Data retention configuration

### Retention policies

Configure how long data is retained:

```bash
# Archive scan outputs after 30 days (currently disabled)
SCAN_OUTPUT_RETENTION_DAYS=30

# Delete old reports after 90 days
REPORT_RETENTION_DAYS=90

# Delete completed scan states after 7 days
SCAN_STATE_RETENTION_DAYS=7
```

### Manual cleanup

Run data retention manually:

```bash
# Docker
docker exec pixelprobe-app python tools/data_retention.py

# Manual installation
python tools/data_retention.py
```

**Warning:** the script has no dry-run mode - running it deletes expired
reports and scan states immediately.

## Notification providers

Notifications are configured through the API, not environment variables. A *provider* is where messages go; a *rule* maps an event to a provider. Provider types are `pushover`, `ntfy`, `webhook`, and `email`.

Event types available to rules are `scan_completed` and `bitrot_suspected`.

### Email (SMTP)

| Field | Default | Description |
|-------|---------|-------------|
| `smtp_host` | (required) | SMTP server hostname |
| `smtp_port` | `587`, or `465` when `security` is `ssl` | SMTP port |
| `security` | `starttls` | `starttls`, `ssl` (implicit TLS), or `none` |
| `username` | (optional) | SMTP username; login is skipped when unset |
| `password` | (optional) | SMTP password; returned masked as `***` |
| `from_address` | (required) | From address |
| `recipients` | (required) | List of addresses, or a comma-separated string |

Create an email provider:

```bash
curl -X POST http://localhost:5000/api/notifications/providers \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Ops mail",
    "provider_type": "email",
    "configuration": {
      "smtp_host": "smtp.example.com",
      "smtp_port": 587,
      "security": "starttls",
      "username": "pixelprobe@example.com",
      "password": "app-password",
      "from_address": "pixelprobe@example.com",
      "recipients": "ops@example.com, admin@example.com"
    }
  }'
```

Send a test message, then route an event to it:

```bash
curl -X POST http://localhost:5000/api/notifications/providers/1/test \
  -H "Authorization: Bearer $API_KEY"

curl -X POST http://localhost:5000/api/notifications/rules \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider_id": 1, "event_type": "scan_completed", "priority": "high"}'
```

**Notes:**
- STARTTLS and implicit SSL both verify certificates against the system trust
  store. `none` sends unencrypted and should only be used for a relay on
  localhost.
- Private, LAN and loopback SMTP hosts are allowed without a
  `TRUSTED_INTERNAL_HOSTS` entry; see [SSRF trusted hosts](#ssrf-trusted-hosts).
- A send failure is logged and recorded on the provider, never raised, so a
  broken mail server cannot fail a scan.
- Because secrets come back masked, submitting `***` for a field keeps the
  stored value rather than overwriting it.

## Security configuration

### SSRF trusted hosts

PixelProbe includes SSRF protection that blocks outbound requests to private/reserved IP ranges. If you use internal services for healthchecks, notifications (ntfy, webhooks), or similar integrations that resolve to private IPs, you can allowlist them:

Email (SMTP) notification providers are the one exception and need no allowlist entry. A self-hosted relay is normally on localhost, a Docker network, or the LAN. Private, LAN, and loopback SMTP hosts are permitted by default. Cloud-metadata and link-local addresses are still refused for SMTP.

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

### Secret key generation

Generate a secure secret key:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output to `SECRET_KEY` in `.env`.

### Session configuration

Session settings are configured in Flask:

```python
# Session timeout (24 hours)
PERMANENT_SESSION_LIFETIME = 86400

# Session cookie settings
SESSION_COOKIE_SECURE = True   # HTTPS only; default true. Plain-HTTP LAN
                               # deployments must set SESSION_COOKIE_SECURE=false
SESSION_COOKIE_HTTPONLY = True # Prevent JavaScript access
SESSION_COOKIE_SAMESITE = 'Lax' # CSRF protection
```

### API token authentication

Users can generate API tokens via:
1. Web UI: Account > API Tokens
2. API: `POST /api/auth/tokens`

Tokens support optional expiration dates.

## Resource recommendations

### CPU requirements

| Library Size | Minimum CPUs | Recommended CPUs |
|--------------|-------------|------------------|
| < 10K files | 2 cores | 4 cores |
| 10K-100K files | 4 cores | 8 cores |
| 100K-1M files | 8 cores | 16 cores |
| 1M+ files | 16 cores | 32 cores |

### Memory requirements

| Library Size | Minimum RAM | Recommended RAM |
|--------------|------------|-----------------|
| < 10K files | 2 GB | 4 GB |
| 10K-100K files | 4 GB | 8 GB |
| 100K-1M files | 8 GB | 16 GB |
| 1M+ files | 16 GB | 32 GB |

### Disk requirements

- Database: 100 MB per 10,000 files (estimated)
- Logs: 1-10 GB (depending on retention)
- Reports: 100 MB per 1,000 reports
- Temp files: 1-2 GB during scans

### Network requirements

- Bandwidth: minimal (local file access)
- Latency: low latency to database required
- Ports: 5000 (web), 5432 (postgres), 6379 (redis)

## Configuration best practices

Start with the defaults and raise `CELERY_CONCURRENCY` gradually, watching `docker stats` for CPU and memory pressure. Raise `MAX_WORKERS` only when selected-file scans need more threads. Back up the database and `.env` before upgrades; keep database passwords and `SECRET_KEY` secret.

## Examples

### Home media server (20K files)
```bash
CELERY_CONCURRENCY=3
REDIS_MAX_MEMORY=1gb
SCAN_PATHS=/movies,/tv
```

### Professional archive (500K files)
```bash
CELERY_CONCURRENCY=6
REDIS_MAX_MEMORY=4gb
SCAN_PATHS=/archive/video,/archive/images
```

### Multi-user production (2M files)
```bash
CELERY_CONCURRENCY=8
REDIS_MAX_MEMORY=8gb
SCAN_PATHS=/storage/media1,/storage/media2,/storage/media3
```

## Troubleshooting configuration

See [troubleshooting.md](troubleshooting.md) for solutions to common configuration issues.

## Scanner settings

These are edited under **System > Tunables** in the web interface, or through `GET`, `PUT` and `DELETE` on `/api/settings`. They are stored in the database and survive a container rebuild. Workers refresh cached values within 60 seconds. Selected-file scans snapshot settings for each submitted file; other checks use cached values at each setting lookup, so a change is not guaranteed to affect work already in progress.

These settings used to be environment variables. At startup, any legacy variable still set seeds its database row only if that row is missing. A saved row wins while it exists. Resetting a setting through the API deletes its row and restores the default until the next startup; if its legacy variable is still set, that value can be seeded again. Remove the legacy variable before resetting a setting if you want the default to persist across restarts.

### Detection

What counts as a finding. These decide which files get flagged.

| Setting | Name in the UI | Default | What it does |
|---------|----------------|---------|--------------|
| `detection.freeze_detection_enabled` | Detect frozen video | `on` | Look for stretches where the picture stops changing. This is a full decode of every video and is the slowest part of a scan. |
| `detection.freeze_min_duration_secs` | Shortest freeze to report | `7.0` seconds | Seconds. Animation holds a drawing still for several seconds at a time, so a low value reports ordinary cartoons. Raise it to report only longer freezes. Range 1.0 to 600.0. |
| `detection.freeze_uncorroborated_min_secs` | Longest freeze to excuse | `60.0` seconds | Seconds. A frozen stretch with its packets present and its frames decodable stopped on purpose, so it is not reported. Past this length it is reported anyway, in case a source recorded a genuinely stuck picture. Range 10.0 to 3600.0. |
| `detection.data_hole_alloc_ratio` | Incomplete file check threshold | `0.9` | A file storing less data than its length claims is opened and checked for unwritten regions. Compressing filesystems store healthy files in less space, so this only decides which files are worth checking. Range 0.0 to 1.0. |
| `detection.data_hole_min_pct` | Unwritten share that means damage | `1.0` percent | Percent of a file that must be unwritten before it is called incomplete. Range 0.0 to 100.0. |

### Performance

Limits on how much work one file may cost. These bound scan time, not accuracy.

| Setting | Name in the UI | Default | What it does |
|---------|----------------|---------|--------------|
| `performance.temporal_sample_secs` | Length of each sampled window | `10` seconds | Seconds decoded at each of three points in a large file when looking for timing anomalies. Range 5 to 120. |
| `performance.temporal_min_frames` | Frames needed to judge a window | `100` | Below this many decoded frames the measurements are noise and no verdict is recorded. Range 1 to 10000. |

### Timeouts

How long to wait on storage and tools before giving up on a file.

| Setting | Name in the UI | Default | What it does |
|---------|----------------|---------|--------------|
| `timeouts.temporal_sample_timeout_secs` | Sampled window timeout | `30` seconds | Seconds to wait on one sampled window. Raise this on a busy host, where a timeout means contention rather than a bad file. Range 10 to 3600. |
| `timeouts.ffprobe_timeout_secs` | Metadata read timeout | `120` seconds | Seconds to wait when reading a file's metadata. Raise it on slow storage. Range 10 to 3600. |
| `timeouts.audio_decode_base_secs` | Audio decode base timeout | `120` seconds | Starting budget for checking one audio file, shared by its decode, container and lossless passes. The real budget scales up with the file's size and playing time, so a long episode is not cut short. Range 30 to 3600. |
| `timeouts.file_read_timeout_secs` | File read timeout | `60` seconds | Seconds to wait on a raw read before treating the file as unreadable and moving on. The hashing deadline scales up with file size on top of this. Range 10 to 3600. |
