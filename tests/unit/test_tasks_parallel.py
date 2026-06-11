"""Unit tests for the chunk-distributed scan engine (tasks_parallel)"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pixelprobe.models import ScanResult, ScanState, ScanChunk


@pytest.fixture
def engine(app):
    """Celery-free engine core (importable without the full app/celery stack)"""
    import pixelprobe.services.scan_engine as engine_module
    return engine_module


def _add_pending(db, paths):
    for p in paths:
        db.session.add(ScanResult(file_path=p, scan_status='pending'))
    db.session.commit()


def _make_scan_state(db, scan_id, phase='scanning', is_active=True, scan_type='full'):
    state = ScanState(scan_id=scan_id, phase=phase, is_active=is_active,
                      scan_type=scan_type)
    state.start_time = datetime.now(timezone.utc)
    db.session.add(state)
    db.session.commit()
    return state


def _make_chunk(db, scan_id, first, last, is_complete=False, status='pending',
                files_scanned=0):
    chunk = ScanChunk(
        scan_id=scan_id,
        chunk_id=f'chunk-{first}',
        directory_path=json.dumps({'t': 'FCP', 'f': first, 'l': last}),
        phase='scanning',
        status=status,
        is_complete=is_complete,
        files_scanned=files_scanned,
        files_discovered=0,
    )
    db.session.add(chunk)
    db.session.commit()
    return chunk


class TestBuildScanChunks:

    def test_chunks_cover_all_pending_in_path_order(self, engine, app, db):
        with app.app_context():
            paths = [f'/media/dir/file{i:03d}.mkv' for i in range(7)]
            _add_pending(db, paths)

            chunks = engine.build_scan_chunks('scan-1')

            assert len(chunks) >= 1
            assert sum(c['files_discovered'] for c in chunks) == len(paths)

            rows = ScanChunk.query.filter_by(scan_id='scan-1').order_by(ScanChunk.id).all()
            ranges = [r.fcp_range() for r in rows]
            assert all(r is not None for r in ranges)
            assert ranges[0][0] == paths[0]
            assert ranges[-1][1] == paths[-1]

    def test_no_pending_files_creates_no_chunks(self, engine, app, db):
        with app.app_context():
            assert engine.build_scan_chunks('scan-1') == []

    def test_completed_files_not_chunked(self, engine, app, db):
        with app.app_context():
            db.session.add(ScanResult(file_path='/media/a.mkv', scan_status='completed'))
            db.session.add(ScanResult(file_path='/media/b.mkv', scan_status='pending'))
            db.session.commit()

            chunks = engine.build_scan_chunks('scan-1')
            assert sum(c['files_discovered'] for c in chunks) == 1


class TestFinalization:

    def test_not_finalized_while_chunks_incomplete(self, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f1')
            _make_chunk(db, 'scan-f1', '/a', '/b', is_complete=False)

            assert engine.maybe_finalize_scan('scan-f1') is False
            state = ScanState.query.filter_by(scan_id='scan-f1').first()
            assert state.is_active is True
            assert state.phase == 'scanning'

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalizes_when_all_chunks_terminal(self, mock_report, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f2')
            _make_chunk(db, 'scan-f2', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=5)
            _make_chunk(db, 'scan-f2', '/c', '/d', is_complete=True,
                        status='completed', files_scanned=3)

            assert engine.maybe_finalize_scan('scan-f2') is True

            state = ScanState.query.filter_by(scan_id='scan-f2').first()
            assert state.phase == 'completed'
            assert state.is_active is False
            assert state.end_time is not None
            # Totals come from chunk sums, not global table counts
            assert state.files_processed == 8
            mock_report.assert_called_once()

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_errored_chunks_finalize_as_error(self, mock_report, engine, app, db):
        """A scan with failed chunks must not report a clean completion"""
        with app.app_context():
            _make_scan_state(db, 'scan-f9')
            _make_chunk(db, 'scan-f9', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=5)
            _make_chunk(db, 'scan-f9', '/c', '/d', is_complete=True,
                        status='error', files_scanned=3)

            assert engine.maybe_finalize_scan('scan-f9') is True

            state = ScanState.query.filter_by(scan_id='scan-f9').first()
            assert state.phase == 'error'
            assert state.is_active is False
            assert 'chunks failed' in state.error_message
            mock_report.assert_called_once()

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalize_is_exactly_once(self, mock_report, engine, app, db):
        with app.app_context():
            _make_scan_state(db, 'scan-f3')
            _make_chunk(db, 'scan-f3', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=1)

            assert engine.maybe_finalize_scan('scan-f3') is True
            # Second caller sees phase != 'scanning' and does nothing
            assert engine.maybe_finalize_scan('scan-f3') is False
            assert mock_report.call_count == 1

    def test_not_finalized_when_scan_inactive(self, engine, app, db):
        """A cancelled scan (is_active=False) must not be flipped to completed"""
        with app.app_context():
            _make_scan_state(db, 'scan-f4', phase='cancelled', is_active=False)
            _make_chunk(db, 'scan-f4', '/a', '/b', is_complete=True, status='cancelled')

            assert engine.maybe_finalize_scan('scan-f4') is False
            state = ScanState.query.filter_by(scan_id='scan-f4').first()
            assert state.phase == 'cancelled'

    def test_not_finalized_with_zero_chunks(self, engine, app, db):
        """An active scan with no chunks (legacy engine or pre-chunking) must
        never be finalized from the chunk path or the sweeper backstop"""
        with app.app_context():
            _make_scan_state(db, 'scan-f7')

            assert engine.maybe_finalize_scan('scan-f7') is False
            state = ScanState.query.filter_by(scan_id='scan-f7').first()
            assert state.is_active is True
            assert state.phase == 'scanning'

    def test_not_finalized_without_scan_type(self, engine, app, db):
        """Legacy-engine scans (no scan_type) are out of the finalizer's scope"""
        with app.app_context():
            _make_scan_state(db, 'scan-f8', scan_type=None)
            _make_chunk(db, 'scan-f8', '/a', '/b', is_complete=True, status='completed')

            assert engine.maybe_finalize_scan('scan-f8') is False
            state = ScanState.query.filter_by(scan_id='scan-f8').first()
            assert state.is_active is True

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalize_reclaims_stuck_scanning_rows(self, mock_report, engine, app, db):
        with app.app_context():
            db.session.add(ScanResult(file_path='/media/stuck.mkv', scan_status='scanning'))
            db.session.commit()
            _make_scan_state(db, 'scan-f5')
            _make_chunk(db, 'scan-f5', '/a', '/b', is_complete=True, status='completed')

            assert engine.maybe_finalize_scan('scan-f5') is True
            row = ScanResult.query.filter_by(file_path='/media/stuck.mkv').first()
            assert row.scan_status == 'pending'

    @patch('pixelprobe.services.scan_engine.create_scan_report')
    def test_finalizer_never_decreases_progress(self, mock_report, engine, app, db):
        with app.app_context():
            state = _make_scan_state(db, 'scan-f6')
            state.files_processed = 100
            db.session.commit()
            _make_chunk(db, 'scan-f6', '/a', '/b', is_complete=True,
                        status='completed', files_scanned=10)

            assert engine.maybe_finalize_scan('scan-f6') is True
            state = ScanState.query.filter_by(scan_id='scan-f6').first()
            assert state.files_processed == 100
