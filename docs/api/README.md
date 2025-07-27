# PixelProbe API Documentation

## Overview

PixelProbe provides a RESTful API for managing media file corruption detection. The API is built with Flask and follows REST conventions.

## Base URL

- Development: `http://localhost:5000`
- Production: `https://pixelprobe.example.com`

## Authentication

Currently, the API does not require authentication. Future versions will implement JWT-based authentication.

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
  "version": "2.0.55",
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
  "version": "2.0.55",
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

### Export Endpoints

#### Export to CSV
```http
POST /api/export/csv
```

Export scan results to CSV format.

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