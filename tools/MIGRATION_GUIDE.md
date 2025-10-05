# Migration Guide - Resolving Database Lock Issues

## Problem: Migration Script Hanging with "lock not available"

When you see this error:
```
2025-08-24 03:18:48.419 UTC [21995] LOG: skipping analyze of "scan_state" --- lock not available
```

The database has active connections that are holding locks on tables we need to modify.

## Solution: Use the Safe Migration Script

### Step 1: Stop the PixelProbe Container
First, stop the PixelProbe container to prevent new connections:

```bash
docker-compose stop pixelprobe
```

### Step 2: Run the Safe Migration Script

#### Option A: Run from Host (Recommended)
```bash
# Set environment variables
export POSTGRES_HOST=localhost  # or your postgres host
export POSTGRES_PORT=5432
export POSTGRES_DB=pixelprobe
export POSTGRES_USER=pixelprobe
export POSTGRES_PASSWORD=your-password-here

# Run the safe migration
python3 tools/migrate_db_safe.py
```

#### Option B: Run Inside PostgreSQL Container
```bash
# Copy script to postgres container
docker cp tools/migrate_db_safe.py pixelprobe-postgres-1:/tmp/

# Run inside container
docker exec -it pixelprobe-postgres-1 bash
cd /tmp
python3 migrate_db_safe.py
```

### Step 3: Handle Blocking Connections

The script will:
1. Check for blocking connections
2. Show you what's blocking (PIDs, queries)
3. Ask if you want to terminate them

Example output:
```
Checking for blocking connections...
Found 2 potentially blocking connections
  PID 12345: active - SELECT * FROM scan_state WHERE...
  PID 12346: idle in transaction - BEGIN

Terminate blocking connections? (y/n): y
Terminated blocking connections
```

### Step 4: Verify Migration

The script will show verification results:
```
5. Verifying migration...
    scan_state.last_update: OK
    scan_chunks.files_processed: OK

 Migration completed successfully!
```

### Step 5: Restart PixelProbe

```bash
docker-compose start pixelprobe
```

## If Migration Still Fails

### Manual SQL Commands

If the script can't apply changes due to persistent locks, run these manually when the database is idle:

```sql
-- Connect to database
docker exec -it pixelprobe-postgres-1 psql -U pixelprobe -d pixelprobe

-- Add missing columns
ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS last_update TIMESTAMP;
ALTER TABLE scan_chunks ADD COLUMN IF NOT EXISTS files_processed INTEGER DEFAULT 0;

-- Update existing data
UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL;
UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0;

-- Clean up stuck scans
UPDATE scan_state 
SET phase = 'crashed',
    is_active = FALSE,
    error_message = 'Cleaned up manually',
    end_time = CURRENT_TIMESTAMP
WHERE is_active = TRUE 
AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour';
```

## Preventing Future Lock Issues

1. **Always stop PixelProbe before migrations**:
   ```bash
   docker-compose stop pixelprobe
   ```

2. **Check for active scans before stopping**:
   - Visit the web UI
   - Ensure no scans are running
   - Wait for any active scans to complete

3. **Use the safe migration script** instead of the regular one - it handles locks gracefully

## Troubleshooting

### "psql: command not found"
Use the Python migration scripts instead of bash scripts.

### "Connection refused"
Check that PostgreSQL is running:
```bash
docker-compose ps postgres
```

### "FATAL: password authentication failed"
Verify your POSTGRES_PASSWORD environment variable matches your docker-compose.yml.

### Database is completely locked
As a last resort, restart PostgreSQL:
```bash
docker-compose restart postgres
```
Then immediately run the migration before PixelProbe reconnects.