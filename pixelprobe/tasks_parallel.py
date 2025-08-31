"""
Enhanced Parallel Celery Tasks for PixelProbe
Distributes work across all available workers for maximum performance

This module contains improved Celery tasks that properly distribute work
across all available workers instead of processing everything in a single task.
"""

from celery import current_task, group, chord
from celery.exceptions import Retry
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

from celery_config import celery_app
from models import db, ScanState, ScanResult, ScanChunk
from media_checker import PixelProbe
from pixelprobe.services.scan_service import ScanService

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_chunk_task(self, chunk_id: int, scan_id: str, scan_type: str = 'full',
                       force_rescan: bool = False):
    """
    Process a single chunk of files
    
    This task processes a specific chunk independently, allowing multiple
    chunks to be processed in parallel by different workers.
    
    Args:
        chunk_id: ID of the ScanChunk to process
        scan_id: Unique scan identifier
        scan_type: Type of scan being performed
        force_rescan: Whether to force rescan of existing files
        
    Returns:
        dict: Chunk processing results
    """
    logger.info(f"Worker processing chunk {chunk_id} for scan {scan_id}")
    
    try:
        from flask import current_app
        
        # Get the chunk from database
        chunk = ScanChunk.query.get(chunk_id)
        if not chunk:
            logger.error(f"Chunk {chunk_id} not found")
            return {
                'status': 'ERROR',
                'chunk_id': chunk_id,
                'error': 'Chunk not found'
            }
        
        # Get scan state
        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if not scan_state:
            logger.error(f"Scan state {scan_id} not found")
            return {
                'status': 'ERROR',
                'chunk_id': chunk_id,
                'error': 'Scan state not found'
            }
        
        # Initialize media checker
        database_uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        checker = PixelProbe(database_path=database_uri)
        
        # Get files in this chunk that need scanning
        files_to_scan = []
        
        if chunk.directory_path == 'PENDING_FILES':
            # Special handling for pending files
            pending_files = ScanResult.query.filter_by(
                scan_status='pending'
            ).limit(1000).all()  # Process 1000 pending files per chunk
            
            files_to_scan = [f.file_path for f in pending_files]
            logger.info(f"Processing {len(files_to_scan)} pending files in chunk {chunk_id}")
        else:
            # Regular directory chunk - get files from database
            files_in_chunk = ScanResult.query.filter(
                ScanResult.file_path.like(f"{chunk.directory_path}%")
            ).filter(
                db.or_(
                    ScanResult.scan_status == 'pending',
                    db.and_(
                        force_rescan == True,
                        ScanResult.scan_status == 'completed'
                    )
                )
            ).limit(1000).all()  # Process up to 1000 files per chunk
            
            files_to_scan = [f.file_path for f in files_in_chunk]
            logger.info(f"Chunk {chunk.chunk_id}: Found {len(files_to_scan)} files to scan in {chunk.directory_path}")
        
        # Skip empty chunks
        if not files_to_scan:
            logger.info(f"Chunk {chunk_id} is empty, marking as complete")
            chunk.is_complete = True
            chunk.files_processed = 0
            
            # IMPORTANT: Clear the current_file in scan_state when skipping empty chunks
            # Otherwise the UI will show stale directory paths
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                # Don't show directory path as current file
                scan_state.current_file = ''
                # Update the message to reflect we're skipping empty directories
                scan_state.progress_message = f'Scanning: skipping empty directory {chunk.directory_path}'
            
            db.session.commit()
            return {
                'status': 'SKIPPED',
                'chunk_id': chunk_id,
                'scan_type': scan_type,
                'files_processed': 0,
                'reason': 'No files to scan in chunk'
            }
        
        # Process files based on scan type
        files_processed = 0
        files_corrupted = 0
        orphans_removed = 0
        
        if scan_type == 'orphan_cleanup':
            # Special handling for orphan cleanup
            for file_path in files_to_scan:
                try:
                    # Update task state
                    current_task.update_state(
                        state='PROGRESS',
                        meta={
                            'chunk_id': chunk_id,
                            'current': files_processed,
                            'total': len(files_to_scan),
                            'current_file': file_path,
                            'scan_id': scan_id,
                            'scan_type': 'orphan_cleanup'
                        }
                    )
                    
                    # Remove orphaned entry from database
                    db_result = ScanResult.query.filter_by(file_path=file_path).first()
                    if db_result:
                        db.session.delete(db_result)
                        orphans_removed += 1
                        
                        # Commit every 100 removals
                        if orphans_removed % 100 == 0:
                            db.session.commit()
                    
                    files_processed += 1
                    
                except Exception as e:
                    logger.error(f"Error removing orphan {file_path}: {e}")
                    continue
        else:
            # Regular file scanning
            for file_path in files_to_scan:
                # Check if scan was cancelled
                scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
                if scan_state and not scan_state.is_active:
                    logger.info(f"Chunk {chunk_id}: Scan cancelled, stopping processing")
                    chunk.status = 'cancelled'
                    chunk.end_time = datetime.now(timezone.utc)
                    db.session.commit()
                    return {
                        'status': 'CANCELLED',
                        'chunk_id': chunk_id,
                        'files_processed': files_processed,
                        'reason': 'Scan was cancelled'
                    }
                
                try:
                    # Update task state
                    current_task.update_state(
                        state='PROGRESS',
                        meta={
                            'chunk_id': chunk_id,
                            'current': files_processed,
                            'total': len(files_to_scan),
                            'current_file': file_path,
                            'scan_id': scan_id,
                            'scan_type': scan_type
                        }
                    )
                    
                    # Scan the file
                    scan_result = checker.scan_file(file_path, force_rescan=force_rescan)
                    
                    if scan_result:
                        # Update database with result
                        db_result = ScanResult.query.filter_by(file_path=file_path).first()
                        if db_result:
                            # CRITICAL FIX: Properly classify files as Corrupted, Warning, or Healthy
                            corruption_details = scan_result.get('corruption_details', '')
                            warning_details = scan_result.get('warning_details', '')
                            is_corrupted = scan_result.get('is_corrupted', False)
                            has_warnings = scan_result.get('has_warnings', False)
                            
                            # If we have corruption_details with serious errors, mark as corrupted
                            if corruption_details:
                                details_lower = corruption_details.lower()
                                if any(err in details_lower for err in ['error', 'failed', 'no such file', 'corrupted']):
                                    is_corrupted = True
                                elif 'warning' in details_lower:
                                    # If it says "warning" but not marked as corrupted, it's a warning
                                    has_warnings = True
                                    if not warning_details:
                                        warning_details = corruption_details
                            
                            # If we have warning_details but no has_warnings flag, set it
                            if warning_details and not has_warnings:
                                has_warnings = True
                            
                            db_result.is_corrupted = is_corrupted
                            db_result.scan_status = 'completed'
                            db_result.scan_date = datetime.now(timezone.utc)
                            db_result.corruption_details = corruption_details  # Keep all details for debugging
                            db_result.scan_output = str(scan_result.get('scan_output', ''))[:10000]
                            
                            # Save warning fields with proper classification
                            db_result.has_warnings = has_warnings
                            db_result.warning_details = warning_details
                            
                            # Save other important fields that were missing
                            db_result.file_hash = scan_result.get('file_hash')
                            db_result.scan_tool = scan_result.get('scan_tool', 'unknown')
                            db_result.scan_duration = scan_result.get('scan_duration')
                            db_result.file_size = scan_result.get('file_size', 0)
                            db_result.file_type = scan_result.get('file_type', 'unknown')
                            
                            if is_corrupted:
                                files_corrupted += 1
                            
                            # Commit every 100 files
                            if files_processed % 100 == 0:
                                db.session.commit()
                    
                    files_processed += 1
                    
                except Exception as e:
                    logger.error(f"Error scanning file {file_path} in chunk {chunk_id}: {e}")
                    continue
        
        # Final commit
        db.session.commit()
        
        # Mark chunk as complete
        chunk.is_complete = True
        chunk.files_processed = files_processed
        db.session.commit()
        
        if scan_type == 'orphan_cleanup':
            logger.info(f"Chunk {chunk_id} completed: {orphans_removed} orphans removed")
            return {
                'status': 'SUCCESS',
                'chunk_id': chunk_id,
                'scan_type': scan_type,
                'orphans_removed': orphans_removed,
                'completed_at': datetime.now(timezone.utc).isoformat()
            }
        else:
            logger.info(f"Chunk {chunk_id} completed: {files_processed} files processed, {files_corrupted} corrupted")
            return {
                'status': 'SUCCESS',
                'chunk_id': chunk_id,
                'scan_type': scan_type,
                'files_processed': files_processed,
                'files_corrupted': files_corrupted,
                'completed_at': datetime.now(timezone.utc).isoformat()
            }
        
    except Exception as exc:
        logger.error(f"Chunk processing task {self.request.id} failed: {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            retry_delay = 30 * (2 ** self.request.retries)  # 30s, 60s, 120s
            raise self.retry(exc=exc, countdown=retry_delay)
        else:
            # Mark chunk as failed after max retries
            try:
                chunk = ScanChunk.query.get(chunk_id)
                if chunk:
                    chunk.error_message = str(exc)
                    db.session.commit()
            except:
                pass
            raise exc


@celery_app.task(bind=True, soft_time_limit=120, time_limit=180)
def discover_directory_task(self, directory: str, scan_id: str, 
                           excluded_paths: List[str] = None, 
                           excluded_extensions: List[str] = None):
    """
    Discover files in a single directory - runs on a separate Celery worker
    
    Args:
        directory: Directory path to discover
        scan_id: Scan identifier for tracking
        excluded_paths: List of paths to exclude
        excluded_extensions: List of extensions to exclude
        
    Returns:
        List of discovered file paths
    """
    logger.info(f"Worker {self.request.id} discovering files in {directory}")
    
    try:
        import os
        import time
        from celery.exceptions import SoftTimeLimitExceeded
        from media_checker import PixelProbe
        
        discovered_files = []
        excluded_paths = excluded_paths or []
        excluded_extensions = excluded_extensions or []
        
        # Initialize media checker for file type detection only
        checker = PixelProbe(
            database_path=None  # No DB needed for discovery
        )
        
        # Track progress
        start_time = time.time()
        last_log_time = start_time
        files_checked = 0
        
        # Walk directory and discover files with progress reporting
        try:
            for root, dirs, files in os.walk(directory):
                # Skip excluded directories
                dirs[:] = [d for d in dirs if not any(
                    os.path.join(root, d).startswith(exc) for exc in excluded_paths
                )]
                
                for file in files:
                    files_checked += 1
                    
                    # Report progress every 1000 files or every 10 seconds
                    current_time = time.time()
                    if files_checked % 1000 == 0 or (current_time - last_log_time) > 10:
                        logger.info(f"Discovery progress in {directory}: checked {files_checked} files, found {len(discovered_files)} media files")
                        last_log_time = current_time
                        
                        # Update task state for monitoring
                        self.update_state(
                            state='PROGRESS',
                            meta={
                                'directory': directory,
                                'files_checked': files_checked,
                                'files_found': len(discovered_files),
                                'elapsed_time': current_time - start_time
                            }
                        )
                    
                    file_path = os.path.join(root, file)
                    
                    # Skip excluded extensions
                    ext = os.path.splitext(file)[1].lower()
                    if ext in excluded_extensions:
                        continue
                    
                    # Skip excluded paths
                    if any(file_path.startswith(exc) for exc in excluded_paths):
                        continue
                    
                    # Check if file is supported media type
                    if checker._is_supported_file(file_path):
                        discovered_files.append(file_path)
                        
        except SoftTimeLimitExceeded:
            logger.warning(f"Discovery task for {directory} timed out after checking {files_checked} files")
            # Return what we found so far
            
        except Exception as e:
            logger.error(f"Error during directory walk of {directory}: {e}")
            # Return what we found so far
        
        elapsed_time = time.time() - start_time
        logger.info(f"Worker discovered {len(discovered_files)} files in {directory} "
                   f"(checked {files_checked} total files in {elapsed_time:.2f} seconds)")
        return discovered_files
        
    except Exception as e:
        logger.error(f"Error discovering files in {directory}: {e}")
        return []


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def parallel_scan_orchestrator(self, scan_id: str, paths: List[str] = None,
                               scan_type: str = 'full', force_rescan: bool = False):
    """
    Universal orchestrator task that handles ALL scan types with parallel processing
    
    This orchestrator handles:
    - Full scan: Scan all files in specified directories
    - Parallel scan: Same as full but explicitly parallel
    - Pending scan: Scan only files marked as pending
    - File changes scan: Detect and scan changed files
    - Orphan cleanup: Remove orphaned entries
    
    All scan types are distributed across all available workers for maximum performance.
    Discovery phase is also parallelized across workers.
    
    Args:
        scan_id: Unique scan identifier
        paths: List of paths to scan (optional for pending/orphan scans)
        scan_type: Type of scan ('full', 'parallel', 'pending', 'file_changes', 'orphan_cleanup')
        force_rescan: Whether to force rescan of existing files
        
    Returns:
        dict: Overall scan results
    """
    logger.info(f"Starting parallel scan orchestrator for scan {scan_id}, type: {scan_type}")
    
    try:
        from flask import current_app
        from celery import group
        import os
        
        # Update scan state
        scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
        if not scan_state:
            # Create new scan state
            scan_state = ScanState(
                scan_id=scan_id,
                phase='discovering',
                is_active=True,
                celery_task_id=self.request.id
            )
            db.session.add(scan_state)
            db.session.commit()
        
        # Phase 1: Discovery - find files based on scan type
        logger.info(f"Phase 1: Discovering files for {scan_type} scan")
        scan_state.phase = 'discovering'
        db.session.commit()
        
        discovered_files = []
        
        if scan_type in ['full', 'parallel']:
            # Parallel directory discovery across Celery workers
            if not paths:
                raise ValueError("Paths required for full/parallel scan")
            
            # Load exclusions from database
            from models import Exclusion
            exclusions = Exclusion.query.filter_by(is_active=True).all()
            excluded_paths = [exc.pattern for exc in exclusions if exc.exclusion_type == 'path']
            excluded_extensions = [exc.pattern for exc in exclusions if exc.exclusion_type == 'extension']
            
            logger.info(f"Loaded {len(excluded_paths)} path exclusions and {len(excluded_extensions)} extension exclusions")
            
            # Create discovery tasks for each path
            discovery_tasks = []
            for path in paths:
                # For each path, create sub-tasks for major subdirectories
                if os.path.exists(path):
                    # Always add the main path as a discovery task
                    discovery_tasks.append(
                        discover_directory_task.s(path, scan_id, excluded_paths, excluded_extensions)
                    )
                    logger.info(f"Added discovery task for {path}")
            
            if discovery_tasks:
                logger.info(f"Launching {len(discovery_tasks)} parallel discovery tasks")
                
                # Execute discovery tasks in parallel
                job = group(discovery_tasks)
                result = job.apply_async()
                
                # Wait for all discovery tasks to complete with longer timeout for large directories
                try:
                    discovery_results = result.get(timeout=600)  # 10 minute timeout
                    
                    # Combine all discovered files
                    for task_files in discovery_results:
                        if task_files:
                            discovered_files.extend(task_files)
                    
                    logger.info(f"Parallel discovery complete: found {len(discovered_files)} total files")
                    
                except Exception as e:
                    logger.error(f"Error getting discovery results: {e}")
                    # Try to get partial results
                    for task_result in result.results:
                        try:
                            if task_result and task_result.ready():
                                task_files = task_result.get(timeout=1)
                                if task_files:
                                    discovered_files.extend(task_files)
                        except:
                            continue
                    logger.warning(f"Partial discovery results: found {len(discovered_files)} files before timeout")
                
                # Filter out files already in database
                if discovered_files:
                    existing_files = set()
                    # Load existing paths in chunks to avoid memory issues
                    chunk_size = 10000
                    for i in range(0, len(discovered_files), chunk_size):
                        chunk = discovered_files[i:i+chunk_size]
                        existing = ScanResult.query.filter(
                            ScanResult.file_path.in_(chunk)
                        ).with_entities(ScanResult.file_path).all()
                        existing_files.update([r[0] for r in existing])
                    
                    # Filter to only new files
                    new_files = [f for f in discovered_files if f not in existing_files]
                    logger.info(f"Filtered to {len(new_files)} new files (excluded {len(existing_files)} existing)")
                    discovered_files = new_files
            
        elif scan_type == 'pending':
            # Get all pending files from database
            pending_results = ScanResult.query.filter_by(
                scan_status='pending'
            ).all()
            discovered_files = [r.file_path for r in pending_results]
            logger.info(f"Found {len(discovered_files)} pending files to scan")
            
        elif scan_type == 'file_changes':
            # Detect changed files based on modification time
            if not paths:
                raise ValueError("Paths required for file changes scan")
            
            # Get files with their modification times
            all_files = []
            for path in paths:
                for root, dirs, files in os.walk(path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            mtime = os.path.getmtime(file_path)
                            all_files.append((file_path, mtime))
                        except:
                            continue
            
            # Check against database for changes
            for file_path, mtime in all_files:
                db_result = ScanResult.query.filter_by(file_path=file_path).first()
                if db_result:
                    # Check if file was modified after last scan
                    if db_result.scan_date:
                        last_scan_time = db_result.scan_date.timestamp()
                        if mtime > last_scan_time:
                            discovered_files.append(file_path)
                            logger.debug(f"File changed: {file_path}")
                else:
                    # New file not in database
                    discovered_files.append(file_path)
            
            logger.info(f"Found {len(discovered_files)} changed/new files")
            
        elif scan_type == 'orphan_cleanup':
            # Find orphaned entries (files that no longer exist)
            if not paths:
                # Get all scan results
                all_results = ScanResult.query.all()
            else:
                # Get results for specific paths
                all_results = []
                for path in paths:
                    path_results = ScanResult.query.filter(
                        ScanResult.file_path.like(f"{path}%")
                    ).all()
                    all_results.extend(path_results)
            
            # Check which files no longer exist
            orphaned_files = []
            for result in all_results:
                if not os.path.exists(result.file_path):
                    orphaned_files.append(result.file_path)
            
            discovered_files = orphaned_files
            logger.info(f"Found {len(discovered_files)} orphaned entries to clean up")
        
        else:
            raise ValueError(f"Unknown scan type: {scan_type}")
        
        total_files = len(discovered_files)
        
        logger.info(f"Discovered {total_files} files to scan")
        
        # Phase 2: Create chunks for parallel processing
        logger.info(f"Phase 2: Creating chunks for parallel processing")
        scan_state.phase = 'chunking'
        db.session.commit()
        
        # Get the number of available workers dynamically
        from celery import current_app as celery_app
        inspect = celery_app.control.inspect()
        stats = inspect.stats()
        
        total_workers = 0
        if stats:
            for worker_name, worker_stats in stats.items():
                pool_info = worker_stats.get('pool', {})
                concurrency = pool_info.get('max-concurrency', 0)
                total_workers += concurrency
                logger.info(f"Worker {worker_name} has {concurrency} processes")
        
        if total_workers == 0:
            # Fallback to environment variable or default
            total_workers = int(os.environ.get('CELERY_CONCURRENCY', 4))
            logger.warning(f"Could not detect workers, using configured value: {total_workers}")
        
        logger.info(f"Total available worker processes: {total_workers}")
        
        # Calculate optimal chunk size based on number of files and workers
        # We want at least 2-3 chunks per worker for good load balancing
        # But not too many chunks to avoid overhead
        if total_files > 0:
            min_chunks = total_workers * 2  # At least 2 chunks per worker
            max_chunks = total_workers * 10  # At most 10 chunks per worker
            
            # Calculate chunk size
            if total_files <= min_chunks * 100:
                # Small dataset - create minimum chunks
                chunk_size = max(1, total_files // min_chunks)
            elif total_files >= max_chunks * 1000:
                # Large dataset - limit chunk count
                chunk_size = total_files // max_chunks
            else:
                # Medium dataset - aim for ~1000 files per chunk
                chunk_size = 1000
            
            # Ensure chunk size is reasonable
            chunk_size = max(100, min(10000, chunk_size))
            
            logger.info(f"Using chunk size of {chunk_size} files for {total_workers} workers")
        else:
            logger.warning("No files to process")
            return {
                'status': 'NO_FILES',
                'scan_id': scan_id,
                'message': 'No files found to process'
            }
        
        # Delete old chunks based on scan type
        if scan_type == 'orphan_cleanup':
            # Clean up orphan chunks
            ScanChunk.query.filter_by(scan_id=scan_id).delete(synchronize_session=False)
        elif paths:
            # Delete old chunks for these paths
            for path in paths:
                ScanChunk.query.filter(
                    ScanChunk.directory_path.like(f"{path}%")
                ).delete(synchronize_session=False)
        db.session.commit()
        
        chunks_created = []
        
        # Group files by directory for better locality
        # For efficiency, batch check which files need scanning
        files_by_dir = {}
        
        # Get all pending files in one query
        if scan_type in ['full', 'file_changes']:
            # SIMPLIFIED: Just get files that need scanning directly from database
            # No need to iterate through discovered_files if we can query directly
            
            if force_rescan:
                # Get all files (new, pending, and completed for rescan)
                files_to_scan = ScanResult.query.filter(
                    db.or_(
                        ScanResult.scan_status == 'pending',
                        ScanResult.scan_status == 'completed'
                    )
                ).with_entities(ScanResult.file_path).all()
                files_needing_scan = [f[0] for f in files_to_scan]
            else:
                # Just get pending files (new and never scanned)
                files_to_scan = ScanResult.query.filter_by(
                    scan_status='pending'
                ).with_entities(ScanResult.file_path).all()
                files_needing_scan = [f[0] for f in files_to_scan]
            
            logger.info(f"Found {len(files_needing_scan)} files that need scanning (pending or force rescan)")
            
            # Group by directory for chunk creation
            for file_path in files_needing_scan:
                dir_path = '/'.join(file_path.split('/')[:-1])
                if dir_path not in files_by_dir:
                    files_by_dir[dir_path] = []
                files_by_dir[dir_path].append(file_path)
        else:
            # For other scan types, use all discovered files
            for file_path in discovered_files:
                dir_path = '/'.join(file_path.split('/')[:-1])
                if dir_path not in files_by_dir:
                    files_by_dir[dir_path] = []
                files_by_dir[dir_path].append(file_path)
        
        # Log how many files actually need scanning
        total_files_to_scan = sum(len(files) for files in files_by_dir.values())
        logger.info(f"Found {total_files_to_scan} files that need scanning out of {len(discovered_files)} discovered")
        
        # Create chunks
        chunk_id = 1
        for dir_path, dir_files in files_by_dir.items():
            # Create chunks for this directory
            for i in range(0, len(dir_files), chunk_size):
                chunk_files = dir_files[i:i+chunk_size]
                
                chunk = ScanChunk(
                    scan_id=scan_id,
                    chunk_id=chunk_id,
                    directory_path=dir_path,
                    files_discovered=len(chunk_files),
                    is_complete=False
                )
                db.session.add(chunk)
                chunks_created.append(chunk_id)
                chunk_id += 1
                
                # Add files to database as pending
                for file_path in chunk_files:
                    # Check if file exists in DB
                    existing = ScanResult.query.filter_by(file_path=file_path).first()
                    if not existing:
                        # Add as pending
                        result = ScanResult(
                            file_path=file_path,
                            scan_status='pending',
                            is_corrupted=None,
                            discovered_date=datetime.now(timezone.utc)
                        )
                        db.session.add(result)
                    elif force_rescan and existing.scan_status == 'completed':
                        # Mark for rescan
                        existing.scan_status = 'pending'
                
                # Commit every 10 chunks
                if chunk_id % 10 == 0:
                    db.session.commit()
        
        db.session.commit()
        logger.info(f"Created {len(chunks_created)} chunks for parallel processing")
        
        # Phase 3: Spawn parallel chunk processing tasks
        logger.info(f"Phase 3: Spawning {len(chunks_created)} parallel tasks")
        scan_state.phase = 'scanning'
        scan_state.estimated_total = total_files
        db.session.commit()
        
        # Create a group of parallel tasks
        job = group(
            process_chunk_task.s(chunk_id, scan_id, scan_type, force_rescan)
            for chunk_id in chunks_created
        )
        
        # Execute all tasks in parallel
        result = job.apply_async()
        
        # Save all child task IDs to chunks for cancellation support
        if hasattr(result, 'children') and result.children:
            logger.info(f"Saving {len(result.children)} task IDs to chunks for cancellation support")
            try:
                for idx, (chunk_id, child_result) in enumerate(zip(chunks_created, result.children)):
                    chunk = ScanChunk.query.filter_by(chunk_id=chunk_id).first()
                    if chunk and hasattr(child_result, 'id'):
                        chunk.celery_task_id = child_result.id
                        logger.debug(f"Saved task ID {child_result.id} to chunk {chunk_id}")
                db.session.commit()
                logger.info(f"Successfully saved task IDs for {len(result.children)} chunks")
            except Exception as e:
                logger.error(f"Error saving task IDs to chunks: {e}")
        
        # Monitor completion (this could be moved to a separate monitoring task)
        logger.info(f"Parallel scan orchestrator spawned {len(chunks_created)} tasks")
        
        return {
            'status': 'LAUNCHED',
            'scan_id': scan_id,
            'total_files': total_files,
            'chunks_created': len(chunks_created),
            'task_id': self.request.id,
            'message': f'Launched {len(chunks_created)} parallel tasks across all workers'
        }
        
    except Exception as exc:
        logger.error(f"Parallel scan orchestrator failed: {str(exc)}")
        
        # Update scan state to crashed
        try:
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                scan_state.phase = 'crashed'
                scan_state.is_active = False
                scan_state.error_message = str(exc)
                db.session.commit()
        except:
            pass
        
        raise exc


@celery_app.task
def scan_completion_monitor(scan_id: str):
    """
    Monitor task that checks if all chunks are complete and updates scan state
    
    This task runs periodically to check if all chunks for a scan are complete
    and updates the overall scan status accordingly.
    """
    try:
        # Check if all chunks are complete
        total_chunks = ScanChunk.query.filter_by(scan_id=scan_id).count()
        complete_chunks = ScanChunk.query.filter_by(
            scan_id=scan_id, 
            is_complete=True
        ).count()
        
        logger.info(f"Scan {scan_id}: {complete_chunks}/{total_chunks} chunks complete")
        
        if total_chunks > 0 and complete_chunks == total_chunks:
            # All chunks complete - mark scan as complete
            scan_state = ScanState.query.filter_by(scan_id=scan_id).first()
            if scan_state:
                scan_state.phase = 'completed'
                scan_state.is_active = False
                scan_state.end_time = datetime.now(timezone.utc)
                
                # Calculate final statistics
                total_files = ScanResult.query.filter_by(scan_status='completed').count()
                corrupted_files = ScanResult.query.filter_by(
                    scan_status='completed',
                    is_corrupted=True
                ).count()
                
                scan_state.files_processed = total_files
                scan_state.progress_message = f"Scan completed: {total_files} files processed, {corrupted_files} corrupted"
                
                db.session.commit()
                logger.info(f"Scan {scan_id} completed successfully")
                
                return {
                    'status': 'COMPLETED',
                    'scan_id': scan_id,
                    'total_files': total_files,
                    'corrupted_files': corrupted_files
                }
        else:
            # Still processing
            return {
                'status': 'IN_PROGRESS',
                'scan_id': scan_id,
                'complete': complete_chunks,
                'total': total_chunks
            }
            
    except Exception as e:
        logger.error(f"Error monitoring scan {scan_id}: {e}")
        return {
            'status': 'ERROR',
            'scan_id': scan_id,
            'error': str(e)
        }