"""
Data sources.

  NSESource     downloads the official daily equity bhavcopy (UDiFF format) from NSE archives
  SampleSource  generates a synthetic bhavcopy in the *same* UDiFF zip format, so the rest of the
                pipeline (parse -> validate -> load -> report) is identical for demo, tests and production.
"""
from __future__ import annotations

import io
import logging
import random
import time
import zipfile
import zlib
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .config import RAW_DIR

log = logging.getLogger(__name__)

UDIFF_COLUMNS = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs",
    "XpryDt", "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric",
    "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst",
    "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks",
    "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
]


class NoDataForDate(Exception):
    """Raised when the exchange has no file for a date (holiday / weekend / not yet published)."""


class NSESource:
    name = "nse"
    URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://www.nseindia.com/all-reports",
    }

    def __init__(self, retries: int = 3, backoff: float = 2.0, timeout: int = 30):
        self.retries, self.backoff, self.timeout = retries, backoff, timeout
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def fetch(self, day: date) -> Path:
        dest = RAW_DIR / f"bhavcopy_{day:%Y%m%d}.csv.zip"
        if dest.exists():
            log.info("Using cached file %s", dest.name)
            return dest
        url = self.URL.format(d=day)
        for attempt in range(1, self.retries + 1):
            try:
                if attempt == 1:  # NSE sets cookies on the home page; warm the session once
                    self.session.get("https://www.nseindia.com", timeout=self.timeout)
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    raise NoDataForDate(f"No bhavcopy for {day} (holiday or not published yet)")
                r.raise_for_status()
                zipfile.ZipFile(io.BytesIO(r.content)).testzip()  # fail fast on corrupt/HTML responses
                dest.write_bytes(r.content)
                log.info("Downloaded %s (%s KB)", dest.name, len(r.content) // 1024)
                return dest
            except NoDataForDate:
                raise
            except Exception as e:  # network error, 403, bad zip ...
                wait = self.backoff ** attempt
                log.warning("Attempt %s/%s for %s failed: %s - retrying in %.0fs", attempt, self.retries, day, e, wait)
                time.sleep(wait)
        raise RuntimeError(f"Could not download bhavcopy for {day} after {self.retries} attempts")


class SampleSource:
    """Deterministic synthetic market: ~250 symbols following a random walk, plus a few bad rows."""
    name = "sample"
    SECTORS = ["BANK", "IT", "PHARMA", "AUTO", "FMCG", "METAL", "ENERGY", "INFRA", "MEDIA", "REALTY"]
    SYLLABLES = ["AR", "BH", "CO", "DE", "EL", "FI", "GA", "HI", "IN", "JA", "KA", "LU", "MA", "NI", "OR",
                 "PA", "RA", "SU", "TA", "VI"]

    def __init__(self, n_symbols: int = 250, seed: int = 7):
        rng = random.Random(seed)
        self.seed = seed
        names = set()
        while len(names) < n_symbols:
            names.add("".join(rng.choice(self.SYLLABLES) for _ in range(rng.randint(2, 3)))
                      + rng.choice(["", "TECH", "FIN", "IND", "LTD", "POWER", "LABS"]))
        self.universe = []
        for i, sym in enumerate(sorted(names)):
            self.universe.append({
                "symbol": sym,
                "isin": f"INE{rng.randint(100, 999)}{chr(65 + i % 26)}{rng.randint(1000, 9999)}",
                "series": "BE" if rng.random() < 0.06 else "EQ",
                "base": round(rng.lognormvariate(5.5, 1.1), 2),          # ~ INR 30 to 3,000+
                "vol": rng.uniform(0.008, 0.035),                         # daily volatility
                "liquidity": rng.lognormvariate(12.5, 1.4),               # typical shares/day
                "sector": rng.choice(self.SECTORS),
            })

    def _price_on(self, inst: dict, day: date) -> tuple[float, float]:
        """Random walk that is reproducible for any date (no state between runs)."""
        start = date(2026, 1, 1)
        rng = random.Random(f"{self.seed}-{inst['symbol']}")
        price = inst["base"]
        prev = price
        for _ in range(max((day - start).days, 1)):
            prev = price
            price = max(1.0, price * (1 + rng.gauss(0.0004, inst["vol"])))
        return round(prev, 2), round(price, 2)

    def fetch(self, day: date) -> Path:
        if day.weekday() >= 5:
            raise NoDataForDate(f"{day} is a weekend")
        dest = RAW_DIR / f"sample_bhavcopy_{day:%Y%m%d}.csv.zip"
        if dest.exists():
            return dest
        rng = random.Random(f"{self.seed}-{day}")
        rows = []
        for inst in self.universe:
            prev_close, close = self._price_on(inst, day)
            hi = round(max(prev_close, close) * (1 + abs(rng.gauss(0, inst["vol"] / 2))), 2)
            lo = round(min(prev_close, close) * (1 - abs(rng.gauss(0, inst["vol"] / 2))), 2)
            op = round(rng.uniform(lo, hi), 2)
            qty = int(inst["liquidity"] * rng.lognormvariate(0, 0.5))
            trades = max(1, qty // rng.randint(40, 400))
            row = dict.fromkeys(UDIFF_COLUMNS, "")
            row.update({
                "TradDt": day.isoformat(), "BizDt": day.isoformat(), "Sgmt": "CM", "Src": "NSE",
                "FinInstrmTp": "STK", "FinInstrmId": str(zlib.crc32(inst["symbol"].encode()) % 100000),
                "ISIN": inst["isin"], "TckrSymb": inst["symbol"], "SctySrs": inst["series"],
                "FinInstrmNm": f"{inst['symbol'].title()} Limited", "OpnPric": op, "HghPric": hi, "LwPric": lo,
                "ClsPric": close, "LastPric": close, "PrvsClsgPric": prev_close, "SttlmPric": close,
                "TtlTradgVol": qty, "TtlTrfVal": round(qty * (hi + lo) / 2, 2), "TtlNbOfTxsExctd": trades,
                "SsnId": "F1", "NewBrdLotQty": 1,
            })
            rows.append(row)
        # Real files are never perfect: inject a few bad rows the validator must quarantine
        bad = rng.sample(range(len(rows)), 3)
        rows[bad[0]]["HghPric"], rows[bad[0]]["LwPric"] = rows[bad[0]]["LwPric"], rows[bad[0]]["HghPric"]
        rows[bad[1]]["TtlTradgVol"] = -abs(int(rows[bad[1]]["TtlTradgVol"]) or 10)
        rows[bad[2]]["ClsPric"] = 0
        rows.append(dict(rows[bad[0] - 1]))  # duplicate row

        buf = io.StringIO()
        pd.DataFrame(rows, columns=UDIFF_COLUMNS).to_csv(buf, index=False)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(dest.name.replace(".zip", ""), buf.getvalue())
        return dest

    def sector_of(self) -> dict[str, str]:
        return {i["symbol"]: i["sector"] for i in self.universe}


SOURCES = {"nse": NSESource, "sample": SampleSource}
