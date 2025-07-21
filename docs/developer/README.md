# PixelProbe Developer Guide

## Table of Contents
1. [Development Setup](#development-setup)
2. [Architecture Overview](#architecture-overview)
3. [Code Structure](#code-structure)
4. [Development Workflow](#development-workflow)
5. [Testing](#testing)
6. [Security Guidelines](#security-guidelines)
7. [Contributing](#contributing)
8. [Deployment](#deployment)

## Development Setup

### Prerequisites

- Python 3.8+
- FFmpeg and ImageMagick installed
- SQLite3 (for development)
- Git

### Local Development Setup

1. **Clone the repository:**
```bash
git clone https://github.com/ttlequals0/PixelProbe.git
cd PixelProbe
```

2. **Create a virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Install system dependencies:**

On Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg imagemagick libmagic1
```

On macOS:
```bash
brew install ffmpeg imagemagick libmagic
```

5. **Set up environment variables:**
```bash
cp .env.example .env
# Edit .env with your configuration
```

6. **Initialize the database:**
```bash
python -c "from app import create_tables; create_tables()"
```

7. **Run the development server:**
```bash
python app.py
```

The application will be available at `http://localhost:5000`

### Docker Development

1. **Build the Docker image:**
```bash
docker build -t pixelprobe:dev .
```

2. **Run with Docker Compose:**
```bash
docker-compose up -d
```

## Architecture Overview

### System Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Web Client    │────▶│   Flask API     │────▶│   SQLite DB     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │  Media Scanner  │
                        └─────────────────┘
                               │
                        ┌──────┴──────┐
                        ▼             ▼
                   ┌─────────┐   ┌─────────┐
                   │ FFmpeg  │   │ImageMag │
                   └─────────┘   └─────────┘
```

### Application Layers

1. **Presentation Layer** (`templates/`, `static/`)
   - HTML templates with Bootstrap UI
   - JavaScript for dynamic interactions
   - Real-time progress updates

2. **API Layer** (`pixelprobe/api/`)
   - RESTful endpoints
   - Request validation
   - Rate limiting
   - CSRF protection

3. **Business Logic Layer** (`pixelprobe/services/`)
   - Scan orchestration
   - Statistics calculation
   - Export functionality
   - Maintenance operations

4. **Data Access Layer** (`pixelprobe/repositories/`)
   - Database operations
   - Query optimization
   - Transaction management

5. **Core Scanner** (`media_checker.py`)
   - File discovery
   - Corruption detection
   - Multi-tool validation

## Code Structure

```
PixelProbe/
├── app.py                    # Application entry point
├── media_checker.py          # Core scanning engine
├── models.py                 # SQLAlchemy models
├── scheduler.py              # Scheduled scan management
├── version.py                # Version information
├── requirements.txt          # Python dependencies
├── Dockerfile               # Docker configuration
├── docker-compose.yml       # Docker Compose setup
│
├── pixelprobe/              # Main application package
│   ├── __init__.py
│   ├── api/                 # API endpoints
│   │   ├── scan_routes.py   # Scanning endpoints
│   │   ├── stats_routes.py  # Statistics endpoints
│   │   ├── admin_routes.py  # Admin endpoints
│   │   ├── export_routes.py # Export endpoints
│   │   └── maintenance_routes.py
│   │
│   ├── services/            # Business logic
│   │   ├── scan_service.py
│   │   ├── stats_service.py
│   │   ├── export_service.py
│   │   └── maintenance_service.py
│   │
│   ├── repositories/        # Data access
│   │   ├── base_repository.py
│   │   ├── scan_repository.py
│   │   └── config_repository.py
│   │
│   └── utils/              # Utilities
│       ├── security.py     # Security utilities
│       ├── validators.py   # Input validation
│       ├── decorators.py   # Custom decorators
│       └── helpers.py      # Helper functions
│
├── templates/              # HTML templates
│   ├── index.html         # Main UI
│   └── api_docs.html      # API documentation
│
├── static/                # Static assets
│   ├── css/
│   ├── js/
│   └── images/
│
├── tests/                 # Test suite
│   ├── unit/
│   └── integration/
│
├── docs/                  # Documentation
│   ├── api/              # API docs
│   ├── developer/        # Developer guides
│   └── examples/         # Code examples
│
└── tools/                # Utility scripts
    └── fix_*.py          # Database repair tools
```

## Development Workflow

### Code Style

- Follow PEP 8 for Python code
- Use type hints where appropriate
- Maximum line length: 100 characters
- Use meaningful variable names

### Git Workflow

1. **Create a feature branch:**
```bash
git checkout -b feature/your-feature-name
```

2. **Make your changes and commit:**
```bash
git add .
git commit -m "feat: add new scanning feature"
```

3. **Push and create PR:**
```bash
git push origin feature/your-feature-name
```

### Commit Message Convention

Follow the Conventional Commits specification:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes
- `refactor:` Code refactoring
- `test:` Test additions/changes
- `chore:` Maintenance tasks

### Adding New Features

1. **API Endpoint:**
```python
# pixelprobe/api/your_routes.py
from flask import Blueprint, request, jsonify
from pixelprobe.utils.security import validate_json_input

your_bp = Blueprint('your_feature', __name__, url_prefix='/api')

@your_bp.route('/your-endpoint', methods=['POST'])
@validate_json_input({
    'field': {'required': True, 'type': str}
})
def your_endpoint():
    """Your endpoint description"""
    data = request.get_json()
    # Implementation
    return jsonify({'result': 'success'})
```

2. **Register Blueprint:**
```python
# app.py
from pixelprobe.api.your_routes import your_bp
app.register_blueprint(your_bp)
```

3. **Add Service Logic:**
```python
# pixelprobe/services/your_service.py
class YourService:
    def __init__(self):
        pass
    
    def process_data(self, data):
        # Business logic here
        return result
```

### Database Migrations

When adding new database fields:

1. **Update the model:**
```python
# models.py
class YourModel(db.Model):
    new_field = db.Column(db.String(100))
```

2. **Add migration in app.py:**
```python
def migrate_database():
    # ... existing code ...
    migrations = [
        ('new_field', "ALTER TABLE your_table ADD COLUMN new_field VARCHAR(100)")
    ]
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=pixelprobe

# Run specific test file
pytest tests/unit/test_scan_service.py
```

### Writing Tests

1. **Unit Test Example:**
```python
# tests/unit/test_scan_service.py
import pytest
from pixelprobe.services.scan_service import ScanService

def test_scan_file_validation():
    service = ScanService()
    
    # Test invalid path
    with pytest.raises(ValueError):
        service.scan_file("../../../etc/passwd")
    
    # Test valid path
    result = service.scan_file("/allowed/path/image.jpg")
    assert result is not None
```

2. **Integration Test Example:**
```python
# tests/integration/test_api_endpoints.py
def test_scan_endpoint(client):
    response = client.post('/api/scan-file', json={
        'file_path': '/test/image.jpg'
    })
    assert response.status_code == 200
    assert 'message' in response.json
```

### Test Data

Use the provided test database creation script:
```bash
python scripts/create_test_database.py
```

## Security Guidelines

### Input Validation

Always validate user input:
```python
from pixelprobe.utils.security import validate_file_path, validate_json_input

# Path validation
try:
    safe_path = validate_file_path(user_input)
except PathTraversalError:
    return jsonify({'error': 'Invalid path'}), 400

# JSON validation decorator
@validate_json_input({
    'field': {'required': True, 'type': str, 'max_length': 100}
})
```

### Subprocess Execution

Always use the safe wrapper:
```python
from pixelprobe.utils.security import safe_subprocess_run

# Safe
result = safe_subprocess_run(['ffmpeg', '-i', file_path])

# Never do this
result = subprocess.run(f'ffmpeg -i {file_path}', shell=True)  # DANGEROUS!
```

### Authentication (Future)

When implementing authentication:
- Use JWT tokens
- Implement refresh tokens
- Add role-based access control
- Secure sensitive endpoints

## Contributing

### Before Contributing

1. Read the [Code of Conduct](CODE_OF_CONDUCT.md)
2. Check existing issues and PRs
3. Discuss major changes in an issue first

### Pull Request Process

1. Update documentation for new features
2. Add tests for new functionality
3. Ensure all tests pass
4. Update CHANGELOG.md
5. Request review from maintainers

### Code Review Checklist

- [ ] Code follows style guidelines
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Security considerations addressed
- [ ] Performance impact considered
- [ ] Backward compatibility maintained

## Deployment

### Production Configuration

1. **Environment Variables:**
```bash
# .env.production
DEBUG=False
SECRET_KEY=your-strong-secret-key
DATABASE_URL=postgresql://user:pass@host/db
ALLOWED_SCAN_PATHS=/media/photos:/media/videos
TZ=UTC
```

2. **Gunicorn Configuration:**
```python
# gunicorn_config.py
bind = "0.0.0.0:5000"
workers = 4
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 120
```

3. **Run with Gunicorn:**
```bash
gunicorn -c gunicorn_config.py app:app
```

### Docker Deployment

1. **Build production image:**
```bash
docker build -t pixelprobe:latest .
```

2. **Run container:**
```bash
docker run -d \
  --name pixelprobe \
  -p 5000:5000 \
  -v /media:/media:ro \
  -v pixelprobe_data:/app/data \
  -e SECRET_KEY=your-secret \
  pixelprobe:latest
```

### Monitoring

1. **Health Checks:**
   - Monitor `/health` endpoint
   - Check scan queue status
   - Monitor disk space

2. **Logging:**
   - Application logs: `/app/logs/`
   - Scan logs: Include timestamps and file paths
   - Error tracking: Log all exceptions

3. **Performance:**
   - Monitor scan duration
   - Track memory usage
   - Database query performance

### Backup

Regular backups of:
- SQLite database
- Configuration files
- Scan results
- Error logs

### Updates

1. Test updates in staging environment
2. Backup database before updates
3. Run database migrations
4. Monitor for issues after deployment

## Troubleshooting

### Common Issues

1. **"No module named 'magic'"**
   - Install: `pip install python-magic`
   - On Windows: Also need `python-magic-bin`

2. **"ffmpeg not found"**
   - Ensure FFmpeg is in PATH
   - Install with package manager

3. **Database locked errors**
   - Check for concurrent access
   - Consider PostgreSQL for production

4. **Memory issues with large scans**
   - Increase worker memory limits
   - Use parallel scanning
   - Implement batch processing

### Debug Mode

Enable debug logging:
```python
# .env
DEBUG=True
LOG_LEVEL=DEBUG
```

### Performance Profiling

```python
# Enable profiling
from werkzeug.middleware.profiler import ProfilerMiddleware
app.wsgi_app = ProfilerMiddleware(app.wsgi_app)
```

## Resources

- [Flask Documentation](https://flask.palletsprojects.com/)
- [SQLAlchemy Documentation](https://www.sqlalchemy.org/)
- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
- [ImageMagick Documentation](https://imagemagick.org/)