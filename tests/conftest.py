"""Pytest configuration providing a lightweight UI test harness."""
from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

# Disable auto-loading external pytest plugins that might expect unavailable GUI bindings.
os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
