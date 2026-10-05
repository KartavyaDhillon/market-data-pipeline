"""Point the pipeline at a throw-away data folder before anything is imported."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="market_test_"))
os.environ["DATA_DIR"] = str(_tmp / "data")
os.environ["DB_PATH"] = str(_tmp / "data" / "market.db")
os.environ["REPORT_DIR"] = str(_tmp / "reports")
os.environ.pop("LLM_API_KEY", None)
(_tmp / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
