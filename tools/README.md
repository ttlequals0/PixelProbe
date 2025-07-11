# PixelProbe Utility Tools

This directory contains utility scripts for maintenance and migration tasks. These scripts are designed to be run directly against the PixelProbe database for specific fixes or updates.

## Scripts

### Database Migration Scripts

#### `add_warning_columns.py`
**Purpose**: Adds the warning state columns (`has_warnings` and `warning_details`) to the database schema.

**When to use**: 
- When upgrading from a version before warnings were introduced
- If the database is missing these columns for any reason

**Usage**:
```bash
python add_warning_columns.py
```

### False Positive Fix Scripts

#### `fix_tile_data_false_positives.py`
**Purpose**: Fixes files incorrectly marked as corrupted due to PIL "Image has no tile data" errors.

**When to use**: 
- After upgrading to version 1.11+
- When you have images marked as corrupted with "Image has no tile data" error

**Usage**:
```bash
# Dry run (shows what would be fixed)
python fix_tile_data_false_positives.py

# Execute fixes
python fix_tile_data_false_positives.py --execute
```

#### `fix_tile_data_sql.py`
**Purpose**: Direct SQL approach to fix tile data false positives (alternative to the Python script).

**When to use**: 
- When the Python script approach is too slow
- For large databases where SQL updates are more efficient

**Usage**:
```bash
python fix_tile_data_sql.py
```

#### `fix_imagemagick_profile_warnings.py`
**Purpose**: Fixes files incorrectly marked as corrupted due to ImageMagick "CorruptImageProfile" warnings.

**When to use**: 
- After upgrading to version 1.11+
- When you have images marked as corrupted with "CorruptImageProfile" warnings

**Usage**:
```bash
# Dry run
python fix_imagemagick_profile_warnings.py

# Execute fixes
python fix_imagemagick_profile_warnings.py --execute
```

#### `fix_nal_unit_false_positives.py`
**Purpose**: Fixes video files incorrectly marked as corrupted due to FFmpeg NAL unit errors.

**When to use**: 
- After upgrading to version 1.11+
- When you have videos marked as corrupted with NAL unit errors that actually play fine

**Usage**:
```bash
# Dry run
python fix_nal_unit_false_positives.py

# Execute fixes
python fix_nal_unit_false_positives.py --execute
```

#### `reset_nal_files_for_rescan.py`
**Purpose**: Resets files with NAL unit errors back to pending status for rescanning with the new warning logic.

**When to use**: 
- After upgrading to version 1.12+ which introduced warning states
- When you want NAL unit errors to be treated as warnings instead of corruption

**Usage**:
```bash
# Dry run
python reset_nal_files_for_rescan.py

# Execute reset
python reset_nal_files_for_rescan.py --execute
```

#### `fix_imagemagick_utf8_errors.py`
**Purpose**: Converts files marked as corrupted due to ImageMagick UTF-8 decode errors to warnings.

**When to use**: 
- When you have images marked as corrupted with "utf-8 codec can't decode" errors
- These are false positives - the images are valid but contain binary metadata

**Usage**:
```bash
# Dry run
python fix_imagemagick_utf8_errors.py

# Execute fixes
python fix_imagemagick_utf8_errors.py --execute
```

#### `reset_imagemagick_utf8_files.py`
**Purpose**: Resets files with ImageMagick UTF-8 decode errors to pending for rescanning.

**When to use**: 
- After upgrading to version 1.25+ which properly handles UTF-8 decode errors
- When you want these files rescanned with the updated logic

**Usage**:
```bash
# Dry run
python reset_imagemagick_utf8_files.py

# Execute reset
python reset_imagemagick_utf8_files.py --execute
```

## Important Notes

1. **Always backup your database** before running any of these scripts
2. **Run in dry-run mode first** (without `--execute`) to see what changes will be made
3. **Stop the PixelProbe server** before running database modification scripts
4. These scripts assume the default database location (`instance/media_checker.db`)
5. For Docker installations, run these scripts inside the container:
   ```bash
   docker exec -it <container_name> python /app/tools/<script_name>.py
   ```

## Database Path

If your database is not in the default location, set the `DATABASE_PATH` environment variable:

```bash
DATABASE_PATH=/path/to/your/media_checker.db python <script_name>.py
```