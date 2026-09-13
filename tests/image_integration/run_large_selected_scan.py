#!/usr/bin/env python3
"""Exercise a large selected-file scan through the production container image."""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


SELECTED_PARENT_COUNT = 51
SELECTED_CHILD_COUNT = 51
SELECTED_TOTAL = SELECTED_PARENT_COUNT + SELECTED_CHILD_COUNT + 1


def run(*args, check=True, capture_output=False):
    return subprocess.run(args, check=check, text=True, capture_output=capture_output)


def request(opener, base_url, method, path, payload=None, token=None, csrf=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if csrf:
        headers['X-CSRFToken'] = csrf
    try:
        with opener.open(Request(f'{base_url}{path}', data=data, headers=headers, method=method), timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read() or b'{}')


def wait_for(predicate, timeout, description):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(f'Timed out waiting for {description}')


def postgres_scalar(container, sql):
    output = run('docker', 'exec', container, 'psql', '-U', 'pixelprobe', '-d', 'pixelprobe', '-Atqc', sql,
                 capture_output=True).stdout.strip()
    return output


def container_logs(container):
    result = run('docker', 'logs', container, check=False, capture_output=True)
    return f'{result.stdout}\n{result.stderr}'


def setting_value(payload, key):
    for group in payload.get('groups', []):
        for setting in group.get('settings', []):
            if setting.get('key') == key:
                return setting.get('value')
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--timeout', type=int, default=420)
    args = parser.parse_args()

    suffix = uuid4().hex[:12]
    prefix = f'pixelprobe-image-selected-{suffix}'
    network = f'{prefix}-net'
    postgres = f'{prefix}-postgres'
    valkey = f'{prefix}-valkey'
    web = f'{prefix}-web'
    worker = f'{prefix}-worker'
    harness_root = Path(os.environ.get('PIXELPROBE_IMAGE_TEST_TMPDIR', Path.cwd() / '.claude' / 'image-integration'))
    harness_root.mkdir(parents=True, exist_ok=True)
    media_dir = Path(tempfile.mkdtemp(prefix=f'{prefix}-', dir=harness_root))
    labels = ['--label', 'pixelprobe.image_integration=true']
    hardening = ['--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                 '--pids-limit', '256', '--memory', '2g', '--cpus', '2']
    containers = []

    try:
        os.chmod(media_dir, 0o777)
        run('docker', 'network', 'create', *labels, network)
        run('docker', 'run', '-d', '--name', postgres, *labels, '--network', network,
            '--network-alias', 'postgres', '-e', 'POSTGRES_DB=pixelprobe',
            '-e', 'POSTGRES_USER=pixelprobe', '-e', 'POSTGRES_PASSWORD=pixelprobe-image-test',
            'postgres:18-alpine')
        containers.append(postgres)
        run('docker', 'run', '-d', '--name', valkey, *labels, '--network', network,
            '--network-alias', 'valkey', 'valkey/valkey:9-alpine', 'valkey-server', '--save', '')
        containers.append(valkey)
        wait_for(lambda: run('docker', 'exec', postgres, 'pg_isready', '-U', 'pixelprobe', '-d', 'pixelprobe',
                             check=False).returncode == 0, 60, 'PostgreSQL')

        fixture_script = (
            'set -eu; '
            'mkdir -p /media/baseline /media/selected-parent/child /media/unselected; '
            'ffmpeg -y -f lavfi -i testsrc2=size=64x64:rate=8 -t 1 -pix_fmt yuv420p /media/template.mp4 >/dev/null 2>&1; '
            'cp /media/template.mp4 /media/baseline/baseline.mp4; '
            'cp /media/template.mp4 /media/unselected/sibling.mp4; '
            'for n in $(seq -w 0 50); do cp /media/template.mp4 /media/selected-parent/parent-$n.mp4; done; '
            'for n in $(seq -w 0 50); do cp /media/template.mp4 /media/selected-parent/child/child-$n.mp4; done; '
            'rm /media/template.mp4'
        )
        run('docker', 'run', '--rm', '-u', '0:0', '-v', f'{media_dir}:/media', args.image,
            'bash', '-lc', fixture_script)

        env = [
            '-e', 'SECRET_KEY=image-integration-only-secret',
            '-e', 'SESSION_COOKIE_SECURE=false',
            '-e', 'SCHEDULER_ENABLED=false',
            '-e', 'POSTGRES_HOST=postgres', '-e', 'POSTGRES_PORT=5432',
            '-e', 'POSTGRES_DB=pixelprobe', '-e', 'POSTGRES_USER=pixelprobe',
            '-e', 'POSTGRES_PASSWORD=pixelprobe-image-test',
            '-e', 'CELERY_BROKER_URL=redis://valkey:6379/0',
            '-e', 'CELERY_RESULT_BACKEND=redis://valkey:6379/0',
            '-e', 'SCAN_PATHS=/media', '-e', 'EXCLUDED_EXTENSIONS=',
            '-e', 'MAX_WORKERS=4', '-e', 'DB_POOL_SIZE=8',
        ]
        run('docker', 'run', '-d', '--name', web, *labels, *hardening, '--network', network, '-p', '127.0.0.1::5000',
            '-v', f'{media_dir}:/media:ro', *env, args.image)
        containers.append(web)
        run('docker', 'run', '-d', '--name', worker, *labels, *hardening, '--network', network,
            '-v', f'{media_dir}:/media:ro', *env, '-e', 'CELERY_CONCURRENCY=1',
            args.image, 'python', 'celery_worker.py')
        containers.append(worker)

        port_output = run('docker', 'port', web, '5000/tcp', capture_output=True).stdout.strip()
        port = port_output.rsplit(':', 1)[1]
        base_url = f'http://127.0.0.1:{port}'
        wait_for(lambda: run('curl', '--fail', '--silent', f'{base_url}/healthz', check=False).returncode == 0,
                 120, 'web health')

        cookies = http.cookiejar.CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookies))
        with opener.open(f'{base_url}/login', timeout=20) as response:
            login_page = response.read().decode()
        csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)">', login_page)
        if not csrf_match:
            raise RuntimeError('Login page did not provide a CSRF token')
        csrf = csrf_match.group(1)
        status, setup = request(opener, base_url, 'POST', '/api/auth/setup',
                                {'password': 'image-integration-password'}, csrf=csrf)
        if status != 200:
            raise RuntimeError(f'Initial admin setup failed: {status} {setup}')
        status, token_response = request(opener, base_url, 'POST', '/api/tokens',
                                         {'description': 'image integration'}, csrf=csrf)
        if status != 201 or not token_response.get('token'):
            raise RuntimeError(f'API token creation failed: {status} {token_response}')
        token = token_response['token']

        setting_key = 'timeouts.temporal_sample_timeout_secs'
        status, settings = request(opener, base_url, 'PUT', '/api/settings', {setting_key: 120}, token=token, csrf=csrf)
        if status != 200 or setting_key not in settings.get('updated', []):
            raise RuntimeError(f'Scanner setting update failed: {status} {settings}')
        status, settings = request(opener, base_url, 'GET', '/api/settings', token=token)
        if status != 200 or setting_value(settings, setting_key) != 120:
            raise RuntimeError(f'Scanner setting was not retained: {status} {settings}')

        status, baseline = request(opener, base_url, 'POST', '/api/scan',
                                   {'directories': ['/media/baseline'], 'force_rescan': True}, token=token)
        if status != 200 or not baseline.get('scan_id'):
            raise RuntimeError(f'Baseline scan launch failed: {status} {baseline}')
        baseline_id = baseline['scan_id']

        def terminal(scan_id):
            phase = postgres_scalar(postgres, f"SELECT phase FROM scan_state WHERE scan_id = '{scan_id}'")
            return phase if phase in {'completed', 'error', 'cancelled', 'crashed'} else None

        if wait_for(lambda: terminal(baseline_id), args.timeout, 'baseline terminal state') != 'completed':
            raise RuntimeError(f'Baseline scan was not completed: {container_logs(worker)}')

        selected = ['/media/baseline/baseline.mp4']
        selected.extend(f'/media/selected-parent/parent-{number:02d}.mp4' for number in range(SELECTED_PARENT_COUNT))
        selected.extend(f'/media/selected-parent/child/child-{number:02d}.mp4' for number in range(SELECTED_CHILD_COUNT))
        status, launched = request(opener, base_url, 'POST', '/api/scan-files-parallel', {
            'file_paths': selected,
            'force_rescan': True,
            'num_workers': 4,
        }, token=token)
        if status != 200 or launched.get('file_count') != SELECTED_TOTAL or not launched.get('scan_id'):
            raise RuntimeError(f'Large selected scan launch failed: {status} {launched}')
        selected_id = launched['scan_id']

        def live_status():
            response_status, payload = request(opener, base_url, 'GET', '/api/scan-status', token=token)
            if response_status != 200 or payload.get('scan_id') != selected_id:
                return None
            active_files = payload.get('active_files')
            if (payload.get('files_processed', payload.get('current', 0)) <= 0
                    or not payload.get('eta') or not isinstance(active_files, list) or not active_files):
                return None
            return payload

        live = wait_for(live_status, 90, 'active selected-file progress with ETA')
        active_files = live['active_files']
        if (len(active_files) > 4 or live.get('active_file_count', 0) > 4
                or live.get('active_file_count', 0) < len(active_files)
                or any(entry.get('file') not in selected
                       or entry.get('directory') != os.path.dirname(entry['file'])
                       for entry in active_files)):
            raise RuntimeError(f'Active file evidence mismatch: {live}')
        if wait_for(lambda: terminal(selected_id), args.timeout, 'large selected scan terminal state') != 'completed':
            raise RuntimeError(f'Large selected scan was not completed: {container_logs(worker)}')

        status, completed_status = request(opener, base_url, 'GET', '/api/scan-status', token=token)
        if (status != 200 or completed_status.get('scan_id') != selected_id
                or completed_status.get('active_files') != []
                or completed_status.get('active_file_count') != 0):
            raise RuntimeError(f'Completed scan retained active files: {status} {completed_status}')
        if not wait_for(lambda: run('docker', 'exec', valkey, 'valkey-cli', '--raw', 'EXISTS',
                                    f'scan_progress:{selected_id}', capture_output=True).stdout.strip() == '0',
                        60, 'completed scan Redis progress clear'):
            raise RuntimeError('Completed scan Redis progress key remained present')

        member_total = postgres_scalar(postgres, f"SELECT count(*) FROM scan_run_files WHERE scan_id = '{selected_id}'")
        distinct_paths = postgres_scalar(postgres, f"SELECT count(DISTINCT file_path) FROM scan_run_files WHERE scan_id = '{selected_id}'")
        completed_total = postgres_scalar(postgres, f"SELECT count(*) FROM scan_run_files WHERE scan_id = '{selected_id}' AND status = 'completed'")
        distinct_results = postgres_scalar(postgres, f"SELECT count(DISTINCT scan_result_id) FROM scan_run_files WHERE scan_id = '{selected_id}' AND scan_result_id IS NOT NULL")
        state_values = postgres_scalar(postgres, f"SELECT force_rescan::text || ',' || num_workers || ',' || estimated_total || ',' || files_processed || ',' || phase FROM scan_state WHERE scan_id = '{selected_id}'")
        force_rescan, workers, estimated_total, files_processed, phase = state_values.split(',', 4)
        chunk_total = postgres_scalar(postgres, f"SELECT count(*) FROM scan_chunks WHERE scan_id = '{selected_id}'")
        completed_chunks = postgres_scalar(postgres, f"SELECT count(*) FROM scan_chunks WHERE scan_id = '{selected_id}' AND status = 'completed'")
        chunk_scanned = postgres_scalar(postgres, f"SELECT COALESCE(sum(files_scanned), 0) FROM scan_chunks WHERE scan_id = '{selected_id}'")
        report_scanned = postgres_scalar(postgres, f"SELECT files_scanned FROM scan_reports WHERE scan_id = '{selected_id}'")
        selected_result_total = postgres_scalar(postgres, "SELECT count(*) FROM scan_results WHERE file_path LIKE '/media/selected-parent/%' OR file_path = '/media/baseline/baseline.mp4'")
        unselected_total = postgres_scalar(postgres, "SELECT count(*) FROM scan_results WHERE file_path = '/media/unselected/sibling.mp4'")
        terminal_history = postgres_scalar(postgres, "SELECT count(*) FROM scan_state WHERE phase = 'completed'")
        expected = str(SELECTED_TOTAL)
        if (member_total, distinct_paths, completed_total, distinct_results, files_processed,
                chunk_scanned, report_scanned, selected_result_total, unselected_total) != (
                expected, expected, expected, expected, expected, expected, expected, expected, '0') or int(chunk_total) == 0 or chunk_total != completed_chunks:
            raise RuntimeError('Large selected scan evidence mismatch: '
                               f'members={member_total} paths={distinct_paths} completed={completed_total} '
                               f'distinct={distinct_results} processed={files_processed} chunks={chunk_scanned}/{completed_chunks}/{chunk_total} '
                               f'report={report_scanned} results={selected_result_total} sibling={unselected_total}')
        if (force_rescan, workers, estimated_total, files_processed, phase) != ('true', '4', expected, expected, 'completed'):
            raise RuntimeError(f'Selected scan state mismatch: {state_values}')
        if int(terminal_history) < 2:
            raise RuntimeError(f'Terminal scan history missing baseline or selected run: {terminal_history}')

        status, cancelled = request(opener, base_url, 'POST', '/api/cancel-scan', {'scan_id': selected_id}, token=token)
        if status not in {200, 409} or cancelled.get('cancelled'):
            raise RuntimeError(f'Terminal selected scan changed by cancel request: {status} {cancelled}')

        status, launched = request(opener, base_url, 'POST', '/api/scan-files-parallel', {
            'file_paths': selected,
            'force_rescan': True,
            'num_workers': 4,
        }, token=token)
        if status != 200 or not launched.get('scan_id'):
            raise RuntimeError(f'Cancellation scan launch failed: {status} {launched}')
        cancelled_id = launched['scan_id']

        def cancelled_run_active():
            response_status, payload = request(opener, base_url, 'GET', '/api/scan-status', token=token)
            if response_status != 200 or payload.get('scan_id') != cancelled_id:
                return None
            active_files = payload.get('active_files')
            if not isinstance(active_files, list) or not active_files:
                return None
            return payload

        wait_for(cancelled_run_active, 90, 'active selected-file futures before cancellation')
        status, cancelled = request(opener, base_url, 'POST', '/api/cancel-scan', {'scan_id': cancelled_id}, token=token)
        if status != 200 or not cancelled.get('cancelled'):
            raise RuntimeError(f'Active selected scan did not cancel: {status} {cancelled}')
        if wait_for(lambda: terminal(cancelled_id), 90, 'cancelled selected scan terminal state') != 'cancelled':
            raise RuntimeError(f'Cancellation did not terminalize: {container_logs(worker)}')
        status, cancelled_status = request(opener, base_url, 'GET', '/api/scan-status', token=token)
        if (status != 200 or cancelled_status.get('scan_id') != cancelled_id
                or cancelled_status.get('active_files') != []
                or cancelled_status.get('active_file_count') != 0):
            raise RuntimeError(f'Cancelled scan retained active files: {status} {cancelled_status}')
        if not wait_for(lambda: run('docker', 'exec', valkey, 'valkey-cli', '--raw', 'EXISTS',
                                    f'scan_progress:{cancelled_id}', capture_output=True).stdout.strip() == '0',
                        60, 'cancelled scan Redis progress clear'):
            raise RuntimeError('Cancelled scan Redis progress key remained present')
        worker_output = container_logs(worker)
        if 'Could not read scanner settings' in worker_output:
            raise RuntimeError('Worker fell back to default scanner settings')
        print(json.dumps({'selected_members': SELECTED_TOTAL, 'scan_id': selected_id,
                          'active_files': len(active_files), 'eta': bool(live.get('eta')), 'status': 'passed'}))
    finally:
        for container in reversed(containers):
            run('docker', 'rm', '-fv', container, check=False)
        run('docker', 'network', 'rm', network, check=False)
        shutil.rmtree(media_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
