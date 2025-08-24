# Docker Setup Guide

## Quick Start

This guide explains the Docker Compose setup for PixelProbe v2.2.47 and what each container does.

## Container Overview

PixelProbe uses 4 main containers:

| Container | Purpose | Ports | Dependencies |
|-----------|---------|-------|--------------|
| **postgres** | Database storage | 5432 | None |
| **redis** | Message queue | 6379 | None |
| **mediachecker** | Web UI & API | 5000 | postgres, redis |
| **celery-worker** | Background processing | None | postgres, redis |

## Complete Docker Compose File

```yaml
version: '3.8'

services:
  # PostgreSQL Database - Stores all scan results and metadata
  postgres:
    image: postgres:15-alpine
    container_name: pixelprobe-postgres
    environment:
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"  # Optional: expose for external tools
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pixelprobe"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  # Redis - Message broker for Celery task queue
  redis:
    image: redis:7-alpine
    container_name: pixelprobe-redis
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"  # Optional: expose for monitoring
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  # Main Web Application - Serves UI and API
  mediachecker:
    image: ttlequals0/pixelprobe:2.2.47
    container_name: pixelprobe-web
    ports:
      - "5000:5000"  # Required: web interface access
    environment:
      # Database Configuration
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      
      # Redis Configuration
      REDIS_HOST: redis
      REDIS_PORT: 6379
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      
      # Application Settings
      SECRET_KEY: ${SECRET_KEY}
      MAX_WORKERS: 10
      BATCH_SIZE: 100
      OUTPUT_ROTATION_ENABLED: true
      
      # Timezone (optional)
      TZ: America/New_York
    volumes:
      # Media directories (read-only for safety)
      - /path/to/media:/media:ro
      - /path/to/photos:/photos:ro
      - /path/to/videos:/videos:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

  # Celery Worker - Processes scan tasks in parallel
  celery-worker:
    image: ttlequals0/pixelprobe:2.2.47
    container_name: pixelprobe-celery
    command: celery -A celery_app worker --loglevel=info --concurrency=8
    environment:
      # Database Configuration
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      
      # Redis Configuration
      REDIS_HOST: redis
      REDIS_PORT: 6379
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/0
      
      # Worker Settings
      CELERY_WORKERS: 8  # Number of parallel workers
      MAX_WORKERS: 10
      BATCH_SIZE: 100
      
      # Timezone (optional)
      TZ: America/New_York
    volumes:
      # Same media directories as web container
      - /path/to/media:/media:ro
      - /path/to/photos:/photos:ro
      - /path/to/videos:/videos:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    deploy:
      # Resource limits (adjust based on your system)
      resources:
        limits:
          cpus: '4'
          memory: 4G
        reservations:
          cpus: '2'
          memory: 2G
    restart: unless-stopped

volumes:
  postgres_data:
    driver: local

networks:
  default:
    name: pixelprobe-network
    driver: bridge
```

## Environment Variables (.env file)

Create a `.env` file in the same directory as your `docker-compose.yml`:

```bash
# Required secrets
POSTGRES_PASSWORD=your-secure-database-password-here
SECRET_KEY=your-secret-key-for-sessions-here

# Optional settings
TZ=America/New_York
CELERY_WORKERS=8
MAX_WORKERS=10
BATCH_SIZE=100
```

## Container Responsibilities

### PostgreSQL Container
- **What it does**: Stores all persistent data
- **Stores**:
  - Scan results and metadata
  - File corruption status
  - User configurations
  - Scan history
  - Scheduled scan settings
- **Why needed**: Provides reliable, ACID-compliant data storage

### Redis Container
- **What it does**: Manages task queue and caching
- **Handles**:
  - Celery task messages
  - Worker coordination
  - Result caching
  - Progress updates
- **Why needed**: Enables parallel processing and real-time updates

### Web Application Container
- **What it does**: Serves the user interface and API
- **Provides**:
  - Web dashboard at port 5000
  - REST API endpoints
  - User authentication
  - Real-time scan progress
  - Scheduled scan management
- **Why needed**: Primary interface for users to interact with the system

### Celery Worker Container
- **What it does**: Performs the actual file scanning
- **Executes**:
  - Media file corruption detection
  - Parallel file discovery
  - Batch processing
  - Cleanup operations
- **Tools used**:
  - FFmpeg for video/audio
  - ImageMagick for images
  - Python PIL for additional image processing
- **Why needed**: Handles CPU-intensive scanning tasks in parallel

## Scaling Options

### Adding More Workers

To increase scanning speed, you can run multiple worker containers:

```yaml
celery-worker:
  # ... existing configuration ...
  deploy:
    replicas: 3  # Run 3 worker containers
```

Or increase concurrency in a single container:

```yaml
environment:
  CELERY_WORKERS: 16  # Increase from 8 to 16 workers
```

### Performance Tuning

Adjust these settings based on your system:

| Setting | Default | Description | Recommendation |
|---------|---------|-------------|----------------|
| CELERY_WORKERS | 8 | Parallel workers per container | Set to CPU cores |
| MAX_WORKERS | 10 | Max concurrent operations | 1.5x CPU cores |
| BATCH_SIZE | 100 | Files per processing chunk | 50-200 based on file sizes |
| postgres memory | - | Database cache | 25% of system RAM |
| redis maxmemory | 256mb | Cache size | 512mb-1gb for large libraries |

## Volume Mounts

### Media Directories

Mount your media directories as **read-only** to prevent accidental modifications:

```yaml
volumes:
  - /media/movies:/movies:ro
  - /media/tv:/tv:ro
  - /media/photos:/photos:ro
  - /media/music:/music:ro
```

### Database Persistence

The PostgreSQL data is stored in a named volume:

```yaml
volumes:
  postgres_data:  # Persists across container restarts
```

To backup:
```bash
docker exec pixelprobe-postgres pg_dump -U pixelprobe pixelprobe > backup.sql
```

## Network Communication

All containers communicate on an internal Docker network:

```
mediachecker:5000 ←→ redis:6379 ←→ celery-worker
       ↓                              ↓
       └────→ postgres:5432 ←────────┘
```

- Web app submits tasks to Redis
- Workers pull tasks from Redis
- Both web and workers write to PostgreSQL
- Redis coordinates everything

## Starting the System

1. Create your `.env` file with passwords
2. Update volume mounts to your media paths
3. Start the system:

```bash
# Start all containers
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f

# Stop all containers
docker-compose down
```

## Monitoring

### Check Worker Status
```bash
docker exec pixelprobe-celery celery -A celery_app inspect active
```

### View Queue Length
```bash
docker exec pixelprobe-redis redis-cli LLEN celery
```

### Database Connections
```bash
docker exec pixelprobe-postgres psql -U pixelprobe -c "SELECT count(*) FROM pg_stat_activity;"
```

## Troubleshooting

### Workers Not Processing
1. Check Redis is running: `docker-compose ps redis`
2. Check worker logs: `docker-compose logs celery-worker`
3. Verify queue: `docker exec pixelprobe-redis redis-cli LLEN celery`

### Database Connection Issues
1. Check PostgreSQL is healthy: `docker-compose ps postgres`
2. Test connection: `docker exec pixelprobe-postgres pg_isready`
3. Check password in `.env` file

### High Memory Usage
1. Reduce CELERY_WORKERS
2. Lower BATCH_SIZE
3. Enable OUTPUT_ROTATION_ENABLED
4. Increase redis maxmemory limit

## Security Considerations

1. **Use strong passwords** in `.env` file
2. **Mount media as read-only** (`:ro` flag)
3. **Don't expose ports** unless needed (remove `ports:` sections)
4. **Use firewall** if exposing ports
5. **Regular updates**: Pull latest images periodically

## Backup Strategy

### Database Backup
```bash
# Backup
docker exec pixelprobe-postgres pg_dump -U pixelprobe pixelprobe | gzip > backup_$(date +%Y%m%d).sql.gz

# Restore
gunzip < backup_20250823.sql.gz | docker exec -i pixelprobe-postgres psql -U pixelprobe pixelprobe
```

### Configuration Backup
```bash
# Save docker-compose and env
tar -czf pixelprobe_config_$(date +%Y%m%d).tar.gz docker-compose.yml .env
```

## Upgrade Process

1. Backup database
2. Stop containers: `docker-compose down`
3. Update image version in `docker-compose.yml`
4. Pull new image: `docker-compose pull`
5. Start containers: `docker-compose up -d`
6. Check logs: `docker-compose logs -f`