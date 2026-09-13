"""Issue #70: a file whose raw reads stall forever (dead network mount,
failing disk sector) must not hang the scan worker. Stat/magic/hash are
pure-Python reads with no subprocess timeout to save them, so a watchdog
deadline skips the file and lets the scan continue."""
import hashlib
import os
import threading
import time
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pixelprobe.media_checker as media_checker
from pixelprobe.media_checker import (
    PixelProbe,
    FileReadTimeoutError,
    _read_with_timeout,
)
from pixelprobe.models import ScanResult, db


class TestReadWithTimeout:
    def test_returns_result(self):
        assert _read_with_timeout(lambda: 42, 5, '/tmp/x', 'test') == 42

    def test_propagates_exception(self):
        def boom():
            raise ValueError('bad')
        with pytest.raises(ValueError, match='bad'):
            _read_with_timeout(boom, 5, '/tmp/x', 'test')

    def test_raises_on_stalled_read(self):
        start = time.time()
        with pytest.raises(FileReadTimeoutError):
            _read_with_timeout(threading.Event().wait, 0.2, '/tmp/x', 'test')
        assert time.time() - start < 5


class TestAbandonedThreadCap:
    def test_fails_fast_at_cap_without_spawning(self, monkeypatch):
        monkeypatch.setattr(media_checker, '_MAX_ABANDONED_READ_THREADS', 0)
        with pytest.raises(FileReadTimeoutError, match='already stalled'):
            _read_with_timeout(lambda: 42, 5, '/tmp/x', 'test')

    def test_timed_out_thread_tracked_then_pruned(self):
        release = threading.Event()
        with pytest.raises(FileReadTimeoutError):
            _read_with_timeout(release.wait, 0.2, '/tmp/x', 'stall-test')

        with media_checker._abandoned_read_threads_lock:
            mine = [t for t in media_checker._abandoned_read_threads
                    if t.name == 'read-watchdog:stall-test']
        assert len(mine) == 1 and mine[0].is_alive()

        release.set()
        mine[0].join(5)
        # Next call prunes finished threads from the registry
        assert _read_with_timeout(lambda: 1, 5, '/tmp/x', 'prune-test') == 1
        with media_checker._abandoned_read_threads_lock:
            assert all(t.name != 'read-watchdog:stall-test'
                       for t in media_checker._abandoned_read_threads)


class TestHashReadTimeout:
    def test_stalled_read_raises(self, tmp_path):
        # A FIFO with no writer blocks open() exactly like stalled storage
        fifo = tmp_path / 'stalled.mkv'
        os.mkfifo(fifo)
        checker = PixelProbe(database_path=None)
        with pytest.raises(FileReadTimeoutError):
            checker.calculate_file_hash(str(fifo), timeout=0.5)

    def test_normal_file_still_hashes(self, tmp_path):
        f = tmp_path / 'ok.bin'
        f.write_bytes(b'hello world')
        checker = PixelProbe(database_path=None)
        expected = hashlib.sha256(b'hello world').hexdigest()
        assert checker.calculate_file_hash(str(f)) == expected

    def test_known_file_size_skips_stat(self, tmp_path):
        f = tmp_path / 'ok.bin'
        f.write_bytes(b'hello world')
        checker = PixelProbe(database_path=None)
        expected = hashlib.sha256(b'hello world').hexdigest()
        assert checker.calculate_file_hash(str(f), file_size=11) == expected

    def test_missing_file_still_returns_none(self, tmp_path):
        checker = PixelProbe(database_path=None)
        assert checker.calculate_file_hash(str(tmp_path / 'gone.bin')) is None


class TestGetFileInfoTimeout:
    def test_stalled_magic_raises(self, tmp_path, monkeypatch):
        f = tmp_path / 'poison.mkv'
        f.write_bytes(b'x' * 1024)

        def stalled_magic(*args, **kwargs):
            threading.Event().wait()

        monkeypatch.setattr('pixelprobe.media_checker.magic.from_file', stalled_magic)
        checker = PixelProbe(database_path=None)
        with pytest.raises(FileReadTimeoutError):
            checker.get_file_info(str(f), timeout=0.2)

    def test_normal_file_unaffected(self, tmp_path):
        f = tmp_path / 'ok.bin'
        f.write_bytes(b'hello world')
        checker = PixelProbe(database_path=None)
        info = checker.get_file_info(str(f))
        assert info['file_size'] == 11


class TestScanFileSkipsUnreadableFile:
    def test_read_timeout_marks_file_and_continues(self, tmp_path, monkeypatch):
        f = tmp_path / 'poison.mkv'
        f.write_bytes(b'x' * 1024)
        checker = PixelProbe(database_path=None)

        def stalled_hash(path, timeout=None, file_size=None):
            raise FileReadTimeoutError(
                f'hash read of {path} stalled for 300s - skipping unreadable file')

        monkeypatch.setattr(checker, 'calculate_file_hash', stalled_hash)
        result = checker.scan_file(str(f))
        assert result['is_corrupted'] is None
        assert 'stalled' in result['corruption_details']
        assert result['scan_tool'] == 'error'
        assert result['outcome'] == 'unreadable'

    def test_unreadable_rescan_preserves_baseline_until_a_stable_read(self, tmp_path, monkeypatch):
        path = tmp_path / 'known.png'
        Image.new('RGB', (8, 8), 'blue').save(path)
        uri = f"sqlite:///{tmp_path / 'results.db'}"
        engine = create_engine(uri)
        db.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            original_hash = 'a' * 64
            original_size = 12345
            original_mtime = datetime.now(timezone.utc)
            session.add(ScanResult(file_path=str(path), file_hash=original_hash,
                                   file_size=original_size,
                                   last_modified=original_mtime,
                                   scan_status='completed', is_corrupted=False))
            session.commit()

            checker = PixelProbe(database_path=uri)
            monkeypatch.setattr(checker, 'calculate_file_hash',
                                lambda *args, **kwargs: None)
            failed = checker.scan_file(str(path), force_rescan=True)
            session.expire_all()
            row = session.query(ScanResult).filter_by(file_path=str(path)).one()
            assert failed['outcome'] == 'unreadable'
            assert row.scan_status == 'unreadable'
            assert row.is_corrupted is None
            assert row.file_hash == original_hash
            assert row.file_size == original_size
            assert row.last_modified == original_mtime.replace(tzinfo=None)

            monkeypatch.undo()
            monkeypatch.setattr(checker, '_check_image_corruption',
                                lambda _path, **_kwargs: (False, [], 'pil', [], []))
            restored = checker.scan_file(str(path), force_rescan=True)
            session.expire_all()
            row = session.query(ScanResult).filter_by(file_path=str(path)).one()
            assert restored['outcome'] == 'completed'
            assert row.scan_status == 'completed'
            assert row.file_hash == restored['file_hash']
            assert row.file_size == path.stat().st_size
        finally:
            session.close()
            engine.dispose()
