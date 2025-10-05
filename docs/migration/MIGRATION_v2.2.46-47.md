# Migration Guide: v2.2.45 → v2.2.46/47

## Critical Database Schema Updates Required

###  IMPORTANT: Database Migration Required
Version 2.2.46 and 2.2.47 require database schema updates before the application will function properly. These versions add critical database columns and connection improvements.

## Migration Steps

### Step 1: Apply Database Schema Updates

**Option A: Using the Migration Script (Recommended)**
```bash
# Download and run the migration script
wget https://raw.githubusercontent.com/ttlequals0/PixelProbe/v2.2.46/apply_v2_2_46_migration.sh
chmod +x apply_v2_2_46_migration.sh
./apply_v2_2_46_migration.sh
```

**Option B: Manual SQL Migration**
```bash
# Connect to your PostgreSQL database
docker exec -it pixelprobe-postgres-1 psql -U pixelprobe -d pixelprobe

# Run these SQL commands:
ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS last_update TIMESTAMP;
UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL;

ALTER TABLE scan_chunks ADD COLUMN IF NOT EXISTS files_processed INTEGER DEFAULT 0;
UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0;

# Clean up any stuck scans
UPDATE scan_state 
SET phase = 'crashed', is_active = FALSE, end_time = CURRENT_TIMESTAMP
WHERE is_active = TRUE 
AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled');

\q
```

### Step 2: Update Docker Images

Update your `docker-compose.yml`:
```yaml
services:
  mediachecker:
    image: ttlequals0/pixelprobe:2.2.47  # Updated from 2.2.45
    
  celery-worker:
    image: ttlequals0/pixelprobe:2.2.47  # Updated from 2.2.45
```

### Step 3: Restart Containers

```bash
# Stop current containers
docker-compose down

# Pull new images
docker-compose pull

# Start with new version
docker-compose up -d

# Verify logs
docker-compose logs -f
```

## What's Fixed in v2.2.46

### Database Schema Issues
- Added missing `last_update` column to `scan_state` table
- Added missing `files_processed` column to `scan_chunks` table
- Created migration scripts for production database updates

### Transaction Management
- Fixed PostgreSQL "lost synchronization with server" errors
- Improved transaction state management in Celery workers
- Added proper session rollback handling

## What's Fixed in v2.2.47

### Connection Reliability
- Automatic recovery from database connection losses
- Connection pool with pre-ping and recycling
- Retry logic with exponential backoff

### Query Issues
- Fixed "ResourceClosedError: This result object does not return rows"
- Fixed "NoSuchColumnError: Could not locate column in row for column 'count(*)'"
- Improved query result handling

### UI Updates
- Fixed scan progress not updating during phase 3
- Resolved all scan status display issues

## Troubleshooting

### If you see "column does not exist" errors:
The database migration hasn't been applied. Run the migration script from Step 1.

### If you see "lost synchronization" errors:
1. Restart the PostgreSQL container: `docker restart pixelprobe-postgres-1`
2. Then restart the app containers: `docker-compose restart`

### If scans appear stuck:
The migration script will automatically clean up stuck scans. If issues persist:
```sql
UPDATE scan_state SET is_active = FALSE, phase = 'crashed' 
WHERE is_active = TRUE;
```

## Rollback Instructions

If you need to rollback to v2.2.45:
```bash
# Update docker-compose.yml to use 2.2.45
sed -i 's/2.2.47/2.2.45/g' docker-compose.yml

# Restart with previous version
docker-compose down
docker-compose up -d
```

Note: The database schema changes are backward compatible, so no schema rollback is needed.

## Support

For issues or questions:
- GitHub Issues: https://github.com/ttlequals0/PixelProbe/issues
- Check logs: `docker-compose logs pixelprobe`
- Database logs: `docker-compose logs postgres`