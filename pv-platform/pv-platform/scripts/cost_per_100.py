#!/usr/bin/env python3
"""Cost per 100 triage runs, computed from what actually happened (Prometheus counters).

  PRICE_IN_PER_MTOK=<usd> PRICE_OUT_PER_MTOK=<usd> [INFRA_USD_PER_HOUR=<usd>] \
      python scripts/cost_per_100.py --window 600

Set the prices to your model's CURRENT list prices (they change; I deliberately don't hardcode any).
Only llm-sourced runs cost tokens: fallback runs are free, and cache hits never reach the worker.
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from prom_snapshot import q

ap = argparse.ArgumentParser()
ap.add_argument("--window", type=int, default=600, help="seconds of history to cost")
a = ap.parse_args()
p_in, p_out = float(os.getenv("PRICE_IN_PER_MTOK", "nan")), float(os.getenv("PRICE_OUT_PER_MTOK", "nan"))
infra = float(os.getenv("INFRA_USD_PER_HOUR", "0"))
if p_in != p_in or p_out != p_out:
    sys.exit("Set PRICE_IN_PER_MTOK and PRICE_OUT_PER_MTOK (USD per million tokens).")

w = f"{a.window}s"
tin = q(f'sum(increase(pv_llm_tokens_total{{direction="input"}}[{w}]))')
tout = q(f'sum(increase(pv_llm_tokens_total{{direction="output"}}[{w}]))')
llm_runs = q(f'sum(increase(pv_triage_source_total{{source="llm"}}[{w}]))')
all_runs = q(f'sum(increase(pv_triage_source_total[{w}]))')
if not all_runs or all_runs != all_runs:
    sys.exit("No completed runs in that window.")
tok_cost = (tin * p_in + tout * p_out) / 1e6
infra_cost = infra * a.window / 3600
print(f"window            : {a.window}s")
print(f"completed runs    : {all_runs:.0f}  (llm {llm_runs:.0f}, fallback {all_runs - llm_runs:.0f})")
print(f"tokens in / out   : {tin:.0f} / {tout:.0f}   (avg per llm run: {tin/max(llm_runs,1):.0f} / {tout/max(llm_runs,1):.0f})")
print(f"token cost        : ${tok_cost:.4f}")
print(f"infra cost        : ${infra_cost:.4f}  (INFRA_USD_PER_HOUR={infra})")
print(f"COST PER 100 RUNS : ${(tok_cost + infra_cost) / all_runs * 100:.4f}   (tokens only: ${tok_cost / all_runs * 100:.4f})")
