"""
API endpoints for the View Logs feature.

Provides log retrieval with filtering, pagination, download,
retention configuration, and purge operations.
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import func
from flask import Blueprint, request, Response, stream_with_context

from pixelprobe.models import db, LogEntry, AppConfig, ScanState
from pixelprobe.auth import auth_required
from pixelprobe.utils.rate_limiting import rate_limit
from pixelprobe.utils.timezone import from_utc_to_configured
from pixelprobe.constants import CONFIG_LOG_RETENTION_DAYS, SYSTEM_LOG_ID

logger = logging.getLogger(__name__)

log_bp = Blueprint('logs', __name__, url_prefix='/api')

# Map level names to numeric severity for "minimum level" filtering
LEVEL_ORDER = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}


def _parse_iso(value):
    """Parse an ISO timestamp string, returning None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _apply_log_filters(query, args):
    """Apply common log filters from request args to a LogEntry query.

    Args:
        query: SQLAlchemy query on LogEntry
        args: dict-like object with filter keys (scan_id, level, search, start_time, end_time)

    Returns:
        Filtered query
    """
    scan_id = args.get('scan_id')
    if scan_id:
        if scan_id == SYSTEM_LOG_ID:
            query = query.filter(LogEntry.scan_id.is_(None))
        else:
            query = query.filter(LogEntry.scan_id == scan_id)

    level = (args.get('level') or '').upper()
    if level and level in LEVEL_ORDER:
        min_severity = LEVEL_ORDER[level]
        allowed_levels = [name for name, sev in LEVEL_ORDER.items() if sev >= min_severity]
        query = query.filter(LogEntry.level.in_(allowed_levels))

    search = (args.get('search') or '').strip()
    if search:
        # Escape LIKE wildcards to prevent pattern injection
        safe_search = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        query = query.filter(LogEntry.message.ilike(f'%{safe_search}%', escape='\\'))

    start_dt = _parse_iso(args.get('start_time'))
    if start_dt:
        query = query.filter(LogEntry.timestamp >= start_dt)

    end_dt = _parse_iso(args.get('end_time'))
    if end_dt:
        query = query.filter(LogEntry.timestamp <= end_dt)

    return query


@log_bp.route('/logs')
@rate_limit("30 per minute")
@auth_required
def get_logs():
    """Fetch logs with filters and pagination.

    Query params:
        since       - ISO timestamp, return only newer entries (for polling)
        scan_id     - filter by job run ("system" for untagged)
        level       - minimum log level
        search      - ILIKE search on message
        start_time  - time range start (ISO)
        end_time    - time range end (ISO)
        page        - page number (default 1)
        per_page    - results per page (default 200, max 1000)
    """
    since = request.args.get('since')
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 200, type=int), 1000)

    query = _apply_log_filters(LogEntry.query, request.args)

    # Polling: return only entries newer than `since`
    if since:
        since_dt = _parse_iso(since)
        if since_dt:
            query = query.filter(LogEntry.timestamp > since_dt)
        query = query.order_by(LogEntry.timestamp.desc())
        logs = query.limit(1000).all()
        return {
            'logs': [_format_log(log) for log in logs],
            'total': len(logs),
            'has_more': len(logs) == 1000
        }

    # Paginate
    query = query.order_by(LogEntry.timestamp.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return {
        'logs': [_format_log(log) for log in pagination.items],
        'total': pagination.total,
        'has_more': pagination.page < pagination.pages
    }


@log_bp.route('/logs/runs')
@rate_limit("30 per minute")
@auth_required
def get_log_runs():
    """List job runs that have associated log entries.

    Query params:
        scan_type - filter runs by type (full, parallel, scheduled, cleanup, system)
    """
    scan_type = request.args.get('scan_type')

    runs_query = db.session.query(
        LogEntry.scan_id,
        func.min(LogEntry.timestamp).label('first_log'),
        func.max(LogEntry.timestamp).label('last_log'),
        func.count(LogEntry.id).label('log_count')
    ).group_by(LogEntry.scan_id).order_by(func.max(LogEntry.timestamp).desc())

    # Pre-filter in SQL where possible to avoid post-LIMIT data loss
    if scan_type == SYSTEM_LOG_ID:
        runs_query = runs_query.filter(LogEntry.scan_id.is_(None))
    elif scan_type == 'scheduled':
        runs_query = runs_query.filter(LogEntry.scan_id.like('scheduled_%'))
    # Other scan_type values still need post-filter (requires ScanState join)

    runs = runs_query.limit(100).all()

    # Batch-load ScanState records to avoid N+1 queries
    scan_ids = [r.scan_id for r in runs if r.scan_id]
    scan_states = {}
    if scan_ids:
        states = ScanState.query.filter(ScanState.scan_id.in_(scan_ids)).all()
        scan_states = {s.scan_id: s for s in states}

    results = []
    for run in runs:
        run_id = run.scan_id or SYSTEM_LOG_ID
        entry = {
            'scan_id': run_id,
            'start_time': run.first_log.isoformat() if run.first_log else None,
            'end_time': run.last_log.isoformat() if run.last_log else None,
            'log_count': run.log_count,
            'scan_type': _infer_scan_type(run.scan_id)
        }

        scan_state = scan_states.get(run.scan_id)
        if scan_state:
            entry['phase'] = scan_state.phase
            entry['scan_type'] = _infer_scan_type_from_state(scan_state)

        # Post-filter for types that can't be pre-filtered in SQL
        if scan_type and entry['scan_type'] != scan_type:
            continue

        results.append(entry)

    return {'runs': results}


@log_bp.route('/logs/download')
@auth_required
def download_logs():
    """Download filtered logs as plain text .log file.

    Accepts the same filter params as GET /api/logs (no pagination).
    Streams the response to avoid buffering large results.
    Hard cap at 100k rows to prevent unbounded queries.
    """
    query = _apply_log_filters(LogEntry.query, request.args)
    query = query.order_by(LogEntry.timestamp.asc()).limit(100000)

    def generate():
        # Close the session in finally so a client disconnect mid-download
        # (GeneratorExit) does not leave the streaming cursor / pooled connection
        # pinned open until garbage collection.
        try:
            for log in query.yield_per(500):
                ts = log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '????-??-?? ??:??:??'
                line = f"{ts}  {log.level:<8}  {log.logger_name or '':<30}  {log.message}\n"
                yield line
                if log.traceback:
                    for tb_line in log.traceback.splitlines():
                        yield f"  {tb_line}\n"
        finally:
            db.session.close()

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    filename = f'pixelprobe-logs-{date_str}.log'

    return Response(
        stream_with_context(generate()),
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@log_bp.route('/logs/retention', methods=['GET', 'PUT'])
@auth_required
def log_retention():
    """Get or set log retention configuration."""
    if request.method == 'GET':
        config = AppConfig.query.filter_by(key=CONFIG_LOG_RETENTION_DAYS).first()
        return {
            'log_retention_days': int(config.value) if config else 30
        }

    # PUT
    data = request.get_json() or {}
    days = data.get('log_retention_days')
    if days is None or not isinstance(days, int) or days < 1:
        return {'error': 'log_retention_days must be a positive integer'}, 400

    config = AppConfig.query.filter_by(key=CONFIG_LOG_RETENTION_DAYS).first()
    if config:
        config.value = str(days)
    else:
        config = AppConfig(key=CONFIG_LOG_RETENTION_DAYS, value=str(days),
                           description='Number of days to retain log entries')
        db.session.add(config)
    db.session.commit()

    return {'log_retention_days': days}


@log_bp.route('/logs/purge', methods=['POST'])
@rate_limit("2 per minute")
@auth_required
def purge_logs():
    """Manually purge log entries.

    Requires at least one filter to prevent accidental full purge.

    JSON body (at least one required):
        scan_id  - purge only logs for a specific scan
        before   - purge logs older than this ISO timestamp
        level    - purge logs at or above this level
    """
    data = request.get_json() or {}
    scan_id = data.get('scan_id')
    before = data.get('before')
    level = data.get('level')

    if not scan_id and not before and not level:
        return {'error': 'At least one filter (scan_id, before, or level) is required to prevent accidental full purge'}, 400

    # Build query with only the documented purge filters (scan_id, level)
    # Don't pass raw data to _apply_log_filters which would also accept
    # undocumented search/start_time/end_time keys
    purge_filters = {}
    if scan_id:
        purge_filters['scan_id'] = scan_id
    if level:
        purge_filters['level'] = level
    query = _apply_log_filters(LogEntry.query, purge_filters)

    if before:
        before_dt = _parse_iso(before)
        if not before_dt:
            return {'error': 'Invalid "before" timestamp'}, 400
        query = query.filter(LogEntry.timestamp < before_dt)

    count = query.delete(synchronize_session=False)
    db.session.commit()

    logger.info(f"Purged {count} log entries")
    return {'deleted': count}


def _format_log(log):
    """Format a LogEntry for API response."""
    result = log.to_dict()
    if log.timestamp:
        display_dt = from_utc_to_configured(log.timestamp)
        result['timestamp'] = display_dt.isoformat() if display_dt else result['timestamp']
    return result


def _infer_scan_type(scan_id):
    """Infer scan type from scan_id string."""
    if not scan_id:
        return SYSTEM_LOG_ID
    if scan_id.startswith('scheduled_'):
        return 'scheduled'
    return 'full'


def _infer_scan_type_from_state(scan_state):
    """Infer scan type from ScanState record."""
    if not scan_state:
        return SYSTEM_LOG_ID
    scan_id = scan_state.scan_id or ''
    if scan_id.startswith('scheduled_'):
        return 'scheduled'
    dirs = scan_state.directories or ''
    if 'PENDING_FILES_SCAN' in dirs:
        return 'pending'
    return 'full'
