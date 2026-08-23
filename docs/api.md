# PixelProbe API documentation

## Overview

PixelProbe exposes its media corruption detection through a REST API built with Flask.

## Base URL

- Development: `http://localhost:5000`
- Production: `https://pixelprobe.example.com`

## Authentication

**As of v2.4.1, all API endpoints require authentication**, with these exceptions:

- `GET /healthz` (liveness probe)
- `GET /api/auth/status`
- `POST /api/auth/setup` (first-run only; rejected once a user exists)
- `POST /api/auth/login`
- `GET /api/openapi.yaml` and `GET /api/openapi.json`

PixelProbe supports two authentication methods:

### 1. Session-based authentication (web UI)
- Used automatically when logged in through the web interface
- Managed via secure HTTP-only cookies
- Best for browser-based access
- Sessions expire after 30 minutes of inactivity; an expired session receives
  `401 {"error": "Session expired due to inactivity"}` and must log in again

### 2. API token authentication (programmatic access)
- Generate tokens through the web UI under Account -> API Tokens
- Include in requests using the Authorization header
- Two formats are supported:
  - Standard: `Authorization: Bearer <your-token>`
  - Direct: `Authorization: <your-token>` (for Swagger UI compatibility)

#### Example with curl
```bash
# Using Bearer format
curl -H "Authorization: Bearer your-api-token-here" \
     http://localhost:5000/api/scan-status

# Using direct format (Swagger UI style)
curl -H "Authorization: your-api-token-here" \
     http://localhost:5000/api/scan-status
```

#### Example with Python
```python
import requests

headers = {
    'Authorization': 'Bearer your-api-token-here'
}

response = requests.get('http://localhost:5000/api/scan-status', headers=headers)
```

### Getting an API token
1. Log in to the web interface
2. Navigate to Account -> API Tokens
3. Click "Create New Token"
4. Provide a description
5. Copy the generated token (it won't be shown again)

### Internal header (not for integrations)

An `X-Internal-Secret` request header exists solely for the scheduler's HTTP
self-call inside the container. The secret is generated at startup and never
exposed; do not build integrations against it - use API tokens instead.

## Rate limiting

Only individually decorated endpoints are rate limited; there are
**no default/global limits**. The decorated limits are:

| Endpoints | Limit |
|-----------|-------|
| `POST /api/scan`, `POST /api/scan-parallel`, `POST /api/scan-files-parallel` | 2 requests/min |
| `POST /api/scan-file` | 5 requests/min |
| `POST /api/cancel-scan` | 10 requests/min |
| `POST /api/force-cleanup-scan`, `POST /api/scan/recovery`, `POST /api/reset-for-rescan`, `POST /api/reset-files-by-path` | 5 requests/min |
| `POST /api/force-scan-pending`, `POST /api/reset-incomplete-scans` | 2 requests/min |
| `GET /api/diagnose-incomplete-scans`, `GET /api/diagnose-pending-files` | 5 requests/min |
| `GET /api/error-files` | 10 requests/min |
| `POST /api/mark-as-good`, `POST /api/bitrot/accept` | 10 requests/min |
| `GET /api/logs`, `GET /api/logs/runs` | 30 requests/min |
| `POST /api/logs/purge` | 2 requests/min |

**Exemptions**: requests from localhost and private/Docker networks (127.0.0.1, 10.x, 172.x, 192.168.x) are exempt from rate limiting.

Rate limit headers are included in responses:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining
- `X-RateLimit-Reset`: Time when the limit resets

## Request/response format

- All requests must include `Content-Type: application/json` for POST requests
- All responses are in JSON format
- Dates are in ISO 8601 format
- File sizes are in bytes

## Error handling

Errors are returned with appropriate HTTP status codes and a JSON body:

```json
{
  "error": "Description of the error"
}
```

Common status codes:
- `200`: Success
- `400`: Bad Request (invalid input)
- `401`: Unauthorized (authentication required)
- `403`: Forbidden (insufficient permissions)
- `404`: Not Found
- `409`: Conflict (e.g., scan already running)
- `429`: Too Many Requests (rate limit exceeded)
- `500`: Internal Server Error
- `503`: Service Unavailable - scan launch endpoints return `{"error": "Celery workers not available"}` when no Celery workers are up

## API endpoints

### System endpoints

#### Liveness probe (unauthenticated)
```http
GET /healthz
```

Unauthenticated liveness probe intended for container healthchecks and load balancers.

**Response:**
```json
{
  "status": "ok",
  "version": "<current_version>"
}
```

#### Health check (authenticated)
```http
GET /health
```

Check if the service is running. Requires authentication.

**Response:**
```json
{
  "status": "healthy",
  "version": "<current_version>",
  "timestamp": "2025-01-20T12:00:00Z"
}
```

#### Version
```http
GET /api/version
```

Get version information, including infrastructure component versions.

**Response:**
```json
{
  "version": "<current_version>",
  "github_url": "https://github.com/ttlequals0/PixelProbe",
  "api_version": "1.0",
  "infrastructure": {
    "celery": "5.x",
    "redis": "7.x",
    "postgresql": "15.x"
  }
}
```

### Scan endpoints

#### Get scan results
```http
GET /api/scan-results?page=1&per_page=100&scan_status=all&is_corrupted=all
```

Get paginated scan results with optional filters.

**Query parameters:**
- `page` (integer): Page number (default: 1)
- `per_page` (integer): Results per page (default: 100, use -1 for all)
- `scan_status` (string): Filter by status: `all`, `pending`, `scanning`, `completed`, `error`
- `is_corrupted` (string): Filter by corruption: `all`, `true`, `false`
- `has_warnings` (string): Filter by warning flag: `all`, `true`, `false` (`true` excludes corrupted and marked-as-good files)
- `bitrot_suspected` (string): Filter by suspected bitrot: `all`, `true`, `false`
- `search` (string): Case-insensitive substring match on file path
- `path` (string): Restrict results to one configured scan path (must exactly match a configured path)
- `sort_field` (string): Field to sort by (default: `scan_date`). Valid values: `scan_date`, `file_path`, `file_size`, `file_type`, `scan_status`, `status`, `is_corrupted`, `marked_as_good`, `scan_tool`, `corruption_details`, `discovered_date`, `last_modified` (`status` sorts by corruption status; unknown values fall back to `scan_date` descending)
- `sort_order` (string): `asc` or `desc` (default: `desc`)

`per_page=-1` returns every matching row in a single response (no pagination).

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "file_path": "/media/photos/image.jpg",
      "file_name": "image.jpg",
      "file_size": 2048576,
      "scan_date": "2025-01-20T12:00:00Z",
      "discovered_date": "2025-01-19T10:00:00Z",
      "last_modified": "2025-01-18T08:00:00Z",
      "file_hash": "sha256_hash_here",
      "scan_status": "completed",
      "error_message": null,
      "is_corrupted": false,
      "marked_as_good": false,
      "media_info": "{\"width\": 1920, \"height\": 1080, \"format\": \"JPEG\"}",
      "file_exists": true
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 100,
  "pages": 2
}
```

Notes:
- `media_info` is a JSON-encoded **string**, not a nested object - parse it client-side (e.g. `json.loads(result["media_info"])`).
- Items also carry warning, bitrot, and integrity fields: `has_warnings`, `warning_details`, `bitrot_suspected`, `last_integrity_check_date`, and related hash/baseline columns.

#### Get single scan result
```http
GET /api/scan-results/{result_id}
```

Get detailed information about a specific scan result.

**Response:** Same as individual result in the list above.

#### Scan single file
```http
POST /api/scan-file
```

Scan a single file for corruption. Rate limited to 5 requests per minute.

**Request body:**
```json
{
  "file_path": "/media/photos/image.jpg"
}
```

**Response:**
```json
{
  "status": "queued",
  "scan_id": "uuid-string",
  "task_id": "celery-task-id",
  "file_path": "/media/photos/image.jpg",
  "message": "Single file scan queued successfully using Celery task queue",
  "celery_enabled": true
}
```

**Errors:** `404 {"error": "File not found"}` if the file does not exist; `400 {"error": "Invalid file path"}` if the path is outside the configured scan directories.

#### Start scan
```http
POST /api/scan
```

Start scanning all configured directories (or a supplied list). Distributes work across all available Celery workers in chunks. Rate limited to 2 requests per minute.

**Request body:**
```json
{
  "force_rescan": false,
  "directories": ["/media/photos", "/media/videos"]
}
```

Both fields are optional; if `directories` is omitted, the configured scan paths are used.

**Response:**
```json
{
  "status": "queued",
  "scan_id": "uuid-string",
  "task_id": "celery-task-id",
  "message": "Scan queued successfully using Celery task queue",
  "celery_enabled": true
}
```

#### Parallel scan (deprecated)
```http
POST /api/scan-parallel
```

Deprecated alias of `/api/scan`; both run the same chunk-distributed engine. Kept for API compatibility and will be removed in a future major release. Rate limited to 2 requests per minute.

**Request body:**
```json
{
  "directories": ["/media/photos"],
  "force_rescan": false
}
```

`directories` is required; `force_rescan` is optional. The response matches `/api/scan` except that `status` is `"launched"` and the legacy fields `scan_type: "parallel_v2"` and `force_rescan` are added. Since v2.8.1 the response no longer echoes `directories`.

#### Get parallel scan status
```http
GET /api/scan-parallel/status/<scan_id>
```

Get detailed status of a scan including chunk progress.

**Response:**
```json
{
  "scan_id": "uuid-string",
  "phase": "scanning",
  "is_active": true,
  "progress_percent": 35.71,
  "chunks": {
    "total": 42,
    "complete": 15,
    "remaining": 27
  },
  "active_workers": 8,
  "active_tasks": [
    {
      "worker": "celery@worker-1",
      "task_id": "task-uuid",
      "name": "pixelprobe.tasks_parallel.scan_chunk",
      "args": [],
      "kwargs": {"scan_id": "uuid-string"}
    }
  ],
  "files_processed": 1500,
  "estimated_total": 4200,
  "start_time": "2025-01-20T12:00:00Z",
  "message": "Processing 15/42 chunks"
}
```

#### Get worker status
```http
GET /api/scan-parallel/workers
```

Get current status and utilization of all Celery workers. Returns `{"status": "offline", "message": "No Celery workers available"}` when no workers are up; otherwise per-worker details including pool size and active tasks.

#### Get scan status
```http
GET /api/scan-status
```

Get the current scan progress and status.

**Response:**
```json
{
  "current": 45,
  "total": 100,
  "file": "/media/video.mp4",
  "status": "scanning",
  "is_running": true,
  "is_scanning": true,
  "is_active": true,
  "scan_id": 123,
  "start_time": "2025-01-20T12:00:00Z",
  "end_time": null,
  "directories": ["/media/photos"],
  "force_rescan": false,
  "phase": "scanning",
  "phase_number": 3,
  "total_phases": 3,
  "phase_current": 45,
  "phase_total": 100,
  "progress_message": "Phase 3 of 3: Scanning files - 45 of 100 files",
  "eta": "2025-01-20T12:30:00+00:00",
  "files_per_second": 1.25,
  "chunks": [
    {
      "chunk_id": "ab12cd34",
      "directory": "/media/photos",
      "status": "processing",
      "files_scanned": 45,
      "files_total": 100
    }
  ]
}
```

Field notes:
- `is_scanning` mirrors `is_running` (legacy compatibility); `is_active` reflects the database scan-state row.
- `eta` is an ISO-8601 timestamp (or `null`); `files_per_second` is a float.
- `chunks` is only present during the `scanning` phase and lists per-worker chunk progress.

**Status values:**
- `idle`: No scan running
- `initializing`: Preparing to scan
- `discovering`: Finding media files (phase 1)
- `adding`: Adding discovered files to the database (phase 2)
- `scanning`: Scanning files (phase 3)
- `completed`: Scan finished

`status` never reports `cancelled` or `failed` - a cancelled, failed, or crashed scan surfaces as `idle`. To distinguish, read the `phase` field, which can additionally be `error`, `cancelled`, or `crashed`.

#### Cancel scan
```http
POST /api/cancel-scan
```

Cancel the currently running scan.

### Statistics endpoints

#### Statistics
```http
GET /api/stats
```

Get overall statistics about scanned files.

**Response:**
```json
{
  "total_files": 1000,
  "completed_files": 950,
  "pending_files": 50,
  "scanning_files": 0,
  "error_files": 5,
  "corrupted_files": 10,
  "healthy_files": 940,
  "marked_as_good": 3,
  "warning_files": 7,
  "integrity": {
    "total_files": 1000,
    "checked_files": 800,
    "checked_percent": 80.0,
    "checked_last_30_days": 500,
    "never_checked": 200,
    "oldest_check_date": "2025-01-01T00:00:00Z",
    "bitrot_suspected": 2
  }
}
```

#### Scan trends
```http
GET /api/stats/trends?days=30
```

Get scan counter metrics over time.

**Query parameters:**
- `days` (integer): Number of days to look back (default: 30, max: 365)

**Response:**
```json
{
  "period_days": 30,
  "start_date": "2024-12-21T12:00:00Z",
  "summary": {
    "total_scans": 12,
    "total_files_scanned": 5000,
    "total_corrupted": 8,
    "total_warnings": 15
  },
  "daily_trends": [
    {
      "date": "2025-01-20",
      "scan_count": 2,
      "scan_types_count": 1,
      "files_scanned": 100,
      "files_corrupted": 2,
      "files_with_warnings": 1,
      "avg_duration_seconds": 120.5
    }
  ]
}
```

#### Scan duration histogram
```http
GET /api/stats/duration-histogram?days=30&buckets=10
```

Get a histogram of scan durations.

**Query parameters:**
- `days` (integer): Number of days to look back (default: 30, max: 365)
- `buckets` (integer): Number of histogram buckets (default: 10, min: 2, max: 50)

**Response:**
```json
{
  "period_days": 30,
  "start_date": "2024-12-21T12:00:00Z",
  "histogram": [
    {
      "range_start": 0.0,
      "range_end": 60.0,
      "count": 5,
      "percentage": 41.67
    }
  ],
  "summary": {
    "total_scans": 12,
    "min_duration": 10.2,
    "max_duration": 600.0,
    "avg_duration": 180.4,
    "median_duration": 150.0
  }
}
```

Also includes per-scan-type duration statistics.

#### System information
```http
GET /api/system-info
```

Get system information including database statistics (total/completed/pending/corrupted/healthy/warning file counts) and per-path file counts for the monitored paths.

### Admin endpoints

#### Mark files as good
```http
POST /api/mark-as-good
```

Mark files as healthy/good (removes corruption flag). Rate limited to 10 requests per minute.

**Request body:**
```json
{
  "file_ids": [1, 2, 3, 4, 5]
}
```

`file_ids` accepts at most 1000 IDs per request (400 `{"error": "Too many file IDs (max 1000)"}` beyond that). The same limit applies to `POST /api/bitrot/accept`.

#### Ignored error patterns
```http
GET /api/ignored-patterns
```

Get all ignored error patterns.

```http
POST /api/ignored-patterns
```

Add a new pattern to ignore in error detection.

**Request body:**
```json
{
  "pattern": "moov atom not found",
  "description": "Common false positive for certain MP4 files"
}
```

Constraints: `pattern` max 200 characters, `description` max 500 characters. Patterns containing dangerous regex syntax (inline flag groups such as `(?i`, named groups `(?P<`, or comments `(?#`) are rejected with 400, as are duplicates of an existing active pattern (400).

#### Scan configurations
```http
GET /api/configurations
```

Get all scan directory configurations.

```http
POST /api/configurations
```

Add a new directory to scan.

**Request body:**
```json
{
  "path": "/media/new-photos"
}
```

`path` max 1000 characters.

### Error management endpoints

#### Get error files
```http
GET /api/error-files
```

List all files that failed to scan, with error details. Rate limited to 10 requests per minute.

Use this to review scan failures, identify error patterns, or find files to retry.

**Query parameters:**
- `page` (integer): Page number (default: 1)
- `per_page` (integer): Results per page (default: 100, use -1 for all)
- `sort_field` (string): Field to sort by - `scan_date`, `file_path`, `file_size`, `file_type`, `scan_duration` (default: scan_date)
- `sort_order` (string): Sort order - `asc` or `desc` (default: desc)
- `search` (string): Filter by file path (optional, case-insensitive)

**Response:**
```json
{
  "error_files": [
    {
      "id": 123,
      "file_path": "/media/videos/corrupted.mp4",
      "file_size": 15728640,
      "file_type": "video/mp4",
      "error_message": "SQLAlchemy session error: This Session's transaction has been rolled back",
      "scan_date": "2025-01-20T15:30:00Z",
      "scan_duration": 2.5,
      "scan_tool": "ffmpeg",
      "discovered_date": "2025-01-19T10:00:00Z"
    }
  ],
  "total": 32,
  "pages": 1,
  "current_page": 1
}
```

Each item carries exactly these fields: `id`, `file_path`, `file_size`, `file_type`, `error_message`, `scan_date`, `scan_duration`, `scan_tool`, `discovered_date`. The envelope has `error_files`, `total`, `pages`, and `current_page` (no `per_page`).

**Usage examples:**

Get all error files:
```bash
curl -H "Authorization: Bearer your-token" \
  http://localhost:5000/api/error-files
```

Search for specific errors:
```bash
curl -H "Authorization: Bearer your-token" \
  "http://localhost:5000/api/error-files?search=videos&sort_field=file_size&sort_order=desc"
```

Get paginated results:
```bash
curl -H "Authorization: Bearer your-token" \
  "http://localhost:5000/api/error-files?page=1&per_page=50"
```

Python example:
```python
import requests

headers = {'Authorization': 'Bearer your-token'}
response = requests.get(
    'http://localhost:5000/api/error-files',
    headers=headers,
    params={
        'search': 'mp4',
        'sort_field': 'scan_date',
        'sort_order': 'desc',
        'per_page': 100
    }
)

error_files = response.json()
print(f"Found {error_files['total']} files with errors")

for file in error_files['error_files']:
    print(f"{file['file_path']}: {file['error_message']}")
```

**Note**: Files with scan_status='error' indicate the scanning process failed, not that the file is corrupted. These errors may be due to:
- Database connection issues (temporary)
- Unsupported file formats
- Permission issues
- Corrupted file metadata
- Tool failures (ffmpeg, exiftool, etc.)

After fixing underlying issues (e.g., database problems), use the `/api/reset-files-by-path` endpoint to reset error files to 'pending' status for rescanning.

### Export endpoints

#### Export scan results
```http
GET /api/export?format=csv
POST /api/export
```

Export scan results in multiple formats (CSV, JSON, or PDF).

**Query parameters (GET):**
- `format` (string): Output format - `csv`, `json`, or `pdf` (default: csv)
- `filter` (string): Filter type - `all`, `corrupted`, `healthy`, `pending`, `error` (default: all). `warning` is **not** handled on GET; use the POST form for it.
- `search` (string): Search term to filter by file path

**Request body (POST):**
```json
{
  "format": "pdf",
  "filter": "corrupted",
  "search": "vacation",
  "file_ids": [1, 2, 3]
}
```

In the POST form, `filter` accepts `all`, `corrupted`, `healthy`, or `warning`. If `file_ids` is provided, only those specific results are exported and `filter`/`search` are ignored.

**Response:** File download in requested format

### Maintenance endpoints

#### Cleanup orphaned entries
```http
POST /api/cleanup-orphaned
```

Start a background cleanup of database entries for files that no longer exist on disk. Returns `409 Conflict` if a cleanup is already in progress. Progress can be monitored via `GET /api/cleanup-status`.

**Request body (optional):**
```json
{
  "file_paths": ["/media/photos/missing.jpg"]
}
```

If `file_paths` is omitted, all files are checked.

**Response:**
```json
{
  "status": "started",
  "message": "Cleanup operation started for all files",
  "cleanup_id": 12,
  "file_count": null
}
```

#### Vacuum database
```http
POST /api/vacuum
```

Optimize the database by running VACUUM. **SQLite only**: on PostgreSQL deployments (the default since v2.2.0) this returns `400 {"error": "VACUUM operation only supported for SQLite databases"}`.

### Log endpoints

#### Get logs
```http
GET /api/logs?level=ERROR&per_page=50
```

Get paginated log entries with optional filters.

**Query parameters:**
- `since` (string): ISO timestamp for polling (returns only newer entries)
- `scan_id` (string): Filter by scan run ("system" for non-scan logs)
- `level` (string): Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `search` (string): Search text on message (case-insensitive)
- `start_time` / `end_time` (string): Time range filter
- `page` / `per_page` (integer): Pagination (default 200 per page, max 1000)

#### Get log runs
```http
GET /api/logs/runs
```

List scan/job runs with log entry counts.

#### Download logs
```http
GET /api/logs/download?level=WARNING
```

Download filtered logs as a `.log` text file.

#### Log retention
```http
GET /api/logs/retention
PUT /api/logs/retention
```

Get or set log retention period (days).

#### Purge logs
```http
POST /api/logs/purge
```

Manually purge log entries. Requires at least one filter parameter.

#### Get scan paths
```http
GET /api/scan-paths
```

Get list of active configured scan paths for the path filter dropdown.

## Additional endpoints

The endpoints below are not documented in detail above; methods and one-line purposes only. See the route sources under `pixelprobe/api/` for exact payloads.

### Authentication, users, and tokens

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/auth/status` | Setup/login status (unauthenticated) |
| POST | `/api/auth/setup` | Create the first admin account (first run only, unauthenticated) |
| POST | `/api/auth/login` | Log in, sets session cookie (unauthenticated) |
| POST | `/api/auth/logout` | Log out the current session |
| GET, POST | `/api/users` | List users / create a user |
| DELETE | `/api/users/{id}` | Delete a user |
| PUT | `/api/users/{id}/password` | Change a user's password |
| GET, POST | `/api/tokens` | List API tokens / create a token (value shown once) |
| DELETE | `/api/tokens/{id}` | Revoke an API token |

### Scan recovery and diagnostics

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/force-cleanup-scan`, `/api/scan/recovery` | Force-clear a stuck scan state (same handler, two paths) |
| POST | `/api/scan-files-parallel` | Legacy parallel scan of specific file lists |
| POST | `/api/reset-for-rescan` | Reset files to pending by criteria |
| POST | `/api/force-scan-pending` | Scan all pending files regardless of directory |
| POST | `/api/reset-files-by-path` | Reset specific files to pending by path |
| POST | `/api/reset-incomplete-scans` | Reset completed files with incomplete scan data |
| GET | `/api/diagnose-incomplete-scans` | Report files with incomplete scan data |
| GET | `/api/diagnose-pending-files` | Report why files are stuck pending |
| GET | `/api/worker-status` | Celery worker availability summary |
| GET | `/api/scan-output/{result_id}` | Full scan tool output for one result |

### Maintenance

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET, POST | `/api/file-changes` | Start a file-changes (integrity) check; optional `file_paths`, `time_budget_minutes` |
| GET | `/api/file-changes-status` | File-changes check progress |
| GET | `/api/cleanup-status` | Orphan cleanup progress |
| POST | `/api/cancel-cleanup` | Cancel a running cleanup |
| POST | `/api/reset-cleanup-state` | Clear a stuck cleanup state |
| POST | `/api/cancel-file-changes` | Cancel a running file-changes check |
| POST | `/api/reset-file-changes-state` | Clear a stuck file-changes state |

### Admin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/bitrot/accept` | Accept current content of bitrot-flagged files (`file_ids`, max 1000) |
| DELETE | `/api/ignored-patterns/{id}` | Deactivate an ignored error pattern |
| GET, POST | `/api/schedules` | List / create scan schedules (cron expression, scan type, paths) |
| GET, PUT, DELETE | `/api/schedules/{id}` | Read / update / delete a schedule |
| GET, PUT | `/api/exclusions` | Read / replace path and extension exclusions |
| POST, DELETE | `/api/exclusions/{type}` | Add / remove a single exclusion (`type` is `path` or `extension`) |

### Scanner settings

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/settings` | Every scanner setting with its current value, grouped as Detection, Performance and Timeouts |
| PUT | `/api/settings` | Save one or more settings. Send a JSON object of keys and values |
| DELETE | `/api/settings/{key}` | Restore one setting to its default |

Values are validated against the type and range declared for each setting. A `PUT`
carrying a bad value is rejected whole, with a message naming the setting, and
nothing is written. Settings take effect on the next file scanned, including in a
scan that is already running. Every key and default is listed in
[Configuration](configuration.md#scanner-settings).

```bash
# Report only freezes of 10 seconds or longer
curl -X PUT https://your-host/api/settings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"detection.freeze_min_duration_secs": 10}'

# Put it back to the default
curl -X DELETE https://your-host/api/settings/detection.freeze_min_duration_secs \
  -H "Authorization: Bearer $TOKEN"
```

### Notifications

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET, POST | `/api/notifications/providers` | List / create notification providers (webhook, Pushover, ntfy, email) |
| GET, PUT, DELETE | `/api/notifications/providers/{id}` | Read / update / delete a provider |
| POST | `/api/notifications/providers/{id}/test` | Send a test notification |
| GET, POST | `/api/notifications/rules` | List / create event-to-provider rules |
| GET, PUT, DELETE | `/api/notifications/rules/{id}` | Read / update / delete a rule |

### Healthchecks (healthchecks.io pings)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET, POST | `/api/healthcheck` | List / create healthcheck configs |
| GET, PUT, DELETE | `/api/healthcheck/{id}` | Read / update / delete a config |
| GET | `/api/healthcheck/schedule/{schedule_id}` | Config for a specific schedule |
| POST | `/api/healthcheck/{id}/test` | Send a test ping |

### Reports and file access

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/scan-reports` | List scan reports |
| GET | `/api/scan-reports/latest` | Latest report per scan type |
| GET, DELETE | `/api/scan-reports/{report_id}` | Read / delete a report |
| GET | `/api/scan-reports/{report_id}/export` | Export a report (CSV/JSON) |
| GET | `/api/scan-reports/{report_id}/pdf` | Export a report as PDF |
| GET | `/api/generate-pdf-report/{scan_type}/{scan_id}` | Generate a PDF report for a scan |
| POST | `/api/reports/download-multiple` | Download multiple reports as a ZIP |
| GET | `/api/view/{result_id}` | Stream a media file for in-browser viewing |
| GET | `/api/download/{result_id}` | Download the original media file |
| GET | `/api/openapi.yaml`, `/api/openapi.json` | OpenAPI specification (partial; unauthenticated) |

## Code examples

### Python
```python
import requests

# Base URL
BASE_URL = "http://localhost:5000"

# Get scan results
response = requests.get(f"{BASE_URL}/api/scan-results", params={
    "page": 1,
    "per_page": 50,
    "is_corrupted": "true"
})
results = response.json()

# Start a scan
response = requests.post(f"{BASE_URL}/api/scan", json={
    "force_rescan": False,
    "directories": ["/media/photos"]
})

# Check scan status
response = requests.get(f"{BASE_URL}/api/scan-status")
status = response.json()
print(f"Progress: {status['current']}/{status['total']}")
```

### JavaScript (Node.js)
```javascript
const axios = require('axios');

const BASE_URL = 'http://localhost:5000';

// Get scan results
async function getScanResults() {
  const response = await axios.get(`${BASE_URL}/api/scan-results`, {
    params: {
      page: 1,
      per_page: 50,
      is_corrupted: 'true'
    }
  });
  return response.data;
}

// Start a scan
async function startScan() {
  const response = await axios.post(`${BASE_URL}/api/scan`, {
    force_rescan: false,
    directories: ['/media/photos']
  });
  return response.data;
}
```

### cURL
```bash
# Get scan results
curl -X GET "http://localhost:5000/api/scan-results?is_corrupted=true"

# Start a scan
curl -X POST "http://localhost:5000/api/scan" \
  -H "Content-Type: application/json" \
  -d '{"force_rescan": false, "directories": ["/media/photos"]}'

# Check scan status
curl -X GET "http://localhost:5000/api/scan-status"
```

## WebSocket events (future)

Future versions will include WebSocket support for real-time updates:
- `scan:progress`: Scan progress updates
- `scan:complete`: Scan completion notification
- `scan:error`: Scan error notification

## Best practices

- Check `/api/scan-status` before starting a new scan; the API returns 409 if one is already running.
- Use pagination for large result sets rather than `per_page=-1`.
- Watch the rate limit headers and back off with exponential delay on 429 responses.
- After starting a cleanup, poll `GET /api/cleanup-status` for progress.

## Security considerations

- File paths are validated against the configured allowed directories.
- Inputs are validated for type and length.
- Rate limiting protects against abuse and DoS attacks.
- CSRF protection is enabled for the web interface (API endpoints are currently exempt).
- Subprocess calls use validated arguments to prevent command injection.

## Troubleshooting

### Common errors

- **409 "A scan is already in progress (Phase: ..., Files processed: N)"**: wait for the current scan or cancel it via `/api/cancel-scan`
- **400 "Invalid file path"**: the path must be inside a configured scan directory
- **429 Too Many Requests**: back off and retry; see the rate limit headers
- **500 Internal Server Error**: check the server logs