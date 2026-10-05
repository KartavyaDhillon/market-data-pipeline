"""
CLI entry point.

  python run_pipeline.py --source sample --start 2026-09-01 --end 2026-09-30   # backfill demo data
  python run_pipeline.py --source nse                                          # today's real NSE file
  python run_pipeline.py --source nse --date 2026-09-30 --notify               # + email / Telegram
"""
import argparse
import logging
import sys
from datetime import date

from market_pipeline.config import LOG_DIR
from market_pipeline.pipeline import run


def main():
    ap = argparse.ArgumentParser(description="Market data ETL pipeline")
    ap.add_argument("--source", choices=["sample", "nse"], default="sample")
    ap.add_argument("--date", help="single date YYYY-MM-DD (default: today)")
    ap.add_argument("--start", help="backfill start YYYY-MM-DD")
    ap.add_argument("--end", help="backfill end YYYY-MM-DD")
    ap.add_argument("--notify", action="store_true", help="send email / Telegram if configured")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")])
    if args.start:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end or args.start)
    else:
        start = end = date.fromisoformat(args.date) if args.date else date.today()

    out = run(args.source, start, end, notify=args.notify)
    ok = sum(d["status"] == "ok" for d in out["days"])
    failed = sum(d["status"] == "failed" for d in out["days"])
    print(f"\nDays loaded: {ok} | skipped: {sum(d['status'] == 'skipped' for d in out['days'])} | failed: {failed}")
    if out["report"]:
        print(out["report"]["headline"])
        print(f"Excel: {out['report']['excel']}\nChart: {out['report']['chart']}")
    sys.exit(1 if failed and not ok else 0)


if __name__ == "__main__":
    main()
