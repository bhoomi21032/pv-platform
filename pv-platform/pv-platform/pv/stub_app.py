"""llm-stub: a fake LLM with knobs, so we can load-test and run failure drills without spending tokens.

Tune at runtime:  curl -XPOST localhost:9000/admin/config -H 'content-type: application/json' \
                       -d '{"latency_ms_mean": 4000, "error_rate": 0.5}'
"""
import asyncio
import random

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from .rules import rule_based
from .schemas import AEReport

app = FastAPI(title="llm-stub")
CFG = {"latency_ms_mean": 400.0, "latency_ms_jitter": 150.0, "error_rate": 0.0,
       "malformed_rate": 0.0, "hang_rate": 0.0}


class Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latency_ms_mean: float | None = None
    latency_ms_jitter: float | None = None
    error_rate: float | None = None
    malformed_rate: float | None = None
    hang_rate: float | None = None


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/admin/config")
def get_cfg():
    return CFG


@app.post("/admin/config")
def set_cfg(c: Cfg):
    CFG.update({k: v for k, v in c.model_dump().items() if v is not None})
    return CFG


@app.post("/admin/reset")
def reset():
    CFG.update({"latency_ms_mean": 400.0, "latency_ms_jitter": 150.0, "error_rate": 0.0,
                "malformed_rate": 0.0, "hang_rate": 0.0})
    return CFG


@app.post("/v1/triage")
async def triage(body: dict):
    if random.random() < CFG["hang_rate"]:
        await asyncio.sleep(120)  # client timeout should fire long before this
    lat = max(0.0, random.gauss(CFG["latency_ms_mean"], CFG["latency_ms_jitter"])) / 1000
    await asyncio.sleep(lat)
    if random.random() < CFG["error_rate"]:
        raise HTTPException(503, "stub: injected error")
    if random.random() < CFG["malformed_rate"]:
        return {"triage": {"seriousness": "maybe"}, "usage": {"input_tokens": 300, "output_tokens": 10}}
    report = AEReport.model_validate(body["report"])
    t = rule_based(report, source="llm")
    return {"triage": t.model_dump(mode="json"),
            "usage": {"input_tokens": max(1, len(body.get("prompt", "")) // 4), "output_tokens": 120}}
