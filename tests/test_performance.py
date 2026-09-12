"""
Performance Tests - P2 Implementation from Audit Plan
Memory and speed benchmarks for scan operations
"""

import time
import json
import psutil
import pytest
import os
import sys
import subprocess
from uuid import uuid4
from unittest.mock import patch, MagicMock
import tempfile
import random
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')
REDIS_URI = os.environ.get('PIXELPROBE_TEST_REDIS_URI')


class TestPerformance:
    """Performance and resource usage tests"""
    
    @pytest.fixture
    def performance_monitor(self):
        """Monitor resource usage during tests"""
        class PerformanceMonitor:
            def __init__(self):
                self.process = psutil.Process()
                self.start_memory = None
                self.peak_memory = None
                self.start_time = None
                self.cpu_samples = []
            
            def start(self):
                self.start_memory = self.process.memory_info().rss / 1024 / 1024  # MB
                self.peak_memory = self.start_memory
                self.start_time = time.time()
                self.cpu_samples = []
            
            def sample(self):
                current_memory = self.process.memory_info().rss / 1024 / 1024
                self.peak_memory = max(self.peak_memory, current_memory)
                self.cpu_samples.append(self.process.cpu_percent())
            
            def get_results(self):
                duration = time.time() - self.start_time
                memory_increase = self.peak_memory - self.start_memory
                avg_cpu = sum(self.cpu_samples) / len(self.cpu_samples) if self.cpu_samples else 0
                
                return {
                    'duration': duration,
                    'memory_increase_mb': memory_increase,
                    'peak_memory_mb': self.peak_memory,
                    'avg_cpu_percent': avg_cpu
                }
        
        return PerformanceMonitor()
    
    def test_large_file_discovery_performance(self, app, performance_monitor):
        """Test performance of discovering large number of files"""
        from pixelprobe.services.scan_service import ScanService
        
        # Create mock files
        num_files = 10000
        mock_files = [f'/test/video_{i}.mp4' for i in range(num_files)]
        
        performance_monitor.start()
        
        with patch('os.walk') as mock_walk:
            # Simulate directory structure
            mock_walk.return_value = [
                ('/test', ['subdir1', 'subdir2'], mock_files[:5000]),
                ('/test/subdir1', [], mock_files[5000:7500]),
                ('/test/subdir2', [], mock_files[7500:])
            ]
            
            service = ScanService(app.config['SQLALCHEMY_DATABASE_URI'])
            
            # Measure discovery performance
            start = time.time()
            discovered = []
            
            for root, dirs, files in os.walk('/test'):
                for file in files:
                    if file.endswith(('.mp4', '.avi', '.mkv')):
                        discovered.append(os.path.join(root, file))
                        
                        # Sample performance every 1000 files
                        if len(discovered) % 1000 == 0:
                            performance_monitor.sample()
            
            discovery_time = time.time() - start
        
        results = performance_monitor.get_results()
        
        # Performance assertions
        assert discovery_time < 5.0, f"Discovery took {discovery_time:.2f}s, expected < 5s"
        assert results['memory_increase_mb'] < 100, \
            f"Memory increased by {results['memory_increase_mb']:.2f}MB, expected < 100MB"
        assert len(discovered) == num_files, f"Expected {num_files} files, found {len(discovered)}"
        
        # Report performance metrics
        print(f"\nDiscovery Performance:")
        print(f"  Files: {num_files}")
        print(f"  Time: {discovery_time:.2f}s")
        print(f"  Rate: {num_files/discovery_time:.0f} files/second")
        print(f"  Memory: +{results['memory_increase_mb']:.2f}MB")
        print(f"  CPU: {results['avg_cpu_percent']:.1f}%")
    
    def test_api_response_time(self, app, db):
        """Test API endpoint response times"""
        from pixelprobe.models import ScanResult

        # Create test data
        for i in range(100):
            file = ScanResult(
                file_path=f'/test/api_{i}.mp4',
                scan_status='completed',
                is_corrupted=i % 10 == 0,
                file_size=random.randint(1000000, 100000000)
            )
            db.session.add(file)
        db.session.commit()

        endpoints = [
            ('/api/stats', 'GET', None),
            ('/api/scan-status', 'GET', None),
            ('/api/scan-results?page=1&per_page=50', 'GET', None),
        ]

        response_times = {}

        # Use internal secret header to bypass authentication in tests
        headers = {'X-Internal-Secret': app.config.get('INTERNAL_API_SECRET', 'test-internal-secret')}

        with app.test_client() as client:
            for endpoint, method, data in endpoints:
                times = []

                # Warm up
                if method == 'GET':
                    client.get(endpoint, headers=headers)
                else:
                    client.post(endpoint, json=data, headers=headers)

                # Measure response times
                for _ in range(10):
                    start = time.time()

                    if method == 'GET':
                        response = client.get(endpoint, headers=headers)
                    else:
                        response = client.post(endpoint, json=data, headers=headers)

                    elapsed = time.time() - start
                    times.append(elapsed)

                    assert response.status_code in [200, 409], \
                        f"Endpoint {endpoint} returned {response.status_code}"

                avg_time = sum(times) / len(times)
                response_times[endpoint] = avg_time

                # Assert response time is acceptable
                assert avg_time < 0.2, \
                    f"Endpoint {endpoint} avg response time {avg_time:.3f}s, expected < 200ms"

        # Report results
        print("\nAPI Response Times:")
        for endpoint, avg_time in response_times.items():
            print(f"  {endpoint}: {avg_time*1000:.1f}ms")
    
    @pytest.mark.slow
    @pytest.mark.postgres
    @pytest.mark.timeout(120)
    @pytest.mark.skipif(not (POSTGRES_URI and REDIS_URI),
                        reason='requires PIXELPROBE_TEST_POSTGRES_URI and PIXELPROBE_TEST_REDIS_URI')
    def test_sustained_load(self):
        """Test concurrent API requests against the PostgreSQL application."""
        script = r'''
import concurrent.futures
import json
import os
import random
import threading
import time

import psutil
from sqlalchemy import text
from pixelprobe.config import ProductionConfig
from pixelprobe.models import db

engine_options = ProductionConfig.SQLALCHEMY_ENGINE_OPTIONS.copy()
connect_args = engine_options.get('connect_args', {}).copy()
connect_args['options'] = (
    f"-c timezone=UTC -c search_path={os.environ['PERFORMANCE_TEST_SCHEMA']}")
engine_options['connect_args'] = connect_args
ProductionConfig.SQLALCHEMY_ENGINE_OPTIONS = engine_options
import app as app_module

app = app_module.app
with app.app_context():
    assert db.engine.dialect.name == 'postgresql'
    assert db.session.execute(text('SELECT current_schema()')).scalar() == os.environ[
        'PERFORMANCE_TEST_SCHEMA']
headers = {'X-Internal-Secret': app.config['INTERNAL_API_SECRET']}
endpoints = ('/api/stats', '/api/scan-status', '/api/scan-results?page=1')
error_kinds = {}
completed = 0
lock = threading.Lock()
process = psutil.Process()
start_memory = process.memory_info().rss
peak_memory = start_memory
cpu_samples = []

def make_request():
    global completed
    try:
        with app.test_client() as client:
            response = client.get(random.choice(endpoints), headers=headers)
        with lock:
            if response.status_code == 200:
                completed += 1
            else:
                error_kinds[f'Status {response.status_code}'] = (
                    error_kinds.get(f'Status {response.status_code}', 0) + 1)
    except Exception as exc:
        with lock:
            name = type(exc).__name__
            error_kinds[name] = error_kinds.get(name, 0) + 1

started = time.monotonic()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    futures = []
    while time.monotonic() - started < 30:
        futures.append(executor.submit(make_request))
        time.sleep(0.01)
        if len(futures) % 100 == 0:
            peak_memory = max(peak_memory, process.memory_info().rss)
            cpu_samples.append(process.cpu_percent())
    concurrent.futures.wait(futures, timeout=10)

error_count = sum(error_kinds.values())
total = completed + error_count
print(json.dumps({
    'requests_completed': completed,
    'error_count': error_count,
    'error_kinds': error_kinds,
    'error_rate': error_count / total if total else 1.0,
    'memory_increase_mb': (peak_memory - start_memory) / 1024 / 1024,
    'avg_cpu_percent': sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0,
}))
'''
        schema = f'performance_{uuid4().hex[:12]}'
        admin_engine = create_engine(POSTGRES_URI)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        source_url = make_url(POSTGRES_URI)
        database_url = source_url.update_query_dict(
            {'options': f'-csearch_path={schema}'}).render_as_string(hide_password=False)
        isolated_engine = create_engine(
            database_url,
            connect_args={'options': f'-c timezone=UTC -c search_path={schema}'},
        )
        try:
            with isolated_engine.connect() as connection:
                assert connection.execute(text('SELECT current_schema()')).scalar() == schema
        except Exception:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            admin_engine.dispose()
            raise
        finally:
            isolated_engine.dispose()
        env = os.environ | {
            'DATABASE_URL': database_url,
            'CELERY_BROKER_URL': REDIS_URI,
            'CELERY_RESULT_BACKEND': REDIS_URI,
            'POSTGRES_HOST': source_url.host,
            'POSTGRES_PORT': str(source_url.port or 5432),
            'POSTGRES_DB': source_url.database,
            'POSTGRES_USER': source_url.username,
            'POSTGRES_PASSWORD': source_url.password or '',
            'SECRET_KEY': 'performance-test-secret',
            'INTERNAL_API_SECRET': 'performance-test-internal-secret',
            'SCHEDULER_ENABLED': 'false',
            'FLASK_ENV': 'production',
            'PERFORMANCE_TEST_SCHEMA': schema,
        }
        try:
            result = subprocess.run(
                [sys.executable, '-c', script],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        finally:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            admin_engine.dispose()
        assert result.returncode == 0, result.stderr
        payload = next(
            (json.loads(line) for line in reversed(result.stdout.splitlines())
             if line.startswith('{')),
            None,
        )
        assert payload is not None, result.stdout
        print(f"\nSustained Load Test (30s):")
        print(f"  Requests completed: {payload['requests_completed']}")
        print(f"  Errors: {payload['error_count']}")
        print(f"  Error rate: {payload['error_rate'] * 100:.1f}%")
        print(f"  Memory increase: {payload['memory_increase_mb']:.2f}MB")
        print(f"  Avg CPU: {payload['avg_cpu_percent']:.1f}%")
        assert payload['error_rate'] < 0.05, payload['error_kinds']
        assert payload['memory_increase_mb'] < 200
