# Migration Guide

## Automatic Migrations (v2.5.57+)

As of v2.5.57, **database migrations run automatically on application startup**. No manual intervention is required.

### How It Works

1. When PixelProbe starts, `app_startup_migration.py` checks the database schema
2. Missing columns, indexes, and tables are created automatically
3. The app proceeds to normal operation

### What Gets Migrated

The automatic migration handles:
- Authentication tables (`users`, `api_tokens`)
- Schema columns (`last_heartbeat`, `last_integrity_check_date`, etc.)
- Performance indexes (composite indexes for common queries)
- Constraint updates

## Migrating from SQLite to PostgreSQL

If you're migrating from SQLite to PostgreSQL, use the one-time migration script:

```bash
python3 tools/migrate_to_postgres.py \
  --sqlite-path /path/to/pixelprobe.db \
  --pg-host localhost \
  --pg-port 5432 \
  --pg-database pixelprobe \
  --pg-user pixelprobe
```

## Troubleshooting

### Database Lock Issues

If migrations fail due to database locks:

1. **Stop the PixelProbe container**:
   ```bash
   docker-compose stop pixelprobe
   ```

2. **Check for blocking connections** (in PostgreSQL):
   ```sql
   SELECT pid, state, query
   FROM pg_stat_activity
   WHERE datname = 'pixelprobe';
   ```

3. **Terminate blocking connections if needed**:
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE datname = 'pixelprobe'
   AND pid <> pg_backend_pid();
   ```

4. **Restart PixelProbe**:
   ```bash
   docker-compose start pixelprobe
   ```

### Manual Column Addition

If automatic migration fails to add a column, you can add it manually:

```sql
-- Connect to database
docker exec -it pixelprobe-postgres-1 psql -U pixelprobe -d pixelprobe

-- Add missing column (example)
ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS last_update TIMESTAMP;
```

### Clean Up Stuck Scans

If scans are stuck after a crash:

```sql
UPDATE scan_state
SET phase = 'crashed',
    is_active = FALSE,
    error_message = 'Cleaned up manually',
    end_time = CURRENT_TIMESTAMP
WHERE is_active = TRUE
AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour';
```

## Environment Variables

The migration system uses these environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | localhost | PostgreSQL host |
| `POSTGRES_PORT` | 5432 | PostgreSQL port |
| `POSTGRES_DB` | pixelprobe | Database name |
| `POSTGRES_USER` | pixelprobe | Database user |
| `POSTGRES_PASSWORD` | - | Database password |

## Support

For migration issues:
1. Check logs: `/app/instance/logs/`
2. Verify database connectivity
3. Report issues at: https://github.com/ttlequals0/PixelProbe/issues
