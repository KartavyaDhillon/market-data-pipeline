"""
api.py - FastAPI service for the market data pipeline and the NL-to-SQL agent.

Run:  uvicorn api:app --reload      Docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from market_pipeline import report as reporting
from market_pipeline.agent import MarketAgent, UnsafeSQL, execute
from market_pipeline.pipeline import run
from market_pipeline.storage import query

app = FastAPI(title="Market Data Pipeline API", version="1.0.0",
              description="ETL pipeline for NSE daily bhavcopy data with an LLM natural-language-to-SQL agent.")


class RunRequest(BaseModel):
    source: str = Field("sample", pattern="^(sample|nse)$")
    start: date
    end: date | None = None
    notify: bool = False


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, examples=["Top 5 gainers on the latest day"])


class SQLRequest(BaseModel):
    sql: str = Field(..., examples=["SELECT symbol, close FROM daily_prices LIMIT 5"])


@app.get("/health")
def health():
    latest = query("SELECT MAX(trade_date) AS d, COUNT(*) AS n FROM daily_prices")
    return {"status": "ok", "latest_trade_date": latest.d[0], "rows": int(latest.n[0])}


@app.post("/pipeline/run")
def run_pipeline(body: RunRequest):
    if body.end and body.end < body.start:
        raise HTTPException(422, "end must be on or after start")
    out = run(body.source, body.start, body.end or body.start, notify=body.notify)
    return {"days": out["days"], "report": out["report"]}


@app.get("/pipeline/runs")
def pipeline_runs(limit: int = Query(20, le=200)):
    return query("SELECT * FROM pipeline_runs ORDER BY run_at DESC LIMIT ?", (limit,)).to_dict("records")


@app.get("/prices/{symbol}")
def prices(symbol: str, days: int = Query(20, ge=1, le=500)):
    df = query("SELECT * FROM daily_prices WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?", (symbol.upper(), days))
    if df.empty:
        raise HTTPException(404, f"No data for {symbol.upper()}")
    return df.to_dict("records")


@app.get("/report/{trade_date}")
def report_json(trade_date: date):
    try:
        rep = reporting.build(str(trade_date))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"trade_date": rep.trade_date, "headline": rep.headline(), "breadth": rep.breadth,
            "top_gainers": rep.gainers.to_dict("records"), "top_losers": rep.losers.to_dict("records"),
            "most_active": rep.active.to_dict("records"), "momentum_5d": rep.momentum.to_dict("records"),
            "data_quality": rep.quality}


@app.get("/report/{trade_date}/excel")
def report_excel(trade_date: date):
    try:
        out = reporting.generate(str(trade_date))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(out["excel"], filename=out["excel"].name)


@app.get("/report/{trade_date}/chart")
def report_chart(trade_date: date):
    try:
        out = reporting.generate(str(trade_date))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(out["chart"], media_type="image/png")


@app.post("/ask")
def ask(body: AskRequest):
    """Ask a question in plain English - the agent writes and runs safe, read-only SQL."""
    return MarketAgent().ask(body.question).as_dict()


@app.post("/sql")
def run_sql(body: SQLRequest):
    """Run your own read-only SQL (same guardrails as the agent)."""
    try:
        df = execute(body.sql)
    except UnsafeSQL as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"Query failed: {e}") from e
    return {"columns": list(df.columns), "rows": df.to_dict("records")}
