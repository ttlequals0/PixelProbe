import logging
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.app.log import Logging


def test_worker_logs_use_stdout(monkeypatch, capfd):
    worker = Mock()
    worker.worker_main.return_value = 0
    monkeypatch.setitem(sys.modules, 'app', SimpleNamespace(celery=worker))
    configure = Mock()
    monkeypatch.setattr(logging, 'basicConfig', configure)
    entrypoint = Path(__file__).resolve().parents[2] / 'celery_worker.py'
    namespace = runpy.run_path(str(entrypoint))
    with pytest.raises(SystemExit) as stopped:
        namespace['main']()
    assert stopped.value.code == 0

    assert configure.call_args.kwargs['stream'] is sys.stdout
    argv = worker.worker_main.call_args.args[0]
    destination = argv[argv.index('--logfile') + 1]
    assert destination == '/dev/stdout'
    handler = Logging._detect_handler(None, destination)
    try:
        handler.emit(logging.LogRecord('worker', logging.INFO, '', 0,
                                       'stdout regression', (), None))
        captured = capfd.readouterr()
        assert 'stdout regression' in captured.out
        assert 'stdout regression' not in captured.err
    finally:
        handler.close()
