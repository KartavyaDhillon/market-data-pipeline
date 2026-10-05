"""
Natural-language-to-SQL agent over the market database.

  question -> LLM writes SQL (schema + examples in the prompt)
           -> guardrails: single SELECT only, read-only connection, SQLite authorizer,
              row limit and a query time budget
           -> execute; on error the agent feeds the error back to the LLM and retries (self-correction)
           -> LLM writes a short answer grounded ONLY in the returned rows

Any OpenAI-compatible model works (OpenAI, Azure, Groq, Ollama ...): set LLM_API_KEY / LLM_MODEL / LLM_BASE_URL.
Without a key the agent runs in "offline" mode: a rule-based intent parser covers the common questions,
so the API and demo always work.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field

import pandas as pd

from .config import DB_PATH, MIN_TURNOVER_FOR_RANKING
from .storage import read_only

MAX_ROWS = 200
MAX_VM_STEPS = 5_000_000          # ~ query time budget for SQLite's virtual machine
ALLOWED_TABLES = {"daily_prices", "pipeline_runs", "quarantine"}

SCHEMA_DOC = """
Table daily_prices (one row per stock per trading day, NSE cash market)
  trade_date TEXT 'YYYY-MM-DD' | symbol TEXT | series TEXT ('EQ' regular equity, 'BE' trade-for-trade)
  isin TEXT | name TEXT | open, high, low, close, last, prev_close REAL (INR)
  change_pct REAL  -- % change of close vs prev_close
  volume INTEGER (shares) | turnover REAL (INR) | trades INTEGER
  PRIMARY KEY (trade_date, symbol, series)
Table pipeline_runs (run_at, trade_date, source, status, rows_in, rows_loaded, rows_rejected, issues JSON, message)
Table quarantine (trade_date, symbol, series, reason, raw JSON)  -- rows rejected by data-quality checks
Notes: 1 crore = 10,000,000 INR. "Latest" means MAX(trade_date). Rank movers only on series IN ('EQ','BE').
"""

FEW_SHOT = """
Q: top 5 gainers on 2026-09-30
SQL: SELECT symbol, close, change_pct FROM daily_prices WHERE trade_date='2026-09-30' AND series IN ('EQ','BE') ORDER BY change_pct DESC LIMIT 5
Q: which stocks fell more than 3% on the latest day with turnover above 10 crore
SQL: SELECT symbol, change_pct, turnover FROM daily_prices WHERE trade_date=(SELECT MAX(trade_date) FROM daily_prices) AND change_pct < -3 AND turnover > 1e8 ORDER BY change_pct
Q: average daily turnover of each symbol over the last 5 trading days, top 10
SQL: WITH d AS (SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT 5) SELECT symbol, ROUND(AVG(turnover)/1e7,2) AS avg_turnover_cr FROM daily_prices WHERE trade_date IN (SELECT trade_date FROM d) GROUP BY symbol ORDER BY avg_turnover_cr DESC LIMIT 10
"""

SQL_SYSTEM = ("You translate questions into a single SQLite SELECT query. Use only the tables and columns in "
              "the schema. Return JSON: {\"sql\": \"...\"}. No explanations.")
ANSWER_SYSTEM = ("You are a market data analyst. Answer the question in 1-3 sentences using ONLY the query "
                 "result provided. Quote exact numbers. If the result is empty, say no matching data was found.")


class UnsafeSQL(Exception):
    pass


@dataclass
class AgentResult:
    question: str
    answer: str
    sql: str | None
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    mode: str = "offline"
    attempts: int = 0
    error: str | None = None

    def as_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- guardrails
def _authorizer(action, arg1, arg2, db_name, trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if arg1 in ALLOWED_TABLES or arg1 is None else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY                      # INSERT/UPDATE/DELETE/PRAGMA/ATTACH/DDL ...


def check_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    if ";" in sql:
        raise UnsafeSQL("Only a single statement is allowed")
    if not re.match(r"^(select|with)\b", sql, re.I):
        raise UnsafeSQL("Only SELECT queries are allowed")
    return sql


def execute(sql: str, db_path=DB_PATH) -> pd.DataFrame:
    sql = check_sql(sql)
    con = read_only(db_path)
    steps = {"n": 0}

    def budget():
        steps["n"] += 1
        return 1 if steps["n"] > MAX_VM_STEPS // 1000 else 0   # non-zero aborts the query
    try:
        con.set_authorizer(_authorizer)
        con.set_progress_handler(budget, 1000)
        return pd.read_sql_query(f"SELECT * FROM ({sql}) LIMIT {MAX_ROWS}", con)
    finally:
        con.close()


# ---------------------------------------------------------------- agent
class MarketAgent:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.client = None
        if os.getenv("LLM_API_KEY"):
            from openai import OpenAI
            self.client = OpenAI(api_key=os.getenv("LLM_API_KEY"), base_url=os.getenv("LLM_BASE_URL") or None)

    # ---- public
    def ask(self, question: str, max_attempts: int = 3) -> AgentResult:
        if self.client is None:
            return self._offline(question)
        return self._llm(question, max_attempts)

    # ---- LLM path with self-correction
    def _chat(self, system: str, user: str) -> str:
        r = self.client.chat.completions.create(model=self.model, temperature=0,
                                                messages=[{"role": "system", "content": system},
                                                          {"role": "user", "content": user}])
        return r.choices[0].message.content

    def _llm(self, question: str, max_attempts: int) -> AgentResult:
        latest = execute("SELECT MAX(trade_date) AS d FROM daily_prices", self.db_path).d.iloc[0]
        prompt = f"Schema:\n{SCHEMA_DOC}\nLatest trade_date in data: {latest}\n\nExamples:{FEW_SHOT}\nQ: {question}"
        sql, error = None, None
        for attempt in range(1, max_attempts + 1):
            raw = self._chat(SQL_SYSTEM, prompt if error is None else
                             f"{prompt}\n\nYour previous SQL:\n{sql}\nfailed with: {error}\nFix it.")
            sql = _extract_sql(raw)
            try:
                df = execute(sql, self.db_path)
            except (UnsafeSQL, sqlite3.Error, pd.errors.DatabaseError) as e:
                error = str(e)
                continue
            answer = self._chat(ANSWER_SYSTEM, f"Question: {question}\nSQL: {sql}\nResult (first 30 rows):\n"
                                               f"{df.head(30).to_csv(index=False)}")
            return AgentResult(question, answer, sql, list(df.columns), _records(df), "llm", attempt)
        return AgentResult(question, "Sorry, I could not build a valid query for that question.", sql,
                           mode="llm", attempts=max_attempts, error=error)

    # ---- offline intent parser
    def _offline(self, q: str) -> AgentResult:
        ql = q.lower()
        day = self._resolve_date(ql)
        n = int(m.group(1)) if (m := re.search(r"\btop\s+(\d+)", ql)) else 5
        symbols = self._symbols_in(q)
        rank = "series IN ('EQ','BE')"
        liquid = f"{rank} AND turnover >= {MIN_TURNOVER_FOR_RANKING:.0f}"

        if symbols and re.search(r"last\s+\d+|trend|history|days", ql):
            k = int(m.group(1)) if (m := re.search(r"last\s+(\d+)", ql)) else 5
            sql = (f"SELECT trade_date, symbol, close, change_pct, volume FROM daily_prices WHERE symbol='{symbols[0]}' "
                   f"ORDER BY trade_date DESC LIMIT {k}")
            tmpl = lambda df: (f"{symbols[0]} over the last {len(df)} sessions: close moved from "  # noqa: E731
                               f"{df.close.iloc[-1]:,.2f} to {df.close.iloc[0]:,.2f} "
                               f"({(df.close.iloc[0] / df.close.iloc[-1] - 1) * 100:+.2f}%).")
        elif symbols:
            sql = (f"SELECT trade_date, symbol, open, high, low, close, change_pct, volume, turnover FROM daily_prices "
                   f"WHERE symbol='{symbols[0]}' AND trade_date='{day}'")
            tmpl = lambda df: (f"{symbols[0]} on {day}: closed at {df.close.iloc[0]:,.2f} "  # noqa: E731
                               f"({df.change_pct.iloc[0]:+.2f}%), volume {int(df.volume.iloc[0]):,}.")
        elif re.search(r"gainer|top performer|went up|best", ql):
            sql = (f"SELECT symbol, close, change_pct, turnover FROM daily_prices WHERE trade_date='{day}' AND {liquid} "
                   f"ORDER BY change_pct DESC LIMIT {n}")
            tmpl = lambda df: f"Top {len(df)} gainers on {day}: " + ", ".join(  # noqa: E731
                f"{r.symbol} ({r.change_pct:+.2f}%)" for r in df.itertuples()) + "."
        elif re.search(r"loser|fell|worst|went down|declin(?!es)", ql):
            sql = (f"SELECT symbol, close, change_pct, turnover FROM daily_prices WHERE trade_date='{day}' AND {liquid} "
                   f"ORDER BY change_pct ASC LIMIT {n}")
            tmpl = lambda df: f"Top {len(df)} losers on {day}: " + ", ".join(  # noqa: E731
                f"{r.symbol} ({r.change_pct:+.2f}%)" for r in df.itertuples()) + "."
        elif re.search(r"active|turnover|traded|volume", ql):
            sql = (f"SELECT symbol, close, change_pct, ROUND(turnover/1e7, 2) AS turnover_cr FROM daily_prices "
                   f"WHERE trade_date='{day}' ORDER BY turnover DESC LIMIT {n}")
            tmpl = lambda df: f"Most active on {day} by turnover: " + ", ".join(  # noqa: E731
                f"{r.symbol} (INR {r.turnover_cr:,.2f} cr)" for r in df.itertuples()) + "."
        elif re.search(r"breadth|advance|decline", ql):
            sql = (f"SELECT SUM(change_pct>0) AS advances, SUM(change_pct<0) AS declines, SUM(change_pct=0) AS unchanged "
                   f"FROM daily_prices WHERE trade_date='{day}' AND {rank}")
            tmpl = lambda df: (f"On {day}: {int(df.advances[0])} advances, {int(df.declines[0])} declines, "  # noqa: E731
                               f"{int(df.unchanged[0])} unchanged.")
        elif re.search(r"quality|quarantin|reject|bad row", ql):
            sql = f"SELECT symbol, series, reason FROM quarantine WHERE trade_date='{day}'"
            tmpl = lambda df: f"{len(df)} rows were quarantined on {day}: " + ", ".join(  # noqa: E731
                f"{r.symbol} ({r.reason})" for r in df.itertuples()) + "."
        else:
            return AgentResult(q, "Offline mode understands: top gainers/losers, most active, market breadth, "
                                  "a stock's price or recent trend, and data-quality issues. Set LLM_API_KEY in .env "
                                  "to ask anything else.", None)
        df = execute(sql, self.db_path)
        answer = tmpl(df) if len(df) else f"No matching data found for {day}."
        return AgentResult(q, answer, sql, list(df.columns), _records(df), "offline", 1)

    def _resolve_date(self, ql: str) -> str:
        if m := re.search(r"\d{4}-\d{2}-\d{2}", ql):
            return m.group(0)
        dates = execute("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT 2",
                        self.db_path).trade_date.tolist()
        return dates[1] if "yesterday" in ql and len(dates) > 1 else dates[0]

    def _symbols_in(self, q: str) -> list[str]:
        words = {w.upper() for w in re.findall(r"[A-Za-z&]{2,}", q)}
        if not words:
            return []
        known = set(execute("SELECT DISTINCT symbol FROM daily_prices", self.db_path).symbol)
        return sorted(words & known)


def _extract_sql(text: str) -> str:
    text = text.strip()
    if m := re.search(r"```(?:sql|json)?\s*(.*?)```", text, re.S):
        text = m.group(1).strip()
    try:
        return json.loads(text)["sql"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return text


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records"))
