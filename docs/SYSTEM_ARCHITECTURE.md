# PixelProbe System Architecture

## Table of Contents
1. [Overview](#overview)
2. [Container Architecture](#container-architecture)
3. [Celery Queue System](#celery-queue-system)
4. [Data Flow](#data-flow)
5. [Container Interactions](#container-interactions)
6. [Scaling Strategy](#scaling-strategy)

## Overview

PixelProbe is a distributed media corruption detection system built on a microservices architecture using Docker containers. The system leverages Celery for distributed task processing, Redis for message queuing, and PostgreSQL for persistent storage.

## Container Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Docker Network                                  │
│                                                                              │
│  ┌─────────────────┐       ┌─────────────────┐      ┌─────────────────┐   │
│  │                 │       │                 │      │                 │   │
│  │   Web App       │◄─────►│     Redis       │◄────►│  Celery Worker  │   │
│  │   (Flask)       │       │   (Message      │      │     Pool        │   │
│  │   Port: 5000    │       │    Broker)      │      │   (8 workers)   │   │
│  │                 │       │   Port: 6379    │      │                 │   │
│  └────────┬────────┘       └─────────────────┘      └────────┬────────┘   │
│           │                                                   │             │
│           │                                                   │             │
│           ▼                                                   ▼             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         PostgreSQL Database                          │   │
│  │                         Port: 5432                                   │   │
│  │                  (Persistent Storage for All Data)                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────┐                          ┌─────────────────┐          │
│  │   Media Files   │◄─────────────────────────│   FFmpeg &      │          │
│  │   Volume Mount  │                          │   ImageMagick   │          │
│  │  /media:/media  │                          │   (In Workers)  │          │
│  └─────────────────┘                          └─────────────────┘          │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Container Descriptions

#### 1. Web Application Container (`pixelprobe-app`)
- **Image**: `ttlequals0/pixelprobe:latest`
- **Purpose**: Serves the web UI and REST API
- **Responsibilities**:
  - Handle HTTP requests from users
  - Render web interface
  - Submit tasks to Celery queue
  - Query database for results
  - Manage user sessions
- **Key Processes**:
  - Gunicorn WSGI server
  - Flask application
  - APScheduler for scheduled tasks

#### 2. Celery Worker Container (`celery-worker`)
- **Image**: `ttlequals0/pixelprobe:latest` (same image, different entry point)
- **Purpose**: Process background tasks in parallel
- **Responsibilities**:
  - Execute media scanning tasks
  - Process file discovery operations
  - Handle cleanup operations
  - Report progress to Redis
- **Configuration**:
  - Default: 8 concurrent workers
  - Configurable via `CELERY_WORKERS` environment variable
- **Tools Available**:
  - FFmpeg for video/audio analysis
  - ImageMagick for image analysis
  - Python PIL for additional image processing

#### 3. Redis Container
- **Image**: `redis:7-alpine`
- **Purpose**: Message broker and result backend
- **Responsibilities**:
  - Queue task messages
  - Store task results temporarily
  - Coordinate worker pool
  - Cache frequently accessed data
- **Persistence**: Optional volume mount for data persistence

#### 4. PostgreSQL Container
- **Image**: `postgres:15-alpine`
- **Purpose**: Primary data storage
- **Responsibilities**:
  - Store scan results
  - Maintain file metadata
  - Track scan history
  - Store user configurations
  - Manage scan state
- **Features**:
  - Connection pooling (20 base, 40 max)
  - Automatic reconnection
  - Transaction support

## Celery Queue System

### How Celery Works in PixelProbe

```
User Request → Flask App → Celery Task → Redis Queue → Worker Pool → Execution
                              ↓                              ↓
                         Task ID                    Progress Updates
                              ↓                              ↓
                         Return to User            Update Database
```

### Task Types and Queues

#### 1. Main Queue (`pixelprobe`)
All tasks use a single queue for simplicity and load balancing.

**Task Types**:
- `scan_media_task`: Scan files for corruption
- `scan_files_task`: Batch file scanning
- `discover_directory_task`: Parallel directory discovery
- `process_chunk_task`: Process file chunks
- `cleanup_orphaned_task`: Remove missing files
- `scheduled_scan_task`: Automated scans
- `health_check_task`: System health monitoring

### Parallel Scanning Workflow

```
1. User initiates scan via API
   ↓
2. Flask creates orchestrator task
   ↓
3. Orchestrator divides work into chunks
   ↓
4. Discovery Phase (Parallel)
   ├── Worker 1: Discovers files in /media/photos
   ├── Worker 2: Discovers files in /media/videos
   ├── Worker 3: Discovers files in /media/music
   └── Worker 4: Discovers files in /media/documents
   ↓
5. Results aggregated in database
   ↓
6. Scanning Phase (Parallel)
   ├── Worker 1: Scans chunk 1 (files 1-100)
   ├── Worker 2: Scans chunk 2 (files 101-200)
   ├── Worker 3: Scans chunk 3 (files 201-300)
   ├── Worker 4: Scans chunk 4 (files 301-400)
   ├── Worker 5: Scans chunk 5 (files 401-500)
   ├── Worker 6: Scans chunk 6 (files 501-600)
   ├── Worker 7: Scans chunk 7 (files 601-700)
   └── Worker 8: Scans chunk 8 (files 701-800)
   ↓
7. Results stored in PostgreSQL
   ↓
8. Progress updates sent via Redis
   ↓
9. Web UI polls for updates
```

### Task Distribution Strategy

1. **Chunk-Based Distribution**:
   - Files are divided into chunks of configurable size (default: 100 files)
   - Each chunk becomes an independent task
   - Workers pull chunks from queue as they become available

2. **Worker Pool Management**:
   - Workers are stateless and interchangeable
   - Automatic retry on failure (3 attempts)
   - Exponential backoff: 30s, 60s, 120s
   - Dead letter queue for failed tasks

3. **Load Balancing**:
   - Redis automatically distributes tasks to available workers
   - Round-robin distribution ensures even load
   - Workers pull tasks when ready (pull model, not push)

## Data Flow

### Scan Initiation Flow
```
Web UI → API Request → Flask Route → Scan Service → Celery Task → Redis
                                            ↓
                                    PostgreSQL (Create Scan State)
```

### Progress Update Flow
```
Celery Worker → Update Database → Redis (Cache) → API Poll → Web UI
        ↓
   Log Progress
```

### Result Storage Flow
```
Media File → FFmpeg/ImageMagick → Analysis Result → PostgreSQL
                                          ↓
                                    Update Statistics
```

## Container Interactions

### 1. Web App ↔ Redis
- **Protocol**: Redis protocol (TCP)
- **Purpose**: Submit tasks, retrieve results
- **Operations**:
  - `LPUSH`: Add task to queue
  - `GET/SET`: Cache operations
  - `PUBLISH/SUBSCRIBE`: Real-time updates

### 2. Web App ↔ PostgreSQL
- **Protocol**: PostgreSQL wire protocol
- **Purpose**: CRUD operations on data
- **Connection Pool**:
  - Base: 20 connections
  - Max overflow: 20 connections
  - Total max: 40 connections
  - Recycle time: 3600 seconds

### 3. Celery Workers ↔ Redis
- **Protocol**: Redis protocol
- **Purpose**: Receive tasks, report status
- **Operations**:
  - `BRPOP`: Block waiting for tasks
  - `SET`: Store task results
  - `EXPIRE`: Set TTL on results

### 4. Celery Workers ↔ PostgreSQL
- **Protocol**: PostgreSQL wire protocol
- **Purpose**: Store scan results
- **Connection Strategy**:
  - Each worker maintains own connection
  - Connection pooling within worker
  - Automatic reconnection on failure

### 5. All Containers ↔ Media Volume
- **Type**: Docker volume mount
- **Mount Point**: `/media` in containers
- **Access**: Read-only for safety
- **Purpose**: Access media files for scanning

## Environment Variables

### Required Variables
```yaml
# Database
POSTGRES_HOST: postgres
POSTGRES_PORT: 5432
POSTGRES_DB: pixelprobe
POSTGRES_USER: pixelprobe
POSTGRES_PASSWORD: <secure-password>

# Redis
REDIS_HOST: redis
REDIS_PORT: 6379

# Celery
CELERY_BROKER_URL: redis://redis:6379/0
CELERY_RESULT_BACKEND: redis://redis:6379/0
CELERY_WORKERS: 8

# Application
SECRET_KEY: <secure-key>
MAX_WORKERS: 10
BATCH_SIZE: 100
```

## Scaling Strategy

### Horizontal Scaling

1. **Add More Workers**:
```yaml
celery-worker-2:
  image: ttlequals0/pixelprobe:latest
  command: celery -A celery_app worker
  scale: 4  # Creates 4 instances
```

2. **Increase Worker Concurrency**:
```yaml
environment:
  CELERY_WORKERS: 16  # Double the workers
```

### Vertical Scaling

1. **Increase Container Resources**:
```yaml
deploy:
  resources:
    limits:
      cpus: '4'
      memory: 8G
```

2. **Optimize Database**:
- Increase `shared_buffers`
- Increase `work_mem`
- Tune `max_connections`

### Performance Tuning

1. **Chunk Size Optimization**:
   - Smaller chunks (50): Better for many small files
   - Larger chunks (200): Better for fewer large files

2. **Worker Specialization**:
   - Dedicate workers for specific file types
   - Priority queues for important scans

3. **Database Optimization**:
   - Regular `VACUUM` operations
   - Index optimization
   - Query performance monitoring

## Monitoring and Health Checks

### Health Check Endpoints
- `/health`: Web application health
- `/api/scan-status`: Current scan status
- `/api/scan/parallel-v2/workers`: Worker status

### Metrics to Monitor
1. **Queue Depth**: Tasks waiting in Redis
2. **Worker Utilization**: Active vs idle workers
3. **Database Connections**: Active connections
4. **Scan Throughput**: Files/minute
5. **Error Rate**: Failed tasks

### Docker Compose Health Checks
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

## Failure Recovery

### Automatic Recovery Mechanisms

1. **Stuck Scan Detection**:
   - Timeout: 5 minutes for adding phase
   - Timeout: 10 minutes for discovery phase
   - Automatic cleanup and restart

2. **Worker Crash Recovery**:
   - Supervisor monitors worker processes
   - Automatic restart on crash
   - Task reassignment to healthy workers

3. **Database Connection Recovery**:
   - Automatic reconnection
   - Connection pool recovery
   - Transaction rollback on failure

4. **Redis Connection Recovery**:
   - Automatic reconnection with backoff
   - Queue persistence (optional)
   - Task redelivery on reconnection

## Security Considerations

1. **Network Isolation**:
   - Internal Docker network
   - No direct external access to Redis/PostgreSQL
   - API authentication required

2. **Resource Limits**:
   - Memory limits per container
   - CPU limits to prevent DoS
   - File size limits (5GB max)
   - Scan timeout (5 minutes per file)

3. **Input Validation**:
   - Path traversal prevention
   - Command injection protection
   - SQL injection prevention
   - File type validation

## Deployment Example

### Docker Compose Configuration
```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: pixelprobe
      POSTGRES_USER: pixelprobe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pixelprobe"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  pixelprobe:
    image: ttlequals0/pixelprobe:latest
    ports:
      - "5000:5000"
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      REDIS_HOST: redis
      SECRET_KEY: ${SECRET_KEY}
      CELERY_BROKER_URL: redis://redis:6379/0
    volumes:
      - /media:/media:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  celery-worker:
    image: ttlequals0/pixelprobe:latest
    command: celery -A celery_app worker --loglevel=info --concurrency=8
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      REDIS_HOST: redis
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_WORKERS: 8
    volumes:
      - /media:/media:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '4'
          memory: 4G

volumes:
  postgres_data:

networks:
  default:
    name: pixelprobe-network
```

## Conclusion

PixelProbe's architecture is designed for:
- **Scalability**: Easily add more workers or containers
- **Reliability**: Automatic recovery from failures
- **Performance**: Parallel processing across all CPU cores
- **Maintainability**: Clear separation of concerns
- **Security**: Defense in depth approach

The system can handle libraries of 1M+ files efficiently by distributing work across all available resources while maintaining data consistency and providing real-time progress updates.