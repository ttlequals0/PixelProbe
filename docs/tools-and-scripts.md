# PixelProbe tools and scripts

All tools and scripts available in PixelProbe v2.8.0+.

## Table of contents
1. [Database tools](#database-tools)
2. [False positive fixes](#false-positive-fixes)
3. [Development scripts](#development-scripts)
4. [Release tooling](#release-tooling)
5. [Testing tools](#testing-tools)
6. [Shell utilities](#shell-utilities)

---

## Database tools

> **Note:** Database migrations run automatically on application startup via `tools/app_startup_migration.py` and `pixelprobe/migrations/startup.py`. No manual intervention is required.

### Maintenance scripts

#### `tools/fix_database_schema.py`
**Purpose:** Repair missing database tables
**Usage:**
```bash
python3 tools/fix_database_schema.py
```
**When to use:** When tables are missing after a failed initialization. Note: it only repairs missing tables; its attempt to re-run migrations silently no-ops (the functions it imports from `app` no longer exist), which is harmless because migrations already run at every startup.

#### `scripts/fix_database_schema.py`
**Purpose:** Legacy emergency schema fix script (v2.2.46-era column additions)
**When to use:** Rarely; migrations run automatically at startup. Prefer `tools/fix_database_schema.py` for missing-table repair.

#### `tools/app_startup_migration.py`
**Purpose:** Startup migration that adds missing columns and optimizes indexes
**Usage:** Runs automatically at application startup (invoked by `pixelprobe/migrations/startup.py`). Not intended for manual use.

#### `tools/data_retention.py`
**Purpose:** Clean up old data to prevent unbounded database growth (reports after 90 days, completed/failed scan states after 7 days)
**Usage:**
```bash
python3 tools/data_retention.py --dry-run  # preview
python3 tools/data_retention.py            # apply
```
**When to use:** Runs daily via the scheduler; run manually only for an immediate cleanup.

#### `tools/migrate_to_postgres.py`
**Purpose:** One-time migration from SQLite to PostgreSQL (schema creation and data copy)
**Usage:**
```bash
python3 tools/migrate_to_postgres.py
```
**When to use:** Upgrading a pre-v2.2.0 SQLite installation.

#### `tools/fix_scan_concurrency.py`
**Purpose:** Clean up stale scans and enforce single-scan locking
**Usage:**
```bash
python3 tools/fix_scan_concurrency.py
```

#### `scripts/check_db_integrity.py`
**Status:** SQLite-era legacy (reads `DATABASE_PATH`). Not applicable to PostgreSQL deployments.

#### `scripts/create_indexes.py`
**Status:** SQLite-era legacy. Indexes are created automatically at startup on PostgreSQL deployments.

#### `scripts/test_database.py`
**Status:** SQLite-era legacy (reads `DATABASE_PATH`).

#### `scripts/create_test_database.py`
**Purpose:** Create a SQLite test database with sample data (development only, legacy)
**Usage:**
```bash
python3 scripts/create_test_database.py
```

### Manual column management

#### `tools/add_missing_column_manual.py`
**Purpose:** Manually add missing database columns

#### `tools/add_warning_columns.py`
**Purpose:** Add warning tracking columns to database

#### `tools/add_cancel_requested_columns.py`
**Purpose:** Add scan cancellation tracking columns

---

## False positive fixes

These tools fix common false positives in media file corruption detection.

> **Convention:** all false-positive fix tools default to a dry run that only reports what would change. Pass `--execute` to actually write changes.

### Video file fixes

#### `tools/fix_nal_warnings.py`
**Purpose:** Fix H.264/H.265 NAL unit false positives
**Fixes:** "NAL unit" warnings in video files

#### `tools/fix_all_nal_warnings.py`
**Purpose:** Batch fix all NAL unit warnings

#### `tools/reset_nal_files_for_rescan.py`
**Purpose:** Reset NAL-flagged files for re-scanning

#### `tools/reset_nal_files_v2.py`
**Purpose:** Reset NAL-flagged files with retry logic and database lock handling

#### `tools/fix_tile_data_false_positives.py`
**Purpose:** Fix HEIF/HEIC tile data false positives by re-scanning the affected files

#### `tools/fix_tile_data_sql.py`
**Purpose:** Fix HEIF/HEIC tile data false positives via direct SQL (faster for large datasets; does not re-scan files)

### Image file fixes

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

#### `tools/reset_imagemagick_utf8_files.py`
**Purpose:** Reset files flagged by UTF-8 decode errors for re-scanning

### Analysis tools

#### `tools/analyze_gif_header_errors.py`
**Purpose:** Analyze patterns in GIF header errors
**Output:** Statistics and patterns of GIF errors

#### `tools/analyze_webp_errors.py`
**Purpose:** Analyze WebP file errors and patterns

### Incomplete scan diagnostics

#### `scripts/find_incomplete_scans.sql`
**Purpose:** Read-only SQL queries to find files marked completed but missing scan details (shown as "N/A" in the UI)

#### `scripts/find_falsely_healthy_files.sql`
**Purpose:** Read-only SQL queries to find files marked healthy that were never actually scanned

#### `scripts/reset_incomplete_scans.py`
**Purpose:** Reset files marked completed but missing scan data (shown as "N/A" in the UI); supports `--dry-run`
**When to use:** Prefer the equivalent API endpoint, which needs no database access from the host:
```bash
curl -X POST http://localhost:5000/api/reset-incomplete-scans \
  -H "Authorization: Bearer $TOKEN"
```

---

## Development scripts

### Local development

#### `scripts/setup_and_run_local.sh`
**Purpose:** Set up and run PixelProbe locally for development
**Usage:**
```bash
./scripts/setup_and_run_local.sh
```

#### `scripts/setup_test_env.sh`
**Purpose:** Set up the virtual environment and test database for running tests
**Usage:**
```bash
./scripts/setup_test_env.sh
```

---

## Release tooling

See [release-process.md](release-process.md) for the full release workflow.

#### `scripts/changelog_section.py`
**Purpose:** Extract a version's release notes from `CHANGELOG.MD`; used by `publish_release.sh` so the release page and the changelog cannot disagree
**Usage:**
```bash
python3 scripts/changelog_section.py 2.8.0
```

#### `scripts/publish_release.sh`
**Purpose:** Tag a shipped version and publish it as a GitHub release, with notes taken from `CHANGELOG.MD`
**Usage:**
```bash
scripts/publish_release.sh 2.8.0 [--dry-run]
```

---

## Testing tools

### Media sample management

#### `tests/fixtures/media_samples/download_missing_samples.py`
**Purpose:** Download test media samples for testing
**Usage:**
```bash
python3 tests/fixtures/media_samples/download_missing_samples.py
```

### Test suites

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

### Integration tests

- `tests/integration/test_api_endpoints.py` - API endpoint tests
- `tests/integration/test_scan_execution.py` - Scan execution tests
- `tests/integration/test_admin_endpoints.py` - Admin API tests
- `tests/integration/test_maintenance_endpoints.py` - Maintenance API tests

### Unit tests

- `tests/unit/test_scan_service.py` - Scan service unit tests
- `tests/unit/test_stats_service.py` - Statistics service tests
- `tests/unit/test_repositories.py` - Repository pattern tests

---

## Shell utilities

#### `tools/delete_files_from_csv.sh`
**Purpose:** Delete files listed in a CSV export
**Usage:**
```bash
./tools/delete_files_from_csv.sh corrupted_files.csv
```
**Warning:** This permanently deletes files!

#### `tools/reset_nal_files_direct.sh`
**Purpose:** Direct database reset of NAL-flagged files

---

## Quick reference

### Most common operations

1. **Fix false positives** (dry run first, then `--execute`):
   ```bash
   python3 tools/fix_nal_warnings.py            # preview
   python3 tools/fix_nal_warnings.py --execute  # apply

   python3 tools/fix_gif_header_false_positives.py
   python3 tools/fix_gif_header_false_positives.py --execute
   ```

2. **Vacuum the database:**
   ```bash
   curl -X POST http://localhost:5000/api/maintenance/vacuum \
     -H "Authorization: Bearer $TOKEN"
   ```

3. **Reset incomplete scans:**
   ```bash
   curl -X POST http://localhost:5000/api/reset-incomplete-scans \
     -H "Authorization: Bearer $TOKEN"
   ```

4. **Run tests:**
   ```bash
   pytest tests/
   ```

### Environment variables

Tools that import the application (everything under `tools/` plus most of `scripts/`) use the standard PostgreSQL settings:
- `POSTGRES_HOST` - PostgreSQL host (default: localhost)
- `POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `POSTGRES_DB` - Database name (default: pixelprobe)
- `POSTGRES_USER` - Database user (default: pixelprobe)
- `POSTGRES_PASSWORD` - Database password
- `SECRET_KEY` - Flask secret key

Legacy exception: `scripts/check_db_integrity.py`, `scripts/create_indexes.py`, and `scripts/test_database.py` are SQLite-era scripts that read a SQLite path from the environment (`DATABASE_PATH`, or `DATABASE_URL` for `create_indexes.py`). They do not work against PostgreSQL.

### Script categories

**Production use:**
- Database maintenance tools (`tools/fix_database_schema.py`, `tools/data_retention.py`)
- False positive fixes (dry-run by default; back up the database before `--execute`)

**Development only:**
- Test database creators (SQLite legacy)
- Local setup scripts

**Use with caution:**
- `delete_files_from_csv.sh` - Deletes actual files
- Direct database manipulation scripts

---

## Support

For issues with any script:
1. Check the script's docstring for usage
2. Run with `--help` flag if available
3. Check application logs via the web UI or `GET /api/logs` (logs are stored in the LogEntry database table); `GET /api/logs/runs` lists scan runs and `GET /api/logs/download` exports logs
4. Report issues at https://github.com/ttlequals0/PixelProbe/issues
