"""Prometheus metrics. API and worker each expose their own registry (single process each)."""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQS = Counter("pv_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LAT = Histogram("pv_http_request_seconds", "HTTP latency", ["route"],
                     buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5))

# result: accepted | cache_hit | deduped | quarantined | rate_limited | shed
REPORTS = Counter("pv_reports_total", "Intake outcomes", ["result"])
QUEUE_DEPTH = Gauge("pv_queue_depth", "Jobs waiting in Redis queue")

# status: done | retried | dead | reaped
JOBS = Counter("pv_jobs_total", "Worker job outcomes", ["status"])
E2E = Histogram("pv_job_e2e_seconds", "Enqueue -> result stored", buckets=(.25, .5, 1, 2, 5, 10, 20, 30, 60, 120))
TRIAGE_SRC = Counter("pv_triage_source_total", "Which path produced the triage", ["source"])

# outcome: ok | timeout | http_error | connect_error | invalid_output | circuit_open
LLM_REQS = Counter("pv_llm_requests_total", "LLM call attempts by outcome", ["outcome"])
LLM_LAT = Histogram("pv_llm_request_seconds", "LLM attempt latency",
                    buckets=(.1, .25, .5, 1, 2, 3, 5, 10))
LLM_RETRIES = Counter("pv_llm_retries_total", "LLM retries")
BREAKER_STATE = Gauge("pv_llm_breaker_state", "0=closed 1=half_open 2=open")
LLM_TOKENS = Counter("pv_llm_tokens_total", "Tokens consumed", ["direction"])  # input | output

FEEDBACK = Counter("pv_feedback_total", "Reviewer feedback", ["verdict"])
