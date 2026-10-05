"""Central settings. Values can be overridden with environment variables or a .env file."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path = ROOT / ".env") -> None:
    """Tiny .env loader (no extra dependency)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "market.db"))
REPORT_DIR = Path(os.getenv("REPORT_DIR", ROOT / "reports"))
LOG_DIR = ROOT / "logs"

# Only these equity series are used for rankings (EQ = regular equity, BE = trade-for-trade)
RANKING_SERIES = ("EQ", "BE")
MIN_TURNOVER_FOR_RANKING = float(os.getenv("MIN_TURNOVER_FOR_RANKING", 1e7))  # INR 1 crore

for d in (RAW_DIR, REPORT_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)
