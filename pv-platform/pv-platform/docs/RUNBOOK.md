# Runbook

Dashboards: Grafana http://localhost:3000 (PV / "PV intake & triage") | Traces: Jaeger http://localhost:16686 | Prometheus http://localhost:9090/alerts
Logs: `docker compose logs -f api worker` (JSON; filter with `jq 'select(.level!="INFO")'`; every line has `trace_id`, paste it into Jaeger).

First 60 seconds of any page: open the dashboard, look at **breaker state**, **queue depth**, **intake 5xx**, **fallback share**. Then pick the section below.

## LLM breaker open / fallback rate high
Alerts: `PVBreakerOpen`, `PVFallbackRateHigh`. Impact: triage still completes, but via conservative rules; every fallback result is flagged `needs_human_review`.
1. Confirm: `pv_llm_requests_total` by outcome. `timeout` = slow upstream, `http_error` = upstream failing, `invalid_output` = model returning garbage (prompt/model change?), `connect_error` = network/DNS.
2. Check the provider status page / `curl $LLM_BASE_URL/healthz`. In the lab: `curl localhost:9000/admin/config` shows injected faults; `scripts/chaos.sh heal` clears them.
3. Do nothing to the breaker: it half-opens every `BREAKER_RECOVERY_S` (15 s) and closes itself on one good probe.
4. Tell reviewers fallback-flagged items need manual triage. After recovery, list the affected jobs (`source":"fallback"` in worker logs) and re-run them if policy requires.
5. If `invalid_output`: roll back the last prompt/model change.

## Slow triage / queue backlog
Alerts: `PVE2ELatencyHigh`, `PVQueueBacklog`.
1. `pv_queue_depth` rising + `jobs done/s` flat -> workers are the limit: `docker compose up -d --scale worker=N` (watch `pv_jobs_total{status="done"}` rise). If depth is rising and workers are idle -> they cannot reach Redis (see below).
2. High LLM latency instead? Look at `pv_llm_request_seconds`. Slow model + retries keeps workers busy; lowering `LLM_TIMEOUT_S` or `LLM_MAX_RETRIES` trades quality for latency.
3. Jobs stuck in `pv:processing` (worker died)? The reaper re-queues them after `VISIBILITY_TIMEOUT_S` (60 s). Check `pv_jobs_total{status="reaped"}`.
4. Still growing past `MAX_QUEUE_DEPTH`: API sheds with 503 + Retry-After by design; tell upstream callers to back off.

## 5xx on intake
Alert: `PVErrorRateHigh`.
1. `curl localhost:8000/readyz`: 503 means Redis is unreachable.
2. `docker compose ps redis; docker compose logs redis --tail 50`. Restart: `docker compose restart redis` (AOF is on, so queued jobs survive).
3. API crash-looping? `docker compose logs api --tail 100`; roll back the last change.
4. Open one failing trace in Jaeger to see which span errored.

## High intake latency
Alert: `PVIntakeLatencyHigh`. Intake only validates and enqueues, so latency means Redis or CPU. Check Redis `redis-cli --latency`, API container CPU, and whether a load test is running. Big payloads? `MAX_BODY_BYTES` caps at 64 KB.

## Quarantine spike
Alert: `PVQuarantineSpike`. `curl localhost:8000/v1/quarantine?limit=50 | jq`. Same `field` failing across reporters = an upstream form/schema change or a client bug: contact the sender. A mix of random fields = bad data or scanning. Quarantined reports are retained (last 1000, may contain PII: restrict access in production). Nothing in quarantine reaches the LLM.

## 429 storm
`pv_reports_total{result="rate_limited"}` high. Find the client: API logs have `client` on every request line. A legitimate integration may need a higher `RATE_LIMIT_PER_MIN` or its own key; a runaway script should be told to honour `Retry-After`.

## Redis down
API returns 503 on `/readyz` and 5xx on intake; workers log `redis unavailable` and retry every second. No data loss for jobs already persisted (AOF). Restore Redis, then confirm queue depth drains and the reaper re-queues anything stranded.

## Worker crash loop
`docker compose logs worker --tail 100`. Jobs failing in code (not LLM) retry 3 times then go to `pv:dlq` with `status=failed` and an `error`. Inspect: `docker compose exec redis redis-cli lrange pv:dlq 0 -1`. Fix, then re-queue: `redis-cli rpoplpush pv:dlq pv:queue` per job after setting its status back to `queued`.

## Feedback shows disagreement rising
`curl localhost:8000/v1/feedback/stats`. Falling `agreement_rate` after a prompt/model change is a quality regression, not an availability incident: raise it with Partner A. Disagreeing reviewers already evict that report from the cache.

## After any incident
Add an entry to `docs/INCIDENT_LOG.md` (timeline, detection, impact, cause, fix, follow-ups) within 24 h.
