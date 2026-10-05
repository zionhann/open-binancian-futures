import os
import subprocess
import sys
from pathlib import Path


def test_core_import_and_helpers_without_optional_sdks(tmp_path):
    source = '''
import asyncio
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'anthropic', 'openai', 'slack_sdk', 'talib', 'pandas_ta', 'tiktoken', 'toon_format'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockOptional())
import open_binancian_futures
from open_binancian_futures.ai import ask_openai, ask_anthropic
from open_binancian_futures.webhook import Webhook
assert asyncio.run(ask_openai('test', 'unused')) is None
assert asyncio.run(ask_anthropic([], 'unused', 1)) is None
try:
    Webhook.of('https://hooks.slack.com/services/fake')
except RuntimeError as error:
    assert '[slack]' in str(error)
else:
    raise AssertionError('missing Slack extra must be actionable')
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                       OPENAI_API_KEY='test-only', ANTHROPIC_API_KEY='test-only')
    subprocess.run([sys.executable, '-c', source], cwd=tmp_path, env=environment, check=True)
