"""
Statistics service for PixelProbe
"""

import os
import logging
from typing import Dict, List
from datetime import datetime
from sqlalchemy import text

from models import db, ScanResult
from pixelprobe.utils.helpers import get_timezone

logger = logging.getLogger(__name__)

class StatsService:
    """Service for calculating and retrieving statistics"""
    
    def __init__(self):
        self.tz = get_timezone()
        
    def get_file_statistics(self) -> Dict:
        """Get comprehensive file statistics"""
        try:
            # Use optimized single query
            stats = db.session.execute(
                text("""
                    SELECT 
                        COUNT(*) as total_files,
                        SUM(CASE WHEN scan_status = 'completed' THEN 1 ELSE 0 END) as completed_files,
                        SUM(CASE WHEN scan_status = 'pending' THEN 1 ELSE 0 END) as pending_files,
                        SUM(CASE WHEN scan_status = 'scanning' THEN 1 ELSE 0 END) as scanning_files,
                        SUM(CASE WHEN scan_status = 'error' THEN 1 ELSE 0 END) as error_files,
                        SUM(CASE WHEN is_corrupted = 1 AND marked_as_good = 0 AND (has_warnings = 0 OR has_warnings IS NULL) THEN 1 ELSE 0 END) as corrupted_files,
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
            
        except Exception as e:
            logger.error(f"Error getting file statistics: {e}")
            # Fallback to individual queries
            return self._get_stats_fallback()
    
    def get_system_info(self) -> Dict:
        """Get comprehensive system information"""
        try:
            # Get file statistics
            file_stats = self.get_file_statistics()
            
            # Get monitored paths
            monitored_paths = self._get_monitored_paths()
            
            # Get database performance stats
            db_perf = self._get_database_performance()
            
            # Build system info
            return {
                'version': os.environ.get('APP_VERSION', 'unknown'),
                'timezone': str(self.tz),
                'current_time': datetime.now(self.tz).isoformat(),
                'database': {
                    'type': 'sqlite',
                    **file_stats,
                    'performance': db_perf
                },
                'monitored_paths': monitored_paths,
                'filesystem': {
                    'total_files': file_stats['total_files'],
                    'paths_monitored': len(monitored_paths)
                },
                'features': {
                    'deep_scan': True,
                    'parallel_scanning': True,
                    'auto_cleanup': True,
                    'file_monitoring': True,
                    'scheduled_scans': True
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting system info: {e}")
            raise
    
    def get_corruption_statistics(self) -> Dict:
        """Get detailed corruption statistics"""
        try:
            # Get corruption stats by file type
            corruption_by_type = db.session.execute(
                text("""
                    SELECT 
                        file_type,
                        COUNT(*) as total,
                        SUM(CASE WHEN is_corrupted = 1 AND marked_as_good = 0 THEN 1 ELSE 0 END) as corrupted,
                        SUM(CASE WHEN has_warnings = 1 AND marked_as_good = 0 THEN 1 ELSE 0 END) as warnings
                    FROM scan_results
                    GROUP BY file_type
                """)
            ).fetchall()
            
            stats_by_type = {}
            for row in corruption_by_type:
                file_type = row[0] or 'Unknown'
                stats_by_type[file_type] = {
                    'total': row[1],
                    'corrupted': row[2],
                    'warnings': row[3],
                    'corruption_rate': (row[2] / row[1] * 100) if row[1] > 0 else 0
                }
            
            return stats_by_type
            
        except Exception as e:
            logger.error(f"Error getting corruption statistics: {e}")
            raise
    
    def _get_stats_fallback(self) -> Dict:
        """Fallback method using individual queries"""
        try:
            total_files = ScanResult.query.count()
            completed_files = ScanResult.query.filter_by(scan_status='completed').count()
            pending_files = ScanResult.query.filter_by(scan_status='pending').count()
            scanning_files = ScanResult.query.filter_by(scan_status='scanning').count()
            error_files = ScanResult.query.filter_by(scan_status='error').count()
            
            corrupted_files = ScanResult.query.filter(
                (ScanResult.is_corrupted == True) & 
                (ScanResult.marked_as_good == False) &
                ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None))
            ).count()
            
            warning_files = ScanResult.query.filter(
                (ScanResult.has_warnings == True) &
                (ScanResult.marked_as_good == False)
            ).count()
            
            marked_as_good = ScanResult.query.filter_by(marked_as_good=True).count()
            
            healthy_files = ScanResult.query.filter(
                (ScanResult.is_corrupted == False) | (ScanResult.marked_as_good == True)
            ).count()
            
            return {
                'total_files': total_files,
                'completed_files': completed_files,
                'pending_files': pending_files,
                'scanning_files': scanning_files,
                'error_files': error_files,
                'corrupted_files': corrupted_files,
                'healthy_files': healthy_files,
                'marked_as_good': marked_as_good,
                'warning_files': warning_files
            }
        except Exception as e:
            logger.error(f"Fallback stats query also failed: {e}")
            raise
    
    def _get_monitored_paths(self) -> List[Dict]:
        """Get information about monitored paths"""
        try:
            # Get configured scan paths
            scan_paths = os.environ.get('SCAN_PATHS', '/movies,/tv,/originals,/immich').split(',')
            
            # Get file counts per path
            path_counts_query = db.session.execute(
                text("""
                    SELECT 
                        CASE 
                            WHEN file_path LIKE '/movies%' THEN '/movies'
                            WHEN file_path LIKE '/tv%' THEN '/tv'
                            WHEN file_path LIKE '/originals%' THEN '/originals'
                            WHEN file_path LIKE '/immich%' THEN '/immich'
                            ELSE 'other'
                        END as base_path,
                        COUNT(*) as file_count
                    FROM scan_results
                    GROUP BY base_path
                """)
            ).fetchall()
            
            # Convert to dictionary
            path_counts = {row[0]: row[1] for row in path_counts_query}
            
            # Build monitored paths info
            monitored_paths = []
            for path in scan_paths:
                path_info = {
                    'path': path,
                    'exists': os.path.exists(path),
                    'file_count': path_counts.get(path, 0)
                }
                monitored_paths.append(path_info)
            
            return monitored_paths
            
        except Exception as e:
            logger.error(f"Error getting monitored paths: {e}")
            return []
    
    def _get_database_performance(self) -> Dict:
        """Get database performance statistics"""
        try:
            db_perf_query = db.session.execute(
                text("""
                    SELECT 
                        COUNT(*) as total_scans,
                        AVG(CASE 
                            WHEN scan_status = 'completed' 
                            THEN julianday('now') - julianday(scan_date) 
                            ELSE NULL 
                        END) as avg_days_since_scan,
                        MIN(scan_date) as oldest_scan,
                        MAX(scan_date) as newest_scan
                    FROM scan_results
                    WHERE scan_status = 'completed'
                """)
            ).fetchone()
            
            total_scans = db_perf_query[0] or 0
            avg_days_since_scan = db_perf_query[1] or 0
            oldest_scan = db_perf_query[2]
            newest_scan = db_perf_query[3]
            
            # Parse dates
            if oldest_scan:
                try:
                    oldest_scan_dt = datetime.fromisoformat(oldest_scan.replace('Z', '+00:00'))
                    if oldest_scan_dt.tzinfo is None:
                        oldest_scan_dt = self.tz.localize(oldest_scan_dt)
                    oldest_scan = oldest_scan_dt.isoformat()
                except:
                    pass
                    
            if newest_scan:
                try:
                    newest_scan_dt = datetime.fromisoformat(newest_scan.replace('Z', '+00:00'))
                    if newest_scan_dt.tzinfo is None:
                        newest_scan_dt = self.tz.localize(newest_scan_dt)
                    newest_scan = newest_scan_dt.isoformat()
                except:
                    pass
            
            return {
                'total_scans': total_scans,
                'avg_days_since_scan': round(avg_days_since_scan, 2),
                'oldest_scan': oldest_scan,
                'newest_scan': newest_scan
            }
            
        except Exception as e:
            logger.error(f"Error getting database performance stats: {e}")
            return {
                'total_scans': 0,
                'avg_days_since_scan': 0,
                'oldest_scan': None,
                'newest_scan': None
            }