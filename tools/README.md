# PixelProbe Tools Directory

This directory contains utility scripts for database maintenance, false positive fixes, and data analysis.

## Quick Start

Most tools follow this pattern:
```bash
# Dry run (preview changes)
python tools/script_name.py

# Execute changes
python tools/script_name.py --execute
```

## Tool Categories

### 🔧 Database Schema Tools

| Script | Purpose | When to Use |
|--------|---------|------------|
| `fix_database_schema.py` | Comprehensive schema repair | After upgrades, initialization issues |
| `add_warning_columns.py` | Add warning tracking columns | Upgrading from pre-warning versions |
| `add_cancel_requested_columns.py` | Add cancellation tracking | Adding scan cancellation support |
| `add_missing_column_manual.py` | Manually add any missing column | Schema sync issues |

### 🎯 False Positive Fixes

#### Video Files
| Script | Fixes | Pattern |
|--------|-------|---------|
| `fix_nal_warnings.py` | H.264/H.265 NAL unit errors | "NAL unit" in error |
| `fix_all_nal_warnings.py` | Batch fix all NAL warnings | All NAL issues |
| `reset_nal_files_for_rescan.py` | Reset for rescanning | Prepare for new logic |
| `reset_nal_files_v2.py` | Version 2 reset logic | Updated approach |
| `reset_nal_files_direct.sh` | Direct SQL reset | Faster for large datasets |
| `fix_tile_data_false_positives.py` | HEIF/HEIC tile data | "no tile data" errors |

#### Image Files
| Script | Fixes | Pattern |
|--------|-------|---------|
| `fix_gif_header_false_positives.py` | GIF header validation | "Invalid GIF header" |
| `fix_webp_exif_false_positives.py` | WebP EXIF metadata | "Invalid WebP EXIF" |
| `fix_imagemagick_profile_warnings.py` | Color profile warnings | "CorruptImageProfile" |
| `fix_imagemagick_utf8_errors.py` | UTF-8 decode errors | "utf-8 codec can't decode" |
| `reset_imagemagick_utf8_files.py` | Reset UTF-8 flagged files | Prepare for rescan |

### 📊 Analysis Tools

| Script | Purpose | Output |
|--------|---------|--------|
| `analyze_gif_header_errors.py` | Analyze GIF error patterns | Statistics and patterns |
| `analyze_webp_errors.py` | Analyze WebP issues | Error distribution |

### ⚠️ Utility Scripts

| Script | Purpose | Warning |
|--------|---------|---------|
| `delete_files_from_csv.sh` | Delete files from CSV export | **PERMANENTLY DELETES FILES** |
| `fix_tile_data_sql.py` | Direct SQL tile data fix | Faster than Python version |

## Usage Examples

### Fix Common False Positives

```bash
# Fix NAL unit warnings in videos
python tools/fix_nal_warnings.py --execute

# Fix GIF header false positives
python tools/fix_gif_header_false_positives.py --execute

# Fix ImageMagick UTF-8 errors
python tools/fix_imagemagick_utf8_errors.py --execute
```

### Reset Files for Rescanning

```bash
# Reset NAL-flagged videos
python tools/reset_nal_files_for_rescan.py --execute

# Reset UTF-8 error images
python tools/reset_imagemagick_utf8_files.py --execute
```

### Database Maintenance

```bash
# Fix schema issues
python tools/fix_database_schema.py

# Add missing columns
python tools/add_warning_columns.py
python tools/add_cancel_requested_columns.py
```

### Analysis

```bash
# Analyze GIF errors
python tools/analyze_gif_header_errors.py

# Analyze WebP errors
python tools/analyze_webp_errors.py
```

## Docker Usage

Run tools inside the container:

```bash
# Method 1: Direct execution
docker exec pixelprobe python /app/tools/fix_nal_warnings.py --execute

# Method 2: Interactive shell
docker exec -it pixelprobe bash
cd /app
python tools/fix_database_schema.py
```

## Environment Variables

Configure database connection:

```bash
# PostgreSQL (v2.2.0+)
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=pixelprobe
export POSTGRES_USER=pixelprobe
export POSTGRES_PASSWORD=yourpassword

# Run tool
python tools/script_name.py
```

## Safety Guidelines

1. **Always backup your database** before running modification scripts
2. **Run dry-run first** - Don't use `--execute` until you've reviewed the changes
3. **Stop PixelProbe** before running database modifications
4. **Check logs** after running scripts: `/app/instance/logs/`

## Common Patterns

### False Positive Characteristics

**NAL Unit Warnings (Videos)**
- Pattern: "NAL unit" in error message
- Usually: Valid videos with minor encoding quirks
- Fix: Convert to warning state

**GIF Header Issues**
- Pattern: "Invalid GIF header" 
- Usually: Valid GIFs with non-standard headers
- Fix: Mark as healthy if actually playable

**ImageMagick UTF-8**
- Pattern: "utf-8 codec can't decode"
- Usually: Binary metadata in images
- Fix: Convert to warning, not corruption

**WebP EXIF**
- Pattern: "Invalid WebP EXIF"
- Usually: Non-standard EXIF data
- Fix: Mark as warning if image loads

## Script Return Codes

- `0` - Success
- `1` - Error occurred
- `2` - No changes needed (dry run)

## Getting Help

Each script supports:
```bash
# View script documentation
python tools/script_name.py --help

# Dry run mode (default)
python tools/script_name.py

# Verbose output
python tools/script_name.py --verbose
```

## Version Compatibility

| Tool Category | Min Version | Notes |
|--------------|-------------|-------|
| Schema fixes | v2.2.0+ | PostgreSQL support |
| NAL warnings | v1.12+ | Warning state support |
| GIF fixes | v1.25+ | Updated validation |
| WebP fixes | v2.0+ | EXIF handling |

## Support

For issues:
1. Check script output for specific errors
2. Review `/app/instance/logs/` for details
3. Report at: https://github.com/ttlequals0/PixelProbe/issues