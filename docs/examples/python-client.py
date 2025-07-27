#!/usr/bin/env python3
"""
PixelProbe Python Client
A complete Python client for the PixelProbe API

Requirements:
    pip install requests

Usage:
    from pixelprobe_client import PixelProbeClient
    
    client = PixelProbeClient("http://localhost:5000")
    client.scan_directory(["/media/photos"])
"""

import requests
import time
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from urllib.parse import urljoin


class PixelProbeException(Exception):
    """Base exception for PixelProbe client errors"""
    pass


class PixelProbeClient:
    """
    Client for interacting with the PixelProbe API
    
    Example:
        client = PixelProbeClient()
        client.scan_directory(["/media/photos"])
        status = client.wait_for_scan()
        corrupted = client.get_corrupted_files()
    """
    
    def __init__(self, base_url: str = "http://localhost:5000", timeout: int = 30):
        """
        Initialize the PixelProbe client
        
        Args:
            base_url: Base URL of the PixelProbe API
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    
    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an HTTP request to the API"""
        url = urljoin(self.base_url, endpoint)
        kwargs.setdefault('timeout', self.timeout)
        
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            raise PixelProbeException(f"API request failed: {e}")
    
    def health_check(self) -> Dict:
        """Check if the service is healthy"""
        response = self._request('GET', '/health')
        return response.json()
    
    def get_version(self) -> Dict:
        """Get version information"""
        response = self._request('GET', '/api/version')
        return response.json()
    
    # Scanning Operations
    
    def scan_file(self, file_path: str) -> Dict:
        """
        Scan a single file for corruption
        
        Args:
            file_path: Path to the file to scan
            
        Returns:
            Response with scan status
        """
        response = self._request('POST', '/api/scan-file', json={
            'file_path': file_path
        })
        return response.json()
    
    def scan_directory(self, directories: List[str], force_rescan: bool = False) -> Dict:
        """
        Scan multiple directories for corruption
        
        Args:
            directories: List of directory paths to scan
            force_rescan: Force rescan of already scanned files
            
        Returns:
            Response with scan status
        """
        response = self._request('POST', '/api/scan-all', json={
            'directories': directories,
            'force_rescan': force_rescan
        })
        return response.json()
    
    def scan_parallel(self, directories: List[str], num_workers: int = 4, 
                     force_rescan: bool = False) -> Dict:
        """
        Start a parallel scan with multiple workers
        
        Args:
            directories: List of directory paths to scan
            num_workers: Number of parallel workers (1-16)
            force_rescan: Force rescan of already scanned files
            
        Returns:
            Response with scan status
        """
        response = self._request('POST', '/api/scan-parallel', json={
            'directories': directories,
            'num_workers': num_workers,
            'force_rescan': force_rescan
        })
        return response.json()
    
    def get_scan_status(self) -> Dict:
        """Get current scan status and progress"""
        response = self._request('GET', '/api/scan-status')
        return response.json()
    
    def cancel_scan(self) -> Dict:
        """Cancel the currently running scan"""
        response = self._request('POST', '/api/cancel-scan')
        return response.json()
    
    def wait_for_scan(self, check_interval: int = 5, callback=None) -> Dict:
        """
        Wait for scan to complete
        
        Args:
            check_interval: Seconds between status checks
            callback: Optional callback function called with status updates
            
        Returns:
            Final scan status
        """
        while True:
            status = self.get_scan_status()
            
            if callback:
                callback(status)
            
            if status['status'] in ['completed', 'error', 'cancelled', 'idle']:
                return status
            
            time.sleep(check_interval)
    
    # Results and Statistics
    
    def get_scan_results(self, page: int = 1, per_page: int = 100,
                        scan_status: str = 'all', is_corrupted: str = 'all') -> Dict:
        """
        Get paginated scan results
        
        Args:
            page: Page number (starts at 1)
            per_page: Results per page (max 500)
            scan_status: Filter by status (all, pending, scanning, completed, error)
            is_corrupted: Filter by corruption (all, true, false)
            
        Returns:
            Paginated results
        """
        response = self._request('GET', '/api/scan-results', params={
            'page': page,
            'per_page': per_page,
            'scan_status': scan_status,
            'is_corrupted': is_corrupted
        })
        return response.json()
    
    def get_scan_result(self, result_id: int) -> Dict:
        """Get a single scan result by ID"""
        response = self._request('GET', f'/api/scan-results/{result_id}')
        return response.json()
    
    def get_corrupted_files(self, page: int = 1, per_page: int = 100) -> Dict:
        """Get list of corrupted files"""
        return self.get_scan_results(page, per_page, is_corrupted='true')
    
    def get_all_corrupted_files(self) -> List[Dict]:
        """Get all corrupted files (handles pagination automatically)"""
        all_files = []
        page = 1
        
        while True:
            result = self.get_corrupted_files(page=page, per_page=500)
            all_files.extend(result['results'])
            
            if page >= result['pages']:
                break
            
            page += 1
        
        return all_files
    
    def get_statistics(self) -> Dict:
        """Get overall statistics summary"""
        response = self._request('GET', '/api/stats/summary')
        return response.json()
    
    def get_corruption_by_type(self) -> List[Dict]:
        """Get corruption statistics by file type"""
        response = self._request('GET', '/api/stats/corruption-by-type')
        return response.json()
    
    def get_scan_history(self, days: int = 30) -> List[Dict]:
        """Get scan history for the specified number of days"""
        response = self._request('GET', '/api/stats/scan-history', params={
            'days': days
        })
        return response.json()
    
    # Administrative Operations
    
    def mark_files_as_good(self, file_ids: List[int]) -> Dict:
        """Mark files as good/healthy (removes corruption flag)"""
        response = self._request('POST', '/api/mark-as-good', json={
            'file_ids': file_ids
        })
        return response.json()
    
    def get_ignored_patterns(self) -> List[Dict]:
        """Get all ignored error patterns"""
        response = self._request('GET', '/api/ignored-patterns')
        return response.json()
    
    def add_ignored_pattern(self, pattern: str, description: str = "") -> Dict:
        """Add a new ignored error pattern"""
        response = self._request('POST', '/api/ignored-patterns', json={
            'pattern': pattern,
            'description': description
        })
        return response.json()
    
    def delete_ignored_pattern(self, pattern_id: int) -> Dict:
        """Delete an ignored error pattern"""
        response = self._request('DELETE', f'/api/ignored-patterns/{pattern_id}')
        return response.json()
    
    def get_configurations(self) -> List[Dict]:
        """Get all scan configurations"""
        response = self._request('GET', '/api/configurations')
        return response.json()
    
    def add_configuration(self, path: str) -> Dict:
        """Add a new scan configuration"""
        response = self._request('POST', '/api/configurations', json={
            'path': path
        })
        return response.json()
    
    # Export Operations
    
    def export_csv(self, filters: Optional[Dict] = None, output_file: str = None) -> bytes:
        """
        Export scan results to CSV
        
        Args:
            filters: Optional filters (scan_status, is_corrupted, start_date, end_date)
            output_file: Optional file path to save CSV
            
        Returns:
            CSV data as bytes
        """
        response = self._request('POST', '/api/export/csv', 
                               json={'filters': filters or {}})
        
        csv_data = response.content
        
        if output_file:
            with open(output_file, 'wb') as f:
                f.write(csv_data)
        
        return csv_data
    
    # Maintenance Operations
    
    def cleanup_missing_files(self, dry_run: bool = True, 
                            directories: Optional[List[str]] = None) -> Dict:
        """
        Clean up database entries for missing files
        
        Args:
            dry_run: If True, only report what would be cleaned
            directories: Optional list of directories to check
            
        Returns:
            Cleanup results
        """
        response = self._request('POST', '/api/cleanup', json={
            'dry_run': dry_run,
            'directories': directories or []
        })
        return response.json()
    
    def vacuum_database(self) -> Dict:
        """Vacuum the database to optimize performance"""
        response = self._request('POST', '/api/vacuum')
        return response.json()


def main():
    """Example usage of the PixelProbe client"""
    import argparse
    
    parser = argparse.ArgumentParser(description='PixelProbe Client')
    parser.add_argument('--url', default='http://localhost:5000', 
                       help='PixelProbe API URL')
    parser.add_argument('--scan', nargs='+', help='Directories to scan')
    parser.add_argument('--status', action='store_true', 
                       help='Show current scan status')
    parser.add_argument('--stats', action='store_true', 
                       help='Show statistics')
    parser.add_argument('--corrupted', action='store_true', 
                       help='List corrupted files')
    parser.add_argument('--export', help='Export results to CSV file')
    
    args = parser.parse_args()
    
    # Initialize client
    client = PixelProbeClient(args.url)
    
    try:
        # Check health
        health = client.health_check()
        print(f"✅ PixelProbe is {health['status']} (v{health['version']})")
        
        if args.scan:
            # Start scan
            print(f"\n📡 Starting scan of: {', '.join(args.scan)}")
            client.scan_directory(args.scan)
            
            # Wait with progress updates
            def progress_callback(status):
                if status['status'] == 'scanning':
                    pct = (status['current'] / status['total'] * 100) if status['total'] > 0 else 0
                    print(f"\r⏳ Progress: {status['current']}/{status['total']} ({pct:.1f}%) - {status['file']}", end='')
            
            result = client.wait_for_scan(callback=progress_callback)
            print(f"\n✅ Scan {result['status']}")
        
        if args.status:
            # Show status
            status = client.get_scan_status()
            print(f"\n📊 Scan Status: {status['status']}")
            if status['is_running']:
                print(f"   Progress: {status['current']}/{status['total']}")
                print(f"   Current file: {status['file']}")
        
        if args.stats:
            # Show statistics
            stats = client.get_statistics()
            print("\n📈 Statistics:")
            print(f"   Total files: {stats['total_files']:,}")
            print(f"   Scanned: {stats['scanned_files']:,}")
            print(f"   Corrupted: {stats['corrupted_files']:,}")
            print(f"   Corruption rate: {stats['corruption_rate']:.2f}%")
        
        if args.corrupted:
            # List corrupted files
            corrupted = client.get_all_corrupted_files()
            print(f"\n❌ Found {len(corrupted)} corrupted files:")
            for file in corrupted[:10]:  # Show first 10
                print(f"   - {file['file_path']}")
            if len(corrupted) > 10:
                print(f"   ... and {len(corrupted) - 10} more")
        
        if args.export:
            # Export to CSV
            print(f"\n💾 Exporting results to {args.export}")
            client.export_csv(output_file=args.export)
            print("✅ Export complete")
    
    except PixelProbeException as e:
        print(f"\n❌ Error: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())