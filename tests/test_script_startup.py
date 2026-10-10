import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url


@pytest.mark.skipif(not os.environ.get('PIXELPROBE_TEST_POSTGRES_URI'),
                    reason='PIXELPROBE_TEST_POSTGRES_URI not set')
def test_documented_script_starts_with_postgres(tmp_path):
    database = make_url(os.environ['PIXELPROBE_TEST_POSTGRES_URI'])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = dict(os.environ,
               POSTGRES_HOST=database.host,
               POSTGRES_PORT=str(database.port or 5432),
               POSTGRES_DB=database.database,
               POSTGRES_USER=database.username,
               POSTGRES_PASSWORD=database.password or '',
               DATABASE_URL=os.environ['PIXELPROBE_TEST_POSTGRES_URI'],
               FLASK_ENV='production',
               SECRET_KEY='script-startup-test',
               SCHEDULER_ENABLED='false', DEBUG='false', PORT=str(port))
    root = Path(__file__).resolve().parents[1]
    log_path = tmp_path / 'startup.log'
    with log_path.open('w') as output:
        process = subprocess.Popen([sys.executable, 'app.py'], cwd=root,
                                   env=env, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1) as response:
                        assert response.status == 200
                        return
                except urllib.error.URLError:
                    time.sleep(0.1)
            pytest.fail(log_path.read_text())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
