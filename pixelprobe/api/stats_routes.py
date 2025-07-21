from flask import Blueprint, jsonify
from sqlalchemy import text
import os
import time
import logging
import pytz
from datetime import datetime, timezone

from models import db, ScanResult

logger = logging.getLogger(__name__)

# Get timezone from environment variable, default to UTC
APP_TIMEZONE = os.environ.get('TZ', 'UTC')
try:
    tz = pytz.timezone(APP_TIMEZONE)
except pytz.exceptions.UnknownTimeZoneError:
    tz = pytz.UTC
    logger.warning(f"Unknown timezone '{APP_TIMEZONE}', falling back to UTC")

stats_bp = Blueprint('stats', __name__, url_prefix='/api')

@stats_bp.route('/stats')
def get_stats():
    """Get statistics about scanned files"""
    try:
        # Use a single query with subqueries for better performance
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
        
        result = {
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
        
        logger.info(f"Stats requested - Total: {result['total_files']}, Completed: {result['completed_files']}, " +
                   f"Pending: {result['pending_files']}, Scanning: {result['scanning_files']}, " +
                   f"Corrupted: {result['corrupted_files']}, Healthy: {result['healthy_files']}, " +
                   f"Marked Good: {result['marked_as_good']}")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        # Fallback to individual queries if the optimized query fails
        try:
            total_files = ScanResult.query.count()
            completed_files = ScanResult.query.filter_by(scan_status='completed').count()
            pending_files = ScanResult.query.filter_by(scan_status='pending').count()
            scanning_files = ScanResult.query.filter_by(scan_status='scanning').count()
            error_files = ScanResult.query.filter_by(scan_status='error').count()
            
            # Count files, excluding marked_as_good from corrupted and warning counts
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
            
            # Files marked as good should be considered healthy
            healthy_files = ScanResult.query.filter(
                (ScanResult.is_corrupted == False) | (ScanResult.marked_as_good == True)
            ).count()
            
            return jsonify({
                'total_files': total_files,
                'completed_files': completed_files,
                'pending_files': pending_files,
                'scanning_files': scanning_files,
                'error_files': error_files,
                'corrupted_files': corrupted_files,
                'healthy_files': healthy_files,
                'marked_as_good': marked_as_good,
                'warning_files': warning_files
            })
        except Exception as e2:
            logger.error(f"Fallback stats query also failed: {str(e2)}")
            return jsonify({'error': 'Database query failed'}), 500

@stats_bp.route('/system-info')
def get_system_info():
    """Get comprehensive system information - optimized to read from database"""
    try:
        logger.info("System info requested")
        
        # Add overall timeout for the entire endpoint
        start_time = time.time()
        
        # Database statistics - use single query for better performance
        stats_query = db.session.execute(
            text("""
                SELECT 
                    COUNT(*) as total_files,
                    SUM(CASE WHEN scan_status = 'completed' THEN 1 ELSE 0 END) as completed_files,
                    SUM(CASE WHEN scan_status = 'pending' THEN 1 ELSE 0 END) as pending_files,
                    SUM(CASE WHEN scan_status = 'scanning' THEN 1 ELSE 0 END) as scanning_files,
                    SUM(CASE WHEN scan_status = 'error' THEN 1 ELSE 0 END) as error_files,
                    SUM(CASE WHEN is_corrupted = 1 THEN 1 ELSE 0 END) as corrupted_files,
                    SUM(CASE WHEN is_corrupted = 0 THEN 1 ELSE 0 END) as healthy_files,
                    SUM(CASE WHEN marked_as_good = 1 THEN 1 ELSE 0 END) as marked_as_good,
                    SUM(CASE WHEN has_warnings = 1 THEN 1 ELSE 0 END) as warning_files
                FROM scan_results
            """)
        ).fetchone()
        
        db_total_files = stats_query[0] or 0
        db_completed_files = stats_query[1] or 0
        db_pending_files = stats_query[2] or 0
        db_scanning_files = stats_query[3] or 0
        db_error_files = stats_query[4] or 0
        db_corrupted_files = stats_query[5] or 0
        db_healthy_files = stats_query[6] or 0
        db_marked_as_good = stats_query[7] or 0
        db_warning_files = stats_query[8] or 0
        
        # Files marked as good should be considered healthy
        db_healthy_files = ScanResult.query.filter(
            (ScanResult.is_corrupted == False) | (ScanResult.marked_as_good == True)
        ).count()
        
        # Get monitored paths info from database in a single query
        monitored_paths = []
        total_filesystem_files = db_total_files  # Use DB total since all files are scanned
        
        # Get file counts per path using a single aggregated query
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
        
        # Convert to dictionary for easy lookup
        path_counts = {row[0]: row[1] for row in path_counts_query}
        
        # Get configured scan paths from environment
        scan_paths = os.environ.get('SCAN_PATHS', '/movies,/tv,/originals,/immich').split(',')
        
        # Build monitored paths info
        for path in scan_paths:
            path_info = {
                'path': path,
                'exists': True,  # Assume exists since we have DB data
                'file_count': path_counts.get(path, 0)
            }
            monitored_paths.append(path_info)
        
        # Database performance statistics
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
        
        # Parse oldest and newest scan dates
        if oldest_scan:
            try:
                oldest_scan_dt = datetime.fromisoformat(oldest_scan.replace('Z', '+00:00'))
                if oldest_scan_dt.tzinfo is None:
                    oldest_scan_dt = tz.localize(oldest_scan_dt)
                oldest_scan = oldest_scan_dt.isoformat()
            except:
                pass
                
        if newest_scan:
            try:
                newest_scan_dt = datetime.fromisoformat(newest_scan.replace('Z', '+00:00'))
                if newest_scan_dt.tzinfo is None:
                    newest_scan_dt = tz.localize(newest_scan_dt)
                newest_scan = newest_scan_dt.isoformat()
            except:
                pass
        
        # Build response
        system_info = {
            'version': os.environ.get('APP_VERSION', 'unknown'),
            'timezone': APP_TIMEZONE,
            'current_time': datetime.now(tz).isoformat(),
            'database': {
                'type': 'sqlite',
                'total_files': db_total_files,
                'completed_files': db_completed_files,
                'pending_files': db_pending_files,
                'scanning_files': db_scanning_files,
                'error_files': db_error_files,
                'corrupted_files': db_corrupted_files,
                'healthy_files': db_healthy_files,
                'marked_as_good': db_marked_as_good,
                'warning_files': db_warning_files,
                'performance': {
                    'total_scans': total_scans,
                    'avg_days_since_scan': round(avg_days_since_scan, 2),
                    'oldest_scan': oldest_scan,
                    'newest_scan': newest_scan
                }
            },
            'monitored_paths': monitored_paths,
            'filesystem': {
                'total_files': total_filesystem_files,
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
        
        # Check response time
        elapsed_time = time.time() - start_time
        if elapsed_time > 5:
            logger.warning(f"System info endpoint took {elapsed_time:.2f} seconds")
        
        return jsonify(system_info)
        
    except Exception as e:
        logger.error(f"Error getting system info: {str(e)}")
        return jsonify({'error': 'Failed to get system info'}), 500