"""LLM wrapper: per-attempt timeout, bounded retries with exp backoff + jitter, circuit breaker, fallback.

Contract: `LLMClient.triage()` NEVER raises for upstream problems. It returns (TriageResult, usage).
If the model is slow/down/garbage, you get the conservative rule-based result with source="fallback".
Breaker counts one failure per *call* (after its retries are exhausted), not per attempt.
Worst-case latency per job = (timeout x (retries+1)) + backoff  ~= 3 x 3 + 0.6 = ~9.6 s with defaults.
"""
import asyncio
import logging
import random
import time
from typing import Callable

import httpx
from pydantic import ValidationError

from . import config, metrics
from .rules import rule_based
from .schemas import AEReport, TriageResult
from .telemetry import tracer

log = logging.getLogger("pv.llm")
_STATE_NUM = {"closed": 0, "half_open": 1, "open": 2}


class CircuitBreaker:
    CLOSED, HALF_OPEN, OPEN = "closed", "half_open", "open"

    def __init__(self, threshold: int = 5, recovery_s: float = 15.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.recovery_s, self.clock = threshold, recovery_s, clock
        self._state, self._fails, self._opened_at, self._probe = self.CLOSED, 0, 0.0, False
        metrics.BREAKER_STATE.set(0)

    def _set(self, s: str) -> None:
        if s != self._state:
            log.warning("breaker %s -> %s", self._state, s, extra={"ctx": {"breaker_state": s}})
        self._state = s
        metrics.BREAKER_STATE.set(_STATE_NUM[s])

    @property
    def state(self) -> str:
        if self._state == self.OPEN and self.clock() - self._opened_at >= self.recovery_s:
            self._probe = False
            self._set(self.HALF_OPEN)
        return self._state

    def allow(self) -> bool:
        s = self.state
        if s == self.CLOSED:
            return True
        if s == self.OPEN:
            return False
        if not self._probe:  # half-open: exactly one probe call at a time
            self._probe = True
            return True
        return False

    def record_success(self) -> None:
        self._fails, self._probe = 0, False
        self._set(self.CLOSED)

    def record_failure(self) -> None:
        if self._state == self.HALF_OPEN:
            self._trip()
            return
        self._fails += 1
        if self._fails >= self.threshold:
            self._trip()

    def _trip(self) -> None:
        self._opened_at, self._probe = self.clock(), False
        self._set(self.OPEN)


class LLMError(Exception):
    def __init__(self, kind: str, retryable: bool = True):
        super().__init__(kind)
        self.kind, self.retryable = kind, retryable


def build_prompt(report: AEReport) -> str:
    # SEAM: Partner A owns the real prompt. Keep this signature; swap the body.
    return ("You are a pharmacovigilance triage assistant. Classify the adverse event report as serious or "
            "non_serious using ICH E2A criteria. Reply with JSON only: {seriousness, criteria[], priority "
            "(P1|P2|P3), meddra_pt, rationale, needs_human_review}.\n\nREPORT:\n"
            + report.model_dump_json())


class LLMClient:
    def __init__(self, http: httpx.AsyncClient, breaker: CircuitBreaker | None = None, *,
                 base_url: str = config.LLM_BASE_URL, timeout_s: float = config.LLM_TIMEOUT_S,
                 max_retries: int = config.LLM_MAX_RETRIES, backoff_base_s: float = config.LLM_BACKOFF_BASE_S,
                 backoff_cap_s: float = config.LLM_BACKOFF_CAP_S, sleep=asyncio.sleep):
        self.http = http
        self.breaker = breaker or CircuitBreaker(config.BREAKER_FAILURE_THRESHOLD, config.BREAKER_RECOVERY_S)
        self.base_url, self.timeout_s, self.max_retries = base_url, timeout_s, max_retries
        self.backoff_base_s, self.backoff_cap_s, self._sleep = backoff_base_s, backoff_cap_s, sleep

    async def _call_once(self, prompt: str, report: AEReport) -> tuple[TriageResult, dict]:
        t0 = time.perf_counter()
        try:
            resp = await self.http.post(f"{self.base_url}/v1/triage", timeout=self.timeout_s,
                                        json={"prompt": prompt, "report": report.model_dump(mode="json")})
        except httpx.TimeoutException as e:
            raise LLMError("timeout") from e
        except httpx.HTTPError as e:
            raise LLMError("connect_error") from e
        finally:
            metrics.LLM_LAT.observe(time.perf_counter() - t0)
        if resp.status_code >= 500 or resp.status_code == 429:
            raise LLMError("http_error")
        if resp.status_code >= 400:
            raise LLMError("http_error", retryable=False)  # our bug; retrying won't help
        try:
            body = resp.json()
            result = TriageResult.model_validate({**body["triage"], "source": "llm"})
            return result, body.get("usage", {})
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            raise LLMError("invalid_output") from e

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_cap_s, self.backoff_base_s * 2 ** attempt) * (0.5 + random.random() / 2)

    def _fallback(self, report: AEReport, why: str) -> tuple[TriageResult, dict]:
        log.warning("llm fallback used", extra={"ctx": {"reason": why}})
        return rule_based(report, source="fallback"), {"input_tokens": 0, "output_tokens": 0}

    async def triage(self, report: AEReport) -> tuple[TriageResult, dict]:
        with tracer.start_as_current_span("llm.triage") as span:
            if not self.breaker.allow():
                metrics.LLM_REQS.labels("circuit_open").inc()
                span.set_attribute("llm.path", "circuit_open")
                return self._fallback(report, "circuit_open")
            prompt, last = build_prompt(report), "unknown"
            for attempt in range(self.max_retries + 1):
                try:
                    result, usage = await self._call_once(prompt, report)
                except LLMError as e:
                    last = e.kind
                    metrics.LLM_REQS.labels(e.kind).inc()
                    if not e.retryable:
                        break
                    if attempt < self.max_retries:
                        metrics.LLM_RETRIES.inc()
                        await self._sleep(self._backoff(attempt))
                    continue
                metrics.LLM_REQS.labels("ok").inc()
                self.breaker.record_success()
                metrics.LLM_TOKENS.labels("input").inc(usage.get("input_tokens", 0))
                metrics.LLM_TOKENS.labels("output").inc(usage.get("output_tokens", 0))
                span.set_attribute("llm.path", "llm")
                span.set_attribute("llm.attempts", attempt + 1)
                return result, usage
            self.breaker.record_failure()
            span.set_attribute("llm.path", f"fallback:{last}")
            return self._fallback(report, last)
