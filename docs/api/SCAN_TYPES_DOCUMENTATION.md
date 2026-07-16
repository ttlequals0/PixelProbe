# PixelProbe Scan Types Documentation

## Overview
PixelProbe has several scan types for different corruption-detection workflows.

## Scan Types

### 1. Full Scan (`full`)
**Endpoint**: `POST /api/scan`
**Purpose**: Complete scan of specified directories
**When to use**: 
- Initial setup of PixelProbe
- After adding new media directories
- Periodic comprehensive checks

**How it works**:
1. **Phase 1 - Discovery**: Recursively walks through all specified directories to find media files
2. **Phase 2 - Adding**: Adds discovered files to database, checks for duplicates
3. **Phase 3 - Scanning**: Performs corruption detection on each file using FFmpeg/ImageMagick

**Features**:
- Discovers all media files in specified paths
- Adds new files to database
- Updates existing file records
- Performs deep corruption checking
- Supports force_rescan option to re-check previously scanned files

**Example**:
```json
POST /api/scan
{
  "directories": ["/media/movies", "/media/photos"],
  "force_rescan": false
}
```

### 2. Parallel Scan (`parallel`)
**Endpoint**: `POST /api/scan-parallel`
**Purpose**: High-performance scanning using multiple workers
**When to use**:
- Large media libraries (100k+ files)
- When speed is critical
- Multi-core systems with available resources

**How it works**:
1. Divides work into chunks based on available workers
2. Distributes chunks across Celery workers
3. Each worker processes its chunk independently
4. Results are aggregated in real-time

**Features**:
- Automatically detects number of available workers
- Dynamic chunk size calculation
- Linear performance scaling with worker count
- Real-time progress tracking per chunk
- Worker utilization monitoring

**Performance**:
- 8 workers: ~267% faster than sequential
- 16 workers: ~533% faster than sequential

**Example**:
```json
POST /api/scan-parallel
{
  "directories": ["/media"],
  "force_rescan": false
}
```

### 3. Pending Scan (`pending`)
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

### 4. File Changes Scan (`file_changes`)
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

### 5. Cleanup (`cleanup`)
**Endpoint**: `POST /api/cleanup-orphaned`
**Purpose**: Remove database entries for deleted files
**When to use**:
- After deleting media files
- Regular database maintenance
- To keep database in sync with filesystem

**How it works**:
1. **Phase 1 - Identify Orphans**: Checks each database entry against filesystem
2. **Phase 2 - Cleanup**: Removes entries for non-existent files
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

### 6. Single File Scan (`single`)
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

### 7. Scheduled Scan (`scheduled`)
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

**Example Schedule**:
```python
# Daily at 2 AM
cron_expression = "0 2 * * *"
scan_type = "file_changes"
scan_paths = ["/media"]
```

## Scan Phases

All multi-file scan types follow a three-phase approach:

### Phase 1: Discovery
- Walks directories to find media files
- Filters by supported extensions
- Excludes configured paths
- Builds list of files to process

### Phase 2: Adding
- Adds discovered files to database
- Checks for duplicates
- Sets initial status to 'pending'
- Updates file metadata

### Phase 3: Scanning
- Performs actual corruption detection
- Uses FFmpeg for video files
- Uses ImageMagick for image files
- Updates corruption status
- Stores scan results

## Performance Considerations

### Scan Type Selection
- **Small libraries (<10k files)**: Use full scan
- **Large libraries (>100k files)**: Use parallel scan
- **Regular maintenance**: Use file_changes scan
- **After deletions**: Use orphan cleanup
- **Selective scanning**: Use pending scan

### Resource Usage
- **Full/Parallel scans**: High CPU/IO usage
- **File changes**: Moderate usage
- **Orphan cleanup**: Low usage
- **Pending scan**: Depends on pending count

### Optimization Tips
- Use force_rescan sparingly; it re-checks files that already have results.
- On powerful systems, increase the worker count for parallel scans.
- For scheduling cadence, see Best Practices below.

## Scan Status Values

Files can have the following scan statuses:
- `pending`: Discovered but not yet scanned
- `scanning`: Currently being processed
- `completed`: Scan finished successfully
- `error`: Scan failed with error
- `skipped`: File skipped (unsupported format, etc.)

## Corruption Detection

### Video Files (FFmpeg)
- Decodes video streams
- Checks for codec errors
- Validates container format
- Detects truncated files
- Reports specific error types

### Image Files (ImageMagick)
- Validates image headers
- Checks for decode errors
- Detects truncated images
- Validates color profiles
- Reports corruption details

### Detection Levels
- **Healthy**: File passes all checks
- **Warning**: Minor issues (e.g., 10-bit HEVC)
- **Corrupted**: Definite corruption detected
- **Error**: Could not scan file

## API Response Examples

### Successful Scan Start
```json
{
  "status": "success",
  "message": "Scan started successfully",
  "scan_id": "abc-123-def",
  "scan_type": "full",
  "estimated_files": 10000
}
```

### Scan Status
```json
{
  "is_active": true,
  "phase": "scanning",
  "phase_number": 3,
  "files_processed": 5000,
  "estimated_total": 10000,
  "percentage": 50,
  "current_file": "/media/movie.mp4",
  "eta": "10 minutes"
}
```

### Scan Conflict
```json
{
  "error": "Another scan is already in progress",
  "status_code": 409,
  "active_scan": {
    "type": "full",
    "started": "2025-08-25T10:00:00Z",
    "progress": 75
  }
}
```

## Best Practices

- Initial setup: run a full scan on all media directories (parallel for large libraries).
- Ongoing: schedule daily file_changes scans, weekly orphan cleanup, and a monthly full scan for critical data.
- Run heavy scans during off-hours; limit worker count during business hours.
- Re-scan error files individually and mark false positives as good.
- Back up the database before major scans.