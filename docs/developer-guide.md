# PixelProbe developer guide

## Table of contents
1. [Development setup](#development-setup)
2. [Architecture overview](#architecture-overview)
3. [Code structure](#code-structure)
4. [Development workflow](#development-workflow)
5. [Testing](#testing)
6. [Security guidelines](#security-guidelines)
7. [Contributing](#contributing)
8. [Deployment](#deployment)

## Development setup

### Prerequisites

- Python 3.10-3.12 (the Docker image ships 3.12)
- PostgreSQL (required; SQLite is not supported since v2.2.0)
- Redis or Valkey (Celery broker/result backend and scheduler lock)
- Node.js 20 and npm (frontend build)
- FFmpeg and ImageMagick
- Git

### Local development setup

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

3. **Install Python dependencies:**
```bash
pip install -r requirements-test.txt
```

`requirements-test.txt` includes the base `requirements.txt`, so this one
command installs both runtime and test dependencies.

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

5. **Build the frontend assets (required):**
```bash
npm install && npm run build
```

The templates load webpack-built bundles from `static/dist/`; the app will
not render correctly without this step.

6. **Set up environment variables:**
```bash
cp .env.example .env
# Edit .env with your configuration
```

7. **Run the development server:**
```bash
python app.py
```

There is no separate database initialization step: `app.py` runs
`create_tables()` and any pending migrations automatically at import time.

The application will be available at `http://localhost:5000`

### Docker development

The container needs PostgreSQL and Redis/Valkey to start, so use the compose
stack described in [docker-setup.md](docker-setup.md) rather than a bare
`docker run`. To build a local image:

```bash
docker build --platform=linux/amd64 -t pixelprobe:dev .
```

## Architecture overview

### System architecture

```
+-----------------+     +-----------------+     +-----------------+
|   Web Client    |---->|   Flask API     |---->|  PostgreSQL DB  |
+-----------------+     +-----------------+     +-----------------+
                               |
                               v
                        +-----------------+
                        |  Media Scanner  |
                        +-----------------+
                               |
                        +------+------+
                        v             v
                   +---------+   +---------+
                   | FFmpeg  |   |ImageMag |
                   +---------+   +---------+
```

### Application layers

1. **Presentation layer** (`templates/`, `static/`)
   - HTML templates with hand-rolled CSS and JavaScript, bundled by webpack
   - Real-time progress updates

2. **API layer** (`pixelprobe/api/`)
   - RESTful endpoints
   - Request validation
   - Rate limiting
   - CSRF protection

3. **Business logic layer** (`pixelprobe/services/`)
   - Scan orchestration
   - Statistics calculation
   - Export functionality
   - Maintenance operations

4. **Data access layer** (`pixelprobe/repositories/`)
   - Database operations
   - Query optimization
   - Transaction management

5. **Core scanner** (`pixelprobe/media_checker.py`)
   - File discovery
   - Corruption detection
   - Multi-tool validation

## Code structure

The full, maintained directory tree lives in
[project-structure.md](project-structure.md). This guide does not duplicate
it; when the layout changes, update that document only.

## Development workflow

### Code style

- Follow PEP 8 for Python code
- Use type hints where appropriate
- Maximum line length: 100 characters
- Use meaningful variable names

### Git workflow

Never commit directly to `main`. All changes go through a feature or fix
branch and a pull request:

1. **Create a feature branch off main:**
```bash
git checkout -b feature/your-feature-name
```

2. **Make your changes and commit:**
```bash
git add .
git commit -m "feat: add new scanning feature"
```

3. **Push and create a PR:**
```bash
git push origin feature/your-feature-name
```

4. **Wait for both CI and CodeQL to pass** on the PR before building or
   tagging any Docker images. Fixing a CodeQL finding after an image is built
   forces a rebuild and re-push of the same tag.

### Commit message convention

Follow the Conventional Commits specification:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes
- `refactor:` Code refactoring
- `test:` Test additions/changes
- `chore:` Maintenance tasks

### Adding new features

1. **API endpoint:**
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

2. **Register blueprint:**
```python
# app.py
from pixelprobe.api.your_routes import your_bp
app.register_blueprint(your_bp)
```

3. **Add service logic:**
```python
# pixelprobe/services/your_service.py
class YourService:
    def __init__(self):
        pass

    def process_data(self, data):
        # Business logic here
        return result
```

### Database migrations

Migrations live in `pixelprobe/migrations/startup.py` and run automatically
at startup, not in `app.py`. To add a schema change:

1. **Update the model:**
```python
# pixelprobe/models.py
class YourModel(db.Model):
    new_field = db.Column(db.String(100))
```

2. **Add a versioned migration function** in
   `pixelprobe/migrations/startup.py`, following the existing
   `run_vX_Y_Z_migrations(db)` pattern (for example `run_v2_6_61_migrations`).

3. **Register it** in the `_run_all_migrations(db)` registry in the same
   file so it runs at startup.

A PostgreSQL advisory lock coordinates migrations across multiple gunicorn
workers and containers, so each migration runs exactly once per deployment.

## Testing

### Running tests

Install the test dependencies and build the frontend assets first (some tests
and the app itself expect the built static files):

```bash
pip install -r requirements-test.txt
npm install && npm run build
```

```bash
# Default local run: skips tests that need the real media sample corpus
pytest -m "not real_media"

# Run with coverage
pytest -m "not real_media" --cov=pixelprobe

# Run specific test file
pytest tests/unit/test_scan_service.py
```

The `real_media` tests run in CI inside the Docker image, where the exact
FFmpeg and ImageMagick versions match production. See
[testing-guide.md](testing-guide.md) for the full testing reference.

### Writing tests

1. **Unit test example:**
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

2. **Integration test example:**
```python
# tests/integration/test_api_endpoints.py
def test_scan_endpoint(client):
    response = client.post('/api/scan-file', json={
        'file_path': '/test/image.jpg'
    })
    assert response.status_code == 200
    assert 'message' in response.json
```

### Test data

Real media fixtures (valid and corrupted samples per format) live in
`tests/fixtures/media_samples/` and are wired up by the `test_data_dir`
fixture in `tests/conftest.py`.

Note: `scripts/create_test_database.py` is a legacy script from the SQLite
era and does not work with the PostgreSQL-only application. Do not use it.

## Security guidelines

### Input validation

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

### Subprocess execution

Always use the safe wrapper:
```python
from pixelprobe.utils.security import safe_subprocess_run

# Safe
result = safe_subprocess_run(['ffmpeg', '-i', file_path])

# Never do this
result = subprocess.run(f'ffmpeg -i {file_path}', shell=True)  # DANGEROUS!
```

### Authentication

Authentication is implemented in `pixelprobe/auth.py`:
- Session-based login for the web UI (24-hour lifetime, 30-minute inactivity timeout)
- Bearer API tokens for programmatic access (managed via `/api/tokens`)
- Protect new endpoints with the `@auth_required` decorator

## Contributing

### Before contributing

1. Check existing issues and PRs
2. Discuss major changes in an issue first

### Pull request process

1. Update documentation for new features
2. Add tests for new functionality
3. Ensure all tests pass
4. Update CHANGELOG.MD
5. Request review from maintainers

### Code review checklist

- [ ] Code follows style guidelines
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Security considerations addressed
- [ ] Performance impact considered
- [ ] Backward compatibility maintained

## Deployment

### Production configuration

1. **Environment variables:**
```bash
# .env.production
DEBUG=False
SECRET_KEY=your-strong-secret-key
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=pixelprobe
POSTGRES_USER=pixelprobe
POSTGRES_PASSWORD=your-db-password
SCAN_PATHS=/media/photos,/media/videos
TZ=UTC
```

The database is configured via the individual `POSTGRES_*` variables;
`DATABASE_URL` is deprecated since v2.2.0. Scan directories are set with
`SCAN_PATHS` (comma-separated). See [configuration.md](configuration.md)
for the full variable reference.

2. **Gunicorn configuration:**

The real configuration is `gunicorn.conf.py` in the repository root:

- `GUNICORN_WORKERS` - worker count (default 4)
- `GUNICORN_TIMEOUT` - worker timeout in seconds (default 300; long scans
  need the headroom)
- `GUNICORN_BIND` - comma-separated bind address list for dual-stack
  IPv4/IPv6 (default `0.0.0.0:5000`)
- `GUNICORN_LOG_LEVEL` - log level (default `info`)
- Access and error logs go to stdout/stderr

There are no `worker_class` or `max_requests` settings.

3. **Run with Gunicorn:**
```bash
gunicorn -c gunicorn.conf.py app:app
```

### Docker deployment

Build the production image for linux/amd64:
```bash
docker build --platform=linux/amd64 -t pixelprobe:latest .
```

A bare `docker run` will not start: the application requires PostgreSQL and
Redis/Valkey. Deploy with the compose stack documented in
[docker-setup.md](docker-setup.md).

### Monitoring

1. **Health checks:**
   - `/healthz` is the unauthenticated liveness endpoint; use it for
     container healthchecks and uptime monitors
   - `/health` returns status details but requires authentication
   - Check scan queue status
   - Monitor disk space

2. **Logging:**
   - Application logs: `/app/logs/`
   - Scan logs: include timestamps and file paths
   - Error tracking: log all exceptions

3. **Performance:**
   - Monitor scan duration
   - Track memory usage
   - Database query performance

### Backup

Regular backups of:
- PostgreSQL database
- Configuration files
- Scan results
- Error logs

### Updates

1. Test updates in staging environment
2. Backup database before updates
3. Run database migrations
4. Monitor for issues after deployment

## Troubleshooting

### Common issues

1. **"No module named 'magic'"**
   - Install: `pip install python-magic`
   - On Windows: Also need `python-magic-bin`

2. **"ffmpeg not found"**
   - Ensure FFmpeg is in PATH
   - Install with package manager

3. **Database connection issues**
   - Check PostgreSQL service is running
   - Verify the `POSTGRES_*` environment variables

4. **Performance problems (slow scans, memory pressure)**
   - See [performance-tuning.md](performance-tuning.md) for worker sizing,
     batch settings, and database tuning

### Debug mode

Enable debug logging:
```python
# .env
DEBUG=True
LOG_LEVEL=DEBUG
```

### Performance profiling

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
