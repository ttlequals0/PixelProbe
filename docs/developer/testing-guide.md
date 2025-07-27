# Testing Guide

## Overview

PixelProbe uses pytest as its testing framework with comprehensive test coverage across unit, integration, and performance tests. The test suite ensures reliability and catches regressions before deployment.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and test configuration
├── test_media_checker.py    # Core media checking functionality tests
├── unit/                    # Unit tests for individual components
│   ├── test_scan_service.py
│   ├── test_stats_service.py  
│   ├── test_export_service.py
│   ├── test_maintenance_service.py
│   └── test_repositories.py
├── integration/             # API endpoint integration tests
│   ├── test_scan_routes.py
│   ├── test_stats_routes.py
│   ├── test_admin_routes.py
│   └── test_maintenance_routes.py
├── performance/             # Performance and benchmark tests
│   └── test_scan_performance.py
└── fixtures/                # Test data and media samples
    ├── corrupted/          # Known corrupted media files
    └── valid/              # Valid media files for testing
```

## Running Tests

### Basic Test Commands

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_media_checker.py

# Run specific test
pytest tests/test_media_checker.py::test_video_corruption_detection

# Run tests matching pattern
pytest -k "corruption"

# Run with coverage report
pytest --cov=pixelprobe --cov-report=html

# Run only unit tests
pytest tests/unit/

# Run only integration tests  
pytest tests/integration/

# Run with benchmark tests
pytest --benchmark-only
```

### Test Coverage

```bash
# Generate coverage report
pytest --cov=pixelprobe --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=pixelprobe --cov-report=html
# Open htmlcov/index.html in browser

# Coverage requirements
# - Minimum 80% overall coverage
# - 90% coverage for critical paths (scan_service, media_checker)
# - 100% coverage for security modules
```

## Test Categories

### Unit Tests

Unit tests validate individual components in isolation using mocks and fixtures.

#### What's Tested:
- **Services**: Business logic without database/filesystem dependencies
- **Repositories**: Data access patterns with mocked database
- **Utilities**: Helper functions, validators, decorators
- **Models**: Database model methods and properties

#### Example Unit Test:
```python
def test_scan_service_discovery(scan_service, mock_media_files):
    """Test file discovery logic"""
    with patch('os.scandir', return_value=mock_media_files):
        files = scan_service.discover_media_files(['/test'])
        assert len(files) == 3
        assert all(f.endswith(('.mp4', '.jpg')) for f in files)
```

### Integration Tests

Integration tests validate API endpoints and full request/response cycles.

#### What's Tested:
- **API Endpoints**: All routes with various input scenarios
- **Authentication**: Access control and permissions
- **Database Integration**: Real database operations
- **Error Handling**: 4xx/5xx responses and error messages

#### Example Integration Test:
```python
def test_scan_endpoint(client, db):
    """Test full scan workflow via API"""
    response = client.post('/api/scan-all', 
                          json={'directories': ['/media']})
    assert response.status_code == 200
    assert response.json['status'] == 'started'
    
    # Verify database state
    scan = ScanState.query.first()
    assert scan.phase == 'discovering'
```

### Performance Tests

Performance tests ensure operations meet speed requirements.

#### What's Tested:
- **File Discovery**: Speed of finding files in large directories
- **Hash Calculation**: Throughput for different file sizes
- **Database Operations**: Query performance with large datasets
- **API Response Times**: Endpoint latency under load

#### Example Performance Test:
```python
@pytest.mark.benchmark
def test_file_discovery_performance(benchmark, large_directory):
    """Benchmark file discovery for 10k files"""
    result = benchmark(discover_media_files, [large_directory])
    assert benchmark.stats['mean'] < 1.0  # Must complete in < 1 second
```

## Test Fixtures

### Common Fixtures (conftest.py)

```python
@pytest.fixture
def app():
    """Create test Flask application"""
    app = create_app(testing=True)
    return app

@pytest.fixture
def db(app):
    """Create test database"""
    with app.app_context():
        db.create_all()
        yield db
        db.drop_all()

@pytest.fixture
def scan_service(db):
    """Create ScanService instance"""
    return ScanService(database_uri='sqlite:///:memory:')

@pytest.fixture
def mock_scan_result():
    """Create mock scan result"""
    return ScanResult(
        file_path='/test/video.mp4',
        is_corrupted=False,
        file_size=1024000,
        file_type='video/mp4'
    )
```

### Media Fixtures

```python
@pytest.fixture
def corrupted_video():
    """Provide path to corrupted video file"""
    return 'tests/fixtures/corrupted/broken_video.mp4'

@pytest.fixture
def valid_image():
    """Provide path to valid image file"""
    return 'tests/fixtures/valid/good_image.jpg'
```

## Testing Best Practices

### 1. Test Isolation
- Each test should be independent
- Use fixtures for setup/teardown
- Mock external dependencies

### 2. Clear Test Names
```python
# Good
def test_scan_service_handles_missing_directory():
def test_api_returns_404_for_invalid_file():

# Bad  
def test_scan():
def test_error():
```

### 3. Arrange-Act-Assert Pattern
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

### 4. Test Edge Cases
- Empty inputs
- Invalid data types
- Boundary values
- Concurrent operations
- Error conditions

### 5. Use Mocks Appropriately
```python
# Mock external services
@patch('subprocess.run')
def test_ffmpeg_error_handling(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, 'ffmpeg')
    result = check_video_corruption('/test.mp4')
    assert result['is_corrupted'] is True
```

## Test Data

### Creating Test Media Files

```bash
# Create corrupted video for testing
dd if=/dev/urandom of=tests/fixtures/corrupted/broken.mp4 bs=1024 count=100

# Create valid but small video
ffmpeg -f lavfi -i testsrc=duration=1:size=320x240:rate=30 \
       -f lavfi -i sine=frequency=1000:duration=1 \
       -pix_fmt yuv420p tests/fixtures/valid/small.mp4
```

### Test Database

Tests use an in-memory SQLite database that's created fresh for each test:
- No persistence between tests
- Fast execution
- Identical schema to production

## Continuous Integration

### GitHub Actions Workflow

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Install dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y ffmpeg imagemagick
          pip install -r requirements.txt
          pip install -r requirements-test.txt
      - name: Run tests
        run: pytest --cov=pixelprobe --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

## Debugging Tests

### Running Specific Tests
```bash
# Run single test with output
pytest -s -v tests/test_media_checker.py::test_specific_case

# Run with debugger
pytest --pdb tests/failing_test.py

# Show local variables on failure
pytest -l
```

### Common Issues

1. **Import Errors**: Ensure PYTHONPATH includes project root
2. **Database Errors**: Check fixtures are properly scoped
3. **Async Issues**: Use pytest-asyncio for async tests
4. **File Not Found**: Use absolute paths in fixtures

## Adding New Tests

When adding new features:

1. **Write tests first** (TDD approach)
2. **Cover happy path** and error cases
3. **Add integration test** for new endpoints
4. **Update fixtures** if needed
5. **Run full suite** before committing

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

## Test Metrics

Current test coverage goals:
- **Overall**: 80% minimum
- **Core modules**: 90% minimum
- **API routes**: 85% minimum
- **Security modules**: 100% required

Run `pytest --cov` to check current coverage.