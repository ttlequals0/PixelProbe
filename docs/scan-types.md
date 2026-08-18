# PixelProbe scan types

## Overview
PixelProbe has several scan types for different corruption-detection workflows.

## Scan types

### 1. Full scan (`full`)
**Endpoint**: `POST /api/scan`
**Purpose**: Complete scan of specified directories
**When to use**: 
- Initial setup of PixelProbe
- After adding new media directories
- Periodic comprehensive checks

**How it works**:
1. **Phase 1 - discovery**: Recursively walks through all specified directories to find media files
2. **Phase 2 - adding**: Adds discovered files to database, checks for duplicates
3. **Phase 3 - scanning**: Splits the work into chunks and distributes them across all available Celery workers; each chunk performs corruption detection with FFmpeg, PIL/Pillow, and ImageMagick

**Features**:
- Discovers all media files in specified paths
- Adds new files to database
- Updates existing file records
- Performs deep corruption checking
- Chunk-distributed across Celery workers with per-chunk progress tracking
- Supports force_rescan option to re-check previously scanned files

**Deprecated alias**: `POST /api/scan-parallel` runs the identical chunk-distributed engine and differs only in response shape. It is kept for API compatibility and will be removed in a future major release; use `/api/scan`.

**Example**:
```json
POST /api/scan
{
  "directories": ["/media/movies", "/media/photos"],
  "force_rescan": false
}
```

### 2. Pending scan (`pending`)
**Endpoint**: `POST /api/force-scan-pending`
**Purpose**: Scan only files marked as pending
**When to use**:
- After interrupted scans
- To process newly discovered files
- Selective scanning of unprocessed files

**How it works**:
1. Queries database for files with `scan_status='pending'`
2. Skips discovery phase (uses existing database records)
3. Performs corruption detection on pending files only
4. Updates status to 'completed' with corruption results

**Features**:
- No directory walking needed
- Efficient for processing backlog
- Preserves existing scan results
- Can be interrupted and resumed

**Example**:
```json
POST /api/force-scan-pending
```

### 3. File changes scan (`file_changes`)
**Endpoint**: `GET/POST /api/file-changes`
**Purpose**: Verify stored content hashes and classify what changed (rolling integrity queue)
**When to use**:
- Scheduled integrity sweeps (pair with a per-schedule time budget)
- After bulk media edits
- To catch silent corruption (bitrot) over time

**How it works**:
1. Pulls files from a rolling queue ordered stalest-first by `last_integrity_check_date`; files flagged as suspected bitrot jump the queue
2. Re-hashes each file and classifies the result: hash match = unchanged; hash and mtime both changed = modified (queued for rescan); hash changed while mtime did not = suspected bitrot (flagged and notified, stored baseline preserved)
3. Stamps every processed file, so interrupted or budget-limited runs resume where they left off

**Features**:
- Optional `time_budget_minutes` (request body, or the schedule's field): dispatch stops at the deadline, in-flight hashes drain, and the queue resumes at the next run
- Bitrot flags auto-expire after consecutive stable checks plus a clean rescan; accept manually via `POST /api/bitrot/accept`
- Cumulative coverage exposed in `/api/stats` (`integrity` block) and the dashboard's Integrity Checked card
- Reports the changed-file list with hash, mtime, and size detail

**Example**:
```json
POST /api/file-changes
{"time_budget_minutes": 10}
```

`time_budget_minutes` must be a positive integer and is only valid for `file_changes` scans and schedules; supplying it for any other scan type returns 400.

### 4. Cleanup (`cleanup`)
**Endpoint**: `POST /api/cleanup-orphaned`
**Purpose**: Remove database entries for deleted files
**When to use**:
- After deleting media files
- Regular database maintenance
- To keep database in sync with filesystem

**How it works**:
1. **Phase 1 - identify orphans**: Checks each database entry against filesystem
2. **Phase 2 - cleanup**: Removes entries for non-existent files
3. Reports number of orphaned entries removed

**Features**:
- Database cleanup without scanning
- Batch processing for efficiency
- Progress tracking
- Safe operation (only removes, doesn't modify existing)

**Example**:
```json
POST /api/cleanup-orphaned
```

### 5. Single file scan (`single`)
**Endpoint**: `POST /api/scan-file`
**Purpose**: Scan a specific file
**When to use**:
- Testing scan functionality
- Checking specific suspicious files
- Manual file verification

**How it works**:
1. Validates file exists and is a media file
2. Performs corruption detection
3. Updates or creates database record
4. Returns immediate results

**Features**:
- Immediate results
- Detailed corruption information
- Updates existing records
- No discovery phase needed

**Example**:
```json
POST /api/scan-file
{
  "file_path": "/media/movies/suspicious_file.mp4"
}
```

### 6. Scheduled scan (`scheduled`)
**Purpose**: Automated scanning at configured intervals
**When to use**:
- Nightly/weekly maintenance
- Automated monitoring
- Hands-free operation

**How it works**:
1. APScheduler triggers at configured times
2. Uses HTTP self-call to `/api/scan` (v2.2.50 fix)
3. Runs with pre-configured paths and options
4. Can be any scan type (full, changes, cleanup)

**Configuration**:
- Via environment variables
- Via database schedules
- Cron expressions supported

**Example schedule**:
```python
# Daily at 2 AM
cron_expression = "0 2 * * *"
scan_type = "file_changes"
scan_paths = ["/media"]
```

## Scan phases

All directory scans run in three phases:

### Phase 1: discovery
- Walks directories to find media files
- Filters by supported extensions
- Excludes configured paths
- Builds list of files to process

### Phase 2: adding
- Adds discovered files to database
- Checks for duplicates
- Sets initial status to 'pending'
- Updates file metadata

### Phase 3: scanning
- Performs actual corruption detection
- Uses FFmpeg for video and audio files
- Uses PIL/Pillow and ImageMagick for image files
- Updates corruption status
- Stores scan results

## Performance considerations

### Scan type selection
- **Full scan**: initial setup and periodic comprehensive checks (any library size; work is chunk-distributed across workers)
- **Regular maintenance**: Use file_changes scan
- **After deletions**: Use orphan cleanup
- **Selective scanning**: Use pending scan

### Resource usage
- **Full scans**: High CPU/IO usage
- **File changes**: Moderate usage
- **Orphan cleanup**: Low usage
- **Pending scan**: Depends on pending count

### Optimization tips
- Use force_rescan sparingly; it re-checks files that already have results.
- On powerful systems, increase the Celery worker count to scan more chunks concurrently.
- For scheduling cadence, see Best practices below.

## Scan status values

Files can have the following scan statuses:
- `pending`: Discovered but not yet scanned
- `scanning`: Currently being processed
- `completed`: Scan finished successfully
- `error`: Scan failed with error
- `skipped`: File skipped - rare; only set by the scan recovery path, not by normal scans

## Corruption detection

### Video files (FFmpeg)
- Decodes video streams
- Checks for codec errors
- Validates container format
- Detects truncated files
- Reports specific error types

### Audio files (FFmpeg)
- First-class category with its own extension list
- Decodes audio streams via FFmpeg to detect corruption and truncation

### Image files (PIL/Pillow and ImageMagick)
- PIL/Pillow verifies and decodes the image (pillow-heif adds HEIC/HEIF support)
- ImageMagick decodes full pixel data, not just headers
- Detects truncated images and decode errors
- Reports corruption details

### Detection levels
- **Healthy**: File passes all checks
- **Warning**: Minor issues (e.g., 10-bit HEVC)
- **Corrupted**: Definite corruption detected
- **Error**: Could not scan file

## API response examples

### Successful scan start (`POST /api/scan`)
```json
{
  "status": "queued",
  "scan_id": "uuid-string",
  "task_id": "celery-task-id",
  "message": "Scan queued successfully using Celery task queue",
  "celery_enabled": true
}
```

### Scan status (`GET /api/scan-status`, abbreviated)
```json
{
  "current": 5000,
  "total": 10000,
  "file": "/media/movie.mp4",
  "status": "scanning",
  "is_running": true,
  "phase": "scanning",
  "phase_number": 3,
  "total_phases": 3,
  "progress_message": "Phase 3 of 3: Scanning files - 5000 of 10,000 files",
  "eta": "2025-08-25T11:15:00+00:00",
  "files_per_second": 4.2
}
```

### Scan conflict (HTTP 409)
```json
{
  "error": "A scan is already in progress (Phase: scanning, Files processed: 5000). Please wait for it to complete or use /api/cancel-scan to stop it."
}
```

## Best practices

- Initial setup: run a full scan on all media directories.
- Ongoing: schedule daily file_changes scans, weekly orphan cleanup, and a monthly full scan for critical data.
- Run heavy scans during off-hours; limit worker count during business hours.
- Re-scan error files individually and mark false positives as good.
- Back up the database before major scans.