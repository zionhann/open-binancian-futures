import logging
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from open_binancian_futures import cli, logging_config
from open_binancian_futures.constants import settings
from test_cli_backtesting import FakeRunner


def test_import_does_not_create_log_directory(tmp_path):
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    subprocess.run([sys.executable, '-c', 'import open_binancian_futures.cli'],
                   cwd=tmp_path, env=environment, check=True)
    assert not (tmp_path / 'log').exists()


def test_cli_network_override_and_reinitialization_preserve_external_handlers(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_config, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(settings, 'is_testnet', False)
    monkeypatch.setattr(cli, 'LiveTrading', FakeRunner)
    external = logging.NullHandler()
    root = logging.getLogger()
    root.addHandler(external)
    try:
        assert CliRunner().invoke(cli.app, ['strategy.py', '--testnet', '--live']).exit_code == 0
        first = logging_config.file_handler
        assert first is not None and Path(first.baseFilename).parent == tmp_path / 'test'
        assert CliRunner().invoke(cli.app, ['strategy.py', '--mainnet', '--live']).exit_code == 0
        second = logging_config.file_handler
        assert second is not None and Path(second.baseFilename).parent == tmp_path / 'main'
        assert first not in root.handlers and first.stream is None
        assert external in root.handlers
        assert sum(isinstance(handler, logging.handlers.TimedRotatingFileHandler)
                   for handler in root.handlers) == 1
    finally:
        root.removeHandler(external)


def test_named_and_numeric_log_levels(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_config, 'BASE_DIR', str(tmp_path))
    root = logging.getLogger()
    previous = root.level
    try:
        for value, expected in [('DEBUG', logging.DEBUG), ('warning', logging.WARNING),
                                ('20', logging.INFO), ('+10', logging.DEBUG), ('-1', -1)]:
            monkeypatch.setenv('LOGGING_LEVEL', value)
            logging_config.init(__name__)
            assert root.level == expected
    finally:
        root.setLevel(previous)
