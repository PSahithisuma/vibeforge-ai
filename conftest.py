"""
conftest.py — pytest path and async setup for VibeForge Phase 0 tests.
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path so all imports resolve
ROOT = Path(__file__).parent
for p in [ROOT, ROOT / "agents", ROOT / "core"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
