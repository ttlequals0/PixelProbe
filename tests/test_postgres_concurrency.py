"""
Concurrency regression: parallel scan workers sharing one PixelProbe instance
must persist results safely against a real PostgreSQL backend. StaticPool
previously shared a single raw psycopg2 connection across worker threads.

Requires PIXELPROBE_TEST_POSTGRES_URI (provided by CI's postgres service);
skipped otherwise.
"""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from pixelprobe.media_checker import PixelProbe
from pixelprobe.models import db

POSTGRES_URI = os.environ.get('PIXELPROBE_TEST_POSTGRES_URI')


@pytest.mark.postgres
@pytest.mark.timeout(300)
@pytest.mark.skipif(not POSTGRES_URI, reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_parallel_scan_file_saves_are_connection_safe(tmp_path):
    engine = create_engine(POSTGRES_URI)
    db.metadata.create_all(engine)

    files = []
    for i in range(50):
        p = tmp_path / f'img_{i}.png'
        Image.new('RGB', (32, 32), (i * 5 % 255, 100, 150)).save(str(p))
        files.append(str(p))

    checker = PixelProbe(database_path=POSTGRES_URI, max_workers=4)
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(lambda f: checker.scan_file(f, force_rescan=True), files))

        assert len(results) == 50
        assert all(r is not None for r in results)
        assert checker.failed_saves == 0, f'{checker.failed_saves} saves failed under concurrency'

        with engine.connect() as conn:
            count = conn.execute(
                text('SELECT count(*) FROM scan_results WHERE file_path LIKE :p'),
                {'p': f'{tmp_path}%'}
            ).scalar()
        assert count == 50, f'expected 50 persisted rows, got {count}'
    finally:
        with engine.begin() as conn:
            conn.execute(
                text('DELETE FROM scan_results WHERE file_path LIKE :p'),
                {'p': f'{tmp_path}%'}
            )
        engine.dispose()
