# Scaling experiment, caching and rate limiting

**Status: method and predictions are ready; the results table is empty until you run it.** Do not submit predictions as results.

## Question
Does adding worker containers raise triage throughput and cut queue wait, and where is the next bottleneck?

## Setup
- Same Locust load for every run (`load/locustfile.py`, default 80 users, 150 s, first 30 s excluded from the metric window).
- Workers: 1, 2, 4, 8 containers x `WORKER_CONCURRENCY=4`. llm-stub latency ~400 ms (Gaussian, sd 150 ms), no errors.
- Rate limit raised to 100000/min so it does not mask the result; Redis flushed between runs.
- Run: `pip install locust && scripts/run_scaling.sh 80 150` -> `results/scaling.csv`.

## Predictions (to check, not results)
- Per-container capacity ~ concurrency / mean latency = 4 / 0.4 s ~ **10 jobs/s**. Expect `jobs_done_per_s` ~ 10, 20, 40, 80 until arrival rate (the load) caps it.
- When capacity < arrival rate, `queue_depth_max` and `e2e_p95_s` grow without bound during the run (SLO 3 breached). Once capacity > arrival rate, both flatten.
- The curve should flatten before 8 workers: either Locust's arrival rate runs out, or the single-process API / Redis becomes the limit. Intake p95 rising while workers scale means the API is the next bottleneck (scale it behind a load balancer; that is out of scope here).

## Results (fill from `results/scaling.csv`)
| workers | jobs done /s | e2e p50 (s) | e2e p95 (s) | max queue depth | intake p95 (s) | SLO 3 met? |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 4 | | | | | | |
| 8 | | | | | | |

Conclusion (2-3 sentences, include the bottleneck you actually observed): _TODO_

## Caching experiment
Cache key = SHA-256 of the canonical validated report; only `source="llm"` results are cached (never degraded fallback answers); a reviewer "disagree" evicts the entry.
1. Run 150 s with the default mix (about 10% duplicates) and record `cache_hit_ratio` and `intake_p95_s`.
2. Run again with `CACHE_TTL_S=0`... then compare tokens spent (`pv_llm_tokens_total`) and e2e p95. Cache hits skip the queue and the LLM entirely, so they should show up as lower token spend and a lower e2e tail.
Caveat to state in the report: in real traffic exact duplicates are rarer than in this synthetic mix, so the hit ratio here is an upper bound.

## Rate-limiting experiment
Fixed window per `X-API-Key`/IP, in Redis (`INCR` + `EXPIRE`). Returns 429 + `Retry-After`.
1. `RATE_LIMIT_PER_MIN=30 docker compose up -d api`, run Locust with 20 users.
2. Expect `pv_reports_total{result="rate_limited"}` > 0, intake 5xx unchanged, and queue depth bounded.
Known limitation: a fixed window allows up to 2x the limit across a window boundary; a sliding window or token bucket (Lua) fixes it at more complexity.

## Load shedding
`MAX_QUEUE_DEPTH` (default 5000): above it the API returns 503 + `Retry-After` instead of letting latency grow unboundedly. Visible as `pv_reports_total{result="shed"}`.
