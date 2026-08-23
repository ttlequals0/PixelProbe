# How PixelProbe works

## Table of contents
1. [Overview](#overview)
2. [Layered architecture](#layered-architecture)
3. [Container architecture](#container-architecture)
4. [Celery queue system](#celery-queue-system)
5. [Scan lifecycle](#scan-lifecycle)
6. [Data flow](#data-flow)
7. [Validation pipeline](#validation-pipeline)
8. [Container interactions](#container-interactions)
9. [Scheduler and coordination](#scheduler-and-coordination)
10. [Logging and observability](#logging-and-observability)
11. [Failure recovery](#failure-recovery)
12. [Security](#security)
13. [Scaling and performance](#scaling-and-performance)
14. [Environment variables](#environment-variables)
15. [Deployment example](#deployment-example)
16. [Technology stack](#technology-stack)
17. [Extension points](#extension-points)

## Overview

PixelProbe is a distributed media corruption detection system that runs as a set of Docker containers. It uses Celery for distributed task processing, Redis for message queuing, and PostgreSQL for persistent storage. The system distributes work across all available Celery workers, recovers automatically from failures, and has been tested with libraries of 1M+ files.

## Layered architecture

### System components

```
+----------------------------------------------------------------+
|                         Web Interface                          |
|                    (HTML/CSS/JavaScript)                       |
+----------------------------------------------------------------+
                                |
                                v
+----------------------------------------------------------------+
|                        Flask API Layer                         |
|          (Routes, Request Validation, Rate Limiting)           |
+----------------------------------------------------------------+
                                |
                    +-----------+-----------+
                    v                       v
+-------------------------+      +-------------------------+
|      Service Layer      |      |     Security Layer      |
|    (Business Logic)     |      |   (Auth, Validation)    |
+-------------------------+      +-------------------------+
                    |                       |
                    +-----------+-----------+
                                v
+----------------------------------------------------------------+
|                       Repository Layer                         |
|                    (Database Abstraction)                      |
+----------------------------------------------------------------+
                                |
                                v
+----------------------------------------------------------------+
|                      PostgreSQL Database                       |
|                 (Scan Results, Configurations)                 |
+----------------------------------------------------------------+
                                |
                +---------------+---------------+
                v               v               v
+---------------------+ +-------------+ +---------------------+
|    Celery Workers   | |    Redis    | |   Task Scheduler    |
|   (Parallel Tasks)  | |   (Queue)   | |    (APScheduler)    |
+---------------------+ +-------------+ +---------------------+
        |                                       |
        v                                       v
+---------------------+                 +---------------------+
|    Media Scanner    |                 |   Scheduled Scans   |
| (FFmpeg/ImageMagick)|                 |     (Cron-like)     |
+---------------------+                 +---------------------+
```

### Core design principles

1. **Separation of Concerns**
   - Presentation: Web UI components
   - API: RESTful endpoints
   - Business Logic: Service layer
   - Data Access: Repository pattern
   - Security: Centralized validation and authentication

2. **Security First**
   - Input validation at every layer
   - Path traversal protection
   - Command injection prevention
   - Rate limiting
   - Audit logging

3. **Scalability**
   - Stateless API design
   - Parallel scanning support
   - Database connection pooling
   - Efficient file processing

### Presentation layer

**Location**: `templates/`, `static/`

**Responsibilities**:
- User interface rendering
- Progress updates via API polling
- Form validation
- API communication

**Key Components**:
- `index.html`: Main application interface
- `static/js/app.js`: Client-side application logic (vanilla JavaScript)
- Hand-rolled CSS and JavaScript bundled by webpack 5
- Chart.js 4.4.0 and Font Awesome 6.4.0 loaded via CDN

### API layer

**Location**: `pixelprobe/api/`

**Responsibilities**:
- HTTP request handling
- Input validation
- Response formatting
- Rate limiting
- CORS handling

**Key Modules**:
```
api/
+-- scan_routes.py          # Scanning operations
+-- scan_routes_parallel.py # Parallel scan endpoints
+-- scan_launch.py          # Shared scan-launch helper (validate, claim, dispatch)
+-- stats_routes.py         # Statistics and reports
+-- admin_routes.py         # Administrative functions
+-- auth_routes.py          # Login, users, API tokens
+-- auth_decorator.py       # Authentication decorator
+-- export_routes.py        # Data export
+-- healthcheck_routes.py   # Healthcheck integration
+-- log_routes.py           # Log viewing and download
+-- notification_routes.py  # Notification providers/rules
+-- reports_routes.py       # Scan reports
+-- maintenance_routes.py   # Cleanup operations
```

### Service layer

**Location**: `pixelprobe/services/`

**Responsibilities**:
- Business logic implementation
- Data processing
- External tool integration
- Transaction coordination

**Key Services**:
- `ScanService`: Orchestrates media scanning
- `StatsService`: Calculates statistics
- `ExportService`: Handles data exports
- `MaintenanceService`: Database maintenance
- `NotificationService`: Notification provider dispatch and rule evaluation
- `HealthcheckService`: Outbound healthcheck pings for scheduled scans
- `scan_engine`: Celery-free core of the chunk-distributed scan engine (scan-slot claim, chunk building, finalization)
- `scan_reporting`: Scan report creation

### Security layer

**Location**: `pixelprobe/utils/security.py`

**Responsibilities**:
- Path validation
- Input sanitization
- Command injection prevention
- SSRF protection for outbound requests
- Audit logging
- Rate limiting implementation

**Key Functions**:
- `validate_file_path()`: Prevents directory traversal
- `safe_subprocess_run()`: Safe command execution (no shell, argument validation)
- `validate_json_input()`: Input validation decorator
- `AuditLogger`: Security event logging
- `validate_outbound_host()` / `validate_safe_url()`: SSRF protection for outbound requests (private-IP blocking with a `TRUSTED_INTERNAL_HOSTS` allowlist)
- `ensure_cli_safe_path()`: Guards paths passed to CLI tools

### Repository layer

**Location**: `pixelprobe/repositories/`

**Responsibilities**:
- Database operations
- Query optimization
- Transaction management
- Data model mapping

**Key Repositories** (all extend a generic `BaseRepository`):
- `ScanRepository`: Scan result operations
- `ScanStateRepository`: Scan state operations
- `ConfigurationRepository`: Configuration management
- `IgnoredPatternRepository`: Ignored error patterns
- `ScheduleRepository`: Scan schedules

### Data layer

**Technology**: PostgreSQL (required since v2.2.0)

**Models** (all in `pixelprobe/models.py`, 17 total):
- `ScanResult`: File scan results
- `ScanConfiguration`: Directory configurations
- `IgnoredErrorPattern`: False positive patterns
- `Exclusion`: Excluded paths and extensions
- `ScanSchedule`: Scheduled scan configurations
- `ScanState`: Current scan status
- `ScanChunk`: Per-chunk progress for parallel scans
- `ScanReport`: Completed scan reports
- `CleanupState` / `FileChangesState`: Maintenance operation state
- `HealthcheckConfig`: Healthcheck ping configuration
- `User` / `APIToken`: Authentication
- `NotificationProvider` / `NotificationRule`: Notifications
- `LogEntry`: Persistent log storage with scan tagging
- `AppConfig`: Application-level key-value configuration

## Container architecture

```
+-----------------------------------------------------------------------------+
|                              Docker Network                                 |
|                                                                             |
|  +-----------------+       +-----------------+      +-----------------+    |
|  |                 |       |                 |      |                 |    |
|  |   Web App       |<----->|     Redis       |<---->|  Celery Worker  |    |
|  |   (Flask)       |       |   (Message      |      |     Pool        |    |
|  |   Port: 5000    |       |    Broker)      |      |   (4 workers)   |    |
|  |                 |       |   Port: 6379    |      |                 |    |
|  +--------+--------+       +-----------------+      +--------+--------+    |
|           |                                                  |             |
|           v                                                  v             |
|  +----------------------------------------------------------------------+  |
|  |                         PostgreSQL Database                          |  |
|  |                         Port: 5432                                   |  |
|  |                  (Persistent Storage for All Data)                   |  |
|  +----------------------------------------------------------------------+  |
|                                                                             |
|  +-----------------+                          +-----------------+          |
|  |   Media Files   |<-------------------------|   FFmpeg &      |          |
|  |   Volume Mount  |                          |   ImageMagick   |          |
|  |  /media:/media  |                          |   (In Workers)  |          |
|  +-----------------+                          +-----------------+          |
+-----------------------------------------------------------------------------+
```

### Container descriptions

#### 1. Web application container (`pixelprobe-app`)
- **Image**: `ttlequals0/pixelprobe:latest`
- **Purpose**: Serves the web UI and REST API
- **Responsibilities**:
  - Handle HTTP requests from users
  - Render web interface
  - Submit tasks to Celery queue
  - Query database for results
  - Manage user sessions
- **Key Processes**:
  - Gunicorn WSGI server
  - Flask application
  - (Scheduler disabled: `SCHEDULER_ENABLED=false` keeps the web container out of the scheduler lock)

#### 2. Celery worker container (`celery-worker`)
- **Image**: `ttlequals0/pixelprobe:latest` (same image, different entry point)
- **Purpose**: Process background tasks in parallel
- **Responsibilities**:
  - Execute media scanning tasks
  - Process file discovery operations
  - Handle cleanup operations
  - Report progress to Redis
  - Run APScheduler for scheduled tasks (leader election via a Redis lock: `SET NX` with a per-process uuid value, 60s TTL, refreshed by a heartbeat that atomically compares the value before extending the expiry)
- **Configuration**:
  - Started via `python celery_worker.py`, which consumes the `pixelprobe` queue with `--max-tasks-per-child 1000`, a 2GB `--max-memory-per-child` limit, and `--without-gossip/--without-mingle/--without-heartbeat`
  - Default: 4 concurrent workers
  - Configurable via `CELERY_CONCURRENCY` environment variable
- **Tools Available**:
  - FFmpeg for video/audio analysis
  - ImageMagick for image analysis
  - Python PIL for additional image processing

#### 3. Redis container
- **Image**: `valkey/valkey:9-alpine`
- **Purpose**: Message broker and result backend
- **Responsibilities**:
  - Queue task messages
  - Store task results temporarily
  - Coordinate worker pool
- **Persistence**: Optional volume mount for data persistence

#### 4. PostgreSQL container
- **Image**: `postgres:18-alpine`
- **Purpose**: Primary data storage
- **Responsibilities**:
  - Store scan results
  - Maintain file metadata
  - Track scan history
  - Store user configurations
  - Manage scan state
- **Features**:
  - Connection pooling (5 base, 10 overflow, 15 max per process)
  - Automatic reconnection
  - Transaction support

## Celery queue system

### How Celery works in PixelProbe

```
User Request -> Flask App -> Celery Task -> Redis Queue -> Worker Pool -> Execution
                                |                              |
                            Task ID                    Progress Updates
                                |                              |
                          Return to User              Update Database
```

### Task types and queues

#### Main queue (`pixelprobe`)
All tasks use a single queue for simplicity and load balancing.

**Task Types**:
- `parallel_scan_orchestrator`: Directory scan orchestration (discover -> chunk -> fan out)
- `discover_directory_task`: Parallel directory discovery (bulk-inserts pending rows)
- `process_chunk_task`: Process FCP path-range chunks (last chunk finalizes the scan)
- `scan_media_task`: Single-file scans + compatibility shim for queued directory scans
- `scan_files_task`: Selected-file batch rescans
- `check_file_exists_task`: File existence checks for cleanup
- `calculate_file_hash_task`: Hash recalculation for file-changes / integrity checks
- `run_retention_cleanup`: Data retention cleanup
- `reload_schedules_task`: Signal schedule reload after create/update/delete
- `health_check_task`: System health monitoring

### Task distribution strategy

1. **Chunk-Based Distribution**:
   - Files are divided into path-range chunks; chunk size adapts to scan size (<=100 files: single chunk, <=1,000: 100/chunk, <=10,000: 500/chunk, larger: 1,000/chunk)
   - Each chunk becomes an independent task
   - Workers pull chunks from queue as they become available

2. **Worker Pool Management**:
   - Workers are stateless and interchangeable
   - Automatic retry on failure (3 attempts with exponential backoff: 30s, 60s, 120s)
   - `task_acks_late` + `task_reject_on_worker_lost` redeliver tasks lost to a worker crash

3. **Load Balancing**:
   - Workers pull tasks from Redis when ready (pull model, not push)
   - `worker_prefetch_multiplier: 1` means each worker slot takes one task at a time, so long-running chunks never strand queued work behind a busy worker

## Scan lifecycle

A directory scan moves through the following steps:

**Step 0: Validate, then claim the scan slot.** The API validates the requested directories first (`pixelprobe/api/scan_launch.py`); any failure after a claim must release it. `claim_scan_slot()` (`pixelprobe/services/scan_engine.py`) then atomically claims the single scan slot with a `SELECT ... FOR UPDATE NOWAIT` row lock on `ScanState`. If another scan is active, or the row lock cannot be acquired, the request returns HTTP 409. On success the orchestrator task is dispatched; if dispatch fails, the claim is released.

**Step 1: Discovery** (phase `discovering`). The orchestrator launches ONE `discover_directory_task` per configured scan path. Each task walks its directory and bulk-inserts discovered media files as `pending` rows, returning counts only (never file lists). If any discovery task is incomplete, the scan aborts with an error rather than reporting a partial file set as complete.

**Step 2: Chunking** (phase `adding`). `build_scan_chunks()` divides all pending rows into disjoint path-range chunks. Chunk size adapts to scan size:

| Total pending files | Chunk size   |
|---------------------|--------------|
| <= 100              | one chunk    |
| <= 1,000            | 100 files    |
| <= 10,000           | 500 files    |
| larger              | 1,000 files  |

**Step 3: Fan-out** (phase `scanning`). Celery task ids for every chunk are pre-assigned and committed to the database BEFORE dispatch, so a fast worker can never observe a not-yet-written owner id, and cancellation/ownership checks always have the authoritative id.

**Step 4: Chunk execution.** Each `process_chunk_task` bulk-claims its chunk's pending rows in one race-free statement (ranges are disjoint by construction), then validates files and commits progress in batches of 100 files.

**Step 5: Heartbeat.** Each chunk task (and each discovery task) runs a daemon heartbeat thread that bumps `ScanState.last_update` every `CHUNK_HEARTBEAT_INTERVAL_SECS` (default 120s). This keeps a scan busy on one large file (30-60+ minutes on a single movie) from being mistaken for a dead one by the stuck-scan sweeper.

**Step 6: Finalization.** The last chunk to finish finalizes the scan (totals, report, healthcheck ping) under a `ScanState` row lock. The stuck-scan sweeper acts as a backstop if the winning chunk dies between chunk-complete and finalize.

### Scan phases

Phase names are canonical in `pixelprobe/constants.py`:

| Phase          | Meaning                                        | Category |
|----------------|------------------------------------------------|----------|
| `idle`         | No scan running                                | Terminal |
| `initializing` | Scan slot claimed, orchestrator starting       | Active   |
| `discovering`  | Directory walk finding candidate files         | Active   |
| `adding`       | Building scan chunks from pending rows         | Active   |
| `scanning`     | Chunk tasks validating files                   | Active   |
| `completed`    | Scan finished normally                         | Terminal |
| `error`        | Scan ended with an error                       | Terminal |
| `crashed`      | Sweeper marked the scan dead                   | Terminal |
| `cancelled`    | User cancelled the scan                        | Terminal |

## Data flow

### Scan initiation flow
```
Web UI -> API Request -> Flask Route -> Scan Service -> Celery Task -> Redis
                                            |
                                    PostgreSQL (Create Scan State)
```

### Progress update flow
```
Chunk task -> PostgreSQL (scan_state + scan_chunks, every 100 files or 60s)
        | (mirrored on each write)
   Redis (scan_progress:{id}) -> API Poll -> Web UI
```

PostgreSQL is the source of truth for progress. Chunk tasks commit progress at batch boundaries (every 100 files) or at least every 60 seconds, and mirror each write to Redis as a plain `SET` on the `scan_progress:{id}` key. The scan-status API reads that key with a `GET` (Redis first, database fallback) and the web UI polls the API; there is no pub/sub channel.

### Result storage flow
```
Media File -> FFmpeg/ImageMagick -> Analysis Result -> PostgreSQL
                                          |
                                    Update Statistics
```

## Validation pipeline

Each file is validated in `pixelprobe/media_checker.py` (`PixelProbe.scan_file`):

1. **Format detection**: file metadata and MIME type are read via libmagic (with a read timeout so an unreadable file is skipped instead of hanging the scan), and the file is routed by type to the matching checker.

2. **Data integrity** (all media types, before any decode): an interrupted download or copy leaves a file at its correct length with regions inside it that were never written. Two steps, and only the second decides:
   - Allocated blocks are compared against nominal size. This is a gate, not a verdict: filesystems with compression or dedup under-allocate healthy files too, so it only decides which files are worth opening
   - Files below the ratio are queried with `SEEK_HOLE` / `SEEK_DATA`, which reports the regions the filesystem never allocated. This reads no file data and costs a handful of seeks

   The distinction matters. A valid file can legitimately hold long runs of zero bytes, such as digital silence in PCM audio or flat colour in an uncompressed image, and those bytes were written. Only a real hole means data is absent, and a byte-level test cannot tell the two apart.

   A file with unwritten regions is marked **corrupted** and no decode runs. Decoding one produces freeze and frame-count findings that describe the gaps rather than the picture, which is why this check comes first. A filesystem that does not support sparse-region queries yields no verdict rather than a guess. Both thresholds are settings under System > Tunables.

3. **Images**: PIL verification (`Image.open` + `verify()` and a load test) followed by ImageMagick validation with `-regard-warnings` (warnings treated as errors). ImageMagick timeouts scale with file size.

4. **Audio**: ffprobe stream analysis - stream presence, codec, sample rate, channels, and duration checks.

5. **Video**: three passes plus staged deep analysis:
   - ffprobe metadata probe (stream presence, codec, duration)
   - Full remux validation: FFmpeg reads the ENTIRE file with `-map 0 -c copy -f null -` and aggressive error detection to validate container integrity across all streams
   - Enhanced corruption analysis, run for every video:
     - **Stage 1 - Frame integrity** (always, warning-only): the packet count from a demux-only `ffprobe -count_packets` pass is compared against duration and framerate; a mismatch above 5% is confirmed with a full `-count_frames` decode before a warning is recorded. Never a corruption verdict - container framerate metadata lies on sparse-video and VFR files
     - **Stage 2 - Temporal outlier detection** (files > 1GB): sampled decode windows checked for timing anomalies; can mark corrupt or warn
     - **Stage 3 - Multi-point sampling** (files > 5GB): decodes 10s samples at beginning, middle, and end; NEVER marks a file corrupted (seeking produces FFmpeg-version-dependent false positives), results are informational
     - **Stage 4 - Strict error detection** (warnings only): `-err_detect crccheck+bitstream+buffer+explode` over the first 30 seconds; findings are container/muxing warnings, never corruption verdicts

6. **Freeze detection** (videos, separate pass): a full-decode pass through FFmpeg's `freezedetect` filter (with `blackdetect`) flags frozen-picture segments, then three filters run over the candidates:
   - Segments overlapping a black section are dropped, because a real freeze sticks on picture rather than on a fade
   - A solitary short freeze against either end of the file is discounted as a static title or end card. The bound is the smaller of 60 seconds and 10% of runtime, so on a short clip it cannot reach the middle of the file
   - Each surviving candidate is re-checked over its own window at a noise tolerance only repeated frames can clear. The default tolerance compares a whole-frame mean, which limited animation scores below while every frame still differs; the confirmation pass drops those. If the pass cannot run, the candidate is kept, so a failure never erases a finding

   What survives is a warning - it never marks a file corrupted - and the whole pass can be switched off under System > Tunables. Reported frozen time counts overlapping events once and is capped at the runtime.

7. **Dynamic timeouts**: FFmpeg validation timeouts are computed from file size and duration (roughly 3 minutes per GB or ~2x realtime, capped at 2 hours), so large files are neither killed prematurely nor allowed to hang forever.

## Container interactions

### 1. Web app <-> Redis
- **Protocol**: Redis protocol (TCP)
- **Purpose**: Submit tasks, read mirrored scan progress
- **Redis holds exactly three things**:
  - Celery broker queues and result backend
  - The `scan_progress:{id}` progress mirror (plain SET/GET)
  - The scheduler lock (`pixelprobe:scheduler:lock`)
- There is no general cache layer and no pub/sub.

### 2. Web app <-> PostgreSQL
- **Protocol**: PostgreSQL wire protocol
- **Purpose**: CRUD operations on data
- **Connection Pool**:
  - Base: 5 connections (`DB_POOL_SIZE`)
  - Max overflow: 10 connections (`DB_MAX_OVERFLOW`)
  - Total max: 15 connections per process
  - Recycle time: 3600 seconds

### 3. Celery workers <-> Redis
- **Protocol**: Redis protocol
- **Purpose**: Receive tasks, store results, write progress mirror, hold the scheduler lock

### 4. Celery workers <-> PostgreSQL
- **Protocol**: PostgreSQL wire protocol
- **Purpose**: Store scan results
- **Connection Strategy**:
  - Each worker maintains own connection
  - Connection pooling within worker
  - Automatic reconnection on failure

### 5. All containers <-> media volume
- **Type**: Docker volume mount
- **Mount Point**: `/media` in containers
- **Access**: Read-only for safety
- **Purpose**: Access media files for scanning

## Scheduler and coordination

### Scheduler lock

Exactly one process across all containers may run APScheduler. Ownership is claimed via Redis `SET NX` on `pixelprobe:scheduler:lock` with a 60-second TTL. The lock value carries a per-process uuid (hostname/pid are not reliable identity across containers), and a heartbeat thread refreshes the TTL every 30 seconds with an atomic compare-and-expire script that only extends the TTL while this process still holds the lock. A dead holder is recovered by TTL expiry, which standby processes pick up in their retry loop. When Redis is unavailable, a file-based lock is the fallback. `SCHEDULER_ENABLED=false` keeps a process out of the election entirely (used for the web container in multi-container deployments).

### Scheduler jobs

The process holding the lock runs these jobs (plus any user-defined scan schedules):

| Job                     | Trigger            | Purpose                                             |
|-------------------------|--------------------|-----------------------------------------------------|
| `log_retention_cleanup` | cron, daily 03:00  | Delete old `LogEntry` rows per retention policy     |
| `data_retention_cleanup`| cron, daily 04:00  | Delete old data per retention policy                |
| `stuck_scan_checker`    | interval, 5 min    | Detect and revive/crash stuck scans                 |
| `db_schedule_sync`      | interval, 60s      | Re-sync saved schedules from the database via a fingerprint comparison, so schedule create/update/delete in gunicorn workers takes effect without a restart |

### Startup migration coordination

Database migrations run at startup under a PostgreSQL advisory lock (`pg_try_advisory_lock`), so with multiple gunicorn workers and containers starting concurrently, exactly one process runs the migrations while the others wait.

## Logging and observability

### Persistent logging

- Log records are persisted to PostgreSQL as `LogEntry` rows (v2.6.0+), alongside structured logging to stdout
- `DatabaseLogHandler` writes on a background thread with batch inserts, so logging never blocks request or scan paths
- Scan/task-level log tagging via Python contextvars: records emitted inside a scan or Celery task carry the scan id
- Celery prefork children reattach a fresh `DatabaseLogHandler` on `worker_process_init` - the handler set up in the parent does not survive the fork
- Configurable log retention with automatic cleanup (daily 03:00 scheduler job)
- Log viewer UI with filtering, search, and download
- Security audit trail via `AuditLogger`

### Health endpoints

| Endpoint   | Auth            | Purpose                                                                 |
|------------|-----------------|-------------------------------------------------------------------------|
| `/healthz` | Unauthenticated | Liveness probe for container healthchecks. No DB ping: a DB blip must not restart-loop the container |
| `/health`  | Authenticated   | Application health check with version and timestamp                     |

### Metrics to monitor
1. **Queue Depth**: Tasks waiting in Redis
2. **Worker Utilization**: Active vs idle workers
3. **Database Connections**: Active connections
4. **Scan Throughput**: Files/minute
5. **Error Rate**: Failed tasks

## Failure recovery

### Chunk revival

If a worker dies mid-scan (container restart, queue loss), the stuck-scan sweeper revives the scan instead of crashing it. All of the following must hold:

- `ScanState.last_update` is staler than `CHUNK_REVIVE_STALENESS_SECS` (default 600s = 5 missed heartbeats)
- The scan is in phase `scanning` (discovery-phase scans are not revivable - the orchestrator's harvest loop cannot be resumed)
- Chunk rows are still active for the scan
- Fewer than 3 revival attempts have been made for this scan

`redispatch_orphaned_chunks()` then reclaims each orphaned chunk's unscanned rows back to `pending`, commits a NEW Celery task id BEFORE dispatch (so any ghost delivery of the old task is superseded instead of racing the revival), and re-queues the chunk.

### Stuck scan detection

The sweeper runs every 5 minutes and marks a scan `crashed` when any of these branches fires:

1. No update for more than 30 minutes (the chunk heartbeat means this only fires on true crashes)
2. No update for more than 5 minutes AND the scan's Celery work is definitively gone (task state terminal and no active chunks)
3. Scan started more than 30 minutes ago with no `last_update` at all

The sweeper also finalizes scans whose winning chunk died between chunk-complete and finalization.

### Duplicate delivery guards

`task_acks_late` means a task lost to a worker crash is redelivered - so chunk tasks guard against duplicates: a delivery against a chunk that is already finished returns `ALREADY_TERMINAL`, and a delivery whose task id no longer matches the chunk's recorded owner (e.g. after a revival re-dispatch) returns `SUPERSEDED`. Either way the stale delivery is a no-op.

### Worker crash recovery

- Worker exits non-zero on unrecoverable errors; the container restart policy brings it back
- `task_acks_late` + `task_reject_on_worker_lost` redeliver in-flight tasks to healthy workers

### Database connection recovery

- Automatic reconnection
- Connection pool recovery
- Transaction rollback on failure

### Redis connection recovery

- Automatic reconnection with backoff
- Queue persistence (optional)
- Task redelivery on reconnection

## Security

### Defense in depth

1. **Input Layer**:
   - JSON schema validation (`validate_json_input`)
   - Type checking
   - Length limits

2. **Path Security**:
   - Whitelist-based validation against configured scan paths
   - Normalized paths and symlink resolution
   - CLI-safe path guards for tool invocations

3. **Command Execution**:
   - No shell execution (`safe_subprocess_run`)
   - Argument validation
   - Timeout limits

4. **Outbound Requests (SSRF)**:
   - `validate_outbound_host` / `validate_safe_url` block private-IP targets for notification and healthcheck URLs
   - `TRUSTED_INTERNAL_HOSTS` allowlists internal hosts/CIDRs where intended

5. **API Security**:
   - Authentication required (session login or Bearer API token)
   - Rate limiting per endpoint
   - CSRF protection covers the UI forms (login/logout pages); every API blueprint is exempted in `app.py` because API clients authenticate with tokens, not cookies

6. **Network and Resource Isolation**:
   - Internal Docker network; no direct external access to Redis/PostgreSQL
   - Memory limits per container, plus a 2GB `--max-memory-per-child` cap on worker children
   - Per-tool timeouts sized dynamically from file size and duration

### Security flow

```
Request -> Rate Limiter -> Auth -> Input Validation
              |                        |
          Rejected               Path Validation
                                       |
                                 Audit Logging
                                       |
                                 Safe Execution
```

## Scaling and performance

### Horizontal scaling

1. **Add More Workers**:
```yaml
celery-worker-2:
  image: ttlequals0/pixelprobe:latest
  command: python celery_worker.py
  scale: 4  # Creates 4 instances
```

2. **Increase Worker Concurrency**:
```yaml
environment:
  CELERY_CONCURRENCY: 8  # Double the workers
```

### Vertical scaling

1. **Increase Container Resources**:
```yaml
deploy:
  resources:
    limits:
      cpus: '4'
      memory: 8G
```

2. **Optimize Database**:
- Increase `shared_buffers`
- Increase `work_mem`
- Tune `max_connections`

### Concurrency model

- **Directory scans** are distributed as Celery chunk tasks across `CELERY_CONCURRENCY` slots - this is the main scan-throughput knob
- **Selected-file rescans** use a `ThreadPoolExecutor` bounded by `MAX_WORKERS` (default 10) inside a single task; this is the ONLY place the thread pool is used
- Chunk sizing is chosen automatically based on scan size (see [Scan lifecycle](#scan-lifecycle)); it is not configurable

### Caching strategy

- Hash-based change detection: a file whose hash and mtime are unchanged reuses its previous result instead of being re-validated
- Configuration caching in memory
- Result pagination for large datasets

### Database optimization

- Regular `VACUUM` operations
- Index optimization
- Query performance monitoring

## Environment variables

Scanner detection, performance and timeout values are no longer environment
variables. They are stored in the database and edited under System > Tunables or
through `/api/settings`, so a change reaches a running scan without a restart.
See [Configuration](configuration.md#scanner-settings).


### Required

Only one variable is truly required - the app refuses to start without it:

| Variable     | Purpose                          |
|--------------|----------------------------------|
| `SECRET_KEY` | Flask session/CSRF signing key   |

(`POSTGRES_PASSWORD` is effectively required for any real deployment, but the app itself only hard-fails on `SECRET_KEY`.)

### Optional (with defaults)

| Variable                        | Default                    | Purpose                                             |
|---------------------------------|----------------------------|-----------------------------------------------------|
| `POSTGRES_HOST`                 | `localhost`                | Database host                                       |
| `POSTGRES_PORT`                 | `5432`                     | Database port                                       |
| `POSTGRES_DB`                   | `pixelprobe`               | Database name                                       |
| `POSTGRES_USER`                 | `pixelprobe`               | Database user                                       |
| `POSTGRES_PASSWORD`             | (empty)                    | Database password                                   |
| `DB_POOL_SIZE`                  | `5`                        | SQLAlchemy pool size per process                    |
| `DB_MAX_OVERFLOW`               | `10`                       | SQLAlchemy pool overflow per process                |
| `CELERY_BROKER_URL`             | `redis://localhost:6379/0` | Celery broker                                       |
| `CELERY_RESULT_BACKEND`         | `redis://localhost:6379/0` | Celery result backend                               |
| `CELERY_CONCURRENCY`            | `4`                        | Worker pool size (main scan-throughput knob)        |
| `SCAN_PATHS`                    | (empty)                    | Comma-separated directories to scan                 |
| `EXCLUDED_PATHS`                | (empty)                    | Comma-separated excluded paths                      |
| `EXCLUDED_EXTENSIONS`           | `.txt,.log,.md`            | Comma-separated excluded extensions                 |
| `MAX_WORKERS`                   | `10`                       | Thread pool size for selected-file rescans          |
| `BATCH_SIZE`                    | `100`                      | Batch size for bulk operations                      |
| `SCHEDULER_ENABLED`             | `true`                     | Whether this process may compete for the scheduler lock |
| `REDIS_MAX_MEMORY`              | `2gb` (compose)            | Valkey maxmemory for the task queue                 |
| `TRUSTED_INTERNAL_HOSTS`        | (empty)                    | Hosts/CIDRs that bypass SSRF private-IP blocking    |
| `CHUNK_HEARTBEAT_INTERVAL_SECS` | `120`                      | Chunk liveness heartbeat interval                   |
| `CHUNK_REVIVE_STALENESS_SECS`   | `600`                      | Staleness threshold before chunk revival            |

## Deployment example

Condensed from the repository's `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:18-alpine
    environment:
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
    volumes:
      # postgres:18+ images require the mount at /var/lib/postgresql (NOT .../data)
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pixelprobe"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - pixelprobe-network

  # Valkey (Redis-compatible). Service name stays "redis" so redis:// URLs work.
  redis:
    image: valkey/valkey:9-alpine
    command: >
      valkey-server
      --maxmemory ${REDIS_MAX_MEMORY:-2gb}
      --maxmemory-policy noeviction
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - pixelprobe-network

  pixelprobe:
    image: ttlequals0/pixelprobe:latest
    environment:
      SECRET_KEY: ${SECRET_KEY}
      # Scheduler runs in celery-worker; keep the web container out of the lock
      SCHEDULER_ENABLED: "false"
      POSTGRES_HOST: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      SCAN_PATHS: ${SCAN_PATHS:-/media}
      TRUSTED_INTERNAL_HOSTS: ${TRUSTED_INTERNAL_HOSTS:-}
    volumes:
      - ${MEDIA_PATH:-./media}:/media:ro
      # Instance folder for configs (no database files with PostgreSQL)
      - ./instance:/app/instance
    ports:
      - "${PORT:-5000}:5000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 120s  # migrations run before workers serve requests
    networks:
      - pixelprobe-network

  celery-worker:
    image: ttlequals0/pixelprobe:latest
    command: python celery_worker.py
    environment:
      SECRET_KEY: ${SECRET_KEY}
      POSTGRES_HOST: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      CELERY_CONCURRENCY: ${CELERY_CONCURRENCY:-4}
      SCAN_PATHS: ${SCAN_PATHS:-/media}
      TRUSTED_INTERNAL_HOSTS: ${TRUSTED_INTERNAL_HOSTS:-}
    volumes:
      - ${MEDIA_PATH:-./media}:/media:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - pixelprobe-network

networks:
  pixelprobe-network:
    driver: bridge

volumes:
  postgres_data:
```

Notes:
- Postgres and Redis ports are NOT published to the host: the broker has no auth, and app/worker reach both on the compose network
- Valkey runs with `noeviction` so queued tasks are never silently dropped; size it with `REDIS_MAX_MEMORY` (default 2gb)
- The app and celery-worker MUST run as the same user so both can read mounted media files (`user: "${PUID:-1000}:${PGID:-1000}"`)
- The container healthcheck hits the unauthenticated `/healthz` liveness endpoint, with a 120s `start_period` because startup migrations run before workers serve requests

## Technology stack

### Backend
- **Framework**: Flask 3.1.3
- **Database**: SQLAlchemy 2.0.41 on PostgreSQL 18
- **Task Queue**: Celery 5.4.0 on Valkey 9
- **Scheduler**: APScheduler 3.11.0
- **Security**: Flask-Limiter, Flask-WTF, Flask-Login

### Scanner tools
- **FFmpeg 8**: Video/audio analysis
- **ImageMagick 7**: Image validation
- **Pillow**: Python image processing
- **python-magic**: File type detection

### Frontend
- **Framework**: Vanilla JavaScript, hand-rolled CSS, bundled by webpack 5
- **Charts**: Chart.js 4.4.0 (CDN)
- **Icons**: Font Awesome 6.4.0 (CDN)

### Infrastructure
- **Container**: Docker
- **Web Server**: Gunicorn

## Extension points

### Adding new file types

1. Add the extension to the canonical lists in `pixelprobe/constants.py` (`VIDEO_EXTENSIONS`, `IMAGE_EXTENSIONS`, `AUDIO_EXTENSIONS`) - never duplicate them elsewhere
2. Add a detection method if the type needs one
3. Update statistics queries

### Adding new scanners

1. Create scanner method in `PixelProbe` (`pixelprobe/media_checker.py`)
2. Add to scan flow
3. Update result processing

### API extensions

1. Create new route module in `pixelprobe/api/`
2. Add service layer logic
3. Register blueprint (and CSRF-exempt it in `app.py` if it is a token-authenticated API)
4. Update documentation

[< Documentation index](README.md)
