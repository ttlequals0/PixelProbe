# PixelProbe Tools and Scripts Documentation

All tools, scripts, and utilities available in PixelProbe v2.5.57+.

## Table of Contents
1. [Database Tools](#database-tools)
2. [False Positive Fixes](#false-positive-fixes)
3. [Development Scripts](#development-scripts)
4. [Testing Tools](#testing-tools)
5. [Docker Scripts](#docker-scripts)

---

## Database Tools

> **Note:** Database migrations run automatically on application startup via `app_startup_migration.py`. No manual intervention is required.

### Maintenance Scripts

#### `scripts/fix_database_schema.py`
**Purpose:** Emergency database schema fix script  
**Usage:**
```bash
python3 scripts/fix_database_schema.py
```
**When to use:** When database schema is corrupted or out of sync

#### `scripts/check_db_integrity.py`
**Purpose:** Verify database integrity and consistency  
**Usage:**
```bash
python3 scripts/check_db_integrity.py
```

#### `scripts/create_indexes.py`
**Purpose:** Create performance indexes on database tables  
**Usage:**
```bash
python3 scripts/create_indexes.py
```

#### `scripts/test_database.py`
**Purpose:** Test database connection and basic operations  
**Usage:**
```bash
python3 scripts/test_database.py
```

#### `scripts/create_test_database.py`
**Purpose:** Create a test database with sample data  
**Usage:**
```bash
python3 scripts/create_test_database.py --num-files 1000
```

---

## False Positive Fixes

These tools fix common false positives in media file corruption detection:

### Video File Fixes

#### `tools/fix_nal_warnings.py`
**Purpose:** Fix H.264/H.265 NAL unit false positives  
**Fixes:** "NAL unit" warnings in video files  

#### `tools/fix_all_nal_warnings.py`
**Purpose:** Batch fix all NAL unit warnings  

#### `tools/reset_nal_files_for_rescan.py`
**Purpose:** Reset NAL-flagged files for re-scanning  

#### `tools/fix_tile_data_false_positives.py`
**Purpose:** Fix HEIF/HEIC tile data warnings  

### Image File Fixes

#### `tools/fix_gif_header_false_positives.py`
**Purpose:** Fix GIF header validation false positives  
**Fixes:** "Invalid GIF header" errors  

#### `tools/fix_webp_exif_false_positives.py`
**Purpose:** Fix WebP EXIF metadata false positives  
**Fixes:** "Invalid WebP EXIF" warnings  

#### `tools/fix_imagemagick_profile_warnings.py`
**Purpose:** Fix ImageMagick color profile warnings  

#### `tools/fix_imagemagick_utf8_errors.py`
**Purpose:** Fix UTF-8 encoding errors in image metadata  

### Analysis Tools

#### `tools/analyze_gif_header_errors.py`
**Purpose:** Analyze patterns in GIF header errors  
**Output:** Statistics and patterns of GIF errors  

#### `tools/analyze_webp_errors.py`
**Purpose:** Analyze WebP file errors and patterns  

### Manual Column Management

#### `tools/add_missing_column_manual.py`
**Purpose:** Manually add missing database columns  

#### `tools/add_warning_columns.py`
**Purpose:** Add warning tracking columns to database  

#### `tools/add_cancel_requested_columns.py`
**Purpose:** Add scan cancellation tracking columns  

---

## Development Scripts

### Local Development

#### `scripts/setup_and_run_local.sh`
**Purpose:** Set up and run PixelProbe locally for development  
**Usage:**
```bash
./scripts/setup_and_run_local.sh
```

---

## Testing Tools

### Media Sample Management

#### `tests/fixtures/media_samples/download_missing_samples.py`
**Purpose:** Download test media samples for testing  
**Usage:**
```bash
python3 tests/fixtures/media_samples/download_missing_samples.py
```

### Test Suites

#### `tests/test_app.py`
**Purpose:** Main application tests  

#### `tests/test_media_checker.py`
**Purpose:** Media checking engine tests  

#### `tests/test_real_media_samples.py`
**Purpose:** Tests with real media files  

#### `tests/test_scheduler.py`
**Purpose:** Scheduled scan tests  

#### `tests/test_bulk_reports.py`
**Purpose:** Bulk reporting functionality tests  

### Integration Tests

- `tests/integration/test_api_endpoints.py` - API endpoint tests
- `tests/integration/test_scan_execution.py` - Scan execution tests
- `tests/integration/test_admin_endpoints.py` - Admin API tests
- `tests/integration/test_maintenance_endpoints.py` - Maintenance API tests

### Unit Tests

- `tests/unit/test_scan_service.py` - Scan service unit tests
- `tests/unit/test_stats_service.py` - Statistics service tests
- `tests/unit/test_repositories.py` - Repository pattern tests

---

## Docker Scripts

### Utility Scripts

#### `tools/delete_files_from_csv.sh`
**Purpose:** Delete files listed in a CSV export  
**Usage:**
```bash
./tools/delete_files_from_csv.sh corrupted_files.csv
```
** Warning:** This permanently deletes files!

#### `tools/reset_nal_files_direct.sh`
**Purpose:** Direct database reset of NAL-flagged files  

---

## Quick Reference

### Most Common Operations

1. **Fix false positives:**
   ```bash
   python3 tools/fix_nal_warnings.py
   python3 tools/fix_gif_header_false_positives.py
   ```

3. **Check database health:**
   ```bash
   python3 scripts/check_db_integrity.py
   ```

4. **Create test database:**
   ```bash
   python3 scripts/create_test_database.py
   ```

5. **Run tests:**
   ```bash
   pytest tests/
   ```

### Environment Variables

Most scripts respect these environment variables:
- `POSTGRES_HOST` - PostgreSQL host (default: localhost)
- `POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `POSTGRES_DB` - Database name (default: pixelprobe)
- `POSTGRES_USER` - Database user (default: pixelprobe)
- `POSTGRES_PASSWORD` - Database password
- `SECRET_KEY` - Flask secret key

### Script Categories

**Production Ready:**
- All migration scripts
- Database integrity checks
- False positive fixes

**Development Only:**
- Test database creators
- Local setup scripts

**Use with Caution:**
- `delete_files_from_csv.sh` - Deletes actual files
- Direct database manipulation scripts

---

## Support

For issues with any script:
1. Check the script's docstring for usage
2. Run with `--help` flag if available
3. Check application logs via the web UI or `GET /api/logs` (logs are stored in the LogEntry database table)
4. Report issues at https://github.com/ttlequals0/PixelProbe/issues