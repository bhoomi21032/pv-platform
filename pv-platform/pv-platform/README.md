# PV intake & triage: Partner B (platform, reliability, observability, load)

```
 browser ──► FastAPI (/v1/reports) ──► Redis queue ──► worker(s) ──► llm-stub (or real LLM)
             │ validate (Pydantic)      │ BLMOVE          │ timeout · retry · breaker · fallback
             │ quarantine · cache       │ reaper          │
             │ rate limit · shed        ▼                 ▼
             └─► /metrics ──► Prometheus ──► Grafana      traces ──► Jaeger (trace context rides in the job)
```

## Run
```bash
docker compose up -d --build              # web UI http://localhost:8000
docker compose up -d --scale worker=4     # more workers
```
| Thing | URL |
|---|---|
| Web client | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Grafana (dashboard "PV intake & triage") | http://localhost:3000 |
| Jaeger | http://localhost:16686 |
| Prometheus / alerts | http://localhost:9090 |
| llm-stub admin | http://localhost:9000/admin/config |

Local dev without Docker: `pip install -r requirements-dev.txt && pytest` (tests use fakeredis + a mocked LLM; no services needed).

## Scope -> where it lives
| Deliverable | Files |
|---|---|
| Pydantic validation and quarantine (D1) | `pv/schemas.py`, quarantine in `pv/store.py` + `pv/api.py`, `GET /v1/quarantine` |
| FastAPI, Redis queue + worker, web client | `pv/api.py`, `pv/store.py`, `pv/worker.py`, `web/index.html` |
| LLM wrapper + llm-stub | `pv/llm.py` (timeout, retry+jitter, circuit breaker, conservative fallback), `pv/stub_app.py`, `scripts/chaos.sh` |
| Logs, OTel/Jaeger, Prometheus, Grafana | `pv/logging_setup.py`, `pv/telemetry.py`, `pv/metrics.py`, `observability/` |
| SLO, Locust, scaling, caching, rate limiting | `docs/SLO.md`, `load/locustfile.py`, `scripts/run_scaling.sh`, `docs/SCALING.md`, cache/limit in `pv/store.py` |
| Feedback endpoint, runbook, incident log, cost/100 runs | `POST /v1/feedback`, `docs/RUNBOOK.md`, `docs/INCIDENT_LOG.md`, `docs/COST.md`, `scripts/cost_per_100.py` |

## Plug in Partner A's triage
`pv/triage.py` is the seam: `async def run(llm, report) -> (TriageResult, usage)`. Keep calling `llm.triage` (or `llm._call_once`-style helpers) so timeouts, retries, breaker, fallback, token metrics and tracing all apply. Prompt text lives in `pv/llm.py::build_prompt`. Field names live in `pv/schemas.py`: **I guessed the AE fields; align them with the real spec/triage file.**

## Design decisions worth defending in the viva
- **Fallback is conservative and always flagged for human review**; degraded answers are never cached.
- **Reliable queue** (BLMOVE + reaper, at-least-once, idempotent completion) so an accepted report is not lost when a worker dies; DLQ after 3 attempts.
- **Breaker counts failed calls, not attempts**, and half-opens with a single probe.
- **Validation errors never echo input** (PII); quarantine keeps the raw payload for audit.
- **Trace context is injected into the job**, so Jaeger shows API -> queue -> worker -> LLM as one trace.
- Metric labels use route templates, not raw URLs (bounded cardinality). API runs 1 process so `/metrics` stays correct.

## Honest gaps
- Results tables in `docs/SCALING.md`, `docs/COST.md`, `docs/INCIDENT_LOG.md` are **blank on purpose**: run the experiments and fill them from your own numbers.
- No auth beyond an API-key header used as a rate-limit identity. Quarantine/feedback endpoints are unauthenticated (fine for a course demo, not for real PII).
- Compose stack and shell scripts were written and syntax-checked but not executed end to end here; the Python (validation, wrapper, breaker, API+worker flow, reaper) is covered by `pytest` (20 tests).
