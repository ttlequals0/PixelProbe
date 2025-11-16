from flask import Blueprint, jsonify, current_app
from sqlalchemy import text
import os
import time
import logging
from datetime import datetime, timezone
from functools import wraps

from models import db, ScanResult
from version import __version__
from pixelprobe.utils.timezone import from_utc_to_configured, get_configured_timezone_name
from auth import auth_required

logger = logging.getLogger(__name__)

stats_bp = Blueprint('stats', __name__, url_prefix='/api')

def exempt_from_rate_limit(f):
    """Decorator to exempt a function from rate limiting"""
    @wraps(f)
    def wrapped(*args, **kwargs):
        # Get the limiter from the current app
        limiter = current_app.extensions.get('flask-limiter')
        if limiter:
            # Apply exemption dynamically
            exempt_func = limiter.exempt(f)
            return exempt_func(*args, **kwargs)
        else:
            # If no limiter, just call the function
            return f(*args, **kwargs)
    return wrapped

@stats_bp.route('/stats')
@exempt_from_rate_limit
@auth_required
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
                    SUM(CASE WHEN is_corrupted = TRUE AND marked_as_good = FALSE THEN 1 ELSE 0 END) as corrupted_files,
                    SUM(CASE WHEN (is_corrupted = FALSE OR is_corrupted IS NULL OR marked_as_good = TRUE) AND (has_warnings = FALSE OR has_warnings IS NULL) AND scan_status = 'completed' THEN 1 ELSE 0 END) as healthy_files,
                    SUM(CASE WHEN marked_as_good = TRUE THEN 1 ELSE 0 END) as marked_as_good,
                    SUM(CASE WHEN has_warnings = TRUE AND marked_as_good = FALSE AND (is_corrupted = FALSE OR is_corrupted IS NULL) THEN 1 ELSE 0 END) as warning_files
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
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        # Fallback to individual queries if the optimized query fails
        try:
            total_files = ScanResult.query.count()
            completed_files = ScanResult.query.filter_by(scan_status='completed').count()
            pending_files = ScanResult.query.filter(
                ScanResult.scan_status == 'pending'
            ).count()
            scanning_files = ScanResult.query.filter_by(scan_status='scanning').count()
            error_files = ScanResult.query.filter_by(scan_status='error').count()
            
            # Corrupted files: corrupted AND not marked as good
            corrupted_files = ScanResult.query.filter(
                (ScanResult.is_corrupted == True) &
                (ScanResult.marked_as_good == False)
            ).count()

            # Warning files: has warnings, not marked as good, and not corrupted
            warning_files = ScanResult.query.filter(
                (ScanResult.has_warnings == True) &
                (ScanResult.marked_as_good == False) &
                ((ScanResult.is_corrupted == False) | (ScanResult.is_corrupted == None))
            ).count()

            marked_as_good = ScanResult.query.filter_by(marked_as_good=True).count()

            # Healthy files: completed, no warnings, and (not corrupted OR marked as good)
            healthy_files = ScanResult.query.filter(
                ((ScanResult.is_corrupted == False) | (ScanResult.is_corrupted == None) | (ScanResult.marked_as_good == True)) &
                ((ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)) &
                (ScanResult.scan_status == 'completed')
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
@auth_required
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
                    SUM(CASE WHEN is_corrupted = TRUE AND marked_as_good = FALSE THEN 1 ELSE 0 END) as corrupted_files,
                    SUM(CASE WHEN (is_corrupted = FALSE OR is_corrupted IS NULL OR marked_as_good = TRUE) AND (has_warnings = FALSE OR has_warnings IS NULL) AND scan_status = 'completed' THEN 1 ELSE 0 END) as healthy_files,
                    SUM(CASE WHEN marked_as_good = TRUE THEN 1 ELSE 0 END) as marked_as_good,
                    SUM(CASE WHEN has_warnings = TRUE AND marked_as_good = FALSE AND (is_corrupted = FALSE OR is_corrupted IS NULL) THEN 1 ELSE 0 END) as warning_files
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
        
        # Get monitored paths info from database in a single query
        monitored_paths = []
        total_filesystem_files = db_total_files  # Use DB total since all files are scanned
        
        # Get configured scan paths from environment (no hardcoded defaults)
        scan_paths_env = os.environ.get('SCAN_PATHS', '')
        scan_paths = [p.strip() for p in scan_paths_env.split(',') if p.strip()]  # Remove empty strings
        
        if not scan_paths:
            # No scan paths configured - use empty path counts
            path_counts = {}
        else:
            # Build dynamic CASE statement based on actual configured paths
            case_statements = []
            for path in scan_paths:
                # Escape single quotes in path for SQL
                escaped_path = path.replace("'", "''")
                case_statements.append(f"WHEN file_path LIKE '{escaped_path}%' THEN '{escaped_path}'")
            
            # Build the query dynamically based on user's configured paths
            if case_statements:
                case_sql = "\n                        ".join(case_statements)
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
                
                # Get file counts per path using a single aggregated query
                path_counts_query = db.session.execute(text(query)).fetchall()
                
                # Convert to dictionary for easy lookup
                path_counts = {row[0]: row[1] for row in path_counts_query}
            else:
                path_counts = {}
        
        # Build monitored paths info
        for path in scan_paths:
            path_info = {
                'path': path,
                'exists': True,  # Assume exists since we have DB data
                'file_count': path_counts.get(path, 0)
            }
            monitored_paths.append(path_info)
        
        # Database performance statistics - with database-specific fallbacks
        try:
            # Try PostgreSQL-specific query first
            db_perf_query = db.session.execute(
                text("""
                    SELECT
                        COUNT(*) as total_scans,
                        AVG(CASE
                            WHEN scan_status = 'completed'
                            THEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP AT TIME ZONE 'UTC' - scan_date)) / 86400.0
                            ELSE NULL
                        END) as avg_days_since_scan,
                        MIN(scan_date) as oldest_scan,
                        MAX(scan_date) as newest_scan
                    FROM scan_results
                    WHERE scan_status = 'completed'
                """)
            ).fetchone()
        except Exception as e:
            logger.warning(f"PostgreSQL-specific query failed, trying SQLite fallback: {e}")
            try:
                # Fallback to SQLite-specific query
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
            except Exception as e2:
                logger.warning(f"SQLite fallback also failed, using basic query: {e2}")
                # Final fallback to basic ORM queries
                total_scans = ScanResult.query.filter_by(scan_status='completed').count()
                avg_days_since_scan = 0
                oldest_scan = None
                newest_scan = None
                db_perf_query = (total_scans, avg_days_since_scan, oldest_scan, newest_scan)
        
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
        
        # Detect database type
        db_type = 'unknown'
        try:
            db_dialect = db.engine.dialect.name
            if 'postgresql' in db_dialect:
                db_type = 'postgresql'
            elif 'sqlite' in db_dialect:
                db_type = 'sqlite'
            else:
                db_type = db_dialect
        except:
            db_type = 'unknown'
        
        # Build response
        current_time_utc = datetime.now(timezone.utc)
        current_time_display = from_utc_to_configured(current_time_utc)
        
        system_info = {
            'version': __version__,
            'timezone': get_configured_timezone_name(),
            'current_time': current_time_display.isoformat() if current_time_display else current_time_utc.isoformat(),
            'database': {
                'type': db_type,
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