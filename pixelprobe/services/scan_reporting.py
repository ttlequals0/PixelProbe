"""Shared scan reporting and discovery batch-insert helpers.

Used by both the chunk-distributed engine (tasks_parallel) and the
selected-file rescan paths in ScanService.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from pixelprobe.models import db, ScanResult, ScanReport, ScanState, ScanRunFile

logger = logging.getLogger(__name__)

# ScanState.scan_type -> ScanReport.scan_type
_REPORT_SCAN_TYPE = {'full': 'full_scan', 'pending': 'pending_scan'}


def create_scan_report(scan_state: ScanState, scan_type: str = None, commit: bool = True):
    """Create a ScanReport from a finished scan state and send the
    healthcheck completion ping (scheduled-scan identity rides in scan_id).

    scan_type defaults to a mapping of scan_state.scan_type; legacy callers
    (ScanService) pass their own label explicitly.
    """
    if scan_type is None:
        scan_type = _REPORT_SCAN_TYPE.get(scan_state.scan_type, 'full_scan')
    existing = ScanReport.query.filter_by(scan_id=scan_state.scan_id).order_by(ScanReport.id.desc()).first()
    if existing:
        return existing
    stats = db.session.query(
        func.count(ScanRunFile.id).label('total'),
        func.sum(db.case((ScanRunFile.is_corrupted == True, 1), else_=0)).label('corrupted'),
        func.sum(db.case((ScanRunFile.has_warnings == True, 1), else_=0)).label('warnings'),
        func.sum(db.case((ScanRunFile.status.notin_(['completed', 'pending', 'processing']), 1), else_=0)).label('errors'),
        func.sum(db.case((ScanRunFile.status == 'completed', 1), else_=0)).label('completed'),
        func.sum(db.case((ScanRunFile.status == 'pending', 1), else_=0)).label('pending')
    ).filter(ScanRunFile.scan_id == scan_state.scan_id).first()
    pending_count = stats.pending or 0
    if pending_count > 0:
        logger.warning(f"Scan report: {pending_count} files still in 'pending' status after scan completion")

    duration = None
    if scan_state.start_time and scan_state.end_time:
        start_time = scan_state.start_time
        end_time = scan_state.end_time
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        duration = (end_time - start_time).total_seconds()

    report = ScanReport(
        scan_type=scan_type,
        start_time=scan_state.start_time,
        end_time=scan_state.end_time,
        duration_seconds=duration,
        directories_scanned=(scan_state.directories if scan_state.directories else None),
        force_rescan=scan_state.force_rescan,
        num_workers=scan_state.num_workers,
        total_files_discovered=scan_state.estimated_total,
        files_scanned=stats.completed or 0,
        files_added=scan_state.files_added,
        files_updated=scan_state.files_updated,
        files_corrupted=stats.corrupted or 0,
        files_with_warnings=stats.warnings or 0,
        files_error=stats.errors or 0,
        status='completed' if scan_state.phase == 'completed' else scan_state.phase,
        error_message=scan_state.error_message,
        scan_id=scan_state.scan_id
    )

    db.session.add(report)
    if commit:
        db.session.commit()

    logger.info(f"Created scan report {report.report_id} for scan {scan_state.scan_id}")

    if commit:
        try:
            from pixelprobe.scheduler import MediaScheduler
            MediaScheduler.send_healthcheck_completion(report.id)
        except Exception as hc_error:
            logger.error(f"Failed to send healthcheck completion ping: {hc_error}")

    return report


def add_files_batch_to_db(file_paths: List[str], scan_id: str = None) -> Tuple[int, int]:
    """Bulk-insert discovered files as pending rows (ON CONFLICT DO NOTHING).

    Returns:
        Tuple[int, int]: (files_added, duplicates_found)
    """
    added_count = 0
    duplicate_count = 0
    files_to_insert = []

    for file_path in file_paths:
        try:
            stat = os.stat(file_path)
            mod_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            # MIME type and MD5 hash are skipped at discovery: the scan
            # overwrites both, and 1M+ libmagic reads would dominate the
            # discovery phase on network storage.

            files_to_insert.append({
                'file_path': file_path,
                'file_size': stat.st_size,
                'file_type': 'unknown',
                'last_modified': mod_time,
                'discovered_date': datetime.now(timezone.utc),
                'scan_status': 'pending',
                'is_corrupted': None,
                'marked_as_good': False,
                'file_exists': True,
                'has_warnings': False,
                'deep_scan': False
            })

        except Exception as e:
            logger.error(f"Failed to get file info: {file_path} - {e}")
            files_to_insert.append({
                'file_path': file_path,
                'discovered_date': datetime.now(timezone.utc),
                'scan_status': 'error',
                'error_message': str(e),
                'is_corrupted': None,
                'marked_as_good': False,
                'file_exists': True,
                'has_warnings': False,
                'deep_scan': False,
                'file_size': 0,
                'file_type': 'unknown',
                'last_modified': datetime.now(timezone.utc)
            })

    if files_to_insert:
        try:
            stmt = insert(ScanResult).values(files_to_insert)
            stmt = stmt.on_conflict_do_nothing(index_elements=['file_path'])

            # ON CONFLICT DO NOTHING: rowcount is the number actually inserted
            result = db.session.execute(stmt)
            db.session.commit()

            added_count += result.rowcount
            duplicate_count = len(files_to_insert) - result.rowcount

        except Exception as e:
            logger.error(f"Error during batch insert: {e}")
            db.session.rollback()
            for file_data in files_to_insert:
                try:
                    existing = db.session.query(ScanResult).filter_by(
                        file_path=file_data['file_path']
                    ).first()
                    if not existing:
                        db.session.add(ScanResult(**file_data))
                        added_count += 1
                    else:
                        duplicate_count += 1
                except Exception as e2:
                    logger.error(f"Failed to add file: {file_data['file_path']} - {e2}")
            db.session.commit()

    if scan_id and file_paths:
        rows = ScanResult.query.filter(ScanResult.file_path.in_(file_paths)).all()
        membership = [
            {'scan_id': scan_id, 'scan_result_id': row.id,
             'file_path': row.file_path, 'status': 'pending'}
            for row in rows
        ]
        if membership and db.session.get_bind().dialect.name == 'postgresql':
            db.session.execute(
                insert(ScanRunFile).values(membership).on_conflict_do_nothing(
                    index_elements=['scan_id', 'file_path']))
        elif membership:
            existing = {
                path for (path,) in db.session.query(ScanRunFile.file_path).filter(
                    ScanRunFile.scan_id == scan_id,
                    ScanRunFile.file_path.in_(file_paths),
                ).all()
            }
            for row in rows:
                if row.file_path not in existing:
                    db.session.add(ScanRunFile(scan_id=scan_id, scan_result_id=row.id,
                                               file_path=row.file_path, status='pending'))
        db.session.commit()

    return added_count, duplicate_count
