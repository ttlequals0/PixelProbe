import importlib.util
import os

CONF_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'gunicorn.conf.py'
)


def load_conf():
    spec = importlib.util.spec_from_file_location('gunicorn_conf', CONF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGunicornConf:
    """GUNICORN_BIND must default to the historical IPv4 bind and accept a
    comma-separated list for dual-stack setups (issue 63)."""

    def test_defaults_match_previous_dockerfile_cmd(self, monkeypatch):
        for var in ('GUNICORN_BIND', 'GUNICORN_WORKERS', 'GUNICORN_TIMEOUT'):
            monkeypatch.delenv(var, raising=False)
        conf = load_conf()
        assert conf.bind == ['0.0.0.0:5000']
        assert conf.workers == 4
        assert conf.timeout == 300
        assert conf.accesslog == '-'
        assert conf.errorlog == '-'

    def test_single_ipv6_bind(self, monkeypatch):
        monkeypatch.setenv('GUNICORN_BIND', '[::]:5000')
        assert load_conf().bind == ['[::]:5000']

    def test_bind_list_strips_and_drops_blanks(self, monkeypatch):
        monkeypatch.setenv('GUNICORN_BIND', '0.0.0.0:5000, [::]:5000,,')
        assert load_conf().bind == ['0.0.0.0:5000', '[::]:5000']

    def test_blank_bind_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv('GUNICORN_BIND', '  ,')
        assert load_conf().bind == ['0.0.0.0:5000']
