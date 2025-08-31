"""
Unified Scan Executor - Implements DRY principles for scan operations
Part of P2 implementation from audit plan
"""

import logging
import os
from typing import List, Dict, Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import threading

logger = logging.getLogger(__name__)


class ScanExecutor:
    """Generic scan executor pattern to eliminate code duplication across scan methods"""
    
    def __init__(self, scan_type: str, batch_size: int = 100, max_workers: int = None):
        """
        Initialize the scan executor
        
        Args:
            scan_type: Type of scan (full, parallel, pending, file_changes, orphan)
            batch_size: Number of items to process in each batch
            max_workers: Maximum number of parallel workers
        """
        self.scan_type = scan_type
        self.batch_size = batch_size
        self.max_workers = max_workers or int(os.environ.get('MAX_WORKERS', 10))
        self.progress_callback = None
        self.cancel_event = threading.Event()
        self.stats = {
            'total_items': 0,
            'processed_items': 0,
            'failed_items': 0,
            'start_time': None,
            'end_time': None
        }
    
    def set_progress_callback(self, callback: Callable[[Dict], None]):
        """Set a callback function for progress updates"""
        self.progress_callback = callback
    
    def cancel(self):
        """Cancel the ongoing scan"""
        self.cancel_event.set()
        logger.info(f"Scan {self.scan_type} cancellation requested")
    
    def _batch_items(self, items: List[Any]) -> List[List[Any]]:
        """Split items into batches for processing"""
        for i in range(0, len(items), self.batch_size):
            if self.cancel_event.is_set():
                break
            yield items[i:i + self.batch_size]
    
    def _process_batch(self, batch: List[Any], process_func: Callable) -> Dict:
        """
        Process a batch of items
        
        Args:
            batch: List of items to process
            process_func: Function to process each item
            
        Returns:
            Dict with processing results
        """
        results = {
            'successful': 0,
            'failed': 0,
            'errors': []
        }
        
        for item in batch:
            if self.cancel_event.is_set():
                break
                
            try:
                process_func(item)
                results['successful'] += 1
            except Exception as e:
                logger.error(f"Error processing item {item}: {e}")
                results['failed'] += 1
                results['errors'].append(str(e))
        
        return results
    
    def _update_progress(self, batch_results: Dict):
        """Update progress statistics and notify callback"""
        self.stats['processed_items'] += batch_results['successful']
        self.stats['failed_items'] += batch_results['failed']
        
        if self.progress_callback:
            progress_data = {
                'scan_type': self.scan_type,
                'total': self.stats['total_items'],
                'processed': self.stats['processed_items'],
                'failed': self.stats['failed_items'],
                'percentage': (self.stats['processed_items'] / self.stats['total_items'] * 100) 
                              if self.stats['total_items'] > 0 else 0,
                'is_cancelled': self.cancel_event.is_set()
            }
            self.progress_callback(progress_data)
    
    def execute(self, items: List[Any], process_func: Callable, parallel: bool = True) -> Dict:
        """
        Execute scan on items with generic processing
        
        Args:
            items: List of items to scan
            process_func: Function to process each item
            parallel: Whether to use parallel processing
            
        Returns:
            Dict with execution statistics
        """
        self.stats['total_items'] = len(items)
        self.stats['start_time'] = datetime.now(timezone.utc)
        
        logger.info(f"Starting {self.scan_type} scan of {len(items)} items "
                   f"(batch_size={self.batch_size}, parallel={parallel})")
        
        try:
            if parallel:
                # Parallel execution using ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = []
                    
                    for batch in self._batch_items(items):
                        if self.cancel_event.is_set():
                            break
                        future = executor.submit(self._process_batch, batch, process_func)
                        futures.append(future)
                    
                    # Wait for all futures to complete
                    for future in as_completed(futures):
                        if self.cancel_event.is_set():
                            # Cancel remaining futures
                            for f in futures:
                                f.cancel()
                            break
                        
                        try:
                            batch_results = future.result(timeout=300)
                            self._update_progress(batch_results)
                        except Exception as e:
                            logger.error(f"Batch processing failed: {e}")
                            self._update_progress({'successful': 0, 'failed': self.batch_size})
            else:
                # Sequential execution
                for batch in self._batch_items(items):
                    if self.cancel_event.is_set():
                        break
                    batch_results = self._process_batch(batch, process_func)
                    self._update_progress(batch_results)
        
        except Exception as e:
            logger.error(f"Scan execution failed: {e}")
            self.stats['error'] = str(e)
        
        finally:
            self.stats['end_time'] = datetime.now(timezone.utc)
            duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
            self.stats['duration_seconds'] = duration
            
            if self.cancel_event.is_set():
                self.stats['status'] = 'cancelled'
                logger.info(f"{self.scan_type} scan cancelled after {duration:.2f} seconds")
            else:
                self.stats['status'] = 'completed'
                logger.info(f"{self.scan_type} scan completed in {duration:.2f} seconds")
        
        return self.stats
    
    def execute_with_phases(self, phases: List[Dict]) -> Dict:
        """
        Execute scan with multiple phases (discovery, adding, scanning)
        
        Args:
            phases: List of phase configurations with:
                - name: Phase name
                - items_func: Function to get items for this phase
                - process_func: Function to process each item
                - parallel: Whether to use parallel processing
                
        Returns:
            Dict with execution statistics for all phases
        """
        overall_stats = {
            'phases': {},
            'total_duration': 0,
            'status': 'completed'
        }
        
        for phase_config in phases:
            if self.cancel_event.is_set():
                overall_stats['status'] = 'cancelled'
                break
            
            phase_name = phase_config['name']
            logger.info(f"Starting phase: {phase_name}")
            
            # Get items for this phase
            items = phase_config['items_func']()
            
            # Execute the phase
            phase_stats = self.execute(
                items=items,
                process_func=phase_config['process_func'],
                parallel=phase_config.get('parallel', True)
            )
            
            overall_stats['phases'][phase_name] = phase_stats
            overall_stats['total_duration'] += phase_stats.get('duration_seconds', 0)
            
            # If phase failed, stop execution
            if phase_stats.get('status') != 'completed':
                overall_stats['status'] = phase_stats['status']
                break
        
        return overall_stats


class BatchProcessor:
    """Utility class for efficient batch processing operations"""
    
    @staticmethod
    def process_in_chunks(items: List, chunk_size: int, process_func: Callable) -> List[Any]:
        """
        Process items in chunks and collect results
        
        Args:
            items: Items to process
            chunk_size: Size of each chunk
            process_func: Function to process each chunk
            
        Returns:
            List of results from processing each chunk
        """
        results = []
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            chunk_result = process_func(chunk)
            results.append(chunk_result)
        return results
    
    @staticmethod
    def parallel_map(func: Callable, items: List, max_workers: int = None) -> List[Any]:
        """
        Apply a function to items in parallel
        
        Args:
            func: Function to apply to each item
            items: Items to process
            max_workers: Maximum number of parallel workers
            
        Returns:
            List of results in the same order as input items
        """
        max_workers = max_workers or int(os.environ.get('MAX_WORKERS', 10))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {executor.submit(func, item): i for i, item in enumerate(items)}
            
            # Collect results in order
            results = [None] * len(items)
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    logger.error(f"Error processing item at index {index}: {e}")
                    results[index] = None
        
        return results