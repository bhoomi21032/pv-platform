"""All tunables in one place. Every value can be overridden by env var (see docker-compose.yml)."""
import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- LLM wrapper ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:9000")
LLM_TIMEOUT_S = _f("LLM_TIMEOUT_S", 3.0)          # per attempt
LLM_MAX_RETRIES = _i("LLM_MAX_RETRIES", 2)         # retries after the first attempt
LLM_BACKOFF_BASE_S = _f("LLM_BACKOFF_BASE_S", 0.2)
LLM_BACKOFF_CAP_S = _f("LLM_BACKOFF_CAP_S", 2.0)
BREAKER_FAILURE_THRESHOLD = _i("BREAKER_FAILURE_THRESHOLD", 5)  # consecutive failed calls
BREAKER_RECOVERY_S = _f("BREAKER_RECOVERY_S", 15.0)

# --- API protections ---
RATE_LIMIT_PER_MIN = _i("RATE_LIMIT_PER_MIN", 120)   # per API key / client IP
CACHE_TTL_S = _i("CACHE_TTL_S", 3600)
JOB_TTL_S = _i("JOB_TTL_S", 86400)
MAX_QUEUE_DEPTH = _i("MAX_QUEUE_DEPTH", 5000)        # load-shed (503) above this
MAX_BODY_BYTES = _i("MAX_BODY_BYTES", 65536)

# --- Worker ---
WORKER_CONCURRENCY = _i("WORKER_CONCURRENCY", 4)
WORKER_METRICS_PORT = _i("WORKER_METRICS_PORT", 9101)
VISIBILITY_TIMEOUT_S = _i("VISIBILITY_TIMEOUT_S", 60)
MAX_JOB_ATTEMPTS = _i("MAX_JOB_ATTEMPTS", 3)

# --- Telemetry ---
OTLP_TRACES_ENDPOINT = os.getenv("OTLP_TRACES_ENDPOINT", "")  # e.g. http://jaeger:4318/v1/traces
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
