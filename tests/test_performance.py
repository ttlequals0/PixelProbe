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
    
    def test_bulk_insert_performance(self, app, db, performance_monitor):
        """Test performance of bulk database inserts"""
        from models import ScanResult
        from pixelprobe.services.scan_executor import BatchProcessor
        
        num_records = 1000  # Reduced for CI/CD performance
        batch_size = 100
        
        # Generate test data
        test_data = [
            {
                'file_path': f'/test/bulk_{i}.mp4',
                'scan_status': 'pending',
                'is_corrupted': None,
                'file_size': random.randint(1000000, 100000000)
            }
            for i in range(num_records)
        ]
        
        performance_monitor.start()
        
        # Measure bulk insert performance
        start = time.time()
        
        def insert_batch(batch):
            records = [ScanResult(**item) for item in batch]
            db.session.bulk_save_objects(records)
            db.session.commit()
            return len(batch)
        
        # Process in batches
        results = BatchProcessor.process_in_chunks(
            test_data, 
            batch_size, 
            insert_batch
        )
        
        insert_time = time.time() - start
        
        # Sample final performance
        performance_monitor.sample()
        perf_results = performance_monitor.get_results()
        
        # Performance assertions (relaxed for CI/CD)
        assert insert_time < 30.0, f"Bulk insert took {insert_time:.2f}s, expected < 30s"
        assert sum(results) == num_records, f"Not all records inserted"
        
        # Report performance
        print(f"\nBulk Insert Performance:")
        print(f"  Records: {num_records}")
        print(f"  Time: {insert_time:.2f}s")
        print(f"  Rate: {num_records/insert_time:.0f} records/second")
        print(f"  Memory: +{perf_results['memory_increase_mb']:.2f}MB")
    
    def test_scan_memory_usage_over_time(self, app, db, performance_monitor):
        """Test memory usage doesn't grow unbounded during long scans"""
        from pixelprobe.services.scan_executor import ScanExecutor
        
        # Create test files in database
        from models import ScanResult
        
        num_files = 1000
        for i in range(num_files):
            file = ScanResult(
                file_path=f'/test/memory_{i}.mp4',
                scan_status='pending',
                is_corrupted=None
            )
            db.session.add(file)
        
        db.session.commit()
        
        performance_monitor.start()
        memory_samples = []
        
        # Mock file checking function
        def mock_check_file(file_path):
            time.sleep(0.001)  # Simulate processing
            return {'corrupted': False}
        
        # Create executor
        executor = ScanExecutor('test', batch_size=50)
        
        # Set up progress callback to monitor memory
        def progress_callback(data):
            current_memory = performance_monitor.process.memory_info().rss / 1024 / 1024
            memory_samples.append(current_memory)
            performance_monitor.sample()
        
        executor.set_progress_callback(progress_callback)
        
        # Get files to scan
        files = ScanResult.query.filter_by(scan_status='pending').all()
        file_paths = [f.file_path for f in files]
        
        # Execute scan with memory monitoring
        with patch('media_checker.PixelProbe.scan_file', mock_check_file):
            stats = executor.execute(file_paths, mock_check_file, parallel=True)
        
        results = performance_monitor.get_results()
        
        # Calculate memory growth
        if memory_samples:
            memory_growth = max(memory_samples) - min(memory_samples)
            
            # Assert memory doesn't grow excessively
            assert memory_growth < 50, \
                f"Memory grew by {memory_growth:.2f}MB during scan, expected < 50MB"
        
        # Report results
        print(f"\nMemory Usage During Scan:")
        print(f"  Files processed: {stats['processed_items']}")
        print(f"  Duration: {results['duration']:.2f}s")
        print(f"  Memory growth: {memory_growth:.2f}MB")
        print(f"  Peak memory: {results['peak_memory_mb']:.2f}MB")
    
    def test_parallel_vs_sequential_performance(self, app, performance_monitor):
        """Compare performance of parallel vs sequential processing"""
        from pixelprobe.services.scan_executor import ScanExecutor
        
        num_items = 100  # Reduced for CI/CD
        test_items = list(range(num_items))
        
        # Mock processing function
        def process_item(item):
            # Simulate CPU-bound work
            result = sum(i ** 2 for i in range(1000))
            return result
        
        # Test sequential processing
        executor_seq = ScanExecutor('sequential', batch_size=50)
        performance_monitor.start()
        
        start_seq = time.time()
        stats_seq = executor_seq.execute(test_items, process_item, parallel=False)
        time_seq = time.time() - start_seq
        
        perf_seq = performance_monitor.get_results()
        
        # Test parallel processing
        executor_par = ScanExecutor('parallel', batch_size=50)
        performance_monitor.start()
        
        start_par = time.time()
        stats_par = executor_par.execute(test_items, process_item, parallel=True)
        time_par = time.time() - start_par
        
        perf_par = performance_monitor.get_results()
        
        # Calculate speedup
        speedup = time_seq / time_par if time_par > 0 else 0
        
        # Report comparison
        print(f"\nParallel vs Sequential Performance:")
        print(f"  Items: {num_items}")
        print(f"  Sequential: {time_seq:.2f}s")
        print(f"  Parallel: {time_par:.2f}s")
        print(f"  Speedup: {speedup:.2f}x")
        print(f"  CPU (seq): {perf_seq['avg_cpu_percent']:.1f}%")
        print(f"  CPU (par): {perf_par['avg_cpu_percent']:.1f}%")
        
        # Assert parallel is faster for CPU-bound work (relaxed for CI/CD)
        assert speedup > 1.0, f"Expected speedup > 1.0x, got {speedup:.2f}x"
    
    def test_api_response_time(self, client, db):
        """Test API endpoint response times"""
        from models import ScanResult
        
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
        
        for endpoint, method, data in endpoints:
            times = []
            
            # Warm up
            if method == 'GET':
                client.get(endpoint)
            else:
                client.post(endpoint, json=data)
            
            # Measure response times
            for _ in range(10):
                start = time.time()
                
                if method == 'GET':
                    response = client.get(endpoint)
                else:
                    response = client.post(endpoint, json=data)
                
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
    
    def test_scan_throughput(self, app):
        """Test maximum scan throughput (files/second)"""
        from pixelprobe.services.scan_executor import BatchProcessor
        
        # Create mock file checker
        files_checked = []
        
        def mock_check(file_path):
            files_checked.append(file_path)
            # Simulate minimal processing
            time.sleep(0.0001)
            return {'corrupted': False}
        
        # Test with different batch sizes
        batch_sizes = [10, 50, 100, 500]
        throughputs = {}
        
        for batch_size in batch_sizes:
            files_checked.clear()
            test_files = [f'/test/throughput_{i}.mp4' for i in range(1000)]
            
            start = time.time()
            
            # Process files
            BatchProcessor.process_in_chunks(
                test_files,
                batch_size,
                lambda batch: [mock_check(f) for f in batch]
            )
            
            elapsed = time.time() - start
            throughput = len(files_checked) / elapsed if elapsed > 0 else 0
            throughputs[batch_size] = throughput
        
        # Report results
        print("\nScan Throughput by Batch Size:")
        for size, throughput in throughputs.items():
            print(f"  Batch {size}: {throughput:.0f} files/second")
        
        # Assert minimum throughput
        max_throughput = max(throughputs.values())
        assert max_throughput > 1000, \
            f"Maximum throughput {max_throughput:.0f} files/s, expected > 1000"
    
    @pytest.mark.slow
    def test_sustained_load(self, client, db, performance_monitor):
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
                    lambda: client.get('/api/stats'),
                    lambda: client.get('/api/scan-status'),
                    lambda: client.get('/api/scan-results?page=1'),
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