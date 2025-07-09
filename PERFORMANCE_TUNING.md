# Performance Tuning Guide

## Environment Variables for Performance

### Scanning Performance
- `MAX_SCAN_WORKERS=4` - Number of parallel scan workers (default: 4)
- `BATCH_COMMIT_SIZE=50` - Number of scan results to commit in one batch (default: 50)
- `RESET_BATCH_SIZE=500` - Batch size for reset operations (default: 500)
- `SYSTEM_INFO_TIMEOUT=30` - Timeout for system info operations (default: 30 seconds)

### Database Performance
- `DATABASE_URL=sqlite:///media_checker.db` - Database connection string
- SQLite connection pooling is automatically configured with:
  - Pool pre-ping: enabled
  - Pool recycle: 300 seconds
  - Connection timeout: 15 seconds

## Performance Improvements Made

### 1. Database Indexes
Added indexes on frequently queried columns (created automatically on startup):
- `scan_status` - For filtering by scan status
- `scan_date` - For sorting by scan date
- `is_corrupted` - For filtering corrupted files
- `marked_as_good` - For filtering marked files
- `file_hash` - For duplicate detection
- `last_modified` - For change detection

Indexes are created automatically when the application starts. For existing installations, you can also run `python create_indexes.py` manually.

### 2. Batch Processing Optimization
- Reduced batch size from 1000 to 500 for better memory usage
- Added configurable batch sizes via environment variables
- Improved commit frequency to prevent long-running transactions

### 3. Memory Usage Optimization
- Truncated scan output to prevent memory issues (max 100 lines, 5000 chars)
- Used bulk database operations instead of loading all records into memory
- Optimized reset operations to use database-level updates

### 4. Query Optimization
- Replaced individual record updates with bulk operations
- Added proper indexing for common query patterns
- Optimized reset operations to use single UPDATE statements

## Performance Monitoring

### Memory Usage
Monitor the container's memory usage during resets:
```bash
docker stats pixelprobe
```

### Database Performance
Check database size and indexes:
```bash
sqlite3 media_checker.db ".schema"
sqlite3 media_checker.db ".indexes"
```

### Scanning Performance
Monitor scan throughput via logs:
```bash
docker logs pixelprobe | grep "Scan completed"
```

## Recommendations for Large Datasets

### For 1M+ Files
- Increase `MAX_SCAN_WORKERS` to 8-16 (based on CPU cores)
- Set `RESET_BATCH_SIZE` to 1000-2000
- Consider using PostgreSQL instead of SQLite for better concurrent performance

### For Memory-Constrained Environments
- Reduce `MAX_SCAN_WORKERS` to 2-4
- Set `RESET_BATCH_SIZE` to 200-300
- Set `BATCH_COMMIT_SIZE` to 25-30

### For High-Performance Storage
- Increase `MAX_SCAN_WORKERS` based on storage IOPS
- Consider using tmpfs for temporary files during scanning
- Ensure database is on fast storage (SSD)

## Database Migration

If you're upgrading from a previous version, run the index creation script:
```bash
python create_indexes.py
```

This will add performance indexes without affecting existing data.