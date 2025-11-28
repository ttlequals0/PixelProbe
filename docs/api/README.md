# PixelProbe API Documentation

## Overview

PixelProbe provides a RESTful API for managing media file corruption detection. The API is built with Flask and follows REST conventions.

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

The API implements rate limiting to prevent abuse:
- **Default limits**: 200 requests per day, 50 per hour
- **Scan operations**: 2-5 requests per minute
- **Admin operations**: 10 requests per minute
- **Maintenance operations**: 5 requests per minute

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

#### Health Check
```http
GET /health
```

Check if the service is running.

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
- `per_page` (integer): Results per page (default: 100, max: 500)
- `scan_status` (string): Filter by status: `all`, `pending`, `scanning`, `completed`, `error`
- `is_corrupted` (string): Filter by corruption: `all`, `true`, `false`

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

#### Scan All Files
```http
POST /api/scan-all
```

Start scanning all configured directories. Rate limited to 2 requests per minute.

**Request Body:**
```json
{
  "force_rescan": false,
  "directories": ["/media/photos", "/media/videos"]
}
```

**Response:**
```json
{
  "message": "Scan started",
  "directories": ["/media/photos", "/media/videos"],
  "force_rescan": false
}
```

#### Parallel Scan
```http
POST /api/scan-parallel
```

Start a parallel scan with multiple workers. Rate limited to 2 requests per minute.

**Request Body:**
```json
{
  "force_rescan": false,
  "num_workers": 4,
  "directories": ["/media/photos"]
}
```

#### Enhanced Parallel Scan V2
```http
POST /api/scan/parallel-v2
```

Start an enhanced parallel scan that distributes work across all available Celery workers.

**Request Body:**
```json
{
  "directories": ["/media/photos", "/media/videos"],
  "force_rescan": false,
  "chunk_size": 100
}
```

**Response:**
```json
{
  "scan_id": "uuid-string",
  "message": "Enhanced parallel scan started",
  "total_workers": 8,
  "chunks_created": 42
}
```

#### Get Parallel Scan V2 Status
```http
GET /api/scan/parallel-v2/status/<scan_id>
```

Get detailed status of an enhanced parallel scan including chunk progress.

**Response:**
```json
{
  "scan_id": "uuid-string",
  "status": "running",
  "total_chunks": 42,
  "completed_chunks": 15,
  "progress_percentage": 35.7,
  "estimated_time_remaining": "5 minutes",
  "worker_status": {
    "active": 8,
    "idle": 0
  }
}
```

#### Get Worker Status
```http
GET /api/scan/parallel-v2/workers
```

Get current status and utilization of all Celery workers.

**Response:**
```json
{
  "total_workers": 8,
  "active_workers": 6,
  "idle_workers": 2,
  "worker_details": [
    {
      "worker_id": "worker-1",
      "status": "busy",
      "current_task": "processing chunk 5"
    }
  ]
}
```

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

#### Summary Statistics
```http
GET /api/stats/summary
```

Get overall statistics about scanned files.

**Response:**
```json
{
  "total_files": 1000,
  "scanned_files": 950,
  "corrupted_files": 10,
  "healthy_files": 940,
  "pending_files": 50,
  "error_files": 5,
  "total_size": 10737418240,
  "corrupted_size": 52428800,
  "last_scan_date": "2025-01-20T12:00:00Z",
  "corruption_rate": 1.05
}
```

#### Corruption by File Type
```http
GET /api/stats/corruption-by-type
```

Get corruption statistics grouped by file type.

**Response:**
```json
[
  {
    "file_type": "image/jpeg",
    "total_files": 500,
    "corrupted_files": 5,
    "corruption_rate": 1.0
  },
  {
    "file_type": "video/mp4",
    "total_files": 200,
    "corrupted_files": 3,
    "corruption_rate": 1.5
  }
]
```

#### Scan History
```http
GET /api/stats/scan-history?days=30
```

Get scan history for the specified number of days.

**Response:**
```json
[
  {
    "date": "2025-01-20",
    "files_scanned": 100,
    "corrupted_found": 2
  },
  {
    "date": "2025-01-19",
    "files_scanned": 150,
    "corrupted_found": 1
  }
]
```

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

**Why use this endpoint:**
- Monitor and track files that encountered errors during scanning
- Identify patterns in scan failures (e.g., specific file types, corrupted metadata)
- Bulk retry or investigate problematic files
- Generate error reports for troubleshooting
- Clean up or move files that consistently fail to scan

**When to use this endpoint:**
- After a scan completes, to review any failures
- During troubleshooting to identify which files are causing issues
- When implementing automated error handling workflows
- To monitor scan health over time

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

**Common Use Cases:**

1. **Monitoring scan health**: Check for errors after each scan run
2. **Bulk retry**: Get list of error files, reset them, and rescan
3. **Error pattern analysis**: Group by error_message to identify common failures
4. **Storage cleanup**: Identify and remove files that can't be processed
5. **Troubleshooting**: Investigate specific file types or paths that fail consistently

**Note**: Files with scan_status='error' indicate the scanning process failed, not that the file is corrupted. These errors may be due to:
- Database connection issues (temporary)
- Unsupported file formats
- Permission issues
- Corrupted file metadata
- Tool failures (ffmpeg, exiftool, etc.)

After fixing underlying issues (e.g., database problems), use the `/api/reset-files-by-path` endpoint to reset error files to 'pending' status for rescanning.

### Export Endpoints

#### Export Scan Results (Enhanced)
```http
GET /api/export?format=csv
POST /api/export
```

Export scan results in multiple formats (CSV, JSON, or PDF).

**Query Parameters (GET):**
- `format` (string): Output format - `csv`, `json`, or `pdf` (default: csv)
- `scan_status` (string): Filter by status - `all`, `pending`, `completed`, `error`
- `is_corrupted` (string): Filter by corruption - `all`, `true`, `false`
- `start_date` (string): Start date in ISO format
- `end_date` (string): End date in ISO format

**Request Body (POST):**
```json
{
  "format": "pdf",
  "filters": {
    "scan_status": "completed",
    "is_corrupted": "true",
    "start_date": "2025-01-01",
    "end_date": "2025-01-31"
  }
}
```

**Response:** File download in requested format

#### Export to CSV (Legacy)
```http
POST /api/export/csv
```

Export scan results to CSV format (legacy endpoint, use `/api/export` instead).

**Request Body:**
```json
{
  "filters": {
    "scan_status": "completed",
    "is_corrupted": "true",
    "start_date": "2025-01-01",
    "end_date": "2025-01-31"
  }
}
```

**Response:** CSV file download

### Maintenance Endpoints

#### Cleanup Missing Files
```http
POST /api/cleanup
```

Remove database entries for files that no longer exist. Rate limited to 10 requests per minute.

**Request Body:**
```json
{
  "dry_run": true,
  "directories": ["/media/photos"]
}
```

**Response:**
```json
{
  "missing_files": 10,
  "cleaned_files": 0,
  "dry_run": true
}
```

#### Vacuum Database
```http
POST /api/vacuum
```

Optimize the database by running VACUUM. Rate limited to 5 requests per minute.

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
response = requests.post(f"{BASE_URL}/api/scan-all", json={
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
  const response = await axios.post(`${BASE_URL}/api/scan-all`, {
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
curl -X POST "http://localhost:5000/api/scan-all" \
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

1. **Check scan status** before starting a new scan to avoid conflicts
2. **Use pagination** when retrieving large result sets
3. **Implement exponential backoff** when rate limited
4. **Validate file paths** before submitting scan requests
5. **Use dry_run** for cleanup operations to preview changes
6. **Monitor rate limit headers** to avoid hitting limits

## Security Considerations

1. **Path Validation**: All file paths are validated against configured allowed directories
2. **Input Validation**: All inputs are validated for type and length
3. **Rate Limiting**: Prevents abuse and DoS attacks
4. **CSRF Protection**: Enabled for web interface (API endpoints currently exempt)
5. **Command Injection**: All subprocess calls use validated arguments

## Troubleshooting

### Common Errors

**409 Conflict - "Another scan is already in progress"**
- Solution: Wait for current scan to complete or cancel it

**400 Bad Request - "Invalid file path"**
- Solution: Ensure file path is within allowed directories

**429 Too Many Requests**
- Solution: Implement rate limiting in your client

**500 Internal Server Error**
- Solution: Check server logs for details

### Debug Headers

Include these headers for debugging:
- `X-Request-ID`: Unique request identifier
- `X-Response-Time`: Server processing time