"""Pytest configuration for Snippy.

This file exists to:

1. Pre-load `snippy` and the project's `assets/` directory so the
   package's icon/audio assets resolve in tests that don't have a
   `snippy.egg-info` (a clean dev checkout).

2. Make sure Pytest 9.x's auto-import does NOT pick up `pyproject.toml`
   as a Python module — Python 3.12+ added the CWD to `sys.path` via
   the empty-string entry, and a `import pyproject` lookup then resolves
   to the literal `pyproject.toml` file (not a module) and the parser
   raises `SyntaxError: Invalid statement`. Adding this conftest forces
   pytest to use a deterministic rootdir, sidesteps the issue.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Force the CWD to be the project root (where pyproject.toml lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# Remove the empty-string CWD entry from sys.path so that
# `import pyproject` from anywhere in the test process fails cleanly
# with ModuleNotFoundError rather than SyntaxError on the toml file.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
# Then prepend the project root for normal `import snippy` to work.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Default the offscreen Qt platform so tests don't need a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")