# Migration Guide: v2.1.42 → v2.2.0

## Overview
PixelProbe v2.2.0 introduces PostgreSQL as the primary database, replacing SQLite for better performance and scalability. This guide helps you migrate your existing installation.

## Key Changes in v2.2.0

### New Features
- **PostgreSQL Database**: 10-50x better performance for large datasets
- **Output Rotation**: Prevents memory leaks during long scans
- **Improved Scheduled Scans**: More reliable background processing
- **Connection Pooling**: Better database connection management

### Environment Variables
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: PostgreSQL connection
- `OUTPUT_ROTATION_ENABLED`: Enable output rotation (default: true)
- `MAX_OUTPUT_SIZE`: Maximum output size before rotation (default: 10000)
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`: Redis for future features

### Removed
- `DATABASE_URL`: Replaced with specific PostgreSQL variables
- `MAX_SCAN_WORKERS`: Now `MAX_WORKERS`

## Migration Steps

### 1. Backup Your Current Data
```bash
# Stop current container
docker stop pixelprobe

# Backup your SQLite database
cp /path/to/your/instance/pixelprobe.db /path/to/your/instance/pixelprobe.db.backup
```

### 2. Update Docker Compose Configuration

Replace your current docker-compose.yml with this updated version:

```yaml
version: '3.8'

services:
  # PostgreSQL database
  postgres:
    image: postgres:15-alpine
    container_name: pixelprobe-postgres
    environment:
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pixelprobe"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - pixelprobe-network
    restart: unless-stopped

  # Redis for future Celery implementation
  redis:
    image: redis:7-alpine
    container_name: pixelprobe-redis
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - pixelprobe-network
    restart: unless-stopped

  # PixelProbe application
  mediachecker:
    image: ttlequals0/pixelprobe:2.2.0
    container_name: pixelprobe
    environment:
      # Security
      SECRET_KEY: ${SECRET_KEY}
      
      # PostgreSQL database configuration
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
      
      # Redis configuration for future Celery
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      
      # Application settings (adapt your existing settings)
      SCAN_PATHS: ${SCAN_PATHS:-/media}
      MAX_WORKERS: ${MAX_WORKERS:-10}
      TZ: ${TZ:-UTC}
      
      # New v2.2.0 features
      OUTPUT_ROTATION_ENABLED: ${OUTPUT_ROTATION_ENABLED:-true}
      MAX_OUTPUT_SIZE: ${MAX_OUTPUT_SIZE:-10000}
      BATCH_SIZE: ${BATCH_SIZE:-100}
      
      # Production environment
      FLASK_ENV: ${FLASK_ENV:-production}
      
    volumes:
      # Your existing media paths (update as needed)
      - /path/to/your/media:/media:ro
      
      # Instance folder for configs (no database files with PostgreSQL)
      - /path/to/your/instance:/app/instance
      
    ports:
      - "${PORT:-5000}:5000"
    
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    
    networks:
      - pixelprobe-network
    
    restart: unless-stopped
    
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 90s
      timeout: 10s
      retries: 3
      start_period: 40s

networks:
  pixelprobe-network:
    driver: bridge

volumes:
  postgres_data:
```

### 3. Set Environment Variables
Create a `.env` file or set environment variables:

```bash
# Required
SECRET_KEY=your-secret-key-here
POSTGRES_PASSWORD=your-secure-password

# Optional - adapt your existing settings
SCAN_PATHS=/movies,/tv,/photos
MAX_WORKERS=8
TZ=America/New_York
```

### 4. Start PostgreSQL
```bash
# Start only PostgreSQL first
docker-compose up -d postgres

# Wait for it to be ready
sleep 15
```

### 5. Run Migration (If You Have Existing Data)
If you have an existing SQLite database to migrate:

```bash
# Get the network name
NETWORK_NAME=$(docker-compose ps -q postgres | xargs docker inspect --format='{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' | head -c 12)

# Run migration from your SQLite database
docker run --rm \
  --network pixelprobe_pixelprobe-network \
  -v "/path/to/your/instance/pixelprobe.db:/app/pixelprobe.db:ro" \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_USER=pixelprobe \
  -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
  -e POSTGRES_DB=pixelprobe \
  ttlequals0/pixelprobe:2.2.0 \
  python migrate_to_postgres.py \
    --sqlite-path /app/pixelprobe.db \
    --pg-host postgres \
    --pg-user pixelprobe \
    --pg-password $POSTGRES_PASSWORD \
    --pg-database pixelprobe
```

### 6. Start Full Application
```bash
# Start all services
docker-compose up -d

# Check health
curl -f http://localhost:5000/health

# Verify version
curl -s http://localhost:5000/api/version
```

## Verification

1. **Check Application Health**: `curl http://localhost:5000/health`
2. **Verify Version**: Should show `2.2.0`
3. **Test Database**: Try accessing your scans and statistics
4. **Check Logs**: `docker-compose logs pixelprobe`

## Performance Benefits

After migration, you should experience:
- **10-50x faster** database operations
- **Better concurrency** for multiple simultaneous scans
- **Reduced memory usage** during long scans (output rotation)
- **More reliable** scheduled scans

## Rollback Plan

If you need to rollback:

1. Stop v2.2.0 containers: `docker-compose down`
2. Restore your original docker-compose.yml
3. Restore SQLite database: `cp pixelprobe.db.backup pixelprobe.db`
4. Start v2.1.42: `docker-compose up -d`

Your SQLite backup will remain available for rollback.

## Troubleshooting

### PostgreSQL Connection Issues
- Ensure `POSTGRES_PASSWORD` is set
- Check PostgreSQL container logs: `docker-compose logs postgres`
- Verify network connectivity: `docker-compose exec pixelprobe ping postgres`

### Migration Issues
- Ensure SQLite database file is readable
- Check migration logs for specific errors
- Verify PostgreSQL has enough disk space

### Performance Issues
- Monitor PostgreSQL performance: `docker stats`
- Adjust `MAX_WORKERS` based on your system
- Enable output rotation if not already enabled

## Support

For issues specific to v2.2.0:
1. Check application logs: `docker-compose logs pixelprobe`
2. Verify environment variables are set correctly
3. Test PostgreSQL connectivity independently