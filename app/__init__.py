"""SignalForge application package. The event-day reference modules in reference/ import each
other by bare name, so the package puts that directory on sys.path once, here."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "reference"
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))
