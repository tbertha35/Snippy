"""Snippy entry point.

Run with:
    python -m snippy
"""
from __future__ import annotations

import sys

from snippy.app import run


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))