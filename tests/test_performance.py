"""
Performance Tests - P2 Implementation from Audit Plan
Memory and speed benchmarks for scan operations
"""

import time
import psutil
import pytest
import os
import sys
from unittest.mock import patch, MagicMock
import tempfile
import random

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    def test_sustained_load(self, authenticated_client, db, performance_monitor):
        """Test system behavior under sustained load"""
        import concurrent.futures
        
        # Duration of sustained load test
        test_duration = 30  # seconds
        
        performance_monitor.start()
        errors = []
        requests_completed = 0
        
        def make_request():
            nonlocal requests_completed
            try:
                # Mix of different operations
                operations = [
                    lambda: authenticated_client.get('/api/stats'),
                    lambda: authenticated_client.get('/api/scan-status'),
                    lambda: authenticated_client.get('/api/scan-results?page=1'),
                ]
                
                operation = random.choice(operations)
                response = operation()
                
                if response.status_code == 200:
                    requests_completed += 1
                else:
                    errors.append(f"Status {response.status_code}")
                    
            except Exception as e:
                errors.append(str(e))
        
        # Generate sustained load
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            
            while time.time() - start_time < test_duration:
                future = executor.submit(make_request)
                futures.append(future)
                time.sleep(0.01)  # 100 requests/second target
                
                # Sample performance periodically
                if len(futures) % 100 == 0:
                    performance_monitor.sample()
            
            # Wait for remaining requests
            concurrent.futures.wait(futures, timeout=10)
        
        results = performance_monitor.get_results()
        error_rate = len(errors) / (requests_completed + len(errors)) if requests_completed > 0 else 1.0
        
        # Report results
        print(f"\nSustained Load Test ({test_duration}s):")
        print(f"  Requests completed: {requests_completed}")
        print(f"  Errors: {len(errors)}")
        print(f"  Error rate: {error_rate*100:.1f}%")
        print(f"  Memory increase: {results['memory_increase_mb']:.2f}MB")
        print(f"  Avg CPU: {results['avg_cpu_percent']:.1f}%")
        
        # Assertions
        assert error_rate < 0.05, f"Error rate {error_rate*100:.1f}% exceeds 5%"
        assert results['memory_increase_mb'] < 200, \
            f"Memory increased by {results['memory_increase_mb']:.2f}MB, expected < 200MB"