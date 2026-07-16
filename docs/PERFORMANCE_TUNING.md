# Performance Tuning Guide

## Environment Variables for Performance

### Scanning Performance
- `CELERY_CONCURRENCY=4` - Number of Celery worker processes (default: 4). This is the main scan-throughput knob: scans are split into chunks that fan out across these workers.
- `MAX_WORKERS=10` - ThreadPoolExecutor threads for parallel file validation within a scan task (default: 10). Distinct from Celery concurrency.
- `BATCH_SIZE=100` - Number of scan results committed per batch (default: 100).
- `FREEZE_DETECTION_ENABLED=true` - Toggle video freeze-frame detection (default: true). Disabling it speeds up video scans at the cost of coverage.
- `time_budget_minutes` - Per-schedule setting on file-changes schedules. Caps how long a rolling integrity check runs; the next run resumes where the last one stopped.

### Database Performance
- `DATABASE_URL=postgresql://user:pass@host/pixelprobe` - Database connection string
- `DB_POOL_SIZE=5` - SQLAlchemy connection pool size per process (default: 5)
- `DB_MAX_OVERFLOW=10` - Extra connections beyond the pool (default: 10)
- PostgreSQL connection pooling is automatically configured with:
  - Pool pre-ping: enabled
  - Pool recycle: 3600 seconds
  - Pool timeout: 30 seconds
  - Connection timeout: 10 seconds

Keep pool math under PostgreSQL `max_connections` (default 100): 4 gunicorn workers x (5 + 10) = 60 max for the web app, plus Celery children and checker connections.

### Redis
- `REDIS_MAX_MEMORY=2gb` - Memory limit for the Redis/Valkey task queue (default: 2gb, recommended: 1-4gb).

## Chunk Sizing

Scans are divided into path-range chunks automatically; chunk size adapts to the number of pending files and is not configurable:

| Pending files | Chunk size |
|---------------|------------|
| <= 100 | 1 chunk (all files) |
| <= 1,000 | 100 files/chunk |
| <= 10,000 | 500 files/chunk |
| > 10,000 | 1,000 files/chunk |

## Database Indexes

Indexes on frequently queried columns (`scan_status`, `scan_date`, `is_corrupted`, `marked_as_good`, `file_hash`, `last_modified`) are created automatically by the startup migrations. No manual step is needed; `scripts/create_indexes.py` is a legacy script kept for reference.

## Performance Monitoring

### Memory Usage
```bash
docker stats pixelprobe
```

### Database Performance
```bash
psql $DATABASE_URL -c "\dt+"
psql $DATABASE_URL -c "\di+"
```

### Scanning Performance
```bash
docker logs pixelprobe | grep "Scan completed"
```

## Recommendations for Large Datasets

### For 1M+ Files
- Increase `CELERY_CONCURRENCY` to 8-16 (based on CPU cores)
- Ensure PostgreSQL is tuned for high concurrent workloads
- Use `time_budget_minutes` on file-changes schedules so rolling integrity checks stay within a maintenance window

### For Memory-Constrained Environments
- Reduce `CELERY_CONCURRENCY` to 2
- Reduce `MAX_WORKERS` to 4
- Lower `REDIS_MAX_MEMORY` to 1gb

### For High-Performance Storage
- Increase `CELERY_CONCURRENCY` based on storage IOPS
- Ensure the database is on fast storage (SSD)
