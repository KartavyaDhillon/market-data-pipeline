"""Chat with the NL-to-SQL agent from the terminal:  python ask.py  [question]"""
import sys

from market_pipeline.agent import MarketAgent


def show(r):
    print(f"\n{r.answer}")
    if r.sql:
        print(f"\n  SQL ({r.mode}, attempts={r.attempts}): {r.sql}")


agent = MarketAgent()
if len(sys.argv) > 1:
    show(agent.ask(" ".join(sys.argv[1:])))
else:
    print(f"Market agent ({'LLM' if agent.client else 'offline'} mode). Empty line to quit.")
    while q := input("\nAsk: ").strip():
        show(agent.ask(q))
