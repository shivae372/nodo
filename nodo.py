#!/usr/bin/env python3
"""
Standalone launcher so Nodo runs from anywhere without installing.

    python /path/to/nodo/nodo.py /path/to/your/project

This adds the repo to sys.path and dispatches to the package CLI, which keeps
relative imports working even when invoked by absolute path from another folder.
(Running nodo/__main__.py directly would break those imports.)
"""
import os
import sys

# Ensure the cloned repo (this file's directory) is importable as the `nodo` package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nodo.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
