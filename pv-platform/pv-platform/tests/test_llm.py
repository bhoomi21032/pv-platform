import asyncio
import json

import httpx

from pv.llm import CircuitBreaker, LLMClient
from pv.schemas import AEReport

REPORT = AEReport.model_validate(dict(
    reporter_type="nurse", patient_age_years=70, suspect_drug="Warfarin", onset_date="2024-01-02",
    reaction_description="Patient was hospitalised after severe internal bleeding"))
OK = {"triage": {"seriousness": "serious", "criteria": ["hospitalization"], "priority": "P2",
                 "rationale": "ok"}, "usage": {"input_tokens": 100, "output_tokens": 20}}


def make(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async def nosleep(_): pass
    return LLMClient(http, base_url="http://llm", sleep=nosleep, **kw)


def run(coro):
    return asyncio.run(coro)


def test_success():
    c = make(lambda req: httpx.Response(200, json=OK))
    res, usage = run(c.triage(REPORT))
    assert res.source == "llm" and usage["input_tokens"] == 100


def test_retry_then_success():
    calls = []
    def h(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=OK)
    res, _ = run(make(h).triage(REPORT))
    assert res.source == "llm" and len(calls) == 3


def test_exhausted_retries_fall_back_conservatively():
    calls = []
    def h(req):
        calls.append(1)
        return httpx.Response(503)
    c = make(h)
    res, usage = run(c.triage(REPORT))
    assert len(calls) == 3  # 1 try + 2 retries
    assert res.source == "fallback" and res.seriousness.value == "serious" and res.needs_human_review
    assert usage["input_tokens"] == 0


def test_timeout_falls_back():
    def h(req):
        raise httpx.ReadTimeout("slow", request=req)
    res, _ = run(make(h).triage(REPORT))
    assert res.source == "fallback"


def test_garbage_output_falls_back():
    res, _ = run(make(lambda req: httpx.Response(200, json={"triage": {"seriousness": "maybe"}})).triage(REPORT))
    assert res.source == "fallback"


def test_4xx_not_retried():
    calls = []
    def h(req):
        calls.append(1)
        return httpx.Response(400)
    res, _ = run(make(h).triage(REPORT))
    assert len(calls) == 1 and res.source == "fallback"


def test_breaker_opens_then_skips_llm_then_recovers():
    t = [0.0]
    br = CircuitBreaker(threshold=2, recovery_s=10, clock=lambda: t[0])
    calls = []
    healthy = [False]
    def h(req):
        calls.append(1)
        return httpx.Response(200, json=OK) if healthy[0] else httpx.Response(503)
    c = make(h, breaker=br, max_retries=0)
    run(c.triage(REPORT)); run(c.triage(REPORT))
    assert br.state == "open" and len(calls) == 2
    res, _ = run(c.triage(REPORT))                 # open: no upstream call
    assert len(calls) == 2 and res.source == "fallback"
    t[0] = 11                                      # recovery window passed -> half-open probe
    healthy[0] = True
    res, _ = run(c.triage(REPORT))
    assert res.source == "llm" and br.state == "closed"


def test_half_open_probe_failure_reopens():
    t = [0.0]
    br = CircuitBreaker(threshold=1, recovery_s=5, clock=lambda: t[0])
    c = make(lambda req: httpx.Response(503), breaker=br, max_retries=0)
    run(c.triage(REPORT))
    assert br.state == "open"
    t[0] = 6
    run(c.triage(REPORT))
    assert br.state == "open"
