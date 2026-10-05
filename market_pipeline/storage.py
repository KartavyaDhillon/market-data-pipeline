"""SQLite storage: idempotent upserts, a run log, and a read-only connection for the AI agent."""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import DB_PATH

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_prices (
    trade_date  TEXT NOT NULL,      -- YYYY-MM-DD
    symbol      TEXT NOT NULL,
    series      TEXT NOT NULL,      -- EQ = regular equity, BE = trade-for-trade
    isin        TEXT,
    name        TEXT,
    open        REAL, high REAL, low REAL, close REAL, last REAL,
    prev_close  REAL,
    change_pct  REAL,               -- % change vs previous close
    volume      INTEGER,            -- shares traded
    turnover    REAL,               -- traded value in INR
    trades      INTEGER,            -- number of trades
    PRIMARY KEY (trade_date, symbol, series)
);
CREATE INDEX IF NOT EXISTS ix_prices_symbol ON daily_prices(symbol, trade_date);
CREATE TABLE IF NOT EXISTS quarantine (
    trade_date TEXT, symbol TEXT, series TEXT, reason TEXT, raw JSON
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_at TEXT, trade_date TEXT, source TEXT, status TEXT,
    rows_in INTEGER, rows_loaded INTEGER, rows_rejected INTEGER, issues JSON, message TEXT
);
"""

PRICE_COLS = ["trade_date", "symbol", "series", "isin", "name", "open", "high", "low", "close", "last",
              "prev_close", "change_pct", "volume", "turnover", "trades"]


@contextmanager
def connect(db_path: Path = DB_PATH):
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def read_only(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Connection that physically cannot write - used by the AI agent."""
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, check_same_thread=False)


def _blank(v) -> bool:
    return bool(pd.isna(v)) or str(v).strip().lower() in ("", "nan", "none", "<na>")


def upsert_prices(con, df: pd.DataFrame) -> int:
    """Insert rows. Last line of defence: rows with a blank key column are skipped (and logged), never fatal."""
    blank = df[["trade_date", "symbol", "series"]].apply(lambda c: c.map(_blank)).any(axis=1)
    if blank.any():
        log.warning("Skipping %s rows with a blank trade_date/symbol/series: %s", int(blank.sum()),
                    df[blank][["symbol", "series", "isin"]].head(5).to_dict("records"))
        df = df[~blank]
    rows = df[PRICE_COLS].astype(object).where(df[PRICE_COLS].notna(), None).values.tolist()
    con.executemany(
        f"INSERT OR REPLACE INTO daily_prices ({','.join(PRICE_COLS)}) VALUES ({','.join('?' * len(PRICE_COLS))})",
        rows)
    return len(rows)


def save_quarantine(con, df: pd.DataFrame) -> None:
    if df.empty:
        return
    con.execute("DELETE FROM quarantine WHERE trade_date = ?", (str(df.trade_date.iloc[0]),))
    con.executemany("INSERT INTO quarantine VALUES (?,?,?,?,?)",
                    [(r.trade_date, r.symbol, r.series, r.reason, r.drop(labels="reason").to_json())
                     for _, r in df.iterrows()])


def log_run(con, trade_date, source, status, report=None, message=""):
    r = report.as_dict() if report else {}
    con.execute("INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), str(trade_date), source, status,
                 r.get("rows_in"), r.get("rows_loaded"), r.get("rows_rejected"),
                 json.dumps(r.get("issues", {})), message))


def query(sql: str, params=(), db_path: Path = DB_PATH) -> pd.DataFrame:
    with connect(db_path) as con:
        return pd.read_sql_query(sql, con, params=params)
