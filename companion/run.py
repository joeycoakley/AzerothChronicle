#!/usr/bin/env python3
"""Run the companion without installing it.

Keeps the first experience to `python companion/run.py status` on a stock
Python, with no virtualenv and no pip install. Installing the package
properly is still supported via pyproject.toml.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from azeroth_chronicle.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
