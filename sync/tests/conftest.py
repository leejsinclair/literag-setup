"""Shared pytest setup for kb-sync.

Tests run from sync/ (on the host with uv, or in the image), so kb_sync imports from here.
Each test file keeps its own fake endpoints; this file holds only what every file needs.
"""

import sys
from pathlib import Path

SYNC_DIR = Path(__file__).resolve().parent.parent
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))
