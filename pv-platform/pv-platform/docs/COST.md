# Cost per 100 runs

**Definition:** (LLM token cost + infrastructure cost) / completed triage runs x 100, over a measurement window.
A "run" is one completed triage job (LLM or fallback). Cache hits never reach the worker and fallback runs spend no tokens, so both pull the average down; report them separately too.

## Measure it (from real traffic)
```bash
# set the CURRENT list prices of the model you actually use (USD per million tokens)
export PRICE_IN_PER_MTOK=...  PRICE_OUT_PER_MTOK=...
export INFRA_USD_PER_HOUR=...   # optional: your compute bill for the stack (VM or Cloud Run equivalent)
python scripts/cost_per_100.py --window 600
```
The script reads `pv_llm_tokens_total` and `pv_triage_source_total` from Prometheus and prints tokens in/out per run, token cost, infra cost, and **COST PER 100 RUNS**.

## Formula
```
tokens_cost  = (input_tokens x price_in + output_tokens x price_out) / 1,000,000
per_run      = (tokens_cost + infra_cost) / completed_runs
per_100_runs = per_run x 100
```
Levers, in order of impact: shorter prompt (input tokens dominate), cache hit ratio, fewer retries (each retry that returns 5xx costs no tokens, but a retry after a slow success does), a smaller model for P3 candidates.

## Record (fill after running)
| scenario | window | runs | llm / fallback | avg tokens in/out | cost per 100 runs |
|---|---|---|---|---|---|
| steady state, no failures | | | | | |
| 50% LLM errors (INC-002) | | | | | |
| cache on vs off | | | | | |

Note: the llm-stub reports `output_tokens=120` and estimates input as `len(prompt)/4`, so stub numbers are only meaningful once you point `LLM_BASE_URL` at the real model (or replace the stub's usage with real usage).
