# Performance tuning guide

## Environment variables for performance

### Scanning performance
- `CELERY_CONCURRENCY=4` - Number of Celery worker processes (default: 4). This is the main scan-throughput knob: scans are split into chunks that fan out across these workers.
- `MAX_WORKERS=10` - ThreadPoolExecutor threads for parallel file validation within a scan task (default: 10). Distinct from Celery concurrency.
- `BATCH_SIZE=100` - Paths per database lookup batch during file discovery (default: 100). Leave at 100; it does not control scan chunk size, which is automatic (see the chunk table below).
- Freeze detection, its confirmation limits, and the scan timeouts are settings rather than environment variables. Edit them under System > Tunables or through `/api/settings`; a change reaches a running scan without a restart. See [Configuration](configuration.md#scanner-settings).
- The data integrity check is close to free. The allocation gate reuses the block count from the `stat` the scanner already performs, and files that fail it are queried with `SEEK_HOLE`, which reads no file data. A file it marks incomplete skips decoding entirely, which on a large damaged file saves a full-length pass.
- The freeze confirmation pass runs only on files that already produced a candidate, and only over that candidate's own window. Each window is its own ffmpeg process, bounded two ways: a cap on how many run (default 20) and a wall-time budget per file (default 600s). Events past either bound are reported without confirmation rather than dropped.
- `time_budget_minutes` - Per-schedule setting on file-changes schedules. Caps how long a rolling integrity check runs; the next run resumes where the last one stopped.

### Database performance
- `DATABASE_URL` - Deprecated since v2.2.0; use the `POSTGRES_*` variables instead
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

## Chunk sizing

Scans are divided into path-range chunks automatically; chunk size adapts to the number of pending files and is not configurable:

| Pending files | Chunk size |
|---------------|------------|
| <= 100 | 1 chunk (all files) |
| <= 1,000 | 100 files/chunk |
| <= 10,000 | 500 files/chunk |
| > 10,000 | 1,000 files/chunk |

## Database indexes

Indexes on frequently queried columns (`scan_status`, `scan_date`, `is_corrupted`, `marked_as_good`, `file_hash`, `last_modified`) are created automatically by the startup migrations. No manual step is needed; `scripts/create_indexes.py` is a legacy script kept for reference.

## Performance monitoring

### Memory usage
```bash
docker stats pixelprobe-app pixelprobe-celery-worker
```

### Database performance
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "\dt+"
docker exec pixelprobe-postgres psql -U pixelprobe -d pixelprobe -c "\di+"
```

### Scanning performance

The scan-complete log line is emitted by the worker container, not the web app:

```bash
docker logs pixelprobe-celery-worker | grep -i "scan completed"
```

### Worker recycling

Two limits dominate the worker's memory behavior, and both are recycling
thresholds rather than caps on live usage:

- `CELERY_MAX_TASKS_PER_CHILD` (default 1000): a prefork child is replaced
  after processing this many tasks.
- `--max-memory-per-child` (about 1.9 GiB, set in `celery_worker.py`): a
  child that exceeds this resident size is replaced after its current task.

This is why each `CELERY_CONCURRENCY` slot should be budgeted roughly 2 GB
of RAM.

## CPU sizing for video scanning

Freeze detection fully decodes every video, and that decode is the dominant
cost, often more than 90% of per-file scan time. Aggregate scan throughput
is therefore bounded by the host's total decode rate: match
`CELERY_CONCURRENCY` to physical cores rather than oversubscribing. On a
saturated host, raise the sampled window timeout (default 30s) to 60-90
to avoid Stage 2 sample-window timeouts caused by CPU contention rather
than bad files.

## Recommendations for large datasets

### For 1M+ files
- Increase `CELERY_CONCURRENCY` to 8-16 (based on CPU cores)
- Ensure PostgreSQL is tuned for high concurrent workloads
- Use `time_budget_minutes` on file-changes schedules so rolling integrity checks stay within a maintenance window

### For memory-constrained environments
- Reduce `CELERY_CONCURRENCY` to 2
- Reduce `MAX_WORKERS` to 4
- Lower `REDIS_MAX_MEMORY` to 1gb

### For high-performance storage
- Increase `CELERY_CONCURRENCY` based on storage IOPS
- Ensure the database is on fast storage (SSD)
