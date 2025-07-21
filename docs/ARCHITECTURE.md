# PixelProbe Architecture

## Overview

PixelProbe is a media file corruption detection system built with a modular, layered architecture. The system is designed to be scalable, maintainable, and secure.

## System Components

```
┌────────────────────────────────────────────────────────────────┐
│                         Web Interface                           │
│                    (HTML/CSS/JavaScript)                        │
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────┐
│                         Flask API Layer                         │
│        (Routes, Request Validation, Rate Limiting)              │
└────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
┌─────────────────────────┐      ┌─────────────────────────┐
│    Service Layer        │      │    Security Layer       │
│  (Business Logic)       │      │  (Auth, Validation)     │
└─────────────────────────┘      └─────────────────────────┘
                    │                       │
                    └───────────┬───────────┘
                                ▼
┌────────────────────────────────────────────────────────────────┐
│                      Repository Layer                           │
│                  (Database Abstraction)                         │
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────┐
│                      SQLite Database                            │
│              (Scan Results, Configurations)                     │
└────────────────────────────────────────────────────────────────┘

Parallel Components:
┌─────────────────────┐      ┌─────────────────────┐
│   Media Scanner     │      │   Task Scheduler    │
│ (FFmpeg/ImageMagick)│      │   (APScheduler)     │
└─────────────────────┘      └─────────────────────┘
```

## Core Design Principles

### 1. Separation of Concerns
- **Presentation**: Web UI components
- **API**: RESTful endpoints
- **Business Logic**: Service layer
- **Data Access**: Repository pattern
- **Security**: Centralized validation and authentication

### 2. Security First
- Input validation at every layer
- Path traversal protection
- Command injection prevention
- Rate limiting
- Audit logging

### 3. Scalability
- Stateless API design
- Parallel scanning support
- Database connection pooling
- Efficient file processing

## Layer Descriptions

### Presentation Layer

**Location**: `templates/`, `static/`

**Responsibilities**:
- User interface rendering
- Real-time progress updates
- Form validation
- API communication

**Key Components**:
- `index.html`: Main application interface
- `app.js`: Client-side application logic
- Bootstrap for responsive design

### API Layer

**Location**: `pixelprobe/api/`

**Responsibilities**:
- HTTP request handling
- Input validation
- Response formatting
- Rate limiting
- CORS handling

**Key Modules**:
```
api/
├── scan_routes.py      # Scanning operations
├── stats_routes.py     # Statistics and reports
├── admin_routes.py     # Administrative functions
├── export_routes.py    # Data export
└── maintenance_routes.py # Cleanup operations
```

### Service Layer

**Location**: `pixelprobe/services/`

**Responsibilities**:
- Business logic implementation
- Data processing
- External tool integration
- Transaction coordination

**Key Services**:
- `ScanService`: Orchestrates media scanning
- `StatsService`: Calculates statistics
- `ExportService`: Handles data exports
- `MaintenanceService`: Database maintenance

### Security Layer

**Location**: `pixelprobe/utils/security.py`

**Responsibilities**:
- Path validation
- Input sanitization
- Command injection prevention
- Audit logging
- Rate limiting implementation

**Key Functions**:
- `validate_file_path()`: Prevents directory traversal
- `safe_subprocess_run()`: Safe command execution
- `validate_json_input()`: Input validation decorator
- `AuditLogger`: Security event logging

### Repository Layer

**Location**: `pixelprobe/repositories/`

**Responsibilities**:
- Database operations
- Query optimization
- Transaction management
- Data model mapping

**Key Repositories**:
- `ScanRepository`: Scan result operations
- `ConfigRepository`: Configuration management

### Data Layer

**Technology**: SQLite (Development), PostgreSQL (Production)

**Models**:
- `ScanResult`: File scan results
- `ScanConfiguration`: Directory configurations
- `IgnoredErrorPattern`: False positive patterns
- `ScanSchedule`: Scheduled scan configurations
- `ScanState`: Current scan status

## Core Components

### Media Scanner (`media_checker.py`)

The heart of the corruption detection system:

```python
class PixelProbe:
    def __init__(self):
        # Initialize with security configurations
        
    def scan_file(self, file_path):
        # 1. Validate file path
        # 2. Calculate file hash
        # 3. Run detection tools
        # 4. Analyze results
        # 5. Store in database
```

**Detection Flow**:
1. File discovery and validation
2. Format detection using libmagic
3. Tool-specific scanning:
   - Images: PIL, ImageMagick
   - Videos: FFmpeg with multiple checks
   - Audio: FFmpeg audio stream validation
4. Result analysis and storage

### Scheduler (`scheduler.py`)

Manages scheduled scans using APScheduler:

```python
class MediaScheduler:
    def schedule_scan(self, cron_expression, scan_config):
        # Create scheduled job
        
    def run_scheduled_scan(self, job_id):
        # Execute scan with configuration
```

## Data Flow

### Scan Request Flow

```
Client Request → API Endpoint → Validation → Service Layer
                                               ↓
Database ← Repository Layer ← Business Logic ← Scanner
    ↑                                           ↓
    └──────── Scan Results ←──── External Tools
```

### Real-time Updates

```
Scanner Progress → Progress Lock → Shared State
                                      ↓
                   Client ← API ← Status Endpoint
```

## Security Architecture

### Defense in Depth

1. **Input Layer**:
   - JSON schema validation
   - Type checking
   - Length limits

2. **Path Security**:
   - Whitelist-based validation
   - Normalized paths
   - Symlink resolution

3. **Command Execution**:
   - No shell execution
   - Argument validation
   - Timeout limits

4. **API Security**:
   - Rate limiting per endpoint
   - CSRF protection
   - Future: JWT authentication

### Security Flow

```
Request → Rate Limiter → CSRF Check → Input Validation
              ↓                            ↓
          Rejected                   Path Validation
                                          ↓
                                    Audit Logging
                                          ↓
                                    Safe Execution
```

## Performance Considerations

### Optimization Strategies

1. **Parallel Scanning**:
   - ThreadPoolExecutor for concurrent file processing
   - Configurable worker count
   - Progress tracking across threads

2. **Database Performance**:
   - Connection pooling
   - Index optimization
   - Batch operations
   - VACUUM scheduling

3. **File Processing**:
   - Chunked hash calculation
   - Streaming for large files
   - Tool timeout limits

### Caching Strategy

- File hash caching to detect changes
- Configuration caching in memory
- Result pagination for large datasets

## Deployment Architecture

### Development
```
Docker Container
    ├── Flask Development Server
    ├── SQLite Database
    └── Local File System
```

### Production
```
Load Balancer
    ├── Web Server (Nginx)
    │   └── Gunicorn Workers
    │       └── Flask Application
    ├── PostgreSQL Database
    └── Shared Storage (NFS/S3)
```

## Extension Points

### Adding New File Types

1. Update `supported_*_formats` in `PixelProbe`
2. Add detection method
3. Update statistics queries

### Adding New Scanners

1. Create scanner method in `PixelProbe`
2. Add to scan flow
3. Update result processing

### API Extensions

1. Create new route module
2. Add service layer logic
3. Register blueprint
4. Update documentation

## Monitoring and Observability

### Health Checks
- `/health` endpoint
- Database connectivity
- Scanner availability
- Disk space monitoring

### Metrics
- Scan duration
- Files processed per minute
- Error rates
- Queue depth

### Logging
- Structured logging
- Log levels by component
- Security audit trail
- Error tracking

## Future Enhancements

### Planned Features
1. **Distributed Scanning**: Multiple scanner nodes
2. **Cloud Storage Support**: S3, Azure Blob
3. **Machine Learning**: Corruption prediction
4. **WebSocket Support**: Real-time updates
5. **Plugin Architecture**: Custom scanners

### Scalability Path
1. **Horizontal Scaling**: Multiple API instances
2. **Queue-based Processing**: Redis/RabbitMQ
3. **Microservices**: Separate scanner service
4. **Caching Layer**: Redis for results

## Technology Stack

### Backend
- **Framework**: Flask 2.3.3
- **Database**: SQLAlchemy 2.0.21
- **Scheduler**: APScheduler 3.10.4
- **Security**: Flask-Limiter, Flask-WTF

### Scanner Tools
- **FFmpeg**: Video/audio analysis
- **ImageMagick**: Image validation
- **Pillow**: Python image processing
- **python-magic**: File type detection

### Frontend
- **Framework**: Vanilla JavaScript
- **UI**: Bootstrap 5
- **Charts**: Chart.js
- **Icons**: Font Awesome

### Infrastructure
- **Container**: Docker
- **Web Server**: Gunicorn
- **Reverse Proxy**: Nginx (production)
- **Monitoring**: Prometheus (planned)