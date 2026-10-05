"""Orchestration: fetch -> parse -> validate -> load -> report -> notify, one trading day at a time."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import report as reporting
from .notify import send_email, send_telegram
from .sources import SOURCES, NoDataForDate
from .storage import connect, log_run, save_quarantine, upsert_prices
from .transform import parse, validate

log = logging.getLogger(__name__)


def business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def ingest_day(source, day: date) -> dict:
    """Idempotent: re-running a date replaces that date's rows."""
    try:
        raw = source.fetch(day)
    except NoDataForDate as e:
        log.info("Skipping %s: %s", day, e)
        with connect() as con:
            log_run(con, day, source.name, "skipped", message=str(e))
        return {"date": str(day), "status": "skipped", "message": str(e)}

    clean, quarantined, quality = validate(parse(raw))
    with connect() as con:
        con.execute("DELETE FROM daily_prices WHERE trade_date = ?", (str(day),))
        loaded = upsert_prices(con, clean)
        save_quarantine(con, quarantined)
        log_run(con, day, source.name, "ok", quality)
    log.info("%s: loaded %s rows, quarantined %s %s", day, loaded, quality.rows_rejected, quality.issues or "")
    return {"date": str(day), "status": "ok", **quality.as_dict()}


def run(source_name: str, start: date, end: date, notify: bool = False) -> dict:
    source = SOURCES[source_name]()
    results = []
    for day in business_days(start, end):
        try:
            results.append(ingest_day(source, day))
        except Exception as e:  # one bad day must not stop the backfill
            log.exception("Failed for %s", day)
            with connect() as con:
                log_run(con, day, source_name, "failed", message=str(e))
            results.append({"date": str(day), "status": "failed", "message": str(e)})

    loaded_days = [r["date"] for r in results if r["status"] == "ok"]
    out = {"days": results, "report": None}
    if loaded_days:
        latest = max(loaded_days)
        rep = reporting.generate(latest)
        out["report"] = {"trade_date": latest, "excel": str(rep["excel"]), "chart": str(rep["chart"]),
                         "headline": rep["report"].headline()}
        log.info("Report: %s", rep["excel"])
        if notify:
            send_email(f"Market summary - {latest}", rep["html"], [rep["excel"], rep["chart"]])
            send_telegram(f"Market summary {latest}\n{rep['report'].headline()}", rep["chart"])
    return out
