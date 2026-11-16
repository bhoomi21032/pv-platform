# Incident log

Format: blameless. Times from Grafana/Prometheus, not memory. **The three entries below are drills you run on the lab stack and then fill in from what you actually observe.** The "Expected" lines are the design intent to compare against; do not leave them as if they were observations.

Run a drill: start the stack and a steady Locust load (`locust -f load/locustfile.py --headless -u 30 -r 5 -t 10m --host http://localhost:8000`), then inject with `scripts/chaos.sh <mode>` and heal with `scripts/chaos.sh heal`. Screenshot the Grafana dashboard at injection, breaker-open, heal, and breaker-closed.

---
## INC-001 LLM slowdown (latency above timeout)
- Inject: `scripts/chaos.sh slow` (latency ~5 s vs 3 s timeout)
- **Expected:** `timeout` outcomes climb, retries spike, breaker opens after ~5 failed calls, fallback share goes to ~100%, e2e p95 drops back under 10 s once open (jobs stop waiting on the LLM), breaker flaps half-open every 15 s while still slow. Heal -> next probe succeeds -> closed.
- Detected at / by: _TODO (alert name or manual)_
- Timeline (UTC): inject _T0_ | first alert _T+?_ | breaker open _T+?_ | heal _T+?_ | breaker closed _T+?_
- **Observed impact:** peak e2e p95 _?_ s; peak queue depth _?_; fallback share _?_%; reports lost: _?_ (target 0)
- Root cause: injected fault (drill)
- What worked / what did not: _TODO_
- Follow-ups: _TODO (e.g. tune LLM_TIMEOUT_S / BREAKER_FAILURE_THRESHOLD)_

## INC-002 LLM hard errors (50% HTTP 503)
- Inject: `scripts/chaos.sh errors`
- **Expected:** retries absorb many errors (a call fails only if all 3 attempts fail: 0.5^3 = 12.5%), so the breaker may stay closed; e2e p95 rises; fallback share ~10-15%.
- Compare with `scripts/chaos.sh down` (100%): breaker opens quickly.
- Observed: _TODO (record breaker behaviour, fallback share, cost per 100 runs in docs/COST.md)_
- Follow-ups: _TODO_

## INC-003 Worker killed mid-job
- Inject: `docker compose kill -s SIGKILL worker` while load is running, then `docker compose up -d worker`
- **Expected:** jobs in `pv:processing` are stranded, the reaper re-queues them within ~60 s (`pv_jobs_total{status="reaped"}`), all end `done`; queue depth spikes then drains; no report lost (SLO 4).
- Observed: _TODO (count reaped, time to drain)_
- Follow-ups: _TODO_

---
## Template for real incidents
```
## INC-00N <title>
- Severity / status:
- Detected (time, how):
- Timeline (UTC):
- Impact (who, how long, reports affected/lost):
- Root cause:
- Mitigation / fix:
- What went well / badly:
- Follow-ups (owner, due):
```
