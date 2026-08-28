"""Where things live on this machine.

The recorded traffic is portable and belongs in git. Anything MEASURED belongs
to the machine that measured it and is written to the data directory instead,
never inherited from someone else's laptop.
"""
from __future__ import annotations

import os

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("CAUSEWAY_DATA", os.path.join(BACKEND_ROOT, ".data"))
FIXTURE_DIR = os.path.join(BACKEND_ROOT, "fixtures")

TEMPLATE_DB = os.path.join(DATA_DIR, "template.db")
WORK_DB = os.path.join(DATA_DIR, "sandbox.db")
CALIBRATION_PATH = os.path.join(DATA_DIR, "calibration.json")
FIXTURE_ID = "incident-001"
FIXTURE_PATH = os.path.join(FIXTURE_DIR, FIXTURE_ID + ".json")


def repetitions(default: int) -> int:
    """Phases per measurement. A measurement decision, never a threshold - it
    changes how well a phase is estimated and not what it has to clear."""
    try:
        return max(1, int(os.environ.get("CAUSEWAY_REPS", default)))
    except (TypeError, ValueError):
        return default


def is_ready() -> bool:
    return all(os.path.exists(p)
               for p in (TEMPLATE_DB, FIXTURE_PATH, CALIBRATION_PATH))
