import os
import subprocess
import magic
import logging
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from PIL import Image

# Try to import pillow-heif for better HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False
import ffmpeg
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pixelprobe.utils.security import safe_subprocess_run, validate_file_path

logger = logging.getLogger(__name__)

def load_exclusions():
    """Load exclusion patterns from exclusions.json file"""
    try:
        exclusions_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'exclusions.json')
        if os.path.exists(exclusions_file):
            with open(exclusions_file, 'r') as f:
                data = json.load(f)
                return data.get('paths', []), data.get('extensions', [])
        return [], []
    except Exception as e:
        logger.error(f"Error loading exclusions.json: {e}")
        return [], []

def truncate_scan_output(output_lines, max_lines=100, max_chars=5000):
    """Truncate scan output to prevent memory issues"""
    if not output_lines:
        return []
    
    # Join output lines into single string
    full_output = '\n'.join(output_lines)
    
    # Truncate by character count first
    if len(full_output) > max_chars:
        full_output = full_output[:max_chars] + '\n... [Output truncated due to length]'
    
    # Split back into lines and limit line count
    lines = full_output.split('\n')
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ['... [Output truncated due to line count]']
    
    return lines

class PixelProbe:
    def __init__(self, max_workers=None, excluded_paths=None, excluded_extensions=None, database_path=None):
        # Video formats - including HEVC/H.265 and professional formats
        self.supported_video_formats = [
            '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
            '.hevc', '.h265',  # HEVC/H.265 formats
            '.mxf', '.prores',  # ProRes format
            '.dnxhd', '.dnxhr',  # DNxHD/DNxHR formats
            '.mts', '.m2ts', '.avchd',  # AVCHD formats
            '.mpg', '.mpeg', '.vob',  # MPEG formats
            '.3gp', '.3g2',  # Mobile formats
            '.f4v', '.f4p',  # Flash formats
            '.ogv', '.ogg',  # Ogg video
            '.rm', '.rmvb',  # RealMedia
            '.asf', '.amv',  # Other formats
            '.m2v', '.svi', '.mpe', '.mpv', '.m4p'
        ]
        
        # Image formats - including HEIC/HEIF and RAW formats
        self.supported_image_formats = [
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp',
            '.heic', '.heif',  # Apple HEIC/HEIF formats
            '.cr2', '.cr3',  # Canon RAW
            '.nef', '.nrw',  # Nikon RAW
            '.arw', '.srf', '.sr2',  # Sony RAW
            '.dng',  # Adobe Digital Negative
            '.orf',  # Olympus RAW
            '.rw2',  # Panasonic RAW
            '.pef', '.ptx',  # Pentax RAW
            '.raf',  # Fujifilm RAW
            '.raw',  # Generic RAW
            '.x3f',  # Sigma RAW
            '.dcr', '.kdc',  # Kodak RAW
            '.mos',  # Leaf RAW
            '.psd',  # Photoshop
            '.ico',  # Icon files
            '.svg',  # Scalable Vector Graphics
            '.exr',  # OpenEXR
            '.pbm', '.pgm', '.ppm', '.pnm',  # Netpbm formats
            '.hdr', '.pic',  # Radiance HDR
            '.fts', '.fits',  # FITS (astronomy)
        ]
        
        # Audio formats - NEW: Complete audio support
        self.supported_audio_formats = [
            '.mp3',  # MPEG Audio Layer 3
            '.flac',  # Free Lossless Audio Codec
            '.wav', '.wave',  # Waveform Audio
            '.aac', '.m4a',  # Advanced Audio Coding
            '.ogg', '.oga', '.opus',  # Ogg Vorbis/Opus
            '.wma',  # Windows Media Audio
            '.aiff', '.aif', '.aifc',  # Audio Interchange File Format
            '.ape',  # Monkey's Audio
            '.wv',  # WavPack
            '.tta',  # True Audio
            '.m4b',  # Audiobook format
            '.mka',  # Matroska Audio
            '.dsf', '.dff',  # DSD formats
            '.au', '.snd',  # Sun/NeXT audio
            '.voc',  # Creative Voice
            '.amr',  # Adaptive Multi-Rate
            '.ac3',  # Dolby Digital
            '.dts',  # DTS audio
            '.ra', '.ram',  # RealAudio
            '.mid', '.midi',  # MIDI (if needed)
            '.caf',  # Core Audio Format
            '.gsm',  # GSM audio
        ]
        
        self.supported_formats = (self.supported_video_formats + 
                                self.supported_image_formats + 
                                self.supported_audio_formats)
        self.max_workers = max_workers or min(4, os.cpu_count() or 1)
        self.scan_lock = threading.Lock()
        self.current_scan_file = None
        self.scan_start_time = None
        self.excluded_paths = excluded_paths or []
        self.excluded_extensions = excluded_extensions or []
        self.database_path = database_path
    
    def discover_media_files(self, directories, max_files=None, existing_files=None, progress_callback=None):
        """Phase 1: Discover all supported files and return their paths (parallel version)"""
        existing_files = existing_files or set()
        
        logger.info(f"Starting parallel file discovery in {len(directories)} directories")
        logger.info(f"Excluding {len(existing_files)} already-discovered files")
        
        # Use parallel discovery for multiple paths
        if len(directories) > 1:
            return self._discover_files_parallel(directories, max_files, existing_files, progress_callback)
        else:
            # Single path - use original sequential method
            return self._discover_files_sequential(directories, max_files, existing_files, progress_callback)
    
    def _discover_files_sequential(self, directories, max_files=None, existing_files=None, progress_callback=None):
        """Sequential file discovery for single path or fallback"""
        files_discovered = []
        files_count = 0
        existing_files = existing_files or set()
        total_files_checked = 0
        
        for directory in directories:
            if not os.path.exists(directory):
                logger.warning(f"Directory does not exist: {directory}")
                continue
            
            files = self._get_files_sorted_by_age(directory)
            logger.info(f"Found {len(files)} total files in {directory}")
            
            for file_path in files:
                total_files_checked += 1
                
                if max_files and files_count >= max_files:
                    logger.info(f"Reached maximum discovery limit of {max_files} files")
                    return files_discovered
                
                # Skip files that are already in the database
                if file_path in existing_files:
                    continue
                
                if self._is_supported_file(file_path):
                    files_discovered.append(file_path)
                    files_count += 1
                
                # Call progress callback periodically
                if progress_callback and total_files_checked % 100 == 0:
                    progress_callback(total_files_checked, files_count)
        
        logger.info(f"Discovery complete: found {len(files_discovered)} new supported files")
        return files_discovered
    
    def _discover_files_parallel(self, directories, max_files=None, existing_files=None, progress_callback=None):
        """Parallel file discovery across multiple paths"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        existing_files = existing_files or set()
        all_files = []
        max_discovery_workers = min(len(directories), self.max_workers)
        
        logger.info(f"Using {max_discovery_workers} workers for parallel path discovery")
        
        # Create shared state for thread-safe file counting
        shared_state = {
            'files_count': 0,
            'max_reached': False
        }
        count_lock = threading.Lock()
        
        def discover_path(directory):
            """Discover files in a single directory"""
            if not os.path.exists(directory):
                logger.warning(f"Directory does not exist: {directory}")
                return []
            
            try:
                files = self._get_files_sorted_by_age(directory)
                logger.info(f"Found {len(files)} total files in {directory}")
                
                path_files = []
                for file_path in files:
                    # Check global file limit across all paths
                    with count_lock:
                        if max_files and shared_state['files_count'] >= max_files:
                            shared_state['max_reached'] = True
                            logger.info(f"Reached maximum discovery limit of {max_files} files")
                            break
                        
                        # Skip files that are already in the database
                        if file_path in existing_files:
                            continue
                        
                        if self._is_supported_file(file_path):
                            path_files.append(file_path)
                            shared_state['files_count'] += 1
                
                logger.info(f"Path {directory}: discovered {len(path_files)} new supported files")
                return path_files
                
            except Exception as e:
                logger.error(f"Error discovering files in {directory}: {str(e)}")
                return []
        
        # Execute discovery in parallel
        with ThreadPoolExecutor(max_workers=max_discovery_workers) as executor:
            # Submit all path discovery tasks
            future_to_path = {executor.submit(discover_path, directory): directory 
                            for directory in directories}
            
            # Collect results as they complete
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    path_files = future.result()
                    all_files.extend(path_files)
                    
                    # Check if we've reached the maximum files limit
                    if max_files and len(all_files) >= max_files:
                        logger.info(f"Reached maximum discovery limit of {max_files} files")
                        # Cancel remaining tasks
                        for remaining_future in future_to_path:
                            if not remaining_future.done():
                                remaining_future.cancel()
                        break
                        
                except Exception as e:
                    logger.error(f"Error in path discovery for {path}: {str(e)}")
        
        # Sort all discovered files by creation time (oldest first)
        all_files.sort(key=lambda x: os.path.getctime(x))
        
        # Apply max_files limit if needed
        if max_files and len(all_files) > max_files:
            all_files = all_files[:max_files]
        
        logger.info(f"Parallel discovery complete: found {len(all_files)} new supported files across {len(directories)} paths")
        return all_files
    
    def scan_directories(self, directories, max_files=None, skip_paths=None):
        """Legacy method for backward compatibility - now uses new two-phase approach"""
        discovered_files = self.discover_media_files(directories, max_files)
        results = []
        
        skip_paths = skip_paths or set()
        
        for file_path in discovered_files:
            if file_path not in skip_paths:
                logger.info(f"Scanning new file: {file_path}")
                result = self.scan_file(file_path)
                results.append(result)
        
        return results
    
    def _get_files_sorted_by_age(self, directory):
        """Optimized file discovery using os.scandir for better performance"""
        files = []
        
        # Use os.scandir for faster directory traversal
        def scan_directory(path):
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        full_path = entry.path
                        
                        if entry.is_dir(follow_symlinks=False):
                            # Skip excluded directories
                            if not any(full_path.startswith(exc) for exc in self.excluded_paths):
                                # Recursively scan subdirectory
                                scan_directory(full_path)
                        elif entry.is_file(follow_symlinks=False):
                            # Check if file extension is supported
                            extension = os.path.splitext(entry.name)[1].lower()
                            if extension in self.supported_formats and extension not in self.excluded_extensions:
                                try:
                                    # Use DirEntry.stat() for better performance
                                    stat = entry.stat(follow_symlinks=False)
                                    files.append((full_path, stat.st_ctime))
                                except OSError:
                                    # If stat fails, skip this file
                                    continue
            except (OSError, PermissionError) as e:
                logger.warning(f"Cannot access directory {path}: {e}")
        
        # Start scanning from root directory
        scan_directory(directory)
        
        # Sort by creation time (already have the ctime from stat)
        files.sort(key=lambda x: x[1])
        
        # Return just the file paths
        return [f[0] for f in files]
    
    def _is_supported_file(self, file_path):
        extension = Path(file_path).suffix.lower()
        
        # Check if extension is excluded
        if extension in self.excluded_extensions:
            return False
            
        # Check if path is excluded
        for excluded_path in self.excluded_paths:
            if file_path.startswith(excluded_path):
                return False
                
        return extension in self.supported_formats
    
    def get_file_info(self, file_path):
        """Get basic file information without scanning for corruption"""
        try:
            file_stats = os.stat(file_path)
            file_size = file_stats.st_size
            creation_date = datetime.fromtimestamp(file_stats.st_ctime)
            last_modified = datetime.fromtimestamp(file_stats.st_mtime)
            file_type = magic.from_file(file_path, mime=True)
            
            return {
                'file_path': file_path,
                'file_size': file_size,
                'file_type': file_type,
                'creation_date': creation_date,
                'last_modified': last_modified
            }
        except Exception as e:
            logger.error(f"Error getting file info for {file_path}: {str(e)}")
            return {
                'file_path': file_path,
                'file_size': 0,
                'file_type': 'unknown',
                'creation_date': datetime.now(),
                'last_modified': datetime.now()
            }
    
    def calculate_file_hash(self, file_path):
        """Calculate SHA-256 hash of a file with optimized chunk size"""
        try:
            logger.info(f"Calculating hash for: {file_path}")
            hash_sha256 = hashlib.sha256()
            start_time = time.time()
            bytes_processed = 0
            
            # Get file size to determine optimal chunk size
            file_size = os.path.getsize(file_path)
            
            # Use larger chunks for better performance (1MB instead of 4KB)
            # This reduces the number of read operations significantly
            chunk_size = 1024 * 1024  # 1MB chunks
            
            # For very large files (>1GB), use even larger chunks
            if file_size > 1024 * 1024 * 1024:
                chunk_size = 4 * 1024 * 1024  # 4MB chunks
            
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hash_sha256.update(chunk)
                    bytes_processed += len(chunk)
                    
                    # Log progress for large files every 100MB
                    if bytes_processed % (100 * 1024 * 1024) == 0:
                        elapsed = time.time() - start_time
                        mb_processed = bytes_processed / (1024 * 1024)
                        mb_per_sec = mb_processed / elapsed if elapsed > 0 else 0
                        logger.info(f"Hash progress for {file_path}: {mb_processed:.0f}MB processed in {elapsed:.1f}s ({mb_per_sec:.1f}MB/s)")
            
            total_time = time.time() - start_time
            if total_time > 10:  # Log completion time for files that take more than 10 seconds
                mb_size = bytes_processed / (1024 * 1024)
                mb_per_sec = mb_size / total_time if total_time > 0 else 0
                logger.info(f"Hash complete for {file_path}: {mb_size:.1f}MB in {total_time:.1f}s ({mb_per_sec:.1f}MB/s)")
            
            return hash_sha256.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating hash for {file_path}: {str(e)}")
            return None
    
    def scan_files_parallel(self, file_paths, progress_callback=None, deep_scan=False, scan_paths=None, force_rescan=False):
        """Scan multiple files in parallel using ThreadPoolExecutor with path-based optimization"""
        
        # If scan_paths provided and multiple paths, use path-based parallel scanning
        if scan_paths and len(scan_paths) > 1:
            return self._scan_files_by_paths_parallel(file_paths, progress_callback, deep_scan, scan_paths, force_rescan)
        else:
            # Use original single-pool approach
            return self._scan_files_single_pool(file_paths, progress_callback, deep_scan, force_rescan)
    
    def _scan_files_single_pool(self, file_paths, progress_callback=None, deep_scan=False, force_rescan=False):
        """Original single thread pool scanning approach"""
        results = []
        completed = 0
        total = len(file_paths)
        
        logger.info(f"Starting parallel scan of {total} files with {self.max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_file = {
                executor.submit(self.scan_file, file_path, deep_scan, force_rescan): file_path 
                for file_path in file_paths
            }
            
            # Process completed tasks
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1
                    
                    # Update progress callback if provided
                    if progress_callback:
                        progress_callback(completed, total, file_path)
                        
                except Exception as e:
                    logger.error(f"Error scanning {file_path}: {str(e)}")
                    results.append({
                        'file_path': file_path,
                        'file_size': 0,
                        'file_type': 'unknown',
                        'creation_date': datetime.now(),
                        'last_modified': datetime.now(),
                        'is_corrupted': True,
                        'corruption_details': f"Scan error: {str(e)}"
                    })
                    completed += 1
                    
                    if progress_callback:
                        progress_callback(completed, total, file_path)
        
        logger.info(f"Parallel scan completed: {completed}/{total} files processed")
        return results
    
    def _scan_files_by_paths_parallel(self, file_paths, progress_callback=None, deep_scan=False, scan_paths=None, force_rescan=False):
        """Scan files using dedicated worker pools per path"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        # Organize files by their base path
        files_by_path = {}
        for file_path in file_paths:
            # Find which scan path this file belongs to
            base_path = None
            for scan_path in scan_paths:
                if file_path.startswith(scan_path):
                    base_path = scan_path
                    break
            
            if base_path:
                if base_path not in files_by_path:
                    files_by_path[base_path] = []
                files_by_path[base_path].append(file_path)
        
        # Calculate workers per path
        num_paths = len(files_by_path)
        workers_per_path = max(1, self.max_workers // num_paths)
        
        logger.info(f"Starting path-based parallel scanning: {num_paths} paths with {workers_per_path} workers each")
        
        # Shared state for progress tracking
        shared_state = {
            'completed': 0,
            'total': len(file_paths),
            'results': []
        }
        progress_lock = threading.Lock()
        
        def scan_path_files(path, path_files):
            """Scan all files in a specific path"""
            path_results = []
            logger.info(f"Starting scan of {len(path_files)} files in path: {path}")
            
            with ThreadPoolExecutor(max_workers=workers_per_path) as executor:
                # Submit all files in this path
                future_to_file = {
                    executor.submit(self.scan_file, file_path, deep_scan, force_rescan): file_path 
                    for file_path in path_files
                }
                
                # Process completed files
                for future in as_completed(future_to_file):
                    file_path = future_to_file[future]
                    try:
                        result = future.result()
                        path_results.append(result)
                        
                        # Update shared progress
                        with progress_lock:
                            shared_state['completed'] += 1
                            if progress_callback:
                                progress_callback(shared_state['completed'], shared_state['total'], file_path)
                        
                    except Exception as e:
                        logger.error(f"Error scanning {file_path}: {str(e)}")
                        error_result = {
                            'file_path': file_path,
                            'file_size': 0,
                            'file_type': 'unknown',
                            'creation_date': datetime.now(),
                            'last_modified': datetime.now(),
                            'is_corrupted': True,
                            'corruption_details': f"Scan error: {str(e)}"
                        }
                        path_results.append(error_result)
                        
                        # Update shared progress
                        with progress_lock:
                            shared_state['completed'] += 1
                            if progress_callback:
                                progress_callback(shared_state['completed'], shared_state['total'], file_path)
            
            logger.info(f"Path scan completed for {path}: {len(path_results)} files processed")
            return path_results
        
        # Execute path scanning in parallel
        with ThreadPoolExecutor(max_workers=num_paths) as path_executor:
            # Submit path scanning tasks
            future_to_path = {
                path_executor.submit(scan_path_files, path, files): path 
                for path, files in files_by_path.items()
            }
            
            # Collect results from all paths
            all_results = []
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    path_results = future.result()
                    all_results.extend(path_results)
                except Exception as e:
                    logger.error(f"Error in path scanning for {path}: {str(e)}")
        
        logger.info(f"Path-based parallel scan completed: {len(all_results)} files processed across {num_paths} paths")
        return all_results
    
    def scan_file(self, file_path, deep_scan=False, force_rescan=False):
        """Scan a single file for corruption
        
        Args:
            file_path (str): Path to the file to scan
            deep_scan (bool): If True, perform enhanced corruption detection regardless of basic scan results
            force_rescan (bool): If True, rescan the file even if already in cache
        """
        scan_start_time = time.time()
        scan_tool = None
        scan_output = []
        
        try:
            # Only log every 100th file to reduce logging overhead
            if hasattr(self, '_scan_count'):
                self._scan_count += 1
            else:
                self._scan_count = 1
                
            if self._scan_count % 100 == 0:
                logger.info(f"Scanning file #{self._scan_count}: {file_path}")
            
            # Update current scan tracking
            with self.scan_lock:
                self.current_scan_file = file_path
                self.scan_start_time = scan_start_time
            
            # Get basic file info first
            file_info = self.get_file_info(file_path)
            
            # Calculate file hash
            file_hash = self.calculate_file_hash(file_path)
            
            # Check cache if not forcing rescan
            if not force_rescan and self.database_path:
                cached_result = self._check_cache(file_path, file_hash, file_info['last_modified'])
                if cached_result:
                    logger.info(f"Using cached result for {file_path}")
                    return cached_result
            
            is_corrupted = False
            corruption_details = []
            warning_details = []
            
            extension = Path(file_path).suffix.lower()
            
            if extension in self.supported_image_formats:
                is_corrupted, details, tool, output, warnings = self._check_image_corruption(file_path)
                corruption_details.extend(details)
                scan_tool = tool
                scan_output.extend(output)
                warning_details = warnings
            elif extension in self.supported_video_formats:
                is_corrupted, details, tool, output, warnings = self._check_video_corruption(file_path, deep_scan)
                corruption_details.extend(details)
                scan_tool = tool
                scan_output.extend(output)
                warning_details = warnings
            elif extension in self.supported_audio_formats:
                is_corrupted, details, tool, output, warnings = self._check_audio_corruption(file_path, deep_scan)
                corruption_details.extend(details)
                scan_tool = tool
                scan_output.extend(output)
                warning_details = warnings
            else:
                # File type not supported for detailed scanning
                scan_tool = "unsupported"
                scan_output.append(f"File type {extension} not supported for corruption checking")
                corruption_details.append(f"File type {extension} not supported for corruption checking")
                is_corrupted = False  # Consider unsupported files as not corrupted
            
            scan_duration = time.time() - scan_start_time
            
            # Only log corrupted files and periodic status updates
            if is_corrupted:
                status = "CORRUPTED"
                logger.warning(f"CORRUPTED file found: {file_path} - {'; '.join(corruption_details)}")
            elif self._scan_count % 100 == 0:
                logger.info(f"Scan progress: {self._scan_count} files scanned")
            
            # Merge file info with scan results
            result = file_info.copy()
            result.update({
                'is_corrupted': is_corrupted,
                'corruption_details': '; '.join(corruption_details) if corruption_details else None,
                'file_hash': file_hash,
                'scan_tool': scan_tool,
                'scan_duration': scan_duration,
                'scan_output': '\n'.join(scan_output) if scan_output else None,
                'has_warnings': len(warning_details) > 0,
                'warning_details': '; '.join(warning_details) if warning_details else None
            })
            
            # Save to cache
            self._save_to_cache(file_path, result)
            
            return result
        
        except Exception as e:
            scan_duration = time.time() - scan_start_time
            logger.error(f"Error scanning file {file_path}: {str(e)}")
            return {
                'file_path': file_path,
                'file_size': 0,
                'file_type': 'unknown',
                'creation_date': datetime.now(),
                'last_modified': datetime.now(),
                'is_corrupted': True,
                'corruption_details': f"Scan error: {str(e)}",
                'file_hash': None,
                'scan_tool': 'error',
                'scan_duration': scan_duration,
                'scan_output': str(e),
                'has_warnings': False,
                'warning_details': None
            }
        finally:
            # Clear current scan tracking
            with self.scan_lock:
                self.current_scan_file = None
                self.scan_start_time = None
    
    def _check_image_corruption(self, file_path):
        corruption_details = []
        is_corrupted = False
        scan_tool = "pil"
        scan_output = []
        warning_details = []
        
        logger.info(f"Starting PIL verification for: {file_path}")
        
        # Check file type
        file_ext = os.path.splitext(file_path)[1].lower()
        is_gif = file_ext == '.gif'
        is_heic = file_ext in ['.heic', '.heif']
        
        # Check if HEIC is supported
        if is_heic and not HEIF_SUPPORT:
            scan_output.append("PIL verification: SKIPPED (HEIC support not available)")
            logger.info(f"PIL HEIC support not available, skipping PIL verification for {file_path}")
            pil_failed = False
            pil_error = None
        else:
            pil_failed = False
            pil_error = None
            
            try:
                with Image.open(file_path) as img:
                    img.verify()
                logger.info(f"PIL verification passed for: {file_path}")
                scan_output.append("PIL verification: PASSED")
            except Exception as e:
                pil_failed = True
                pil_error = str(e)
                scan_output.append(f"PIL verification: FAILED - {str(e)}")
                logger.warning(f"PIL verification failed for {file_path}: {str(e)}")
        
        pil_load_failed = False
        pil_load_error = None
        
        # Skip load test for HEIC if not supported
        if is_heic and not HEIF_SUPPORT:
            scan_output.append("PIL load test: SKIPPED (HEIC support not available)")
        else:
            try:
                with Image.open(file_path) as img:
                    img.load()
                
                if img.size[0] == 0 or img.size[1] == 0:
                    corruption_details.append("Invalid image dimensions")
                    is_corrupted = True
                    scan_output.append(f"Image dimensions: {img.size[0]}x{img.size[1]} (INVALID)")
                else:
                    scan_output.append(f"Image dimensions: {img.size[0]}x{img.size[1]}")
                
                # Note: After load(), tile data is consumed and cleared in PIL - this is normal behavior
                # Removed incorrect tile data check that was causing false positives
                
                    img.transpose(Image.FLIP_LEFT_RIGHT)
                    scan_output.append("Transform test: PASSED")
            
            except Exception as e:
                pil_load_failed = True
                pil_load_error = str(e)
                scan_output.append(f"PIL load/transform: FAILED - {str(e)}")
        
        logger.info(f"Starting ImageMagick verification for: {file_path}")
        try:
            result = safe_subprocess_run(
                ['identify', '-verbose', file_path],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',  # Replace undecodable bytes with � character
                timeout=30
            )
            
            if result.returncode != 0:
                scan_output.append(f"ImageMagick identify: FAILED (exit code {result.returncode})")
                if result.stderr:
                    scan_output.append(f"ImageMagick stderr: {result.stderr[:200]}")
                    stderr_lower = result.stderr.lower()
                    # Check for GIF header errors specifically
                    if is_gif and 'improper image header' in stderr_lower and 'readgifimage' in stderr_lower:
                        # This is a common false positive for GIFs that still work
                        logger.info(f"GIF header warning (not corruption) for {file_path}")
                    else:
                        corruption_details.append("ImageMagick identify failed")
                        is_corrupted = True
                        scan_tool = "imagemagick"
                else:
                    corruption_details.append("ImageMagick identify failed")
                    is_corrupted = True
                    scan_tool = "imagemagick"
                logger.warning(f"ImageMagick identify failed for {file_path}")
            elif result.stderr:
                # Check if this is just a metadata/profile warning (not actual corruption)
                stderr_lower = result.stderr.lower()
                is_profile_warning = 'corruptimageprofile' in stderr_lower and '@warning/profile.c' in stderr_lower
                
                if is_profile_warning:
                    # Profile warnings (like XMP) don't indicate actual image corruption
                    scan_output.append("ImageMagick identify: PASSED (with profile warnings)")
                    logger.info(f"ImageMagick profile warning (not corruption) for {file_path}: {result.stderr[:100]}")
                elif any(keyword in stderr_lower for keyword in ['error', 'corrupt', 'truncated', 'damaged']):
                    corruption_details.append(f"ImageMagick warnings: {result.stderr[:100]}")
                    is_corrupted = True
                    scan_tool = "imagemagick"
                    scan_output.append(f"ImageMagick warnings: {result.stderr[:200]}")
                    logger.warning(f"ImageMagick warnings for {file_path}: {result.stderr[:100]}")
                else:
                    scan_output.append("ImageMagick identify: PASSED (with warnings)")
            else:
                scan_output.append("ImageMagick identify: PASSED")
                logger.info(f"ImageMagick verification passed for: {file_path}")
        
        except subprocess.TimeoutExpired:
            corruption_details.append("ImageMagick identify timeout")
            is_corrupted = True
            scan_tool = "imagemagick"
            scan_output.append("ImageMagick identify: TIMEOUT")
            logger.warning(f"ImageMagick timeout for: {file_path}")
        except FileNotFoundError:
            scan_output.append("ImageMagick: NOT FOUND")
            logger.warning("ImageMagick not found, skipping advanced image checks")
        except Exception as e:
            corruption_details.append(f"ImageMagick error: {str(e)}")
            is_corrupted = True
            scan_tool = "imagemagick"
            scan_output.append(f"ImageMagick error: {str(e)}")
            logger.warning(f"ImageMagick error for {file_path}: {str(e)}")
        
        try:
            result = safe_subprocess_run(
                ['ffmpeg', '-v', 'error', '-i', file_path, '-f', 'null', '-'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0 and result.stderr:
                # Check if this is a HEIC/HEIF file with known FFmpeg compatibility issues
                file_ext = os.path.splitext(file_path)[1].lower()
                stderr_lower = result.stderr.lower()
                
                if file_ext in ['.heic', '.heif'] and any(msg in stderr_lower for msg in [
                    'moov atom not found',
                    'invalid data found',
                    'could not find codec parameters',
                    'no decoder found',
                    'unrecognized file format'
                ]):
                    # Known FFmpeg HEIC compatibility issue - check with other tools first
                    scan_output.append("FFmpeg image validation: SKIPPED (HEIC compatibility)")
                    logger.info(f"FFmpeg HEIC compatibility issue for {file_path}, relying on PIL/ImageMagick")
                else:
                    corruption_details.append("FFmpeg image validation failed")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
                    scan_output.append(f"FFmpeg image validation: FAILED")
                    scan_output.append(f"FFmpeg stderr: {result.stderr[:200]}")
            elif result.stderr:
                # Check if this is just an EXIF/metadata warning (not actual corruption)
                stderr_lower = result.stderr.lower()
                if 'invalid tiff header in exif data' in stderr_lower:
                    # EXIF metadata warnings don't indicate actual image corruption
                    scan_output.append("FFmpeg image validation: PASSED (with EXIF warnings)")
                    logger.info(f"FFmpeg EXIF warning (not corruption) for {file_path}: {result.stderr[:100]}")
                else:
                    # Other stderr output might be actual issues
                    corruption_details.append("FFmpeg image validation warnings")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
                    scan_output.append(f"FFmpeg image validation: WARNINGS")
                    scan_output.append(f"FFmpeg stderr: {result.stderr[:200]}")
            else:
                scan_output.append("FFmpeg image validation: PASSED")
        
        except subprocess.TimeoutExpired:
            corruption_details.append("FFmpeg image validation timeout")
            scan_output.append("FFmpeg image validation: TIMEOUT")
        except FileNotFoundError:
            scan_output.append("FFmpeg: NOT FOUND")
        except Exception as e:
            scan_output.append(f"FFmpeg image validation error: {str(e)}")
            logger.debug(f"FFmpeg image validation error: {str(e)}")
        
        # Check if this is a GIF with header issues that should be a warning instead
        if is_gif and is_corrupted:
            # Check if all failures are related to "cannot identify" or "improper header"
            gif_header_issue = False
            
            if pil_failed and pil_error and 'cannot identify image file' in pil_error:
                gif_header_issue = True
            
            if any('improper image header' in detail.lower() for detail in corruption_details):
                gif_header_issue = True
            
            # If FFmpeg passed but PIL/ImageMagick failed, it's likely a false positive
            ffmpeg_passed = any('FFmpeg image validation: PASSED' in line for line in scan_output)
            
            if gif_header_issue and (ffmpeg_passed or (pil_failed and not pil_load_failed)):
                # Convert to warning instead of corruption
                logger.info(f"Converting GIF header errors to warnings for {file_path}")
                is_corrupted = False
                warning_details = ["GIF header warning: Non-standard header detected (file may still be playable)"]
                # Clear corruption details since we're treating it as a warning
                corruption_details = []
        
        # Check if this is a HEIC/HEIF with compatibility issues that should be warnings
        if is_heic and is_corrupted:
            # Check if FFmpeg had compatibility issues
            ffmpeg_heic_issue = any('SKIPPED (HEIC compatibility)' in line for line in scan_output)
            
            # Check if PIL couldn't handle HEIC
            pil_heic_skipped = any('SKIPPED (HEIC support not available)' in line for line in scan_output)
            
            # Check if ImageMagick passed
            imagemagick_passed = any('ImageMagick identify: PASSED' in line for line in scan_output)
            
            # If FFmpeg had HEIC issues but ImageMagick passed, it's likely a false positive
            if (ffmpeg_heic_issue or pil_heic_skipped) and imagemagick_passed:
                logger.info(f"Converting HEIC compatibility errors to warnings for {file_path}")
                is_corrupted = False
                warning_details = ["HEIC compatibility warning: FFmpeg/PIL may not fully support this HEIC file (image is valid)"]
                corruption_details = []
        
        # Check if this is a WebP with EXIF issues that should be a warning instead
        is_webp = file_path.lower().endswith('.webp')
        if is_webp and is_corrupted:
            # Check if the only issue is EXIF/TIFF header warnings
            only_exif_issues = True
            
            # Check if FFmpeg only reported EXIF warnings
            ffmpeg_exif_only = any('PASSED (with EXIF warnings)' in line for line in scan_output)
            
            # Check if other tools passed
            pil_passed = not pil_failed or any('PIL verification: PASSED' in line for line in scan_output)
            imagemagick_passed = any('ImageMagick identify: PASSED' in line for line in scan_output)
            
            # If the only failures are EXIF-related, convert to warning
            if ffmpeg_exif_only or (pil_passed and imagemagick_passed and 
                any('invalid tiff header' in detail.lower() for detail in corruption_details)):
                logger.info(f"Converting WebP EXIF errors to warnings for {file_path}")
                is_corrupted = False
                warning_details = ["WebP EXIF warning: Invalid metadata detected (image displays correctly)"]
                # Clear corruption details since we're treating it as a warning
                corruption_details = []
        
        return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
    
    def _check_video_corruption(self, file_path, deep_scan=False):
        corruption_details = []
        is_corrupted = False
        scan_tool = "ffmpeg"
        scan_output = []
        warning_details = []
        codec_name = None
        codec_profile = None
        
        logger.info(f"Starting FFmpeg probe for: {file_path}")
        try:
            probe = ffmpeg.probe(file_path)
            
            if 'streams' not in probe or len(probe['streams']) == 0:
                corruption_details.append("No video streams found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No streams found")
                logger.warning(f"No streams found in {file_path}")
                return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
            
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                corruption_details.append("No video stream found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No video stream found")
                logger.warning(f"No video stream found in {file_path}")
            else:
                codec_name = video_stream.get('codec_name', 'unknown codec')
                codec_profile = video_stream.get('profile', '')
                pix_fmt = video_stream.get('pix_fmt', '')
                
                # Check for HEVC Main 10 specifically
                if codec_name == 'hevc' and 'Main 10' in codec_profile:
                    scan_output.append(f"Video stream: {codec_name} ({codec_profile})")
                    scan_output.append(f"Pixel format: {pix_fmt}")
                    logger.info(f"HEVC Main 10 detected in {file_path}: profile={codec_profile}, pix_fmt={pix_fmt}")
                    
                    # HEVC Main 10 requires 10-bit support - mark as warning if detected
                    if '10' in pix_fmt:  # e.g., yuv420p10le
                        warning_details.append("HEVC Main 10 profile (10-bit) - requires hardware/software support for proper playback")
                        logger.warning(f"HEVC Main 10 10-bit video detected in {file_path} - may have playback issues on some systems")
                else:
                    scan_output.append(f"Video stream: {codec_name}")
                    if codec_profile:
                        scan_output.append(f"Profile: {codec_profile}")
                    
                logger.info(f"Video stream found in {file_path}: {codec_name}")
            
            # Log duration info but don't mark as corrupted - invalid duration doesn't mean corrupted file
            if video_stream and ('duration' not in video_stream or video_stream.get('duration') in [None, 'N/A'] or 
                               (isinstance(video_stream.get('duration'), (int, float, str)) and 
                                float(video_stream.get('duration', 0)) <= 0)):
                logger.warning(f"Invalid duration in {file_path} - metadata issue, not necessarily corruption")
                duration = 'invalid/missing'
                scan_output.append(f"Duration: {duration} (metadata issue)")
            else:
                duration = video_stream.get('duration', 'unknown') if video_stream else 'unknown'
                scan_output.append(f"Duration: {duration}")
                logger.info(f"Video duration for {file_path}: {duration}")
        
        except ffmpeg.Error as e:
            corruption_details.append(f"FFmpeg probe error: {str(e)}")
            is_corrupted = True
            scan_output.append(f"FFmpeg probe: FAILED - {str(e)}")
            logger.warning(f"FFmpeg probe error for {file_path}: {str(e)}")
        except Exception as e:
            corruption_details.append(f"Video analysis error: {str(e)}")
            is_corrupted = True
            scan_output.append(f"Video analysis: FAILED - {str(e)}")
            logger.warning(f"Video analysis error for {file_path}: {str(e)}")
        
        # Get file size and calculate appropriate timeout
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_size_gb = file_size / (1024 * 1024 * 1024)
        
        # Configure shorter timeout for large files to prevent hanging: 30s base + 10s per GB, max 5 minutes
        timeout_seconds = min(30 + int(file_size_gb * 10), 300)
        logger.info(f"Starting FFmpeg validation for {file_size_gb:.2f}GB file (timeout: {timeout_seconds}s)")
        
        # Use improved FFmpeg command for corruption detection
        try:
            result = safe_subprocess_run([
                'ffmpeg', 
                '-v', 'error',           # Show only errors
                '-err_detect', 'ignore_err',  # Continue on errors to get full error report
                '-i', file_path,         # Input file
                '-t', '30',              # Only check first 30 seconds for large files
                '-c', 'copy',            # Copy streams without re-encoding (fast)
                '-f', 'null',            # Null output format
                '-'                      # Output to stdout (discarded)
            ], 
            capture_output=True,
            text=True,
            timeout=timeout_seconds
            )
            
            if result.returncode != 0:
                # Check if the error should be ignored
                if not self._check_ignored_patterns(result.stderr):
                    corruption_details.append("FFmpeg validation failed")
                    is_corrupted = True
                else:
                    logger.info(f"FFmpeg error ignored due to matching pattern for {file_path}")
            
            if result.stderr:
                error_lines = result.stderr.strip().split('\n')
                # Filter for actual corruption indicators, not metadata issues or NAL unit warnings
                # NAL unit errors alone are often false positives (container/muxing issues)
                significant_errors = []
                has_nal_errors = False
                has_reference_frame_warnings = False
                has_other_errors = False
                
                for line in error_lines:
                    line_lower = line.lower()
                    if 'invalid nal unit' in line_lower:
                        has_nal_errors = True
                        # Don't add NAL errors to significant_errors yet
                    elif 'number of reference frames' in line_lower and 'exceeds max' in line_lower:
                        has_reference_frame_warnings = True
                        # This is a common encoding issue that doesn't affect playback
                    elif (('error' in line_lower and 'duration' not in line_lower) or
                          'corrupt' in line_lower or
                          'broken' in line_lower or
                          'no frame' in line_lower):
                        significant_errors.append(line)
                        has_other_errors = True
                
                # Only include NAL errors if there are other errors OR if FFmpeg failed
                if has_nal_errors and (has_other_errors or result.returncode != 0):
                    # Add representative NAL error
                    significant_errors.append("Invalid NAL unit errors detected")
                
                if significant_errors:
                    corruption_details.append(f"FFmpeg errors: {'; '.join(significant_errors[:3])}")
                    is_corrupted = True
                elif (has_nal_errors or has_reference_frame_warnings) and result.returncode == 0:
                    # NAL errors or reference frame warnings only - mark as warning instead of corrupted
                    warnings = []
                    if has_nal_errors:
                        warnings.append("NAL unit errors detected")
                    if has_reference_frame_warnings:
                        warnings.append("H.264 reference frame count exceeds profile limit")
                    
                    warning_msg = " and ".join(warnings) + " (video may have minor playback issues)"
                    logger.info(f"FFmpeg found only minor warnings for {file_path}: {warning_msg}")
                    warning_details = [warning_msg]
                else:
                    logger.info(f"FFmpeg completed with non-critical warnings for {file_path}")
        
        except subprocess.TimeoutExpired:
            corruption_details.append(f"FFmpeg validation timeout ({timeout_seconds}s) - large file may need longer validation")
            is_corrupted = True
            logger.warning(f"FFmpeg timeout for {file_path} - {file_size_gb:.2f}GB file exceeded {timeout_seconds}s timeout")
        except FileNotFoundError:
            logger.warning("FFmpeg not found, skipping advanced video checks")
        except Exception as e:
            corruption_details.append(f"FFmpeg validation error: {str(e)}")
            is_corrupted = True
        
        try:
            result = safe_subprocess_run(
                ['ffmpeg', '-v', 'error', '-t', '10', '-i', file_path, '-f', 'null', '-'],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0 and result.stderr:
                corruption_details.append("Quick scan detected issues in first 10 seconds")
                is_corrupted = True
        
        except subprocess.TimeoutExpired:
            corruption_details.append("Quick scan timeout")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"Quick scan error: {str(e)}")
        
        # Adaptive strategy: Run enhanced checks if basic scan failed or deep_scan requested
        if is_corrupted or deep_scan:
            logger.info(f"Running enhanced corruption detection for {file_path}")
            enhanced_corrupted, enhanced_details, enhanced_output = self._enhanced_corruption_check(file_path, file_size_gb)
            if enhanced_corrupted:
                is_corrupted = True
                corruption_details.extend(enhanced_details)
                scan_output.extend(enhanced_output)
        
        # Additional HEVC Main 10 specific checks
        if not is_corrupted and codec_name == 'hevc' and codec_profile and 'Main 10' in codec_profile:
            hevc_corrupted, hevc_details, hevc_output = self._check_hevc_main10_issues(file_path)
            if hevc_corrupted:
                is_corrupted = True
                corruption_details.extend(hevc_details)
                scan_output.extend(hevc_output)
        
        # Return warning details as well
        return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
    
    def _check_audio_corruption(self, file_path, deep_scan=False):
        """Check audio files for corruption using FFmpeg and format-specific tools"""
        corruption_details = []
        is_corrupted = False
        scan_tool = "ffmpeg"
        scan_output = []
        warning_details = []
        
        # Step 1: Basic FFprobe analysis
        logger.info(f"Running FFprobe on audio file: {file_path}")
        try:
            probe = ffmpeg.probe(file_path)
            scan_output.append("FFprobe: PASSED")
            
            # Check for audio streams
            if 'streams' not in probe or len(probe['streams']) == 0:
                corruption_details.append("No audio streams found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No streams found")
                logger.warning(f"No streams found in {file_path}")
                return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
            
            audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
            if not audio_stream:
                corruption_details.append("No audio stream found")
                is_corrupted = True
                scan_output.append("FFmpeg probe: No audio stream")
                logger.warning(f"No audio stream found in {file_path}")
                return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
                
            # Check audio stream properties
            codec_name = audio_stream.get('codec_name', 'unknown')
            sample_rate = audio_stream.get('sample_rate', 'unknown')
            channels = audio_stream.get('channels', 'unknown')
            bit_rate = audio_stream.get('bit_rate', 'unknown')
            duration = audio_stream.get('duration', 'unknown')
            
            logger.info(f"Audio details - Codec: {codec_name}, Sample rate: {sample_rate}, Channels: {channels}, Bitrate: {bit_rate}")
            scan_output.append(f"Audio stream: {codec_name}, {sample_rate}Hz, {channels}ch")
            
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8') if e.stderr else ''
            if 'Invalid data found when processing input' in stderr:
                if not self._check_ignored_patterns(stderr):
                    corruption_details.append("Invalid data found in audio file")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
            elif 'moov atom not found' in stderr:
                if not self._check_ignored_patterns(stderr):
                    corruption_details.append("Missing moov atom (audio metadata)")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
            else:
                if not self._check_ignored_patterns(stderr):
                    corruption_details.append(f"FFprobe error: {stderr[:100]}")
                    is_corrupted = True
                    scan_tool = "ffmpeg"
            scan_output.append(f"FFprobe: FAILED - {stderr[:200]}")
            logger.error(f"FFprobe error on audio {file_path}: {stderr[:200]}")
            return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
        
        # Step 2: Attempt to decode audio to check for corruption
        logger.info(f"Attempting audio decode test for: {file_path}")
        try:
            # Use ffmpeg to decode a portion of the audio
            decode_duration = 10 if not deep_scan else 30  # Decode first 10s (or 30s for deep scan)
            
            result = safe_subprocess_run([
                'ffmpeg', '-v', 'error',
                '-i', file_path,
                '-t', str(decode_duration),
                '-f', 'null', '-'
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                stderr = result.stderr
                scan_output.append(f"Audio decode: FAILED - {stderr[:200]}")
                
                # Analyze specific audio errors
                if 'Error while decoding stream' in stderr:
                    corruption_details.append("Audio stream decoding errors detected")
                    is_corrupted = True
                elif 'Invalid frame size' in stderr:
                    corruption_details.append("Invalid audio frame size")
                    is_corrupted = True
                elif 'Header missing' in stderr:
                    corruption_details.append("Audio header missing or corrupted")
                    is_corrupted = True
                elif 'Truncated' in stderr:
                    corruption_details.append("Truncated audio file")
                    is_corrupted = True
                else:
                    # Check for specific codec errors
                    if 'mp3' in codec_name.lower() and 'Header missing' in stderr:
                        corruption_details.append("MP3 header corruption")
                        is_corrupted = True
                    elif 'flac' in codec_name.lower() and 'crc mismatch' in stderr:
                        corruption_details.append("FLAC CRC mismatch - data corruption")
                        is_corrupted = True
                    else:
                        corruption_details.append("Audio decoding failed")
                        is_corrupted = True
                        
                logger.warning(f"Audio decode failed for {file_path}: {stderr[:100]}")
            else:
                scan_output.append(f"Audio decode ({decode_duration}s): PASSED")
                logger.info(f"Audio decode test passed for {file_path}")
                
        except subprocess.TimeoutExpired:
            warning_details.append("Audio decode test timeout (file may be very large)")
            scan_output.append("Audio decode: TIMEOUT")
            logger.warning(f"Audio decode timeout for {file_path}")
        except Exception as e:
            scan_output.append(f"Audio decode: ERROR - {str(e)}")
            logger.error(f"Error during audio decode test for {file_path}: {str(e)}")
        
        # Step 3: Deep scan - check entire file if requested
        if deep_scan and not is_corrupted:
            logger.info(f"Running deep audio scan for: {file_path}")
            try:
                # Scan entire file for errors
                result = safe_subprocess_run([
                    'ffmpeg', '-v', 'error',
                    '-i', file_path,
                    '-f', 'null', '-'
                ], capture_output=True, text=True, timeout=300)  # 5 minute timeout for deep scan
                
                if result.stderr:
                    # Look for non-fatal warnings that might indicate issues
                    stderr_lower = result.stderr.lower()
                    if 'non-monotonous dts' in stderr_lower:
                        warning_details.append("Non-monotonous timestamps detected")
                    if 'queue input is backward in time' in stderr_lower:
                        warning_details.append("Timestamp inconsistencies detected")
                    if 'invalid packet size' in stderr_lower:
                        warning_details.append("Invalid packet sizes detected")
                        
                    scan_output.append(f"Deep scan warnings: {result.stderr[:200]}")
                else:
                    scan_output.append("Deep audio scan: PASSED")
                    
            except subprocess.TimeoutExpired:
                warning_details.append("Deep scan timeout")
                scan_output.append("Deep scan: TIMEOUT")
            except Exception as e:
                scan_output.append(f"Deep scan: ERROR - {str(e)}")
                logger.error(f"Error during deep audio scan for {file_path}: {str(e)}")
        
        # Step 4: Format-specific validation for lossless formats
        extension = Path(file_path).suffix.lower()
        if extension == '.flac':
            # FLAC has built-in error detection
            logger.info(f"Running FLAC-specific validation for: {file_path}")
            try:
                result = safe_subprocess_run([
                    'flac', '-t', file_path
                ], capture_output=True, text=True, timeout=60)
                
                if result.returncode != 0:
                    corruption_details.append("FLAC validation failed")
                    is_corrupted = True
                    scan_output.append(f"FLAC test: FAILED - {result.stderr[:200]}")
                else:
                    scan_output.append("FLAC test: PASSED")
            except FileNotFoundError:
                # flac command not available, skip this test
                logger.debug("FLAC command not found, skipping FLAC-specific test")
            except Exception as e:
                logger.debug(f"FLAC test error: {str(e)}")
        
        return is_corrupted, corruption_details, scan_tool, truncate_scan_output(scan_output), warning_details
    
    def _check_hevc_main10_issues(self, file_path):
        """Check for HEVC Main 10 specific issues that cause green tint/freezing"""
        corruption_details = []
        is_corrupted = False
        hevc_output = []
        
        logger.info(f"Running HEVC Main 10 specific checks for {file_path}")
        hevc_output.append("=== HEVC Main 10 Analysis ===")
        
        try:
            # Check for B-frame decoding issues common in HEVC Main 10
            # Using more aggressive error detection to catch issues that cause playback freezing
            result = subprocess.run([
                'ffmpeg',
                '-v', 'warning',
                '-err_detect', 'aggressive',
                '-i', file_path,
                '-vf', 'showinfo',
                '-frames:v', '100',
                '-f', 'null',
                '-'
            ], capture_output=True, text=True, timeout=30)
            
            if result.stderr:
                stderr_lower = result.stderr.lower()
                # Look for specific HEVC Main 10 decoding issues
                if 'reference picture missing' in stderr_lower:
                    corruption_details.append("HEVC reference picture errors - causes video freezing")
                    is_corrupted = True
                    hevc_output.append("Reference picture errors found (causes playback freezing)")
                
                if 'error while decoding' in stderr_lower:
                    corruption_details.append("HEVC decoding errors - video freezes while audio continues")
                    is_corrupted = True
                    hevc_output.append("Decoding errors found (VLC stops, Plex freezes video)")
                
                # Check for slice decoding errors that cause green artifacts
                if 'slice' in stderr_lower and ('error' in stderr_lower or 'invalid' in stderr_lower):
                    corruption_details.append("HEVC slice decoding errors - causes green tint/artifacts")
                    is_corrupted = True
                    hevc_output.append("Slice decoding errors (causes green tint)")
                
                # Check for SEI (Supplemental Enhancement Information) errors
                if 'sei' in stderr_lower and 'error' in stderr_lower:
                    corruption_details.append("HEVC SEI errors detected")
                    is_corrupted = True
                    hevc_output.append("SEI metadata errors found")
            
            # Check for color space conversion issues (10-bit to 8-bit)
            result = subprocess.run([
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=color_space,color_transfer,color_primaries',
                '-of', 'json',
                file_path
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout:
                import json
                try:
                    probe_data = json.loads(result.stdout)
                    if probe_data.get('streams'):
                        stream = probe_data['streams'][0]
                        if stream.get('color_space') == 'bt2020nc' or stream.get('color_primaries') == 'bt2020':
                            hevc_output.append("HDR content detected (BT.2020) - requires HDR display support")
                except json.JSONDecodeError:
                    pass
                    
        except subprocess.TimeoutExpired:
            hevc_output.append("HEVC analysis timeout")
        except Exception as e:
            hevc_output.append(f"HEVC analysis error: {str(e)}")
            logger.error(f"HEVC Main 10 check error for {file_path}: {str(e)}")
        
        return is_corrupted, corruption_details, hevc_output
    
    def _enhanced_corruption_check(self, file_path, file_size_gb):
        """Enhanced multi-stage corruption detection for files that fail basic checks"""
        corruption_details = []
        is_corrupted = False
        enhanced_output = []
        
        logger.info(f"Starting enhanced corruption analysis for {file_path}")
        enhanced_output.append(f"=== Enhanced Corruption Analysis for {file_size_gb:.2f}GB file ===")
        
        # Stage 1: Frame count verification
        frame_corrupted, frame_details = self._check_frame_integrity(file_path)
        enhanced_output.append("Stage 1: Frame integrity check")
        if frame_corrupted:
            is_corrupted = True
            corruption_details.extend(frame_details)
            enhanced_output.append(f"  Result: FAILED - {'; '.join(frame_details)}")
        else:
            enhanced_output.append("  Result: PASSED")
        
        # Stage 2: Temporal outlier detection (for files > 1GB)
        if file_size_gb > 1.0:
            temporal_corrupted, temporal_details = self._check_temporal_outliers(file_path)
            enhanced_output.append("Stage 2: Temporal outlier detection")
            if temporal_corrupted:
                is_corrupted = True
                corruption_details.extend(temporal_details)
                enhanced_output.append(f"  Result: FAILED - {'; '.join(temporal_details)}")
            else:
                enhanced_output.append("  Result: PASSED")
        else:
            enhanced_output.append("Stage 2: Skipped (file < 1GB)")
        
        # Stage 3: Multi-point sampling for large files
        if file_size_gb > 5.0:
            sampling_corrupted, sampling_details = self._check_multipoint_sampling(file_path)
            enhanced_output.append("Stage 3: Multi-point sampling")
            if sampling_corrupted:
                is_corrupted = True
                corruption_details.extend(sampling_details)
                enhanced_output.append(f"  Result: FAILED - {'; '.join(sampling_details)}")
            else:
                enhanced_output.append("  Result: PASSED")
        else:
            enhanced_output.append("Stage 3: Skipped (file < 5GB)")
        
        # Stage 4: Enhanced error detection with strict flags
        strict_corrupted, strict_details = self._check_strict_error_detection(file_path)
        enhanced_output.append("Stage 4: Strict error detection")
        if strict_corrupted:
            is_corrupted = True
            corruption_details.extend(strict_details)
            enhanced_output.append(f"  Result: FAILED - {'; '.join(strict_details)}")
        else:
            enhanced_output.append("  Result: PASSED")
        
        enhanced_output.append(f"=== Enhanced Analysis Complete: {'CORRUPTED' if is_corrupted else 'CLEAN'} ===")
        return is_corrupted, corruption_details, enhanced_output
    
    def _check_frame_integrity(self, file_path):
        """Verify frame count matches expected count based on duration and framerate"""
        corruption_details = []
        is_corrupted = False
        
        try:
            logger.info(f"Checking frame integrity for {file_path}")
            result = safe_subprocess_run([
                'ffprobe', 
                '-show_entries', 'stream=r_frame_rate,nb_read_frames,duration',
                '-select_streams', 'v:0',
                '-count_frames',
                '-of', 'csv=p=0',
                '-v', 'quiet',
                file_path
            ], capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                if lines:
                    # Parse: stream,framerate,frame_count,duration
                    parts = lines[0].split(',')
                    if len(parts) >= 4:
                        framerate_str = parts[1]
                        frame_count_str = parts[2]
                        duration_str = parts[3]
                        
                        if framerate_str and frame_count_str and duration_str:
                            # Calculate expected vs actual frames
                            if '/' in framerate_str:
                                num, den = map(float, framerate_str.split('/'))
                                framerate = num / den if den != 0 else 0
                            else:
                                framerate = float(framerate_str)
                            
                            actual_frames = int(frame_count_str) if frame_count_str.isdigit() else 0
                            duration = float(duration_str)
                            expected_frames = int(framerate * duration)
                            
                            frame_diff = abs(expected_frames - actual_frames)
                            frame_diff_percent = (frame_diff / expected_frames * 100) if expected_frames > 0 else 0
                            
                            logger.info(f"Frame analysis: Expected {expected_frames}, Found {actual_frames}, Diff: {frame_diff} ({frame_diff_percent:.1f}%)")
                            
                            # Consider significant frame loss as corruption (>5% missing)
                            if frame_diff_percent > 5.0:
                                corruption_details.append(f"Significant frame loss: {frame_diff} frames missing ({frame_diff_percent:.1f}%)")
                                is_corrupted = True
                            elif frame_diff_percent > 1.0:
                                corruption_details.append(f"Minor frame inconsistency: {frame_diff} frames ({frame_diff_percent:.1f}%)")
                        
        except subprocess.TimeoutExpired:
            corruption_details.append("Frame integrity check timeout")
        except Exception as e:
            logger.debug(f"Frame integrity check error: {str(e)}")
        
        return is_corrupted, corruption_details
    
    def _check_temporal_outliers(self, file_path):
        """Detect temporal outliers that indicate visual corruption using signalstats"""
        corruption_details = []
        is_corrupted = False
        
        try:
            logger.info(f"Checking temporal outliers for {file_path}")
            result = safe_subprocess_run([
                'ffprobe',
                '-f', 'lavfi',
                '-i', f'movie={file_path},signalstats=stat=tout+vrep',
                '-show_entries', 'frame=pkt_pts_time:frame_tags=lavfi.signalstats.TOUT,lavfi.signalstats.VREP',
                '-of', 'csv=p=0',
                '-v', 'quiet'
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                high_tout_count = 0
                high_vrep_count = 0
                
                for line in lines:
                    parts = line.split(',')
                    if len(parts) >= 3:
                        try:
                            tout_val = float(parts[1]) if parts[1] else 0
                            vrep_val = float(parts[2]) if parts[2] else 0
                            
                            # High temporal outlier values indicate corruption
                            if tout_val > 0.1:  # Threshold for temporal outliers
                                high_tout_count += 1
                            if vrep_val > 0.5:  # Threshold for vertical repetition
                                high_vrep_count += 1
                        except (ValueError, IndexError):
                            continue
                
                total_frames = len(lines)
                if total_frames > 0:
                    tout_percent = (high_tout_count / total_frames) * 100
                    vrep_percent = (high_vrep_count / total_frames) * 100
                    
                    logger.info(f"Temporal analysis: {tout_percent:.1f}% outliers, {vrep_percent:.1f}% vertical repetition")
                    
                    if tout_percent > 5.0:  # >5% of frames have temporal outliers
                        corruption_details.append(f"High temporal outliers detected: {tout_percent:.1f}% of frames")
                        is_corrupted = True
                    if vrep_percent > 10.0:  # >10% of frames have vertical repetition
                        corruption_details.append(f"Excessive vertical line repetition: {vrep_percent:.1f}% of frames")
                        is_corrupted = True
                        
        except subprocess.TimeoutExpired:
            corruption_details.append("Temporal outlier check timeout")
        except Exception as e:
            logger.debug(f"Temporal outlier check error: {str(e)}")
        
        return is_corrupted, corruption_details
    
    def _check_multipoint_sampling(self, file_path):
        """Check beginning, middle, and end of large files for corruption"""
        corruption_details = []
        is_corrupted = False
        
        try:
            # Get video duration first
            probe = ffmpeg.probe(file_path)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream or 'duration' not in video_stream:
                return is_corrupted, corruption_details
            
            duration = float(video_stream['duration'])
            sample_points = [
                (0, 10, "beginning"),
                (duration * 0.5, 10, "middle"), 
                (max(0, duration - 10), 10, "end")
            ]
            
            logger.info(f"Multi-point sampling for {file_path} (duration: {duration:.1f}s)")
            
            for start_time, sample_duration, location in sample_points:
                try:
                    result = safe_subprocess_run([
                        'ffmpeg',
                        '-v', 'error',
                        '-err_detect', 'crccheck+bitstream',
                        '-ss', str(start_time),
                        '-t', str(sample_duration),
                        '-i', file_path,
                        '-f', 'null',
                        '-'
                    ], capture_output=True, text=True, timeout=30)
                    
                    if result.returncode != 0 or result.stderr:
                        corruption_details.append(f"Corruption detected in {location} section")
                        is_corrupted = True
                        logger.warning(f"Corruption found in {location} of {file_path}")
                        
                except subprocess.TimeoutExpired:
                    corruption_details.append(f"Timeout checking {location} section")
                    
        except Exception as e:
            logger.debug(f"Multi-point sampling error: {str(e)}")
        
        return is_corrupted, corruption_details
    
    def _check_strict_error_detection(self, file_path):
        """Enhanced error detection with strict error checking flags"""
        corruption_details = []
        is_corrupted = False
        
        try:
            logger.info(f"Running strict error detection for {file_path}")
            result = safe_subprocess_run([
                'ffmpeg',
                '-v', 'error',
                '-err_detect', 'crccheck+bitstream+buffer+explode',
                '-i', file_path,
                '-t', '30',  # First 30 seconds with strict checking
                '-f', 'null',
                '-'
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                corruption_details.append("Strict error detection failed")
                is_corrupted = True
            
            if result.stderr:
                # Enhanced error pattern recognition
                # NAL unit errors are often false positives (container/muxing issues)
                # Only mark as corrupted if there are multiple types of errors
                error_patterns = [
                    ('invalid nal unit', 'Invalid NAL unit structure'),
                    ('error while decoding mb', 'Macroblock decoding error'),
                    ('cabac decode', 'CABAC decoding failure'),
                    ('concealing errors', 'Error concealment activated'),
                    ('corrupted frame', 'Frame corruption detected'),
                    ('packet corrupt', 'Packet corruption detected'),
                    ('crc mismatch', 'CRC checksum failure')
                ]
                
                stderr_lower = result.stderr.lower()
                found_errors = []
                nal_unit_only = True
                
                for pattern, description in error_patterns:
                    if pattern in stderr_lower:
                        found_errors.append(description)
                        if pattern != 'invalid nal unit':
                            nal_unit_only = False
                        logger.info(f"Detected: {description} in {file_path}")
                
                # Only mark as corrupted if:
                # 1. There are non-NAL unit errors, OR
                # 2. The return code is non-zero AND there are NAL unit errors
                if found_errors and (not nal_unit_only or result.returncode != 0):
                    corruption_details.extend(found_errors)
                    is_corrupted = True
                    logger.warning(f"Marking as corrupted due to: {', '.join(found_errors)}")
                elif nal_unit_only and result.returncode == 0:
                    logger.info(f"NAL unit errors only (not marking as corrupted) for {file_path}")
                    # Don't mark as corrupted, but include in details for warning handling
                    corruption_details.append("NAL unit warnings only (strict mode)")
                    # Note: The calling function will handle this as a warning
                        
        except subprocess.TimeoutExpired:
            corruption_details.append("Strict error detection timeout")
        except Exception as e:
            logger.debug(f"Strict error detection error: {str(e)}")
        
        return is_corrupted, corruption_details
    
    def check_file_changes(self, scan_results_db):
        """Check for file changes by comparing current hashes with stored hashes"""
        changed_files = []
        
        for result in scan_results_db:
            file_path = result.file_path
            stored_hash = result.file_hash
            stored_modified = result.last_modified
            
            if not os.path.exists(file_path):
                changed_files.append({
                    'file_path': file_path,
                    'change_type': 'deleted',
                    'stored_hash': stored_hash,
                    'current_hash': None
                })
                continue
            
            # Check if file was modified
            current_stats = os.stat(file_path)
            current_modified = datetime.fromtimestamp(current_stats.st_mtime)
            
            if stored_modified and current_modified != stored_modified:
                current_hash = self.calculate_file_hash(file_path)
                if current_hash != stored_hash:
                    changed_files.append({
                        'file_path': file_path,
                        'change_type': 'modified',
                        'stored_hash': stored_hash,
                        'current_hash': current_hash,
                        'stored_modified': stored_modified,
                        'current_modified': current_modified
                    })
        
        return changed_files
    
    def find_orphaned_records(self, scan_results_db):
        """Find database records for files that no longer exist"""
        orphaned_records = []
        
        for result in scan_results_db:
            if not os.path.exists(result.file_path):
                orphaned_records.append(result)
        
        return orphaned_records
    
    def get_current_scan_info(self):
        """Get current scan progress information"""
        with self.scan_lock:
            if self.current_scan_file and self.scan_start_time:
                elapsed = time.time() - self.scan_start_time
                return {
                    'current_file': self.current_scan_file,
                    'elapsed_time': elapsed,
                    'is_scanning': True
                }
            return {
                'current_file': None,
                'elapsed_time': 0,
                'is_scanning': False
            }
    
    def _check_cache(self, file_path, file_hash, last_modified):
        """Check if we have a valid cached scan result for this file"""
        if not self.database_path:
            return None
            
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from models import ScanResult
            
            engine = create_engine(self.database_path)
            Session = sessionmaker(bind=engine)
            session = Session()
            
            # Check for existing scan result
            result = session.query(ScanResult).filter_by(file_path=file_path).first()
            
            if result and result.scan_date:
                # Check if file hasn't changed (same hash and modification time)
                if (result.file_hash == file_hash and 
                    result.last_modified and 
                    result.last_modified.replace(tzinfo=None) == last_modified.replace(tzinfo=None)):
                    
                    # Convert database result to expected format
                    cached_data = {
                        'file_path': result.file_path,
                        'file_size': result.file_size,
                        'file_type': result.file_type,
                        'creation_date': result.creation_date,
                        'last_modified': result.last_modified,
                        'is_corrupted': result.is_corrupted,
                        'corruption_details': result.corruption_details,
                        'file_hash': result.file_hash,
                        'scan_tool': result.scan_tool,
                        'scan_duration': result.scan_duration,
                        'scan_output': result.scan_output,
                        'has_warnings': result.has_warnings,
                        'warning_details': result.warning_details
                    }
                    session.close()
                    return cached_data
            
            session.close()
        except Exception as e:
            logger.error(f"Error checking cache for {file_path}: {e}")
        
        return None
    
    def _save_to_cache(self, file_path, scan_result):
        """Save scan result to database cache"""
        if not self.database_path:
            return
            
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from models import ScanResult
            from datetime import datetime, timezone
            
            engine = create_engine(self.database_path)
            Session = sessionmaker(bind=engine)
            session = Session()
            
            # Check for existing record
            db_result = session.query(ScanResult).filter_by(file_path=file_path).first()
            
            if not db_result:
                db_result = ScanResult(file_path=file_path)
                session.add(db_result)
            
            # Update with scan results
            db_result.file_size = scan_result.get('file_size')
            db_result.file_type = scan_result.get('file_type')
            db_result.creation_date = scan_result.get('creation_date')
            db_result.last_modified = scan_result.get('last_modified')
            db_result.is_corrupted = scan_result.get('is_corrupted', False)
            db_result.corruption_details = scan_result.get('corruption_details')
            db_result.file_hash = scan_result.get('file_hash')
            db_result.scan_tool = scan_result.get('scan_tool')
            db_result.scan_duration = scan_result.get('scan_duration')
            db_result.scan_output = scan_result.get('scan_output')
            db_result.has_warnings = scan_result.get('has_warnings', False)
            db_result.warning_details = scan_result.get('warning_details')
            db_result.scan_date = datetime.now(timezone.utc)
            db_result.scan_status = 'completed'
            db_result.file_exists = True
            
            session.commit()
            session.close()
            logger.info(f"Saved scan result to cache for {file_path}")
        except Exception as e:
            logger.error(f"Error saving to cache for {file_path}: {e}")
    
    def _check_ignored_patterns(self, error_output):
        """Check if error output contains any ignored patterns"""
        if not self.database_path or not error_output:
            return False
            
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from models import IgnoredErrorPattern
            
            engine = create_engine(self.database_path)
            Session = sessionmaker(bind=engine)
            session = Session()
            
            # Get active ignored patterns
            patterns = session.query(IgnoredErrorPattern).filter_by(is_active=True).all()
            
            # Check if any pattern matches the error output
            for pattern in patterns:
                if pattern.pattern.lower() in error_output.lower():
                    logger.info(f"Error output matches ignored pattern: {pattern.pattern}")
                    session.close()
                    return True
            
            session.close()
        except Exception as e:
            logger.error(f"Error checking ignored patterns: {e}")
        
        return False