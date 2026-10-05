"""Parse the raw UDiFF bhavcopy and run data-quality checks before anything reaches the database."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

COLUMN_MAP = {
    "TradDt": "trade_date", "TckrSymb": "symbol", "SctySrs": "series", "ISIN": "isin",
    "FinInstrmNm": "name", "OpnPric": "open", "HghPric": "high", "LwPric": "low", "ClsPric": "close",
    "LastPric": "last", "PrvsClsgPric": "prev_close", "TtlTradgVol": "volume",
    "TtlTrfVal": "turnover", "TtlNbOfTxsExctd": "trades",
}
NUMERIC = ["open", "high", "low", "close", "last", "prev_close", "volume", "turnover", "trades"]


@dataclass
class QualityReport:
    trade_date: str
    rows_in: int = 0
    rows_loaded: int = 0
    issues: dict[str, int] = field(default_factory=dict)

    @property
    def rows_rejected(self) -> int:
        return self.rows_in - self.rows_loaded

    def as_dict(self) -> dict:
        return {"trade_date": self.trade_date, "rows_in": self.rows_in, "rows_loaded": self.rows_loaded,
                "rows_rejected": self.rows_rejected, "issues": self.issues}


def parse(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(csv_name) as f:
            raw = pd.read_csv(f, dtype=str)
    missing = [c for c in COLUMN_MAP if c not in raw.columns]
    if missing:
        raise ValueError(f"{zip_path.name}: unexpected file format, missing columns {missing}")
    df = raw[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)
    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["series"] = df["series"].str.strip().str.upper()
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
    return df


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, QualityReport]:
    """Returns (clean rows, quarantined rows with a reason, quality report)."""
    rep = QualityReport(trade_date=str(df["trade_date"].iloc[0]) if len(df) else "", rows_in=len(df))
    checks = {
        "missing_series": df["series"].isna() | (df["series"].astype(str).str.strip() == ""),
        "missing_values": df[["symbol", "open", "high", "low", "close", "volume"]].isna().any(axis=1),
        "non_positive_price": (df[["open", "high", "low", "close"]] <= 0).any(axis=1),
        "high_below_low": df["high"] < df["low"],
        "close_outside_range": (df["close"] > df["high"] * 1.0001) | (df["close"] < df["low"] * 0.9999),
        "negative_volume": df["volume"] < 0,
        "duplicate_row": df.duplicated(subset=["trade_date", "symbol", "series"], keep="first"),
    }
    reason = pd.Series("", index=df.index)
    for name, mask in checks.items():
        mask = mask.fillna(False)
        if mask.any():
            rep.issues[name] = int(mask.sum())
            reason[mask & (reason == "")] = name
    bad = reason != ""
    quarantined = df[bad].assign(reason=reason[bad])
    clean = df[~bad].copy()
    clean["change_pct"] = ((clean["close"] / clean["prev_close"] - 1) * 100).round(2)
    clean["change_pct"] = clean["change_pct"].replace([np.inf, -np.inf], np.nan)   # prev_close = 0 (new listings)
    rep.rows_loaded = len(clean)
    return clean, quarantined, rep
