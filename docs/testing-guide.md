# Testing guide

## Overview

PixelProbe uses pytest with unit, integration, and top-level feature tests.
`pytest.ini` sets `testpaths = tests scripts`, so test files under `scripts/`
(currently `scripts/test_database.py`) are collected too.

## Test structure

```
tests/
|-- conftest.py                    # Shared fixtures and test app factory
|-- test_app.py                    # Application-level tests
|-- test_authentication.py         # Auth and API token tests
|-- test_bulk_reports.py           # Bulk report generation
|-- test_concurrency.py            # Concurrent operation behavior
|-- test_frontend_build.py         # Runs npm install / npm run build
|-- test_gunicorn_conf.py          # gunicorn.conf.py env handling
|-- test_jpeg_pixel.py             # JPEG pixel-level validation
|-- test_logs.py                   # Log endpoints and log capture
|-- test_media_checker.py          # Core media checking functionality
|-- test_migration_lock.py         # Advisory-lock migration coordination
|-- test_performance.py            # Performance-oriented tests
|-- test_read_timeout.py           # Unreadable-file / read timeout handling
|-- test_real_media_samples.py     # Marker-gated real media parser tests
|-- test_scheduler.py              # Scheduled scan management
|-- test_security_fixes.py         # Security regression tests
|-- unit/                          # Unit tests for individual components
|   |-- test_bitrot_classification.py
|   |-- test_celery_settings.py
|   |-- test_helpers.py
|   |-- test_maintenance_service.py
|   |-- test_notification_email.py
|   |-- test_paths.py
|   |-- test_progress_utils.py
|   |-- test_repositories.py
|   |-- test_rolling_integrity_queue.py
|   |-- test_scan_service.py
|   |-- test_security_cli.py
|   |-- test_startup.py
|   |-- test_stats_service.py
|   |-- test_tasks_parallel.py
|-- integration/                   # API endpoint integration tests
|   |-- test_admin_endpoints.py
|   |-- test_api_endpoints.py
|   |-- test_bitrot_endpoints.py
|   |-- test_csrf_and_exclusions.py
|   |-- test_maintenance_endpoints.py
|   |-- test_scan_endpoints_extended.py
|   |-- test_scan_execution.py
|   |-- test_scan_launch.py
|   |-- test_schedule_budget.py
|-- fixtures/
    |-- media_samples/             # Valid and corrupted samples per format
```

Two files deserve a call-out:

- `test_real_media_samples.py` is gated behind the `real_media` marker. It
  exercises the FFmpeg/ImageMagick stderr parsers against the real sample
  corpus and is sensitive to tool versions, so it is excluded from the
  default local run and executed in CI inside the Docker image instead.
- `test_frontend_build.py` actually runs `npm install` and `npm run build`,
  so it needs Node.js 20 and npm available.

## Running tests

### Basic test commands

```bash
# Install test dependencies (includes base requirements.txt)
pip install -r requirements-test.txt

# Build the frontend once (test_frontend_build.py and the app expect it)
npm install && npm run build

# Default local run: everything except the real_media tests
pytest -m "not real_media"

# Run with verbose output
pytest -m "not real_media" -v

# Run specific test file
pytest tests/test_media_checker.py

# Run a specific test (Class::method form)
pytest tests/test_media_checker.py::TestMediaChecker::test_corrupted_mp4_detection

# Run tests matching pattern
pytest -k "corruption"

# Run with coverage report
pytest -m "not real_media" --cov=pixelprobe --cov-report=html

# Run only unit tests
pytest tests/unit/

# Run only integration tests
pytest tests/integration/
```

### Markers

Markers are declared in `pytest.ini`:

| Marker | Meaning |
|--------|---------|
| `real_media` | Requires the real media sample corpus and matching tool versions; deselect locally with `-m "not real_media"` |
| `slow` | Long-running tests; deselect with `-m "not slow"` |
| `integration` | Integration tests |
| `timeout` | Sets a per-test execution timeout |

There is no `benchmark` marker and no dedicated benchmark suite;
`test_performance.py` contains ordinary tests with timing assertions.

### Test coverage

```bash
# Generate coverage report
pytest -m "not real_media" --cov=pixelprobe --cov-report=term-missing

# Generate HTML coverage report
pytest -m "not real_media" --cov=pixelprobe --cov-report=html
# Open htmlcov/index.html in browser
```

Coverage targets (aspirational - nothing enforces them; there is no
`--cov-fail-under` and Codecov runs with `fail_ci_if_error: false`):

- Overall: 80%
- Core modules (scan_service, media_checker): 90%
- API routes: 85%
- Security modules: 100%

## Test categories

### Unit tests

Unit tests validate individual components in isolation using mocks and
fixtures: services (business logic without database/filesystem
dependencies), repositories against a mocked database, utilities (helper
functions, validators, decorators), and database model methods and
properties.

Example:
```python
def test_scan_service_discovery(scan_service, mock_media_files):
    """Test file discovery logic"""
    with patch('os.scandir', return_value=mock_media_files):
        files = scan_service.discover_media_files(['/test'])
        assert len(files) == 3
        assert all(f.endswith(('.mp4', '.jpg')) for f in files)
```

### Integration tests

Integration tests validate API endpoints and full request/response
cycles: all routes with various input scenarios, access control and
permissions, real database operations, and 4xx/5xx responses and error
messages.

Example:
```python
def test_scan_endpoint(client, db):
    """Test full scan workflow via API"""
    response = client.post('/api/scan',
                          json={'directories': ['/media']})
    assert response.status_code == 200
    assert response.json['status'] == 'queued'

    # Verify database state
    scan = ScanState.query.first()
    assert scan.phase == 'discovering'
```

## Test fixtures

All shared fixtures live in `tests/conftest.py`. There is no `create_app`
factory in the codebase; the conftest builds its own test application with
`create_test_app()`, which registers the real blueprints against an
in-memory SQLite database, disables CSRF, and replicates the `/healthz`,
`/health`, and `/api/version` routes from `app.py`. This avoids importing
`app.py` itself (and its PostgreSQL startup) during tests.

Key fixtures:

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `app` | session | Test Flask app from `create_test_app()`, with services and repositories attached |
| `client` | session | Unauthenticated test client for `app` |
| `authenticated_client` | function | Client logged in as a test admin user |
| `db` | function | Creates all tables, yields the SQLAlchemy handle, cleans up after the test |
| `test_data_dir` | session | Temp directory populated from `tests/fixtures/media_samples/`; exposes paths keyed by filename with dots replaced by underscores (e.g. `valid_mp4`, `corrupted_jpg`) |
| `real_scan_results` | function | ScanResult rows backed by the real sample files |
| `mock_scan_result` | function | Single ScanResult row with canned values |
| `tasks_parallel_mod` | function | Imports `pixelprobe.tasks_parallel` with the app/celery circular import stubbed out |

The in-memory SQLite database is shared across the whole session (the `app`
fixture is session-scoped); the function-scoped `db` fixture creates and
drops tables around each test. Production runs PostgreSQL only, so
PostgreSQL-specific behavior (advisory locks, dialect differences) is not
covered by the local suite; the CI image job runs tests inside the
production Docker image to catch environment-specific regressions such as
tool-version changes.

## Testing best practices

### 1. Test isolation
- Each test should be independent
- Use fixtures for setup/teardown
- Mock external dependencies

### 2. Clear test names
```python
# Good
def test_scan_service_handles_missing_directory():
def test_api_returns_404_for_invalid_file():

# Bad
def test_scan():
def test_error():
```

### 3. Arrange-Act-Assert pattern
```python
def test_mark_file_as_good(scan_service, corrupted_file):
    # Arrange
    scan_result = scan_service.scan_file(corrupted_file)

    # Act
    updated = scan_service.mark_as_good(scan_result.id)

    # Assert
    assert updated.marked_as_good is True
    assert updated.is_corrupted is False
```

### 4. Test edge cases
- Empty inputs
- Invalid data types
- Boundary values
- Concurrent operations
- Error conditions

### 5. Use mocks appropriately
```python
# Mock external services
@patch('subprocess.run')
def test_ffmpeg_error_handling(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, 'ffmpeg')
    result = check_video_corruption('/test.mp4')
    assert result['is_corrupted'] is True
```

## Test data

### Creating test media files

```bash
# Create corrupted video for testing
dd if=/dev/urandom of=tests/fixtures/media_samples/broken.mp4 bs=1024 count=100

# Create valid but small video
ffmpeg -f lavfi -i testsrc=duration=1:size=320x240:rate=30 \
       -f lavfi -i sine=frequency=1000:duration=1 \
       -pix_fmt yuv420p tests/fixtures/media_samples/small.mp4
```

### Test database

Tests use an in-memory SQLite database (`sqlite:///:memory:`) created by the
session-scoped `app` fixture in `tests/conftest.py`:
- Shared across the session; the `db` fixture resets tables per test
- Same SQLAlchemy models as production
- No external database or environment variable required

## Continuous integration

CI is defined in
[.github/workflows/test.yml](../.github/workflows/test.yml) with two jobs:

- `test`: runs on every push and pull request across a Python matrix of
  3.10, 3.11, and 3.12 (`actions/checkout@v4`, `actions/setup-python@v5`).
  It sets up Node.js 20, installs `ffmpeg imagemagick libmagic1` via apt,
  installs `requirements-test.txt`, runs `npm install && npm run build`,
  then executes `pytest -m "not real_media" --cov=pixelprobe`. Coverage is
  uploaded to Codecov (`codecov/codecov-action@v5`) with
  `fail_ci_if_error: false`, so a Codecov outage cannot fail the build.
- `image-integration`: builds the production Docker image, runs the
  `real_media` tests (`tests/test_real_media_samples.py`) inside that image
  so the stderr parsers are exercised against the exact FFmpeg/ImageMagick
  versions the image ships, and verifies the tool versions. The parsers
  fail soft (zero events rather than exceptions), so this is the job that
  catches regressions from a base-image bump.

CodeQL analysis runs via GitHub's default setup; there is no `codeql.yml`
workflow file in the repository.

## Debugging tests

### Running specific tests
```bash
# Run single test with output
pytest -s -v tests/test_media_checker.py::TestMediaChecker::test_file_hash_generation

# Run with debugger
pytest --pdb tests/failing_test.py

# Show local variables on failure
pytest -l
```

### Common issues

- Import errors: ensure PYTHONPATH includes project root
- Database errors: check fixtures are properly scoped
- Frontend errors: run `npm install && npm run build` first
- File not found: use absolute paths in fixtures

## Adding new tests

When adding new features:

1. Write tests first (TDD approach)
2. Cover happy path and error cases
3. Add integration test for new endpoints
4. Update fixtures if needed
5. Run full suite before committing

Example for new feature:
```python
# 1. Unit test for service
def test_new_feature_service_logic(scan_service):
    result = scan_service.new_feature(param='value')
    assert result.status == 'success'

# 2. Integration test for API
def test_new_feature_endpoint(client):
    response = client.post('/api/new-feature', json={'param': 'value'})
    assert response.status_code == 200

# 3. Error case test
def test_new_feature_invalid_input(client):
    response = client.post('/api/new-feature', json={})
    assert response.status_code == 400
```
