"""
Repository for scan-related database operations
"""

from typing import List, Optional, Dict, Tuple
from datetime import datetime
from sqlalchemy import text, and_, or_

from models import ScanResult, ScanState
from .base_repository import BaseRepository

class ScanRepository(BaseRepository[ScanResult]):
    """Repository for scan results"""
    
    def __init__(self):
        super().__init__(ScanResult)
    
    def get_paginated_results(self, page: int = 1, per_page: int = 100,
                            scan_status: str = 'all', 
                            is_corrupted: str = 'all') -> Tuple[List[ScanResult], Dict]:
        """Get paginated scan results with filters"""
        query = self.query()
        
        # Apply status filter
        if scan_status != 'all':
            query = query.filter_by(scan_status=scan_status)
        
        # Apply corruption filter  
        if is_corrupted == 'true':
            query = query.filter_by(is_corrupted=True).filter_by(marked_as_good=False)
        elif is_corrupted == 'false':
            query = query.filter(
                or_(
                    ScanResult.is_corrupted == False,
                    ScanResult.marked_as_good == True
                )
            )
        
        # Order by scan date descending
        query = query.order_by(ScanResult.scan_date.desc())
        
        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return pagination.items, {
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'pages': pagination.pages
        }
    
    def get_by_file_path(self, file_path: str) -> Optional[ScanResult]:
        """Get scan result by file path"""
        return self.get_one_by_filter(file_path=file_path)
    
    def get_stuck_scans(self) -> List[ScanResult]:
        """Get all scans stuck in 'scanning' state"""
        return self.get_by_filter(scan_status='scanning')
    
    def reset_stuck_scans(self) -> int:
        """Reset all stuck scans to pending"""
        stuck_results = self.get_stuck_scans()
        count = len(stuck_results)
        
        for result in stuck_results:
            result.scan_status = 'pending'
            result.error_message = 'Reset from stuck scanning state'
        
        self.commit()
        return count
    
    def get_corrupted_files(self, exclude_marked_good: bool = True) -> List[ScanResult]:
        """Get all corrupted files"""
        query = self.query().filter_by(is_corrupted=True)
        if exclude_marked_good:
            query = query.filter_by(marked_as_good=False)
        return query.all()
    
    def get_files_with_warnings(self, exclude_marked_good: bool = True) -> List[ScanResult]:
        """Get all files with warnings"""
        query = self.query().filter_by(has_warnings=True)
        if exclude_marked_good:
            query = query.filter_by(marked_as_good=False)
        return query.all()
    
    def mark_files_as_good(self, file_ids: List[int]) -> int:
        """Mark multiple files as good/healthy"""
        count = 0
        for file_id in file_ids:
            result = self.get_by_id(file_id)
            if result:
                result.marked_as_good = True
                result.is_corrupted = False
                count += 1
        
        self.commit()
        return count
    
    def get_files_for_rescan(self, reset_type: str = 'all', 
                            file_ids: Optional[List[int]] = None) -> List[ScanResult]:
        """Get files that need rescanning based on criteria"""
        if reset_type == 'selected' and file_ids:
            return self.query().filter(ScanResult.id.in_(file_ids)).all()
        elif reset_type == 'corrupted':
            return self.get_corrupted_files()
        elif reset_type == 'error':
            return self.get_by_filter(scan_status='error')
        else:  # all
            return self.get_all()
    
    def reset_for_rescan(self, results: List[ScanResult]) -> int:
        """Reset files for rescanning"""
        count = len(results)
        for result in results:
            result.scan_status = 'pending'
            result.is_corrupted = False
            result.marked_as_good = False
            result.error_message = None
            result.scan_output = None
        
        self.commit()
        return count
    
    def get_orphaned_entries(self) -> List[ScanResult]:
        """Get database entries where file doesn't exist"""
        return self.get_by_filter(file_exists=False)
    
    def get_statistics(self) -> Dict:
        """Get scan statistics using optimized query"""
        stats = self.session.execute(
            text("""
                SELECT 
                    COUNT(*) as total_files,
                    SUM(CASE WHEN scan_status = 'completed' THEN 1 ELSE 0 END) as completed_files,
                    SUM(CASE WHEN scan_status = 'pending' THEN 1 ELSE 0 END) as pending_files,
                    SUM(CASE WHEN scan_status = 'scanning' THEN 1 ELSE 0 END) as scanning_files,
                    SUM(CASE WHEN scan_status = 'error' THEN 1 ELSE 0 END) as error_files,
                    SUM(CASE WHEN is_corrupted = 1 AND marked_as_good = 0 THEN 1 ELSE 0 END) as corrupted_files,
                    SUM(CASE WHEN (is_corrupted = 0 OR marked_as_good = 1) THEN 1 ELSE 0 END) as healthy_files,
                    SUM(CASE WHEN marked_as_good = 1 THEN 1 ELSE 0 END) as marked_as_good,
                    SUM(CASE WHEN has_warnings = 1 AND marked_as_good = 0 THEN 1 ELSE 0 END) as warning_files
                FROM scan_results
            """)
        ).fetchone()
        
        return {
            'total_files': stats[0] or 0,
            'completed_files': stats[1] or 0,
            'pending_files': stats[2] or 0,
            'scanning_files': stats[3] or 0,
            'error_files': stats[4] or 0,
            'corrupted_files': stats[5] or 0,
            'healthy_files': stats[6] or 0,
            'marked_as_good': stats[7] or 0,
            'warning_files': stats[8] or 0
        }
    
    def get_files_by_path_prefix(self, path_prefix: str) -> int:
        """Get count of files with specific path prefix"""
        return self.query().filter(
            ScanResult.file_path.like(f"{path_prefix}%")
        ).count()
    
    def update_file_hash(self, file_path: str, new_hash: str, 
                        last_modified: datetime) -> Optional[ScanResult]:
        """Update file hash and modification time"""
        result = self.get_by_file_path(file_path)
        if result:
            result.file_hash = new_hash
            result.last_modified = last_modified
            self.commit()
        return result


class ScanStateRepository(BaseRepository[ScanState]):
    """Repository for scan state"""
    
    def __init__(self):
        super().__init__(ScanState)
    
    def get_current_state(self) -> Optional[ScanState]:
        """Get the most recent scan state"""
        return self.query().order_by(ScanState.id.desc()).first()
    
    def get_active_scan(self) -> Optional[ScanState]:
        """Get currently active scan if any"""
        return self.get_one_by_filter(status='running')
    
    def create_scan_state(self, directories: List[str], 
                         force_rescan: bool = False) -> ScanState:
        """Create new scan state"""
        scan_state = ScanState.get_or_create()
        scan_state.start_scan(directories, force_rescan)
        self.commit()
        return scan_state
    
    def update_scan_progress(self, scan_id: int, files_scanned: int, 
                           total_files: int) -> Optional[ScanState]:
        """Update scan progress"""
        scan_state = self.get_by_id(scan_id)
        if scan_state:
            scan_state.update_progress(files_scanned, total_files)
            self.commit()
        return scan_state
    
    def complete_scan(self, scan_id: int) -> Optional[ScanState]:
        """Mark scan as complete"""
        scan_state = self.get_by_id(scan_id)
        if scan_state:
            scan_state.complete_scan()
            self.commit()
        return scan_state
    
    def cancel_scan(self, scan_id: int) -> Optional[ScanState]:
        """Cancel a scan"""
        scan_state = self.get_by_id(scan_id)
        if scan_state:
            scan_state.cancel_scan()
            self.commit()
        return scan_state
    
    def error_scan(self, scan_id: int, error_message: str) -> Optional[ScanState]:
        """Mark scan as errored"""
        scan_state = self.get_by_id(scan_id)
        if scan_state:
            scan_state.error_scan(error_message)
            self.commit()
        return scan_state