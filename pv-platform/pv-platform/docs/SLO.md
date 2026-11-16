# SLOs (Partner B)

Window: rolling 30 days. All SLIs come from Prometheus metrics the service already exports.

| # | SLI | Target | PromQL (5m window shown) |
|---|-----|--------|--------------------------|
| 1 | **Intake availability**: share of `POST /v1/reports` that are not 5xx (422 and 429 are the client's doing, so they count as good) | 99.5% | `1 - sum(rate(pv_http_requests_total{route="/v1/reports",status=~"5.."}[5m])) / sum(rate(pv_http_requests_total{route="/v1/reports"}[5m]))` |
| 2 | **Intake latency**: time to acknowledge (validate + enqueue) | p95 < 300 ms | `histogram_quantile(0.95, sum by (le)(rate(pv_http_request_seconds_bucket{route="/v1/reports"}[5m])))` |
| 3 | **Triage freshness**: enqueue to result stored | p95 < 10 s, p99 < 30 s | `histogram_quantile(0.95, sum by (le)(rate(pv_job_e2e_seconds_bucket[5m])))` |
| 4 | **Triage completion**: accepted reports that end `done` (LLM *or* fallback), never lost | 99.9% | `sum(increase(pv_jobs_total{status="done"}[1h])) / sum(increase(pv_reports_total{result="accepted"}[1h]))` |
| 5 | **Safety floor** (not a budget): the fallback never labels a rule-matched serious case non-serious | 100% | enforced by `tests/test_llm.py`; fallback always sets `needs_human_review` |

## Why these numbers
- Intake only validates and enqueues, so 300 ms p95 is generous; the budget protects against Redis/GC stalls.
- 10 s p95 for triage: with defaults, one LLM call that has to be retried takes at most `3 s x 3 attempts + ~0.6 s backoff` ~ 9.6 s before falling back. A p95 under 10 s therefore means "almost nothing needs the full retry ladder". The breaker bounds the tail: once it opens, jobs skip the LLM and finish in milliseconds.
- #4 is the real pharmacovigilance promise: an accepted report is never dropped. This is why the queue is a reliable queue (BLMOVE + reaper), failures retry up to 3 times, then go to a DLQ.
- Fallback results count as `done` for #4 but are tracked separately (`pv_triage_source_total{source="fallback"}`); alert at >20%.

## Error budget
99.5% over 30 days = 216 minutes of full intake outage (0.5% x 43,200 min). If more than 50% of the budget burns in a week, freeze risky changes (config tuning, deploys) until it recovers.

## Alerts
`observability/alerts.yml` has one alert per SLI plus breaker-open, fallback-rate, queue-backlog and quarantine-spike. Each carries a runbook anchor in `docs/RUNBOOK.md`.
