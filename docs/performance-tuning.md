# Performance tuning guide

Configuration values in this guide are operator starting points, not measured performance guarantees. Validate memory, CPU, database connections, and media storage behavior on the target library before increasing concurrency.

## Environment variables for performance

### Scanning performance
- `CELERY_CONCURRENCY=4` - Number of Celery worker processes (default: 4). This is the main scan-throughput knob: scans are split into chunks that fan out across these workers.
- `MAX_WORKERS=10` - Upper bound on thread workers accepted by the selected-file scan API. The API defaults to 4 workers and clamps larger requests to this limit. Distinct from Celery process count.
- `BATCH_SIZE=100` - Legacy media-checker discovery lookup batch. Leave at 100; it does not control parallel discovery inserts or scan chunk commits.
- Freeze detection, its confirmation limits, and scan timeouts are saved in the database. Edit them under System > Tunables or through `/api/settings`. Workers refresh cached values within 60 seconds. Selected-file scans snapshot settings for each submitted file; other checks use cached values at each setting lookup, so an edit is not guaranteed to change a file already in progress. See [Configuration](configuration.md#scanner-settings).
- The data integrity check is close to free. The allocation gate reuses the block count from the `stat` the scanner already performs, and files that fail it are queried with `SEEK_HOLE`, which reads no file data. A file it marks incomplete skips decoding entirely, which on a large damaged file saves a full-length pass.
- Freeze corroboration runs only on files that already produced a candidate. Each event costs one packet probe (a fraction of a second, no decode) and, when the packets are present, one decode of just that window. At most 12 events per file are probed; the rest fall through to the uncorroborated path.
- `time_budget_minutes` - Per-schedule setting on file-changes schedules. Caps how long a rolling integrity check runs; the next run resumes where the last one stopped.

### Database performance
- `DATABASE_URL` - Deprecated compatibility override. If explicitly set, it takes precedence over `POSTGRES_*`; the bundled Compose file does not pass it.
- `DB_POOL_SIZE=5` - SQLAlchemy connection pool size per process (default: 5)
- `DB_MAX_OVERFLOW=10` - Extra connections beyond the pool (default: 10)
- PostgreSQL connection pooling is automatically configured with:
  - Pool pre-ping: enabled
  - Pool recycle: 3600 seconds
  - Pool timeout: 30 seconds
  - Connection timeout: 10 seconds

Keep pool math under PostgreSQL `max_connections` (default 100): 4 gunicorn workers x (5 + 10) = 60 max for the web app, plus `CELERY_CONCURRENCY` worker children and selected-scan connections. `CELERY_CONCURRENCY` sets Celery processes; `MAX_WORKERS` caps selected-file scan threads.

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

Two limits dominate the worker's memory behavior, and both are recycling thresholds rather than caps on live usage:

- `CELERY_MAX_TASKS_PER_CHILD` (default 1000): a prefork child is replaced
  after processing this many tasks.
- `--max-memory-per-child` (about 1.9 GiB, set in `celery_worker.py`): a
  child that exceeds this resident size is replaced after its current task.

This is why each `CELERY_CONCURRENCY` slot should be budgeted roughly 2 GB of RAM.

## CPU sizing for video scanning

Freeze detection fully decodes every video, and that decode is the dominant cost, often more than 90% of per-file scan time. Aggregate scan throughput is therefore bounded by the host's total decode rate: match `CELERY_CONCURRENCY` to physical cores rather than oversubscribing. On a saturated host, raise the sampled window timeout (default 30s) to 60-90 to avoid Stage 2 sample-window timeouts caused by CPU contention rather than bad files.

## Recommendations for large datasets

### For 1M+ files
- Increase `CELERY_CONCURRENCY` to 8-16 (based on CPU cores)
- Ensure PostgreSQL is tuned for high concurrent workloads
- Use `time_budget_minutes` on file-changes schedules so rolling integrity checks stay within a maintenance window

### For memory-constrained environments
- Reduce `CELERY_CONCURRENCY` to 2
- Reduce `MAX_WORKERS` to 4 when selected-file scans use many threads
- Lower `REDIS_MAX_MEMORY` to 1gb

### For high-performance storage
- Increase `CELERY_CONCURRENCY` based on storage IOPS
- Ensure the database is on fast storage (SSD)
