# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0).

## [2.5.67] - 2026-03-05

### Fixed

- **Fix stuck scan bug**: Scans could get permanently stuck as "active" after a Celery task crash (e.g., `psycopg2.DatabaseError`). The crash recovery handler in `scan_service.py` attempted to mark the scan as crashed but failed because the DB session was in a rolled-back state. Added `db.session.rollback()` before recovery writes and re-query the scan state with a fresh session.
- **Fix stuck scan detection for lost Celery tasks**: When Celery task state is `None` (task lost/unreachable), the `is_scan_running()` check previously assumed the scan was still running indefinitely. Now falls through to time-based detection -- if no update for over 1 hour with unknown task state, marks as crashed.
- **Fix scheduler stuck scan checker**: The `_check_stuck_scans` scheduler job now also verifies Celery task state. If a Celery task is gone AND no progress update for 5+ minutes, the scan is marked as crashed (previously only relied on 30-minute time threshold).
- **Fix Phase 3 progress display appearing frozen**: The scan-status API endpoint now reads real-time progress from Redis (instead of only PostgreSQL) when a scan is active. Redis is updated by the Celery scan worker on every file, while PostgreSQL lagged behind, causing the UI to show stale values like "97/397" or appear stuck.
- **Fix final progress not reflected in DB on scan completion**: All scan completion paths now write `files_processed` and `estimated_total` in the same SQL UPDATE that marks the scan as completed. Previously, the completion UPDATE only set `phase` and `is_active`, leaving stale progress values in PostgreSQL.
- **Fix UI worker exiting without final sync**: The `ui_progress_update_task` now performs a final Redis-to-PostgreSQL sync of progress values before exiting when it detects the scan is complete or inactive.
- **Fix ORM staleness in final Redis-to-DB sync**: Added `ui_session.refresh(scan_state)` before comparing Redis vs DB progress values in `_final_sync_redis_to_db()`. After `_mark_scan_completed()` writes via raw SQL, the ORM object could have stale values, causing unnecessary or missed updates.
- **Fix trailing whitespace in scan_routes.py**: Removed trailing whitespace on blank line.
- **Move inline import to module level in tasks.py**: `get_scan_progress_redis` was imported inline at line 775 despite being available from the existing module-level import.
- Files affected: `pixelprobe/services/scan_service.py`, `pixelprobe/api/scan_routes.py`, `pixelprobe/scheduler.py`, `pixelprobe/tasks.py`, `pixelprobe/progress_utils.py`

### Added

- **`get_scan_progress_redis()` in `pixelprobe/progress_utils.py`**: Read function for fetching real-time scan progress from Redis, used by the scan-status API endpoint.
- **`_mark_scan_completed()` in `pixelprobe/services/scan_service.py`**: Extracted helper that consolidates all scan completion SQL into a single method, replacing 7 duplicated SQL blocks.
- **Unit tests for new functions**: Added `tests/unit/test_progress_utils.py` with 12 tests covering `get_scan_progress_redis()`, `_final_sync_redis_to_db()` sync logic, and `_mark_scan_completed()` SQL behavior.

### Changed

- **Move all application modules into `pixelprobe/` package**: Moved `models.py`, `auth.py`, `config.py`, `media_checker.py`, `scheduler.py`, `celery_config.py`, `version.py` from root into the `pixelprobe/` package. Root now only contains entry points (`app.py`, `celery_worker.py`) and build/config files. Updated 73+ import statements across 43 files.
- **Move root `utils.py` into `pixelprobe/utils/helpers.py`**: Consolidated shared utilities (`ProgressTracker`, `create_state_dict`, `batch_process`, etc.) into the package.
- **Documentation cleanup**: Fixed port 5001 references (should be 5000), updated stale version references (v2.4.48/v2.4.93), fixed SQLite references (PostgreSQL-only since v2.2.0), rewrote PROJECT_STRUCTURE.md, fixed container name references.
- **Repository cleanup**: Removed legacy files (`database_migrations.py`, `init_db.py`, `operation_handlers.py`, `utils.py`, `migrations/` directory, broken shell scripts, obsolete patches, outdated development docs).
- Updated docker-compose.yml image tags from `pixelprobe:test-v2.4.93` to `ttlequals0/pixelprobe:2.5.67`.
- Updated `.env.example` with correct PostgreSQL defaults and added missing env vars.

---

## [2.5.66] - 2026-02-23

### Fixed

- **Healthcheck pings blocked by SSRF protection**: Since v2.5.64, healthcheck pings for scheduled scans could fail when the healthcheck server resolves to a private IP address. Added `TRUSTED_INTERNAL_HOSTS` environment variable that lets admins allowlist hostnames and/or CIDR ranges that should bypass SSRF private-IP blocking. Accepts comma-separated values (e.g., `myhost.local,192.168.5.0/24`). SSRF protection remains fully active for non-trusted hosts.
- Files affected: `pixelprobe/utils/security.py`, `config.py`, `version.py`, `docker-compose.yml`, `docs/CONFIGURATION.md`, `tests/test_security_fixes.py`

---

## [2.5.65] - 2026-02-20

### Fixed

- **Share INTERNAL_API_SECRET via Redis across gunicorn workers**: The auto-generated secret was unique per gunicorn worker, causing scheduler internal requests to fail with 401 when routed to a different worker. Now uses Redis `SETNX` to generate the secret once and share it across all workers and the celery container.
- Files affected: `app.py`, `version.py`

---

## [2.5.64] - 2026-02-19

### Security

- **Fix authentication bypass via X-Internal-Request header**: Replace trivially spoofable static string check (`X-Internal-Request: scheduler`) with cryptographic HMAC validation using a shared secret (`X-Internal-Secret`). Secret is auto-generated at startup if not set via `INTERNAL_API_SECRET` environment variable.
- **Fix SSRF in healthcheck and notification services**: Add `validate_safe_url()` function that resolves hostnames and blocks requests to private/reserved IP ranges (RFC 1918, link-local, loopback, cloud metadata 169.254.x.x). Applied to healthcheck pings, webhook notifications, and ntfy notifications.
- **Add redirect-safe HTTP session**: New `create_safe_session()` creates a `requests.Session` with a response hook that validates redirect `Location` headers against the same private IP blocklist, preventing SSRF via DNS rebinding or redirect chains.
- **Fix ntfy config field name mismatch**: `_validate_provider_config()` now accepts both `server_url` (canonical, matching notification_service.py) and `server` (legacy) field names for backward compatibility.
- Files affected: `auth.py`, `config.py`, `app.py`, `scheduler.py`, `pixelprobe/utils/security.py`, `pixelprobe/services/healthcheck_service.py`, `pixelprobe/services/notification_service.py`, `pixelprobe/api/notification_routes.py`, `version.py`

---

## [2.5.63] - 2026-02-18

### Code Simplification

- **Deduplicate `convert_to_tz()` in models.py**: Extract 8 identical inline copies into a single module-level function
- **Consolidate `rate_limit()` / `exempt_from_rate_limit()`**: Update canonical `pixelprobe/utils/rate_limiting.py` to match inline implementation, replace 5 inline copies in route files with imports
- **Deduplicate Bearer token extraction in auth.py**: Extract `_extract_bearer_token()` helper, replace 4 inline token-parsing blocks, remove dead `token = token` no-ops
- **Unify file extension lists**: Remove divergent extension sets from `pixelprobe/utils/helpers.py`, import canonical lists from `pixelprobe/constants.py`
- **Rename ambiguous `validate_file_path`**: Rename `validators.py` version to `validate_file_path_format()` to distinguish from security.py's path traversal prevention
- **Deduplicate `check_celery_available()`**: Move to new `pixelprobe/utils/celery_utils.py`, replace copies in `scan_routes.py` and `scan_routes_parallel.py`
- **Deduplicate `ContextTask` in celery_config.py**: Extract `_make_context_task()` factory, replace 3 identical inner class definitions
- **Replace deprecated `datetime.utcnow()`**: Use `datetime.now(timezone.utc)` in `models.py` (4 locations) and `security.py` (2 locations)
- **Extract `get_configured_scan_paths()`**: Create shared helper in `pixelprobe/utils/helpers.py`, replace 6+ inline DB-fallback-to-env patterns
- **Use `TERMINAL_SCAN_PHASES` constant**: Replace 5 hardcoded `['idle', 'completed', 'error', 'crashed', 'cancelled']` lists with import from `pixelprobe/constants.py`
- **Consolidate scheduler scan methods**: Extract `_filter_excluded_paths()` and `_execute_scan_request()` helpers in `scheduler.py`
- **Remove dead `truncate_scan_output()`**: Remove no-op function from `media_checker.py`, replace 7 call sites with direct variable reference
- **Merge `load_exclusions_with_patterns()`**: Read `exclusions.json` once instead of twice
- **Remove unnecessary `getattr()` calls**: Use direct attribute access in `ScanReport.to_dict()`
- **Break up `app.py`** (1137 -> 541 lines):
  - Extract migration functions to `pixelprobe/migrations/startup.py`
  - Extract scheduler lock management to `pixelprobe/scheduler_lock.py`
  - Extract startup cleanup routines to `pixelprobe/startup.py`
  - Simplify v2.2.68 column migration with loop
- Files affected: `models.py`, `auth.py`, `app.py`, `scheduler.py`, `celery_config.py`, `media_checker.py`, `version.py`, `pixelprobe/utils/rate_limiting.py`, `pixelprobe/utils/helpers.py`, `pixelprobe/utils/validators.py`, `pixelprobe/utils/celery_utils.py` (new), `pixelprobe/utils/__init__.py`, `pixelprobe/constants.py`, `pixelprobe/migrations/startup.py` (new), `pixelprobe/scheduler_lock.py` (new), `pixelprobe/startup.py` (new), `pixelprobe/api/admin_routes.py`, `pixelprobe/api/scan_routes.py`, `pixelprobe/api/scan_routes_parallel.py`, `pixelprobe/api/maintenance_routes.py`, `pixelprobe/api/stats_routes.py`, `pixelprobe/services/stats_service.py`

---

## [2.5.63] - 2026-02-18

### Code Simplification

- **Deduplicate `convert_to_tz()` in models.py**: Extracted 8 identical inline copies into a single module-level function
- **Consolidate `rate_limit()` / `exempt_from_rate_limit()`**: Updated canonical `pixelprobe/utils/rate_limiting.py` to match inline implementations and replaced all 4 inline copies in route files
- **Deduplicate Bearer token extraction in auth.py**: Extracted `_extract_bearer_token()` helper, replaced 4 inline copies, removed `token = token` no-ops
- **Unify file extension lists**: Removed divergent inline sets from `helpers.py`, now imports from `pixelprobe/constants.py`
- **Break up `app.py`**: Extracted 600+ lines into focused modules:
  - `pixelprobe/migrations/startup.py` -- all DB migration functions
  - `pixelprobe/scheduler_lock.py` -- Redis distributed lock management
  - `pixelprobe/startup.py` -- startup cleanup routines
  - Reduced `app.py` from 1137 to 541 lines
- **Deduplicate `check_celery_available()`**: Moved to `pixelprobe/utils/celery_utils.py`, replaced copies in `scan_routes.py` and `scan_routes_parallel.py`
- **Deduplicate `ContextTask` in `celery_config.py`**: Extracted `_make_context_task()` factory, replaced 3 identical class definitions
- **Replace deprecated `datetime.utcnow()`**: All occurrences in `models.py` and `security.py` now use `datetime.now(timezone.utc)`
- **Extract `get_configured_scan_paths()`**: Created shared helper in `pixelprobe/utils/helpers.py`, replaced 6+ inline "read from DB, fallback to env var" patterns
- **Use `TERMINAL_SCAN_PHASES` constant**: Replaced 5 hardcoded `['idle', 'completed', 'error', 'crashed', 'cancelled']` lists with the constant from `pixelprobe/constants.py`
- **Consolidate scheduler scan methods**: Extracted `_filter_excluded_paths()` and `_execute_scan_request()` helpers, reducing duplication between `_run_periodic_scan` and `_run_scheduled_scan`
- **Rename ambiguous `validate_file_path`**: Renamed validators.py version to `validate_file_path_format()` to avoid confusion with the security.py version
- **Remove dead `truncate_scan_output()`**: Was a no-op (returned input unchanged), replaced 7 call sites with direct variable usage
- **Merge `load_exclusions_with_patterns()`**: Now reads `exclusions.json` once instead of twice
- **Remove unnecessary `getattr()` calls**: `ScanReport.to_dict()` now accesses `num_workers`, `files_added`, `files_updated` directly
- **Update stale `deep_scan` comment**: Replaced outdated version-specific comment with generic backward-compat note
- Files affected: `models.py`, `auth.py`, `app.py`, `scheduler.py`, `celery_config.py`, `media_checker.py`, `pixelprobe/utils/rate_limiting.py`, `pixelprobe/utils/helpers.py`, `pixelprobe/utils/validators.py`, `pixelprobe/utils/celery_utils.py` (new), `pixelprobe/utils/security.py`, `pixelprobe/utils/__init__.py`, `pixelprobe/api/admin_routes.py`, `pixelprobe/api/scan_routes.py`, `pixelprobe/api/scan_routes_parallel.py`, `pixelprobe/api/maintenance_routes.py`, `pixelprobe/api/stats_routes.py`, `pixelprobe/services/stats_service.py`, `pixelprobe/migrations/startup.py` (new), `pixelprobe/scheduler_lock.py` (new), `pixelprobe/startup.py` (new)

---

## [2.5.62] - 2026-02-14

### Bug Fixes

- **Fix PostgreSQL CREATE INDEX race condition on container startup**: Replace `fcntl.flock` file lock with PostgreSQL advisory lock (`pg_advisory_lock`) for migration coordination
  - File locks only work within a single container's `/tmp` filesystem -- the app and celery-worker containers each have their own `/tmp`, so they raced against each other
  - Advisory locks work across ALL connections to the same PostgreSQL database, preventing the `duplicate key value violates unique constraint "pg_class_relname_nsp_index"` errors seen on every container restart (~8 errors per restart event)
  - Winner process acquires the lock and runs migrations; other processes block until complete then skip
  - Falls back to uncoordinated execution if advisory lock fails (each DDL statement already has its own idempotency handling via `IF NOT EXISTS` / `try/except`)
  - Extract `_run_all_migrations()` helper for cleaner code structure
  - Files affected: `app.py`

---

## [2.5.61] - 2026-02-02

### Bug Fixes

- **Fix file changes scan SQLAlchemy detached instance error**: Long-running integrity scans (20+ hours for 1M+ files) would crash at ~99.95% with "Instance has been deleted" errors
  - Root cause: ORM objects held in memory for the entire scan duration get expired by `db.session.commit()` calls
  - If concurrent jobs (e.g., "Check for stuck scans" every 5 minutes) delete database rows, subsequent attribute access on expired objects crashes
  - Solution: Load only needed columns and convert to plain Python dictionaries immediately after query
  - Dictionaries are immune to SQLAlchemy session expiration and concurrent database changes
  - Files affected: `pixelprobe/services/maintenance_service.py`

---

## [2.5.60] - 2026-02-01

### Cleanup

- **Repository slop cleanup**: Remove ~500+ lines of dead code and outdated documentation
  - Remove unused tool scripts: `fix_database_schema_v2.py`, `fix_hevc_warnings.py`, `fix_nal_unit_false_positives.py` (377 lines)
  - Remove unused utility functions: `handle_db_errors()` decorator and `update_state_progress()` helper from `utils.py`
  - Remove deprecated API endpoints: `/reset-stuck-scans` and `/recover-stuck-scan` (use `/scan/recovery` instead)
  - Remove orphaned Docker files: `docker-compose.simple.yml`, `docs/docker/Dockerfile.modern*`, `docs/docker/docker-compose.*.yml`
  - Remove all console.log/error/debug statements from JavaScript files (~78 statements)
  - Update documentation: Fix SQLite references to PostgreSQL (now required), remove dead documentation link
  - Delete `audit_reports/` directory - historical data preserved in CHANGELOG.md
  - Files affected: `utils.py`, `pixelprobe/api/scan_routes.py`, `static/js/app.js`, `static/js/state.js`, `static/js/auth.js`, various docs

---

## [2.5.59] - 2026-02-01

### Bug Fixes

- **Fix UI not showing scheduled scan progress**: Add background scan detection
  - Root cause: UI only checked for running scans on page load
  - If user opens page after a scheduled scan starts, no progress was shown
  - Solution: Added 30-second background interval to detect running scans
  - Automatically starts progress monitoring when a scan is detected
  - Affects all scan types: regular scans, cleanup, and integrity scans
  - Files affected: `static/js/app.js`

---

## [2.5.58] - 2026-01-29

### Bug Fixes

- **Fix duplicate scheduler pings (4x pings for scheduled tasks)**: Scheduler lock was being acquired by all 4 Gunicorn workers
  - Root cause: v2.5.11 fix assumed "same hostname = container restart" and force-acquired locks
  - Problem: With 4 Gunicorn workers in one container, ALL workers share the same hostname
    - Worker 1 acquires lock: `pixelprobe-app:pid1:timestamp`
    - Workers 2-4 see "same hostname" and FORCE ACQUIRE (treating it as stale self-lock)
    - Result: All 4 workers run schedulers, sending 4x pings for scheduled tasks
  - Solution: Check BOTH hostname AND PID when deciding whether to force-acquire
    - Same hostname + same PID = self-lock (refresh/re-acquire - OK)
    - Same hostname + different PID = sibling worker (only acquire if stale >65s)
    - Different hostname = remote container (only acquire if stale >65s)
  - Added helper functions for lock parsing and acquisition logic:
    - `parse_scheduler_lock()`: Parse lock value into (hostname, pid, timestamp)
    - `should_force_acquire_lock()`: Decision logic with clear reason codes
    - `start_scheduler_heartbeat()`: Consolidated heartbeat thread creation (DRY)
  - Added unit tests for all lock scenarios (7 test cases)
  - Files affected: `app.py`, `tests/test_scheduler.py`

---

## [2.5.57] - 2026-01-03

### Bug Fixes

- **Fix NameError crash on startup (regression from v2.5.56)**: App failed to start after scheduler fix
  - Root cause: v2.5.56 removed `is_celery_worker` definition but left a reference at line 1091
  - Error: `NameError: name 'is_celery_worker' is not defined`
  - Solution: Remove `is_celery_worker` from the file-lock fallback condition
  - The file-lock fallback (for local dev without Redis) now works for any process
  - Files affected: `app.py`

### Cleanup

- **Remove obsolete migration scripts**: Migrations now run automatically on startup
  - Deleted `tools/migrate_db.py` - v2.2.46/47 migrations now handled by app_startup_migration.py
  - Deleted `tools/migrate_db_safe.py` - lock handling integrated into automatic migrations
  - Deleted `tools/run_migration.py` - legacy wrapper no longer needed
  - Updated documentation to reflect automatic migration system
  - Files affected: `tools/README.md`, `tools/MIGRATION_GUIDE.md`, `docs/maintenance/TOOLS_AND_SCRIPTS.md`

---

## [2.5.56] - 2026-01-03

### Bug Fixes

- **Fix scheduler not executing jobs**: Revert to v2.5.19 scheduler behavior
  - Root cause: v2.5.33 added `is_celery_worker` check that forced scheduler to only run in celery-worker
  - Problem: Celery's prefork model is incompatible with APScheduler's BackgroundScheduler threads
    - Celery main process loads app and starts scheduler thread
    - Celery then forks worker processes
    - Threads don't survive fork - scheduler thread only exists in parent process
    - Parent process doesn't handle requests, Flask context is broken
    - Jobs are scheduled but never execute
  - Solution: Remove `is_celery_worker` check, allow any process to acquire scheduler lock
    - Gunicorn workers work correctly with APScheduler (each imports app independently)
    - Redis SETNX ensures only one scheduler runs at a time
  - Files affected: `app.py`

- **Fix integrity scan reports missing changed files**: Changed files list never populated
  - Root cause: Local `changed_files` variable was populated but never assigned to `self.changed_files_list`
  - The instance variable was initialized empty and never updated
  - Report generation uses `getattr(self, 'changed_files_list', [])` which always returned empty list
  - Solution: Add `self.changed_files_list = changed_files` after Phase 2a processing
  - Files affected: `pixelprobe/services/maintenance_service.py`

---

## [2.5.55] - 2026-01-02

### UI Improvements

- **Rename "File Changes" to "Integrity Scan"**: Consistent terminology throughout the UI
  - The feature was renamed several versions back but some user-facing text still used the old "File Changes" terminology
  - Updated scan reports filter dropdown from "File Changes" to "Integrity Scan"
  - Updated schedule type dropdowns from "Integrity Check" to "Integrity Scan"
  - Updated all notification messages:
    - "File Changes Check Progress" -> "Integrity Scan Progress"
    - "File changes check started..." -> "Integrity scan started..."
    - "File changes check completed!" -> "Integrity scan completed!"
    - "File changes check cancelled" -> "Integrity scan cancelled"
    - "File changes check cancellation requested" -> "Integrity scan cancellation requested"
    - "Checking for file changes..." -> "Checking file integrity..."
    - "No file changes detected" -> "No integrity issues detected"
  - Updated scan type display mapping from "Integrity Check" to "Integrity Scan"
  - Files affected:
    - `templates/index.html` - Dropdown option text (3 locations)
    - `static/js/app.js` - Notification messages and type mapping (8 locations)

---

## [2.5.54] - 2026-01-01

### Bug Fixes

- **Fix persistent Redis connection crashes in integrity scan**: Enhanced Celery task result handling
  - Root cause: v2.5.53 wrappers had insufficient retry logic (3 retries, 0.5s delay)
  - When Redis connection pool gets corrupted, ALL connections are bad
  - Hitting the same broken pool 3 times doesn't help
  - **Solution**: Enhanced safe wrappers with exponential backoff and connection pool reset
    - `safe_task_ready()`: Now 5 retries with exponential backoff (1s, 2s, 3s, 4s)
    - `safe_task_get()`: Now 5 retries with exponential backoff
    - Both now reset the connection pool on first failure to get fresh connections
    - Added `reset_redis_pool()` function to force-disconnect and recreate pool
  - **Additional fixes**:
    - Added `safe_check_task_state()` wrapper for scan_routes.py AsyncResult access
    - Protected all AsyncResult.state access patterns from Redis crashes
    - Added Celery transport options for connection stability:
      - `socket_keepalive: True` to detect dead connections
      - `health_check_interval: 60` for periodic connection validation
      - `socket_timeout: 30` and `socket_connect_timeout: 30`
      - Applied to both broker and result backend transport options
  - **Regression origin**: v2.4.60 introduced `redis.from_url()` which returns low-level Connection
    object instead of full Redis client. v2.5.51 fixed app-level Redis, v2.5.53 added Celery wrappers,
    but wrappers needed enhanced retry logic with pool reset.
  - Files affected:
    - `pixelprobe/progress_utils.py` - Added reset_redis_pool()
    - `pixelprobe/services/maintenance_service.py` - Enhanced safe_task_ready/safe_task_get
    - `pixelprobe/api/scan_routes.py` - Added safe_check_task_state wrapper
    - `celery_config.py` - Added transport options for resilience

---

## [2.5.53] - 2026-01-01

### Bug Fixes

- **Fix Celery task result Redis connection errors**: Integrity scan crashed when checking task results
  - Root cause: Celery's `task.ready()` and `task.get()` use their own internal Redis connection, separate from our fixed Redis client
  - When Celery's Redis connection gets reset, task result checking fails with `ConnectionResetError`
  - Error message: `'Connection' object has no attribute 'register_connect_callback'`
  - **Solution**: Added wrapper functions with retry logic for Celery task result operations
    - `safe_task_ready()`: Safely check task status with 3 retries and 0.5s delay
    - `safe_task_get()`: Safely get task result with 3 retries and 0.5s delay
    - Both functions catch `redis.ConnectionError`, `redis.TimeoutError`, `ConnectionResetError`, and `AttributeError`
    - On transient failure, retries before giving up
  - Files affected: `pixelprobe/services/maintenance_service.py`

---

## [2.5.52] - 2026-01-01

### Bug Fixes

- **Fix HEIC false positive corruption detection for iOS 18 files**: HEIC files from iOS 18 devices were incorrectly flagged as corrupted
  - Root cause: Older libheif versions (Ubuntu 24.04 ships 1.17.x) don't support iOS 18's use of shared auxiliary images
  - Error message: `"Too many auxiliary image references (2.0)"` from libheif/ImageMagick
  - This is a valid HEIF structure per spec, not file corruption (see: github.com/strukturag/libheif/issues/1190)
  - **Solution**: Detect libheif limitation errors and treat as warnings instead of corruption
    - Added detection for "auxiliary image" and "too many auxiliary" errors in ImageMagick stderr
    - Added detection for "cannot identify image file" errors on HEIC files in PIL
    - Files with this error now show as warning "HEIC validation skipped: libheif version limitation"
    - Files are no longer incorrectly marked as corrupted
  - Note: Full iOS 18 HEIC support requires libheif 1.19.8+ (not yet available in Ubuntu repos)
  - Files affected: `media_checker.py`

---

## [2.5.51] - 2026-01-01

### Bug Fixes

- **Fix Redis connection crash in maintenance service**: Application crashed with `ConnectionResetError`
  - Root cause: `redis.from_url()` returns low-level Connection object, not full Redis client
  - Error occurred when Redis connection was reset during file changes check
  - Error message: `'Connection' object has no attribute 'register_connect_callback'`
  - **Solution**: Created robust Redis connection handling with retry logic
    - Added connection pooling with health checks (`health_check_interval=30`)
    - Added automatic retry on connection failures (3 attempts with 1s delay)
    - Added `socket_keepalive=True` to detect stale connections
    - Added `retry_on_timeout=True` for transient failures
  - New utility functions in `progress_utils.py`:
    - `get_redis_client()`: Returns properly configured Redis client with connection pool
    - `get_redis_info()`: Safely gets Redis server info with retry logic
    - `with_redis_retry()`: Decorator for adding retry logic to Redis operations
  - Updated `maintenance_service.py` to use `get_redis_info()` instead of `redis.from_url()`
  - Updated `app.py` version endpoint to use `get_redis_info()` instead of `redis.from_url()`
  - Files affected: `pixelprobe/progress_utils.py`, `pixelprobe/services/maintenance_service.py`, `app.py`

---

## [2.5.50] - 2025-12-29

### Bug Fixes

- **Fix scheduler not starting after container restart**: Stale Redis lock prevented scheduler from starting
  - When container is killed with SIGKILL, Redis scheduler lock is not released
  - If container restarts within 60s TTL, it sees its own stale lock and skips starting scheduler
  - Added hostname to lock value format (`hostname:pid:timestamp`) to detect self-locks
  - Container restart now force-acquires its own stale lock immediately
  - Maintains backward compatibility with old lock format (`pid:timestamp`)
  - Files affected: `app.py`

---

## [2.5.49] - 2025-12-28

### Bug Fixes

- **Fix CSP blocking Chart.js source maps**: Added cdn.jsdelivr.net to connect-src directive
  - Browser was blocking requests to fetch Chart.js source maps from CDN
  - CSP connect-src only allowed 'self', now includes https://cdn.jsdelivr.net
  - Files affected: `app.py`

---

## [2.5.48] - 2025-12-28

### Bug Fixes

- **Fix Python 3.9 compatibility**: Type annotation `str | None` not supported in Python 3.9
  - GitHub Actions runs tests on Python 3.9, which doesn't support PEP 604 union types
  - Changed `-> str | None` to `-> Optional[str]` in notification_routes.py
  - Added `from typing import Optional` import
  - Files affected: `pixelprobe/api/notification_routes.py`

---

## [2.5.47] - 2025-12-28

### Bug Fixes

- **Widen scan_id columns to VARCHAR(64)**: Old queued tasks with 47-char scan_ids were stuck in retry
  - v2.5.46 fixed new scans but old tasks in Celery queue still had long scan_ids
  - Widened scan_id columns in scan_state, scan_chunks, cleanup_state from 36 to 64 chars
  - Added automatic database migration on startup to widen existing columns
  - This allows old stuck tasks to complete and unblock the scheduler
  - Files affected: `models.py`, `tools/app_startup_migration.py`

---

## [2.5.46] - 2025-12-28

### Bug Fixes

- **Fix scheduled scan failing with DataError**: scan_id exceeded VARCHAR(36) column limit
  - Scheduler adds timestamp: `scheduled_{id}_{YYYYMMDD_HHMMSS}` (28 chars)
  - scan_routes.py was adding ANOTHER timestamp, making it 47 chars
  - Database column `scan_state.scan_id` is VARCHAR(36)
  - **Solution**: Removed duplicate timestamp from scan_routes.py
  - Scheduler's timestamp is already unique per scheduled run
  - Files affected: `pixelprobe/api/scan_routes.py`

---

## [2.5.45] - 2025-12-28

### Bug Fixes

- **Fix v2.5.44 syntax error**: Fixed `nonlocal` statement that caused app crash
  - `nonlocal` only works for enclosing function scopes, not block-level scopes
  - Changed `scheduler_initialized` to use mutable list `[False]` pattern
  - Files affected: `app.py`

---

## [2.5.44] - 2025-12-28

### Bug Fixes

- **Fix scheduler not starting after container restart**: Scheduler lock has no retry mechanism
  - When container restarts quickly (~23 seconds), the old Redis lock hasn't expired yet
  - Lock check only happened ONCE at startup - no retry if acquisition failed
  - Old lock would expire at 60s, but new celery-worker never rechecked
  - Result: Scheduler never started, scheduled scans never ran
  - **Solution**: Added background retry thread that checks for lock every 30 seconds
  - Retries up to 10 times (~5 minutes) to acquire expired or stale locks
  - Once acquired, starts normal heartbeat thread to maintain the lock
  - Files affected: `app.py`

---

## [2.5.43] - 2025-12-28

### Bug Fixes

- **Fix startup race condition in sync_scan_paths_to_db()**: v2.5.41 crashed on startup
  - Multiple gunicorn workers starting simultaneously caused duplicate key violations
  - Changed from check-then-insert to PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`
  - This is an atomic operation that safely handles concurrent inserts
  - Files affected: `app.py`

- **Deploy scheduler API hostname fix**: Scheduled scans now work from celery-worker
  - v2.5.42 fix for using `pixelprobe:5000` instead of `localhost:5000` is now deployed
  - Celery-worker can now reach the main app to trigger scans

---

## [2.5.42] - 2025-12-28

### Bug Fixes

- **Fix scheduler API calls from celery-worker**: Scheduler couldn't reach main app API
  - In Docker, celery-worker's `localhost:5000` doesn't reach the main pixelprobe container
  - Added `_get_api_base_url()` method that detects Docker environment
  - Uses `http://pixelprobe:5000` (Docker service name) instead of `localhost`
  - Supports `API_BASE_URL` env var override for custom configurations
  - Files affected: `scheduler.py`

---

## [2.5.41] - 2025-12-28

### Bug Fixes

- **Sync SCAN_PATHS to database**: Fixed scheduler not being able to read scan paths in celery-worker
  - Main app now syncs SCAN_PATHS from environment to `scan_configurations` database table on startup
  - Scheduler reads paths from database instead of environment variable
  - This allows celery-worker to access paths via shared database without needing env var
  - Fallback to env var if database is empty (backwards compatibility)
  - Files affected: `app.py`, `scheduler.py`

---

## [2.5.40] - 2025-12-28

### Bug Fixes

- **Missing SCAN_PATHS in celery-worker**: Fixed docker-compose.yml missing scan-related environment variables for celery-worker container
  - When scheduler was moved to celery-worker in v2.5.28, the SCAN_PATHS env var was never added
  - Added SCAN_PATHS, EXCLUDED_PATHS, EXCLUDED_EXTENSIONS, PERIODIC_SCAN_SCHEDULE, CLEANUP_SCHEDULE, and TZ to celery-worker
  - **IMPORTANT**: Users must also add SCAN_PATHS to their production celery-worker container (Portainer/Docker Compose)
  - Files affected: `docker-compose.yml`

---

## [2.5.39] - 2025-12-28

### Improvements

- **Stale Scheduler Lock Detection**: Added automatic takeover of stale scheduler locks
  - When celery-worker starts and lock acquisition fails, checks if lock timestamp is >65s old
  - If lock is stale (heartbeat stopped), forces acquisition and initializes scheduler
  - Prevents scheduler from being stuck when old worker dies without releasing lock
  - Logs lock age for debugging: "lock held by: X, age=Ys"
  - Files affected: `app.py`

---

## [2.5.38] - 2025-12-28

### Bug Fixes

- **Restore v2.5.19 Scheduled Scan Logic**: Fixed all regressions introduced in v2.5.32
  - Added empty SCAN_PATHS validation with proper error logging
  - Added empty filtered_paths validation after exclusion filtering
  - Restored timestamp in scan_id (`scheduled_{id}_{timestamp}`) to prevent false "already completed" detection
  - Updated schedule_id parsing for healthcheck to handle timestamp format
  - Root cause: v2.5.32 accidentally reverted fixes from v2.5.19 while trying to fix SQLAlchemy session issues
  - Files affected: `scheduler.py`

---

## [2.5.37] - 2025-12-28

### Improvements

- **SCAN_PATHS Fallback Debug Logging**: Added logging to diagnose why scheduled scans with empty paths aren't falling back to SCAN_PATHS env var
  - Logs the raw `scan_paths` value from database
  - Logs the SCAN_PATHS env var value when fallback is triggered
  - Files affected: `scheduler.py`

---

## [2.5.36] - 2025-12-28

### Improvements

- **Schedule Debug Logging**: Added detailed logging to show all schedules in database during scheduler startup
  - Logs total count of schedules in database
  - Lists each schedule with name, id, is_active status, and cron expression
  - Helps diagnose why specific schedules may not be loading
  - Files affected: `scheduler.py`

---

## [2.5.35] - 2025-12-28

### Bug Fixes

#### Fixed

- **Schedule Updates Not Reaching Celery Worker**: Fixed schedule changes in UI not being applied
  - Root cause: v2.5.33/34 moved scheduler to celery worker, but `scheduler.update_schedules()` was called from gunicorn (Flask) where scheduler doesn't run
  - The scheduler silently skipped updates since `scheduler.running = False` in gunicorn
  - Fix: Created `reload_schedules_task` Celery task to trigger schedule reload in the worker
  - Now when schedules are created/updated/deleted via UI, a Celery task notifies the worker to reload
  - Files affected: `pixelprobe/tasks.py`, `pixelprobe/api/admin_routes.py`

---

## [2.5.34] - 2025-12-28

### Bug Fixes

#### Fixed

- **HOTFIX: v2.5.33 Missing Import**: Fixed app crash on startup due to missing `import sys`
  - v2.5.33 added code using `sys.argv` but forgot to import the `sys` module
  - Files affected: `app.py`

---

## [2.5.33] - 2025-12-28

### Bug Fixes

#### Fixed

- **Scheduler Not Running After Container Restart**: Fixed scheduled scans not executing after container restart
  - Root cause: v2.5.28 removed unconditional Redis lock deletion, which unintentionally allowed gunicorn workers to acquire the scheduler lock before the Celery worker
  - Gunicorn's pre-fork model is incompatible with APScheduler's background threads
  - Fix: Only allow Celery worker to attempt scheduler lock acquisition
  - Added check for `celery` in `sys.argv[0]` to detect Celery worker context
  - Non-Celery processes (gunicorn workers) now skip scheduler initialization entirely
  - This restores all scheduled tasks: periodic scans, cleanup scans, stuck scan checker, and user-defined schedules
  - Files affected: `app.py`

---

## [2.5.32] - 2025-12-27

### Bug Fixes

#### Fixed

- **Scheduled Scans Running with Empty Paths**: Fixed scheduled scans not scanning any files after container restart
  - Root cause: SQLAlchemy session expiry after `db.session.commit()` in scheduler
  - After commits (healthcheck ping update, last_run update), the schedule object expires
  - When `schedule.scan_paths` was accessed after commit, lazy-loading failed in celery worker context
  - Fix: Cache `scan_paths`, `scan_type`, and `schedule.name` immediately after loading, before any commits
  - This issue manifested when the scheduler ran in the celery worker instead of the Flask app
  - Files affected: `scheduler.py`

---

## [2.5.31] - 2025-12-26

### Bug Fixes

#### Fixed

- **Pending Files Fix Incomplete in v2.5.30**: The retry mechanism was only added to 2 of 6 scan completion paths
  - Root cause: v2.5.30 added `_retry_pending_files()` to chunk-based scan paths but missed direct file scan paths
  - Fixed scan paths now include:
    - `_sequential_scan` - used for small file lists in sequential mode
    - `_parallel_scan` - used for small file lists in parallel mode
    - `_sequential_scan_selected_chunks` - used for selected file scans in sequential mode
    - `_parallel_scan_selected_chunks` - used for selected file scans in parallel mode
  - Added enhanced logging with scan method identifiers (e.g., "PARALLEL DIRECT", "SEQUENTIAL SELECTED CHUNKS")
  - Warning logged if any files remain pending after retries
  - Files affected: `pixelprobe/services/scan_service.py`

---

## [2.5.30] - 2025-12-24

### Bug Fixes

#### Fixed

- **Pending Files Not Processed During Scan**: Fixed scans completing with files left in 'pending' status
  - Root cause: Scan would iterate through files and mark as "complete" based on iteration count, not actual database saves
  - Added retry mechanism at scan completion to re-scan any files still in 'pending' status (up to 2 retries)
  - Ensures all files are processed before marking scan as complete
  - Added verification warning in logs if any files remain pending after retries
  - Affects both sequential and parallel scan completion paths
  - Files affected: `pixelprobe/services/scan_service.py`

- **Silent Database Save Failures**: Added tracking for failed database saves during scanning
  - Added `failed_saves` and `successful_saves` counters to PixelProbe class
  - Logs cumulative failed save count for debugging
  - Added `get_save_stats()` and `reset_save_stats()` methods for monitoring
  - Files affected: `media_checker.py`

#### Added

- Pending file count now included in scan report statistics query
- Warning logged when files remain in 'pending' status after scan completion

---

## [2.5.29] - 2025-12-21

### Bug Fixes

#### Fixed

- **Scan Chunk Pagination Race Condition**: Fixed files being skipped during scan due to pagination bug
  - Chunks were using OFFSET/LIMIT on pending files query, but as files were scanned their status changed
  - This caused the result set to shrink, making subsequent chunk offsets skip files
  - New approach: Capture file path boundaries at chunk creation time (JSON format: FCP)
  - Query by stable path range (`file_path >= first AND file_path <= last`) instead of offset/limit
  - Maintains backwards compatibility with legacy FILE_CHUNK format
  - Files affected: `pixelprobe/services/scan_service.py`

#### Updated Screenshots

- Updated `docs/screenshots/features/scan-schedules.png` with working Font Awesome icons

---

## [2.5.28] - 2025-12-21

### Bug Fixes

#### Fixed

- **Font Awesome Icons Missing**: Fixed CSP blocking Font Awesome CSS from cdnjs.cloudflare.com
  - Changed Font Awesome CDN from `cdnjs.cloudflare.com` to `cdn.jsdelivr.net` (already in CSP whitelist)
  - Affects both `templates/index.html` and `templates/login.html`
  - Icons now display correctly on all pages (mobile and desktop)

- **Scheduler Race Condition**: Fixed duplicate scheduler initialization during container startup
  - Removed unconditional Redis lock deletion in `app.py` that caused race condition
  - When both pixelprobe and celery-worker containers start simultaneously, they were deleting each other's locks
  - The 60-second TTL already handles stale locks from crashed containers
  - This fixes premature scan completion and pending files accumulation issues

---

## [2.5.27] - 2025-12-20

### Security Audit Gap Implementation

This release addresses gaps identified in the November 2025 security audit verification.

#### Security

- **BREAKING**: Removed API token query parameter support (P0 security fix)
  - Tokens in URLs are logged in server access logs and exposed in browser history
  - API tokens MUST now be sent via `Authorization: Bearer <token>` header only
  - Affects `auth_required()`, `check_auth()`, and `get_authenticated_user()` functions
- Added comprehensive security headers middleware (P1):
  - `X-Frame-Options: SAMEORIGIN` - Prevents clickjacking
  - `X-Content-Type-Options: nosniff` - Prevents MIME sniffing
  - `X-XSS-Protection: 1; mode=block` - Legacy XSS protection
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Content-Security-Policy` - CSP with 'unsafe-inline' for inline handlers
  - `Strict-Transport-Security` - HSTS for HTTPS connections
- Added session inactivity timeout (30 minutes) (P1)
  - Automatically logs out users after 30 minutes of inactivity
  - Returns 401 for API requests, redirects for UI requests

#### Performance

- Added composite database indexes for common query patterns (P1):
  - `idx_status_corrupted` on `(scan_status, is_corrupted)`
  - `idx_scan_date_corrupted` on `(scan_date DESC, is_corrupted)`
  - `idx_exists_status` on `(file_exists, scan_status)`
  - Indexes are created via startup migration with `IF NOT EXISTS`

#### Added

- Notification system API routes (P3 feature completion):
  - `GET/POST /api/notifications/providers` - List/create notification providers
  - `GET/PUT/DELETE /api/notifications/providers/<id>` - CRUD for providers
  - `POST /api/notifications/providers/<id>/test` - Test notification delivery
  - `GET/POST /api/notifications/rules` - List/create notification rules
  - `GET/PUT/DELETE /api/notifications/rules/<id>` - CRUD for rules
  - Supports Pushover, ntfy.sh, and webhook providers
- ARIA accessibility attributes (P3 compliance):
  - Navigation landmarks: `role="navigation"`, `role="main"`, `role="banner"`
  - Decorative icons: `aria-hidden="true"` on Font Awesome icons
  - Interactive elements: `aria-label` on buttons with icons only
  - Progress tracking: `role="progressbar"` with aria-value attributes
  - Live regions: `aria-live="polite"` for dynamic content updates

#### Context7 Validation

- Verified existing PIL/Pillow image validation follows best practices
- Verified existing FFmpeg video validation follows best practices
- Applied SQLAlchemy composite index patterns from Context7 docs
- Applied Flask-Session inactivity timeout patterns from Context7 docs

#### Testing

- Fixed flaky schedule reactivation test that failed due to APScheduler timing edge cases
- Updated pytest configuration to properly scope test discovery to `tests/` and `scripts/` directories
- UI screenshot updates with correct viewport sizing and element-specific captures

---

## [2.5.26] - 2025-12-16

### Fix: Cleanup/File Changes Duplicate Reports & Missing Healthcheck Ping

#### Problem 1: Duplicate Reports

Scheduled cleanup and file_changes scans were creating multiple reports:
- Gunicorn runs 4 workers, each with its own process-local `current_cleanup_thread` variable
- When scheduler triggered 4 simultaneous requests, 3 workers passed the "already running" check
- Result: 3 cleanup operations ran concurrently, creating 3 reports

#### Problem 2: Missing Finish Healthcheck Ping

Cleanup and file_changes scans sent start ping but no completion ping:
- Reports were created without `scan_id` field (needed `scheduled_X` format)
- `send_healthcheck_completion()` was never called after report creation
- Healthcheck monitoring showed scan started but never finished

#### Solution

**Duplicate Prevention:**
- Added database-level check using `CleanupState.is_active` and `FileChangesState.is_active`
- Database check provides cross-worker visibility (vs process-local thread check)

**Healthcheck Integration:**
- Scheduler now passes `schedule_id` in API request body
- API routes accept and pass `schedule_id` to async functions
- Report creation now sets `scan_id=scheduled_{id}` for schedule association
- Calls `MediaScheduler.send_healthcheck_completion()` after report creation

#### Files Modified

- `pixelprobe/api/maintenance_routes.py`: Database-level duplicate checks, schedule_id handling
- `pixelprobe/services/maintenance_service.py`: Schedule-aware report creation with healthcheck ping
- `scheduler.py`: Pass schedule_id to cleanup/file_changes API requests

---

## [2.5.25] - 2025-12-04

### Fix: Scheduled Scans Not Executing (Falsely Detected as Already Completed)

#### Problem

Scheduled scans were being skipped with message "Scan already completed (from previous attempt)":
- All scheduled scans used static scan_id like `scheduled_1` or `scheduled_periodic`
- Celery task checked if scan_id was already completed
- Found PREVIOUS day's completed scan and skipped the new scan
- Healthcheck received "success" ping despite no actual scanning

#### Root Cause

- `scheduler.py` sent `source: scheduled_1` to `/api/scan`
- `scan_routes.py` used this directly as `scan_id`
- `tasks.py` queried ScanState for `scan_id=scheduled_1` and found completed state
- Task returned early thinking it was a retry of a completed scan

#### Solution

- Added timestamp to scheduled scan_id: `scheduled_1_20241204_180000`
- Each scheduled scan now has a unique identifier
- Celery retry detection only matches same-session scans
- Applies to both `scheduled_{id}` and `scheduled_periodic` sources

#### Impact

- Scheduled scans now execute correctly
- New files are discovered on schedule
- Healthcheck pings reflect actual scan execution

---

## [2.5.24] - 2025-12-03

### Fix: Stage 3 FFmpeg Version Compatibility

#### Problem

v2.5.23's exit-code-only approach failed due to FFmpeg version differences:
- **FFmpeg 8.0 (macOS)**: Returns exit code 0 for PPS seek errors
- **FFmpeg 6.1.1 (Docker/Ubuntu 24.04)**: Returns non-zero exit code for same errors

#### Solution

Stage 3 (multi-point sampling) now **never marks files as corrupted**:
- All Stage 3 output is logged as debug info only
- Stage 1 (full decode from beginning, no seeking) remains the authoritative check

#### Impact

- 3 remaining false positives now correctly detected as healthy
- Works consistently across all FFmpeg versions
- No pattern matching or exit code dependencies

---

## [2.5.23] - 2025-12-03

### Fix: Remove Aggressive Error Detection from Stage 3 Multi-Point Sampling

#### Changed

- **Removed `-err_detect crccheck+bitstream` from Stage 3 multi-point sampling**
  - Aggressive error detection is incompatible with seeking mid-stream
  - HEVC decoder reports errors (PPS missing, first slice missing) until finding keyframe
  - These are normal seek artifacts, not corruption

- **Simplified Stage 3 logic**
  - Now just checks FFmpeg exit code (0 = success)
  - Removed complex benign pattern matching (was a maintenance burden)
  - Stage 4 still uses aggressive detection (starts from beginning, no seek issues)

#### Impact

- All false positives from HEVC seek artifacts eliminated
- Cleaner, more maintainable code
- Actual corruption still detected via non-zero exit code

---

## [2.5.22] - 2025-12-03

### Fix: Multi-Point Sampling False Positives (Stage 3)

#### Fixed

- **HEVC seek artifacts no longer trigger false corruption**
  - `First slice in a frame missing` - Normal HEVC seek behavior
  - `PPS id out of range` - HEVC initialization after seek

- **EOF/seek-related messages no longer trigger false corruption**
  - `Cannot determine format` - Video stream shorter than container
  - `Nothing was written into output` - No frames at seek position
  - `Error marking filters` / `Error while filtering` - Consequence of EOF
  - `Received no packets` - No data at seek position

- **Audio codec warnings no longer trigger false corruption**
  - TrueHD, DTS-HD warnings (audio issues, not video corruption)
  - `quant_step_size` TrueHD specific warning

#### Impact

- 19+ files previously marked as "Corrupted" now correctly show as "Healthy"
- Files affected: Station Eleven (all episodes), Law Abiding Citizen, Avengers Endgame, Mufasa, Lord of the Rings, etc.

#### Files Modified

- `media_checker.py` - Added benign patterns in `_check_multipoint_sampling()`

---

## [2.5.21] - 2025-12-03

### Fix: DTS/PTS Warnings Should NOT Mark Files as Warning Status

#### Fixed

- **Files with only DTS/PTS timestamp warnings now marked as HEALTHY (not Warning)**
  - Removed `warning_details` assignment in `_check_video_corruption()` for benign warnings
  - DTS/PTS warnings are common muxer artifacts in H.264/HEVC files
  - Files with NAL unit warnings or reference frame warnings also stay HEALTHY

#### Technical Details

- Root cause: `_check_video_corruption()` at line 1343 was adding warnings to `warning_details`
- This caused `has_warnings = True` even though warnings are benign
- Fix: Log the warnings for debugging but don't add to warning_details

#### Files Modified

- `media_checker.py` - Removed warning_details assignment for benign DTS/PTS warnings

---

## [2.5.20] - 2025-12-03

### Fix: HEVC PPS False Positive in Corruption Detection

#### Fixed

- **HEVC "PPS changed between slices" no longer triggers false positive corruption**
  - Added to benign patterns list in multi-point sampling
  - Common in Blu-ray remux content where dynamic parameter sets are used
  - Files with only PPS warnings now marked as HEALTHY (not corrupted)

- **Added additional HEVC benign patterns**
  - `'skipping nal unit'` - Filler/padding NAL units (type 63)
  - `'last message repeated'` - FFmpeg continuation line format

#### Changed

- **Improved warning logging format**
  - Now shows counts: "14 PPS parameter changes, 9 NAL unit skips in middle"
  - Files with only benign warnings stay HEALTHY (no warning_details added)

#### Technical Details

- Root cause: Multi-point sampling in `_check_multipoint_sampling()` was flagging legitimate HEVC stream features
- PPS (Picture Parameter Set) changes are part of HEVC spec for adaptive encoding
- NAL unit 63 is reserved/unspecified, often used for Blu-ray markers

#### Files Modified

- `media_checker.py` - Updated benign_patterns list and warning logging

---

## [2.5.18] - 2025-11-25

### Feature: Enhanced Version API Endpoint with Infrastructure Versions

#### Added

- **`/api/version` endpoint now returns infrastructure component versions**
  - Celery version (task queue)
  - Redis version (message broker)
  - PostgreSQL version (database)
  - Example response includes `infrastructure: { celery: "5.3.4", redis: "7.2.3", postgresql: "15.2" }`

- **`/api/version` endpoint added to OpenAPI documentation**
  - New "System" tag category for system information endpoints
  - Full schema documentation for version response

#### Changed

- **Removed `tools/update_version.py`** - Version should only be updated manually in `version.py`
- **`openapi.yaml` version is now dynamic** - Served via `/api/openapi.yaml` with version injected from `version.py` at runtime
- **Static `openapi.yaml` uses placeholder version `0.0.0`** - Indicates it's dynamically set

#### Technical Details

- `version.py` is the single source of truth for application version
- All version references read from `version.py` dynamically
- Infrastructure versions queried at runtime from actual services

#### Files Modified

- `app.py` - Enhanced `/api/version` endpoint with infrastructure versions
- `openapi.yaml` - Added `/api/version` endpoint documentation, added "System" tag
- Deleted: `tools/update_version.py`

## [2.5.17] - 2025-11-25

### Fix: Complete Stale Scheduler Lock Fix (v2.5.16 was incomplete)

#### Fixed

- **v2.5.16 fix was incomplete - existing stale lock was not cleared**
  - v2.5.16 only changed TTL for NEW locks from 24h to 60s with heartbeat
  - But existing stale lock (created with 24h TTL by v2.5.15) still blocked scheduler
  - Fix: Now explicitly deletes any stale lock on startup BEFORE attempting to acquire
  - Safe because on fresh container startup, any existing lock is from a dead process
  - Workers within same container race to acquire after delete, first one wins

#### Technical Details

- Added `redis_client.delete(lock_key)` before `set(..., nx=True)` in `app.py`
- Added log message: "Cleared stale scheduler lock (if any) on startup"

#### Files Modified

- `app.py` - Added delete of stale lock before acquiring new lock

## [2.5.16] - 2025-11-25

### Fix: Stale Scheduler Lock Prevents Scheduled Tasks After Container Restart (INCOMPLETE)

#### Fixed

- **Scheduler not starting after container restart due to stale Redis lock**
  - Root cause: Redis scheduler lock used 24-hour TTL (86400 seconds)
  - When container is replaced during deployment, old container's lock persists
  - New container finds existing lock and skips scheduler initialization
  - Result: No scheduler running - all scheduled scans fail to execute
  - Fix: Changed lock TTL from 24 hours to 60 seconds with heartbeat refresh
  - Added background thread that refreshes the lock every 30 seconds
  - Stale locks from crashed containers now expire within 60 seconds
  - Active schedulers maintain their lock indefinitely through heartbeat

#### Technical Details

- Modified scheduler lock logic in `app.py` lines 721-755
- Lock TTL reduced from `ex=86400` to `ex=60`
- Added daemon thread `refresh_scheduler_lock()` that runs every 30 seconds
- Thread refreshes lock with new timestamp and 60-second TTL
- Worst case recovery time: 60 seconds after container crash

#### Files Modified

- `app.py` - Changed lock TTL and added heartbeat thread

## [2.5.15] - 2025-11-25

### Fix: Healthcheck Completion Ping Not Sent When Scan Already Completed

#### Fixed

- **No completion ping sent when scheduled scan detects existing completed scan**
  - Root cause: When Celery task detects that a scan with the same `scan_id` (e.g., `scheduled_11`) already exists in `completed` phase, it returns early to prevent duplicate work
  - This early return bypassed `_create_scan_report()` which calls `send_healthcheck_completion()`
  - Result: Start ping sent by scheduler, but no completion ping ever sent
  - Fix: When returning early for "already completed" scans, still send completion ping since the scheduler already sent a start ping and healthcheck services expect a completion signal
  - Now finds the existing ScanReport for the scan_id and sends the completion ping before returning early

#### Technical Details

- Modified early-return path in `scan_media_task()` at `pixelprobe/tasks.py:100-126`
- When `scan_id.startswith('scheduled_')` and scan is already completed, queries for existing `ScanReport` and calls `MediaScheduler.send_healthcheck_completion()`
- Added `ScanReport` to imports in tasks.py

#### Files Modified

- `pixelprobe/tasks.py` - Added completion ping to early-return path for already-completed scans

## [2.5.14] - 2025-11-25

### Fix: Schedule Re-enable Does Not Update next_run Time

#### Fixed

- **Re-enabling a disabled schedule shows stale next_run time**
  - Root cause: In multi-worker Gunicorn deployment, scheduler runs in only ONE worker
  - When API request to re-enable schedule arrives, it may be handled by a worker without the scheduler
  - `scheduler.update_schedules()` returns early if `not self.scheduler.running`
  - The `next_run` is never recalculated for re-enabled schedules
  - Fix: Calculate `next_run` directly in the API endpoint using APScheduler's `CronTrigger.get_next_fire_time()`
  - This works without requiring a running APScheduler instance
  - Supports both cron expressions (e.g., "*/5 * * * *") and interval expressions (e.g., "interval:hours:6")
  - Also recalculates `next_run` when cron expression is changed on an active schedule

#### Technical Details

- Added `calculate_next_run()` helper function to `admin_routes.py`
- Uses APScheduler's `CronTrigger` class which can calculate next fire time without a running scheduler
- Detects when schedule transitions from `is_active=False` to `is_active=True`
- Also triggers recalculation when `cron_expression` changes on an active schedule

#### Files Modified

- `pixelprobe/api/admin_routes.py` - Added `calculate_next_run()` helper and updated `update_schedule()` endpoint
- `tests/integration/test_admin_endpoints.py` - Added tests for schedule reactivation scenarios

## [2.5.13] - 2025-11-25

### Fix: Scheduled Scan Duplicate Key + DetachedInstanceError + Celery Retry

#### Fixed

- **Scheduled scans fail with duplicate key constraint violation**
  - Root cause: `scan_id` has unique constraint in ScanState model
  - When a scheduled scan runs again, it tries to INSERT with same scan_id (e.g., "scheduled_1")
  - Previous scan_state record remains in database after completion
  - Fix: `create_new_scan()` now deletes existing scan_state with same scan_id before creating new
  - Scheduled scans can now run repeatedly without constraint violations

- **DetachedInstanceError after scan completion**
  - Root cause: `db.session.close()` in retry loop at line 729 detaches all SQLAlchemy objects
  - `final_scan_state` was fetched before retry loop but accessed after potential session closure
  - Fix: Re-fetch `final_scan_state` after retry loop completes
  - Added graceful fallback if scan_state cannot be re-fetched

- **Celery retry creates duplicate scan state on already-completed scans**
  - Root cause: When Celery retries a task after DetachedInstanceError, scan may have already completed
  - Retry would try to create new scan_state with same scan_id
  - Fix: Check if scan_id already has completed phase before starting
  - Returns early with success if scan already finished

#### Technical Details

- `models.py`: `create_new_scan()` deletes existing scan_state with same scan_id before INSERT
- `scan_service.py`: Re-fetch final_scan_state after db.session.close() to avoid DetachedInstanceError
- `tasks.py`: Early-exit check if scan_id already has phase='completed'

#### Files Modified

- `models.py` - Delete existing scan_state before creating new one for same scan_id
- `pixelprobe/services/scan_service.py` - Re-fetch scan_state after retry loop
- `pixelprobe/tasks.py` - Skip retry if scan already completed

## [2.5.12] - 2025-11-25

### Fix: Orphaned Pending Files + FFmpeg Warning Format + Chunk Retry

#### Fixed

- **Orphaned pending files from failed scan chunks now get processed**
  - Root cause: SQLAlchemy concurrency error "This session is provisioning a new connection" caused scan chunks to fail silently
  - Scan would report success but files in failed chunks stayed in `pending` status forever
  - Fix 1: Remove directory filter from pending files query so ALL pending files are included in every scan
  - Fix 2: Add retry logic for failed chunks - failed chunks are retried sequentially after parallel processing completes
  - This ensures orphaned files from previous interrupted scans eventually get processed

- **FFmpeg warning details now show actual DTS/PTS counts**
  - Before: Generic "Benign FFmpeg warnings in middle section" message
  - After: Specific counts like "3 DTS, 2 PTS warnings in middle"
  - Helps identify actual warning severity without looking at raw logs

- **Failed scan chunks are now retried**
  - Before: If a chunk failed during parallel processing, it was logged and ignored
  - After: Failed chunks are tracked and retried sequentially after the main parallel loop
  - Sequential retry avoids the concurrency issues that caused the original failure

#### Technical Details

- Pending files from failed chunks (e.g., SQLAlchemy concurrency errors) are now recovered
- `scan_service.py` line 573: Removed directory filter from pending files count
- `scan_service.py` lines 1562-1590: Added failed chunk tracking and sequential retry
- `media_checker.py` lines 1915-1927: Parse stderr for DTS/PTS counts instead of generic message

#### Files Modified

- `pixelprobe/services/scan_service.py` - Include all pending files in scan, add chunk retry logic
- `media_checker.py` - Show actual DTS/PTS warning counts

## [2.5.11] - 2025-11-24

### Fix: Duplicate Scheduler Start Pings in Multi-Container Deployments

#### Fixed

- **Duplicate start pings due to scheduler running in multiple containers**
  - Bug: File-based lock (`fcntl.flock`) doesn't work across containers (each has separate `/tmp` filesystem)
  - Both gunicorn and celery-worker containers would acquire their own lock and initialize the scheduler
  - Fix: Replaced file lock with Redis-based distributed lock using `SETNX`
  - Uses `redis.set(lock_key, lock_value, nx=True, ex=86400)` for atomic acquisition
  - 24-hour expiry ensures auto-recovery if container crashes
  - Falls back to file lock if Redis unavailable (for local development)

#### Technical Details

- Lock key: `pixelprobe:scheduler:lock`
- Lock value: `{pid}:{timestamp}` for debugging
- Only the first container to acquire the Redis lock will initialize the scheduler
- Other containers log which process holds the lock

#### Files Modified

- `app.py` - Replace file-based lock with Redis distributed lock

## [2.5.10] - 2025-11-24

### Hotfix: Pass Scheduled Scan ID Through Entire Scan Pipeline

#### Fixed

- **Scheduled scan ID not preserved through scan pipeline**
  - Bug: Even though `/api/scan` now passes `scheduled_XX` as scan_id, `ScanService.scan_directories()` was creating a new UUID
  - The scan_id was not passed through: Celery task -> scan_directories() -> ScanState.create_new_scan()
  - Fix: Added `scan_id` parameter through the entire chain
  - `ScanState.create_new_scan(scan_id=None)` now accepts optional scan_id
  - `scan_directories(scan_id=None)` now passes it to create_new_scan
  - Celery task now passes scan_id to scan_directories

#### Files Modified

- `models.py` - `create_new_scan()` accepts optional scan_id parameter
- `pixelprobe/services/scan_service.py` - `scan_directories()` accepts and passes scan_id
- `pixelprobe/tasks.py` - Pass scan_id to scan_directories()

## [2.5.9] - 2025-11-24

### Hotfix: Scheduled Scan ID Not Passed to Healthcheck

#### Fixed

- **Scheduled scans not identified for healthcheck completion pings**
  - Bug: `/api/scan` endpoint ignored the `source` field from scheduler
  - The scheduler sent `source: 'scheduled_XX'` but endpoint generated a new UUID
  - `send_healthcheck_completion()` checks for `scan_id.startswith('scheduled_')` to identify scheduled scans
  - Since scan_id was always a UUID, completion pings were never sent
  - Fix: Use `source` as `scan_id` when it starts with `scheduled_`

#### Files Modified

- `pixelprobe/api/scan_routes.py` - Use scheduler source as scan_id for scheduled scans

## [2.5.8] - 2025-11-24

### Hotfix: Healthcheck Completion Ping Type Error

#### Fixed

- **Healthcheck completion ping failed with type error**
  - Bug: Passing `report.report_id` (UUID string) instead of `report.id` (integer primary key)
  - Error: `invalid input syntax for type integer: "UUID-string"`
  - Fix: Changed to pass `report.id` to `send_healthcheck_completion()`
  - Completion pings now work correctly for all scan types including empty scans (0 files)

#### Files Modified

- `pixelprobe/services/scan_service.py` - Fixed parameter type (report.id instead of report.report_id)

## [2.5.7] - 2025-11-24

### Critical Fix: Healthcheck Completion Pings + Mobile UI

#### Fixed

- **Healthcheck Completion Pings Now Working**
  - CRITICAL BUG: `send_healthcheck_completion()` was defined but never called
  - Start pings worked, but success/failure pings on scan completion were never sent
  - Added call to `MediaScheduler.send_healthcheck_completion()` in `scan_service._create_scan_report()`
  - Now properly sends success ping when scan completes successfully
  - Now properly sends failure ping when scan fails/errors
  - Includes scan report data in success pings when configured

- **Improved Healthcheck URL Logging**
  - All ping methods now log the full URL being pinged
  - Logs include HTTP status code on success
  - Error messages now include the URL that failed
  - Helps debug connectivity issues with self-hosted instances

- **Self-Hosted Healthchecks.io Support**
  - Removed confusing warning message for non-standard URL formats
  - Supports any URL format (public hc-ping.com or self-hosted instances)
  - Self-hosted URLs can use custom paths, not just `/ping/UUID`

#### Changed

- **Schedule Action Buttons - Now All Icons**
  - Disable button: Changed from text "Disable" to pause icon (`fa-pause`)
  - Enable button: Changed from text "Enable" to play icon (`fa-play`)
  - All four buttons now use icons only: edit, healthcheck, pause/play, trash
  - Added title tooltips for accessibility

- **Uniform Button Sizing on Mobile**
  - All schedule action buttons now have fixed 2.25rem width/height
  - Buttons use flexbox centering for consistent icon alignment
  - Removed variable sizing that caused layout issues

#### Files Modified

- `pixelprobe/services/scan_service.py` - Added healthcheck completion ping call
- `scheduler.py` - Updated method signature for easier calling
- `pixelprobe/services/healthcheck_service.py` - Improved logging, removed restrictive URL warning
- `static/js/app.js` - Changed Enable/Disable to icons
