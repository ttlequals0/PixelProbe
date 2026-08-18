# PixelProbe API Documentation

## Overview

PixelProbe exposes its media corruption detection through a REST API built with Flask.

## Base URL

- Development: `http://localhost:5000`
- Production: `https://pixelprobe.example.com`

## Authentication

**As of v2.4.1, all API endpoints require authentication.**

PixelProbe supports two authentication methods:

### 1. Session-Based Authentication (Web UI)
- Used automatically when logged in through the web interface
- Managed via secure HTTP-only cookies
- Best for browser-based access

### 2. API Token Authentication (Programmatic Access)
- Generate tokens through the web UI under Account → API Tokens
- Include in requests using the Authorization header
- Two formats are supported:
  - Standard: `Authorization: Bearer <your-token>`
  - Direct: `Authorization: <your-token>` (for Swagger UI compatibility)

#### Example with curl:
```bash
# Using Bearer format
curl -H "Authorization: Bearer your-api-token-here" \
     http://localhost:5000/api/scan-status

# Using direct format (Swagger UI style)
curl -H "Authorization: your-api-token-here" \
     http://localhost:5000/api/scan-status
```

#### Example with Python:
```python
import requests

headers = {
    'Authorization': 'Bearer your-api-token-here'
}

response = requests.get('http://localhost:5000/api/scan-status', headers=headers)
```

### Getting an API Token
1. Log in to the web interface
2. Navigate to Account → API Tokens
3. Click "Create New Token"
4. Provide a description
5. Copy the generated token (it won't be shown again)

## Rate Limiting

The API implements rate limiting on specific endpoints to prevent abuse:
- **No default/global limits**: only individually decorated endpoints are rate limited
- **Scan operations**: 2-5 requests per minute
- **Admin operations**: 10 requests per minute
- **Maintenance operations**: 5 requests per minute
- **Exemptions**: requests from localhost and private/Docker networks (127.0.0.1, 10.x, 172.x, 192.168.x) are exempt from rate limiting

Rate limit headers are included in responses:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining
- `X-RateLimit-Reset`: Time when the limit resets

## Request/Response Format

- All requests must include `Content-Type: application/json` for POST requests
- All responses are in JSON format
- Dates are in ISO 8601 format
- File sizes are in bytes

## Error Handling

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

## API Endpoints

### System Endpoints

#### Liveness Probe (Unauthenticated)
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

#### Health Check (Authenticated)
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

Get version information.

**Response:**
```json
{
  "version": "<current_version>",
  "github_url": "https://github.com/ttlequals0/PixelProbe",
  "api_version": "1.0"
}
```

### Scan Endpoints

#### Get Scan Results
```http
GET /api/scan-results?page=1&per_page=100&scan_status=all&is_corrupted=all
```

Get paginated scan results with optional filters.

**Query Parameters:**
- `page` (integer): Page number (default: 1)
- `per_page` (integer): Results per page (default: 100, use -1 for all)
- `scan_status` (string): Filter by status: `all`, `pending`, `scanning`, `completed`, `error`
- `is_corrupted` (string): Filter by corruption: `all`, `true`, `false`
- `search` (string): Case-insensitive substring match on file path
- `path` (string): Restrict results to one configured scan path (must exactly match a configured path)

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
      "media_info": {
        "width": 1920,
        "height": 1080,
        "format": "JPEG"
      },
      "file_exists": true
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 100,
  "pages": 2
}
```

#### Get Single Scan Result
```http
GET /api/scan-results/{result_id}
```

Get detailed information about a specific scan result.

**Response:** Same as individual result in the list above.

#### Scan Single File
```http
POST /api/scan-file
```

Scan a single file for corruption. Rate limited to 5 requests per minute.

**Request Body:**
```json
{
  "file_path": "/media/photos/image.jpg"
}
```

**Response:**
```json
{
  "message": "Scan started",
  "file_path": "/media/photos/image.jpg"
}
```

#### Start Scan
```http
POST /api/scan
```

Start scanning all configured directories (or a supplied list). Distributes work across all available Celery workers in chunks. Rate limited to 2 requests per minute.

**Request Body:**
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

#### Parallel Scan (Deprecated)
```http
POST /api/scan-parallel
```

Deprecated alias of `/api/scan`; both run the same chunk-distributed engine. Kept for API compatibility and will be removed in a future major release. Rate limited to 2 requests per minute.

**Request Body:**
```json
{
  "directories": ["/media/photos"],
  "force_rescan": false
}
```

`directories` is required; `force_rescan` is optional. The response matches `/api/scan` plus legacy fields (`status: "launched"`, `scan_type`, `force_rescan`). Since v2.8.1 the response no longer echoes the requested `directories` back.

#### Get Parallel Scan Status
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

#### Get Worker Status
```http
GET /api/scan-parallel/workers
```

Get current status and utilization of all Celery workers. Returns `{"status": "offline", "message": "No Celery workers available"}` when no workers are up; otherwise per-worker details including pool size and active tasks.

#### Get Scan Status
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
  "scan_id": 123,
  "start_time": "2025-01-20T12:00:00Z",
  "end_time": null,
  "directories": ["/media/photos"],
  "force_rescan": false
}
```

**Status Values:**
- `idle`: No scan running
- `initializing`: Preparing to scan
- `discovering`: Finding media files
- `scanning`: Scanning files
- `completed`: Scan finished
- `cancelled`: Scan was cancelled
- `error`: Scan encountered an error

#### Cancel Scan
```http
POST /api/cancel-scan
```

Cancel the currently running scan.

### Statistics Endpoints

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

#### Scan Trends
```http
GET /api/stats/trends?days=30
```

Get scan counter metrics over time.

**Query Parameters:**
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

#### Scan Duration Histogram
```http
GET /api/stats/duration-histogram?days=30&buckets=10
```

Get a histogram of scan durations.

**Query Parameters:**
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

#### System Information
```http
GET /api/system-info
```

Get system information including database statistics (total/completed/pending/corrupted/healthy/warning file counts) and per-path file counts for the monitored paths.

### Admin Endpoints

#### Mark Files as Good
```http
POST /api/mark-as-good
```

Mark files as healthy/good (removes corruption flag). Rate limited to 10 requests per minute.

**Request Body:**
```json
{
  "file_ids": [1, 2, 3, 4, 5]
}
```

#### Ignored Error Patterns
```http
GET /api/ignored-patterns
```

Get all ignored error patterns.

```http
POST /api/ignored-patterns
```

Add a new pattern to ignore in error detection.

**Request Body:**
```json
{
  "pattern": "moov atom not found",
  "description": "Common false positive for certain MP4 files"
}
```

#### Scan Configurations
```http
GET /api/configurations
```

Get all scan directory configurations.

```http
POST /api/configurations
```

Add a new directory to scan.

**Request Body:**
```json
{
  "path": "/media/new-photos"
}
```

### Error Management Endpoints

#### Get Error Files
```http
GET /api/error-files
```

Retrieve a list of all files that failed to scan, with detailed error information. Rate limited to 10 requests per minute.

Use this to review scan failures, identify error patterns, or find files to retry.

**Query Parameters:**
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
      "file_name": "corrupted.mp4",
      "file_size": 15728640,
      "file_type": "video/mp4",
      "scan_status": "error",
      "error_message": "SQLAlchemy session error: This Session's transaction has been rolled back",
      "scan_date": "2025-01-20T15:30:00Z",
      "scan_duration": 2.5,
      "tool_name": "ffmpeg",
      "discovered_date": "2025-01-19T10:00:00Z",
      "last_modified": "2025-01-18T08:00:00Z"
    }
  ],
  "total": 32,
  "pages": 1,
  "current_page": 1,
  "per_page": 100
}
```

**Usage Examples:**

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

### Export Endpoints

#### Export Scan Results
```http
GET /api/export?format=csv
POST /api/export
```

Export scan results in multiple formats (CSV, JSON, or PDF).

**Query Parameters (GET):**
- `format` (string): Output format - `csv`, `json`, or `pdf` (default: csv)
- `filter` (string): Filter type - `all`, `corrupted`, `healthy`, `warning` (default: all)
- `search` (string): Search term to filter by file path

**Request Body (POST):**
```json
{
  "format": "pdf",
  "filter": "corrupted",
  "search": "vacation",
  "file_ids": [1, 2, 3]
}
```

If `file_ids` is provided, only those specific results are exported and `filter`/`search` are ignored.

**Response:** File download in requested format

### Maintenance Endpoints

#### Cleanup Orphaned Entries
```http
POST /api/cleanup-orphaned
```

Start a background cleanup of database entries for files that no longer exist on disk. Returns `409 Conflict` if a cleanup is already in progress. Progress can be monitored via `GET /api/cleanup-status`.

**Request Body (optional):**
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

#### Vacuum Database
```http
POST /api/vacuum
```

Optimize the database by running VACUUM. Rate limited to 5 requests per minute.

### Log Endpoints

#### Get Logs
```http
GET /api/logs?level=ERROR&per_page=50
```

Get paginated log entries with optional filters.

**Query Parameters:**
- `since` (string): ISO timestamp for polling (returns only newer entries)
- `scan_id` (string): Filter by scan run ("system" for non-scan logs)
- `level` (string): Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `search` (string): Search text on message (case-insensitive)
- `start_time` / `end_time` (string): Time range filter
- `page` / `per_page` (integer): Pagination (default 200 per page, max 1000)

#### Get Log Runs
```http
GET /api/logs/runs
```

List scan/job runs with log entry counts.

#### Download Logs
```http
GET /api/logs/download?level=WARNING
```

Download filtered logs as a `.log` text file.

#### Log Retention
```http
GET /api/logs/retention
PUT /api/logs/retention
```

Get or set log retention period (days).

#### Purge Logs
```http
POST /api/logs/purge
```

Manually purge log entries. Requires at least one filter parameter.

#### Get Scan Paths
```http
GET /api/scan-paths
```

Get list of active configured scan paths for the path filter dropdown.

## Code Examples

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

## WebSocket Events (Future)

Future versions will include WebSocket support for real-time updates:
- `scan:progress`: Scan progress updates
- `scan:complete`: Scan completion notification
- `scan:error`: Scan error notification

## Best Practices

- Check `/api/scan-status` before starting a new scan; the API returns 409 if one is already running.
- Use pagination for large result sets rather than `per_page=-1`.
- Watch the rate limit headers and back off with exponential delay on 429 responses.
- After starting a cleanup, poll `GET /api/cleanup-status` for progress.

## Security Considerations

1. **Path Validation**: All file paths are validated against configured allowed directories
2. **Input Validation**: All inputs are validated for type and length
3. **Rate Limiting**: Prevents abuse and DoS attacks
4. **CSRF Protection**: Enabled for web interface (API endpoints currently exempt)
5. **Command Injection**: All subprocess calls use validated arguments

## Troubleshooting

### Common Errors

- **409 "Another scan is already in progress"**: wait for the current scan or cancel it via `/api/cancel-scan`
- **400 "Invalid file path"**: the path must be inside a configured scan directory
- **429 Too Many Requests**: back off and retry; see the rate limit headers
- **500 Internal Server Error**: check the server logs

### Debug Headers

Include these headers for debugging:
- `X-Request-ID`: Unique request identifier
- `X-Response-Time`: Server processing time