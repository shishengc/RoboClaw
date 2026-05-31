#!/usr/bin/env python3
"""
Run the interactive NewAgent CLI.
"""

import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_PATH = os.path.join(PROJECT_ROOT, "src")

if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from new_agent.cli import main


if __name__ == "__main__":
    main()
