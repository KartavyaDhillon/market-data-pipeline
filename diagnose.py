"""Prints what is inside the downloaded NSE file and which validator code is running. Run diagnose.bat."""
import sys
from pathlib import Path

import pandas as pd

import market_pipeline.transform as t
from market_pipeline.config import RAW_DIR

print(f"python {sys.version.split()[0]} | pandas {pd.__version__}")
print("validator file:", t.__file__)
print("has missing_series check:", "missing_series" in Path(t.__file__).read_text(encoding="utf-8"))
files = sorted(RAW_DIR.glob("bhavcopy_*.zip"))
if not files:
    sys.exit("No downloaded NSE file found in data/raw - run run_today_nse.bat first.")
f = files[-1]
df = t.parse(f)
print(f"\n{f.name}: {len(df)} rows")
print("series values:", df.series.value_counts(dropna=False).head(15).to_dict())
bad = df[df.series.isna() | (df.series.astype(str).str.strip() == "")]
print(f"rows with blank series: {len(bad)}")
if len(bad):
    print(bad.head(10).to_string())
clean, quarantined, rep = t.validate(df)
print(f"\nafter validate: {len(clean)} clean, {len(quarantined)} quarantined, blank series left in clean: "
      f"{int(clean.series.isna().sum())}")
print("issues:", rep.issues)
