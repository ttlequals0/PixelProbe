"""
Statistics service for PixelProbe
"""

import os
import logging
from typing import Dict, List
from datetime import datetime, timedelta
from sqlalchemy import text, func

from pixelprobe.models import db, ScanResult, ScanReport
from pixelprobe.utils.timezone import from_utc_to_configured, get_configured_timezone
from pixelprobe.version import __version__

logger = logging.getLogger(__name__)

class StatsService:
    """Service for calculating and retrieving statistics"""
    
    def __init__(self):
        self.tz = get_configured_timezone()
        
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
                        SUM(CASE WHEN is_corrupted = TRUE AND marked_as_good = FALSE THEN 1 ELSE 0 END) as corrupted_files,
                        SUM(CASE WHEN (is_corrupted = FALSE OR is_corrupted IS NULL OR marked_as_good = TRUE) AND (has_warnings = FALSE OR has_warnings IS NULL) AND scan_status = 'completed' THEN 1 ELSE 0 END) as healthy_files,
                        SUM(CASE WHEN marked_as_good = TRUE THEN 1 ELSE 0 END) as marked_as_good,
                        SUM(CASE WHEN has_warnings = TRUE AND marked_as_good = FALSE AND (is_corrupted = FALSE OR is_corrupted IS NULL) THEN 1 ELSE 0 END) as warning_files
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
                'version': __version__,
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
                        SUM(CASE WHEN is_corrupted = TRUE AND marked_as_good = FALSE THEN 1 ELSE 0 END) as corrupted,
                        SUM(CASE WHEN has_warnings = TRUE AND marked_as_good = FALSE THEN 1 ELSE 0 END) as warnings
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
                (ScanResult.marked_as_good == False)
            ).count()

            warning_files = ScanResult.query.filter(
                (ScanResult.has_warnings == True) &
                (ScanResult.marked_as_good == False) &
                ((ScanResult.is_corrupted == False) | (ScanResult.is_corrupted == None))
            ).count()

            marked_as_good = ScanResult.query.filter_by(marked_as_good=True).count()

            healthy_files = ScanResult.query.filter(
                ((ScanResult.is_corrupted == False) | (ScanResult.is_corrupted == None) | (ScanResult.marked_as_good == True)) &
                ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)) &
                (ScanResult.scan_status == 'completed')
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
            from pixelprobe.utils.helpers import get_configured_scan_paths
            scan_paths = get_configured_scan_paths()
            
            if not scan_paths:
                # No scan paths configured
                path_counts_query = []
            else:
                # Build dynamic CASE statement based on actual configured paths
                case_statements = []
                for path in scan_paths:
                    # Escape single quotes in path for SQL
                    escaped_path = path.replace("'", "''")
                    case_statements.append(f"WHEN file_path LIKE '{escaped_path}%' THEN '{escaped_path}'")
                
                # Build the query dynamically
                if case_statements:
                    case_sql = "\n                            ".join(case_statements)
                    query = f"""
                        SELECT 
                            CASE 
                                {case_sql}
                                ELSE 'other'
                            END as base_path,
                            COUNT(*) as file_count
                        FROM scan_results
                        GROUP BY base_path
                    """
                    
                    # Get file counts per path
                    path_counts_query = db.session.execute(text(query)).fetchall()
                else:
                    path_counts_query = []
            
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
            
            # Convert scan dates to configured timezone for display
            if oldest_scan:
                try:
                    oldest_scan_dt = datetime.fromisoformat(oldest_scan.replace('Z', '+00:00'))
                    oldest_scan_display = from_utc_to_configured(oldest_scan_dt)
                    if oldest_scan_display:
                        oldest_scan = oldest_scan_display.isoformat()
                except:
                    pass
                    
            if newest_scan:
                try:
                    newest_scan_dt = datetime.fromisoformat(newest_scan.replace('Z', '+00:00'))
                    newest_scan_display = from_utc_to_configured(newest_scan_dt)
                    if newest_scan_display:
                        newest_scan = newest_scan_display.isoformat()
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

    def get_scan_trends(self, days: int = 30) -> Dict:
        """Get scan counter metrics over time

        Args:
            days: Number of days to look back (default: 30)

        Returns:
            Dictionary with scan counts over time
        """
        try:
            # Calculate date threshold
            start_date = datetime.now(self.tz) - timedelta(days=days)

            # Get scan reports grouped by date
            scan_counts = db.session.execute(
                text("""
                    SELECT
                        DATE(start_time) as scan_date,
                        COUNT(*) as scan_count,
                        COUNT(DISTINCT scan_type) as scan_types_count,
                        SUM(files_scanned) as total_files_scanned,
                        SUM(files_corrupted) as total_corrupted,
                        SUM(files_with_warnings) as total_warnings,
                        AVG(duration_seconds) as avg_duration
                    FROM scan_reports
                    WHERE start_time >= :start_date
                        AND status = 'completed'
                    GROUP BY DATE(start_time)
                    ORDER BY scan_date DESC
                """),
                {'start_date': start_date}
            ).fetchall()

            # Format results
            trends = []
            for row in scan_counts:
                trends.append({
                    'date': row[0],
                    'scan_count': row[1] or 0,
                    'scan_types_count': row[2] or 0,
                    'files_scanned': row[3] or 0,
                    'files_corrupted': row[4] or 0,
                    'files_with_warnings': row[5] or 0,
                    'avg_duration_seconds': round(row[6], 2) if row[6] else 0
                })

            # Get summary statistics
            total_query = db.session.execute(
                text("""
                    SELECT
                        COUNT(*) as total_scans,
                        SUM(files_scanned) as total_files_scanned,
                        SUM(files_corrupted) as total_corrupted,
                        SUM(files_with_warnings) as total_warnings
                    FROM scan_reports
                    WHERE start_time >= :start_date
                        AND status = 'completed'
                """),
                {'start_date': start_date}
            ).fetchone()

            return {
                'period_days': days,
                'start_date': start_date.isoformat(),
                'summary': {
                    'total_scans': total_query[0] or 0,
                    'total_files_scanned': total_query[1] or 0,
                    'total_corrupted': total_query[2] or 0,
                    'total_warnings': total_query[3] or 0
                },
                'daily_trends': trends
            }

        except Exception as e:
            logger.error(f"Error getting scan trends: {e}")
            raise

    def get_duration_histogram(self, days: int = 30, bucket_count: int = 10) -> Dict:
        """Get scan duration histogram

        Args:
            days: Number of days to look back (default: 30)
            bucket_count: Number of histogram buckets (default: 10)

        Returns:
            Dictionary with duration histogram data
        """
        try:
            # Calculate date threshold
            start_date = datetime.now(self.tz) - timedelta(days=days)

            # Get all durations in the period
            durations_query = db.session.execute(
                text("""
                    SELECT
                        duration_seconds,
                        scan_type,
                        files_scanned
                    FROM scan_reports
                    WHERE start_time >= :start_date
                        AND status = 'completed'
                        AND duration_seconds IS NOT NULL
                    ORDER BY duration_seconds
                """),
                {'start_date': start_date}
            ).fetchall()

            if not durations_query:
                return {
                    'period_days': days,
                    'start_date': start_date.isoformat(),
                    'histogram': [],
                    'summary': {
                        'total_scans': 0,
                        'min_duration': 0,
                        'max_duration': 0,
                        'avg_duration': 0,
                        'median_duration': 0
                    }
                }

            durations = [row[0] for row in durations_query]

            # Calculate statistics
            min_duration = min(durations)
            max_duration = max(durations)
            avg_duration = sum(durations) / len(durations)
            median_duration = sorted(durations)[len(durations) // 2]

            # Create histogram buckets
            bucket_size = (max_duration - min_duration) / bucket_count if max_duration > min_duration else 1
            buckets = []

            for i in range(bucket_count):
                bucket_start = min_duration + (i * bucket_size)
                bucket_end = bucket_start + bucket_size

                # Count scans in this bucket
                count = sum(1 for d in durations if bucket_start <= d < bucket_end or (i == bucket_count - 1 and d == bucket_end))

                buckets.append({
                    'range_start': round(bucket_start, 2),
                    'range_end': round(bucket_end, 2),
                    'count': count,
                    'percentage': round((count / len(durations)) * 100, 2) if len(durations) > 0 else 0
                })

            # Get duration stats by scan type
            by_scan_type = {}
            for row in durations_query:
                duration, scan_type, files_scanned = row
                if scan_type not in by_scan_type:
                    by_scan_type[scan_type] = {
                        'count': 0,
                        'total_duration': 0,
                        'total_files': 0,
                        'durations': []
                    }
                by_scan_type[scan_type]['count'] += 1
                by_scan_type[scan_type]['total_duration'] += duration
                by_scan_type[scan_type]['total_files'] += files_scanned or 0
                by_scan_type[scan_type]['durations'].append(duration)

            # Calculate averages per scan type
            scan_type_stats = {}
            for scan_type, stats in by_scan_type.items():
                scan_type_stats[scan_type] = {
                    'count': stats['count'],
                    'avg_duration': round(stats['total_duration'] / stats['count'], 2),
                    'total_files_scanned': stats['total_files'],
                    'avg_files_per_scan': round(stats['total_files'] / stats['count'], 2) if stats['count'] > 0 else 0,
                    'min_duration': round(min(stats['durations']), 2),
                    'max_duration': round(max(stats['durations']), 2)
                }

            return {
                'period_days': days,
                'start_date': start_date.isoformat(),
                'histogram': buckets,
                'summary': {
                    'total_scans': len(durations),
                    'min_duration': round(min_duration, 2),
                    'max_duration': round(max_duration, 2),
                    'avg_duration': round(avg_duration, 2),
                    'median_duration': round(median_duration, 2)
                },
                'by_scan_type': scan_type_stats
            }

        except Exception as e:
            logger.error(f"Error getting duration histogram: {e}")
            raise