"""Runtime configuration. Secrets come from the process environment or a local .env that is never committed."""
import os
from pathlib import Path

from app import ROOT


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env(ROOT / ".env")

DB_PATH = Path(os.environ.get("SF_DB") or ROOT / "data" / "signalforge.sqlite")
WEB_DIR = ROOT / "app" / "web"
MODEL_IDS = {"view": "gemini-4-neon", "gate": "gemini-decision-1", "goal": "gemini-4-carbon", "bind": "gemini-4-carbon"}
GATE_THRESHOLD = 0.75
MAX_UPLOAD_BYTES = 5_000_000
MAX_ACTIVE_RUNS = 14
MAX_ACTIVE_RUNS_PER_SESSION = 3


def key_present() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
