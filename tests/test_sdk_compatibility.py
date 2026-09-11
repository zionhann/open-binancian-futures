from __future__ import annotations

import subprocess
import sys


def test_package_imports_with_the_declared_binance_sdk() -> None:
    """Verify the package imports in a fresh interpreter with installed dependencies."""
    result = subprocess.run(
        [sys.executable, "-c", "import open_binancian_futures"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
