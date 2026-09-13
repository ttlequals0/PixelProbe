import csv
import io
import json
import tracemalloc
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from pixelprobe.models import (db, CleanupFileDecision, ScanResult, ScanReport,
                               ScanRunFile, ScanRunRoot, ScanState)
from pixelprobe.api.reports_routes import (_report_directories, _snapshot_available,
                                           _snapshot_status)


def test_large_generic_exports_stream_all_rows(authenticated_client, app):
    with app.app_context():
        rows = [ScanResult(file_path=f'/media/{i}.mp4', file_size=i,
                           file_type='video/mp4', scan_status='completed',
                           scan_output='x' * 10000) for i in range(1001)]
        rows[0].scan_status = 'pending'
        rows[1].scan_status = 'error'
        db.session.add_all(rows)
        db.session.commit()
    response = authenticated_client.get('/api/export?format=csv')
    assert response.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))) == 1001
    response = authenticated_client.get('/api/export?format=json')
    assert response.status_code == 200
    assert len(json.loads(response.get_data(as_text=True))) == 1001


def test_complete_report_export_streams_immutable_snapshot(authenticated_client, app):
    scan_id = 'stream-test'
    with app.app_context():
        report = ScanReport(report_id='stream-report', scan_id=scan_id,
                            scan_type='full_scan', start_time=datetime.now(timezone.utc))
        db.session.add(report)
        db.session.add_all([ScanRunFile(scan_id=scan_id, file_path=f'/snapshot/{i}.mp4',
                                        status='completed', file_size=i) for i in range(1001)])
        db.session.commit()
    response = authenticated_client.get('/api/scan-reports/stream-report/export?complete=true')
    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert len(payload['scan_results']) == 1001
    assert payload['scan_results'][0]['file_path'] == '/snapshot/0.mp4'


def test_cleanup_export_streams_durable_decisions_without_path_list(authenticated_client, app):
    cleanup_run_id = 'cleanup-stream-test'
    with app.app_context():
        report = ScanReport(report_id='cleanup-stream-report', scan_type='cleanup',
                            cleanup_run_id=cleanup_run_id,
                            start_time=datetime.now(timezone.utc),
                            cleanup_details_total=1001, cleanup_details_truncated=True)
        db.session.add(report)
        db.session.add_all([
            CleanupFileDecision(cleanup_run_id=cleanup_run_id,
                                scan_result_id=index + 1,
                                file_path=f'/cleanup/{index}.mp4',
                                decision='deleted',
                                reason='media_absent_confirmed')
            for index in range(1001)
        ])
        db.session.commit()

    response = authenticated_client.get('/api/scan-reports/cleanup-stream-report/export')
    assert response.status_code == 200
    preview = json.loads(response.get_data(as_text=True))
    assert preview['cleanup_decisions_total'] == 1001
    assert preview['cleanup_decisions_truncated'] is True
    assert len(preview['scan_results']) == 1000

    response = authenticated_client.get(
        '/api/scan-reports/cleanup-stream-report/export?complete=true')
    assert response.status_code == 200
    complete = json.loads(response.get_data(as_text=True))
    assert complete['cleanup_decisions_truncated'] is False
    assert len(complete['scan_results']) == 1001
    assert complete['scan_results'][0]['action'] == 'inventory_record_removed'


def test_generic_stream_has_initial_high_watermark_and_no_open_request_transaction(
        authenticated_client, app, db):
    with app.app_context():
        db.session.add_all([
            ScanResult(file_path=f'/media/original-{index}.mp4', scan_status='completed')
            for index in range(501)
        ])
        db.session.commit()
        initial_total = ScanResult.query.count()

    response = authenticated_client.get('/api/export?format=csv', buffered=False)
    iterator = iter(response.response)
    first_chunk = next(iterator)
    assert b'File Path' in first_chunk
    with app.app_context():
        assert db.session().in_transaction() is False
        db.session.add(ScanResult(file_path='/media/inserted-after-start.mp4',
                                  scan_status='completed'))
        db.session.commit()
    payload = first_chunk + b''.join(iterator)
    response.close()

    exported = list(csv.DictReader(io.StringIO(payload.decode('utf-8'))))
    assert len(exported) == initial_total
    assert all(row['File Path'] != '/media/inserted-after-start.mp4' for row in exported)


def test_report_csv_streams_complete_immutable_snapshot(authenticated_client, app, db):
    scan_id = 'csv-stream-test'
    with app.app_context():
        db.session.add(ScanReport(report_id='csv-stream-report', scan_id=scan_id,
                                  scan_type='full_scan', start_time=datetime.now(timezone.utc)))
        db.session.add_all([
            ScanRunFile(scan_id=scan_id, file_path=f'/snapshot/{index}.mp4',
                        status='completed', scan_output='x' * 10000)
            for index in range(1001)
        ])
        db.session.commit()
    response = authenticated_client.get('/api/scan-reports/csv-stream-report/export?format=csv')
    assert response.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))) == 1001


def test_snapshot_statuses_do_not_label_noncompleted_work_healthy():
    assert _snapshot_status(SimpleNamespace(status='scanning', outcome=None,
                                            is_corrupted=False, has_warnings=False,
                                            marked_as_good=False)) == 'scanning'
    assert _snapshot_status(SimpleNamespace(status='pending', outcome=None,
                                            is_corrupted=False, has_warnings=False,
                                            marked_as_good=False)) == 'pending'
    assert _snapshot_status(SimpleNamespace(status='completed', outcome='unreadable',
                                            is_corrupted=False, has_warnings=False,
                                            marked_as_good=False)) == 'unreadable'
    assert _snapshot_status(SimpleNamespace(status='cancelled', outcome=None,
                                            is_corrupted=False, has_warnings=False,
                                            marked_as_good=False)) == 'cancelled'
    assert _snapshot_status(SimpleNamespace(status='unknown', outcome=None,
                                            is_corrupted=False, has_warnings=False,
                                            marked_as_good=False)) == 'unknown'


def test_report_summary_counts_error_only_runs_as_zero_success(authenticated_client, app):
    with app.app_context():
        db.session.add(ScanReport(
            report_id='error-only-summary', scan_type='full_scan',
            start_time=datetime.now(timezone.utc), files_scanned=0,
            files_corrupted=0, files_error=4,
        ))
        db.session.add(ScanReport(
            report_id='empty-summary', scan_type='full_scan',
            start_time=datetime.now(timezone.utc), files_scanned=0,
        ))
        db.session.add(ScanReport(
            report_id='mixed-summary', scan_type='full_scan',
            start_time=datetime.now(timezone.utc), files_scanned=3,
            files_corrupted=1, files_error=2,
        ))
        db.session.commit()
    error_only = authenticated_client.get('/api/scan-reports/error-only-summary').get_json()
    empty = authenticated_client.get('/api/scan-reports/empty-summary').get_json()
    assert error_only['summary']['successful_observations'] == 0
    assert error_only['summary']['success_rate'] == 0
    assert empty['summary']['success_rate'] == 0
    mixed = authenticated_client.get('/api/scan-reports/mixed-summary').get_json()
    assert mixed['summary']['successful_observations'] == 2
    assert mixed['summary']['observed_terminal_files'] == 5
    assert mixed['summary']['success_rate'] == 40


def test_legacy_double_encoded_report_directories_remain_an_array():
    value = json.dumps(json.dumps(['/media/a', '/media/b']))
    assert _report_directories(value) == ['/media/a', '/media/b']


def test_snapshot_availability_requires_members_or_completed_root_evidence(app, db):
    with app.app_context():
        legacy = ScanReport(report_id='legacy-no-history', scan_id='legacy-no-history',
                            scan_type='full_scan', start_time=datetime.now(timezone.utc))
        empty = ScanReport(report_id='valid-empty-history', scan_id='valid-empty-history',
                           scan_type='full_scan', start_time=datetime.now(timezone.utc))
        db.session.add_all([legacy, empty])
        db.session.add(ScanState(scan_id='legacy-no-history', phase='completed', is_active=False))
        db.session.add(ScanRunRoot(scan_id='valid-empty-history', root_path='/unavailable',
                                   status='unavailable', discovered_count=0))
        db.session.commit()
        assert _snapshot_available(legacy) is False
        assert _snapshot_available(empty) is True


@pytest.mark.parametrize('count', [999, 1000, 1001])
def test_bounded_pdf_paths_escape_metadata_and_omit_large_scan_output(
        authenticated_client, app, db, count, record_property):
    scan_id = f'pdf-limit-{count}'
    report_id = f'pdf-limit-report-{count}'
    with app.app_context():
        db.session.add(ScanReport(
            report_id=report_id, scan_id=scan_id, scan_type='full_scan',
            start_time=datetime.now(timezone.utc), directories_scanned=json.dumps(
                json.dumps(['/media/<metadata>&']))))
        db.session.add_all([
            ScanRunFile(
                scan_id=scan_id, file_path=f'/media/<path-{index}>&.mp4',
                status='completed', corruption_details='<detail>&',
                scan_output='x' * 10000,
            )
            for index in range(count)
        ])
        db.session.commit()

    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if 'scan_run_files' in statement.lower():
            statements.append(statement.lower())

    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute', capture)
    tracemalloc.start()
    try:
        per_run = authenticated_client.get(f'/api/scan-reports/{report_id}/pdf')
        combined = authenticated_client.post('/api/reports/download-multiple', json={
            'report_ids': [report_id], 'format': 'pdf'})
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        with app.app_context():
            event.remove(db.engine, 'before_cursor_execute', capture)

    assert per_run.status_code == 200
    assert combined.status_code == 200
    assert per_run.data.startswith(b'%PDF')
    assert combined.data.startswith(b'%PDF')
    assert statements
    assert all('scan_output' not in statement for statement in statements), statements
    detail_queries = [statement for statement in statements if 'order by' in statement]
    assert len(detail_queries) <= 2
    record_property(f'pdf_peak_allocations_{count}_rows', peak)
    assert peak < 64 * 1024 * 1024, f'PDF peak allocation was {peak} bytes'


def test_combined_pdf_uses_one_report_lookup_and_global_detail_budget(
        authenticated_client, app, db):
    report_ids = []
    with app.app_context():
        for report_number in range(2):
            scan_id = f'combined-budget-{report_number}'
            report_id = f'combined-budget-report-{report_number}'
            report_ids.append(report_id)
            db.session.add(ScanReport(report_id=report_id, scan_id=scan_id,
                                      scan_type='full_scan',
                                      start_time=datetime.now(timezone.utc)))
            db.session.add_all([
                ScanRunFile(scan_id=scan_id, file_path=f'/combined/{report_number}-{index}.mp4',
                            status='completed')
                for index in range(600)
            ])
        db.session.commit()

    report_queries = []
    file_queries = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if 'from scan_reports' in statement.lower():
            report_queries.append(statement)
        if 'from scan_run_files' in statement.lower() and 'order by' in statement.lower():
            file_queries.append(statement)

    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        response = authenticated_client.post('/api/reports/download-multiple', json={
            'report_ids': report_ids, 'format': 'pdf'})
    finally:
        with app.app_context():
            event.remove(db.engine, 'before_cursor_execute', capture)

    assert response.status_code == 200
    assert response.data.startswith(b'%PDF')
    assert len(report_queries) == 1
    assert len(file_queries) == 2
