"""End-to-end through the real FastAPI app + worker code, with fakeredis and a mocked LLM."""
import asyncio

import fakeredis
import httpx

from pv import config, store, worker
from pv.api import create_app
from pv.llm import LLMClient

GOOD = dict(reporter_type="physician", patient_age_years=54, patient_sex="F", suspect_drug="Amoxicillin",
            reaction_description="Patient hospitalised with anaphylaxis after the second dose",
            onset_date="2024-03-01")
OK = {"triage": {"seriousness": "serious", "criteria": ["life_threatening"], "priority": "P1",
                 "rationale": "ok"}, "usage": {"input_tokens": 100, "output_tokens": 20}}


async def _flow():
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    app = create_app()
    app.state.redis = r
    llm_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=OK)))
    llm = LLMClient(llm_http, base_url="http://llm")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        # accepted -> worker -> done
        resp = await c.post("/v1/reports", json=GOOD, headers={"x-api-key": "a"})
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        assert (await c.get(f"/v1/reports/{job_id}")).json()["status"] == "queued"
        claimed = await r.blmove(store.QUEUE, store.PROCESSING, 1, src="RIGHT", dest="LEFT")
        assert claimed == job_id
        await worker.handle_one(r, llm, job_id)
        done = (await c.get(f"/v1/reports/{job_id}")).json()
        assert done["status"] == "done" and done["result"]["source"] == "llm"
        assert await r.llen(store.PROCESSING) == 0

        # identical report -> cache hit, no queue growth
        again = await c.post("/v1/reports", json=GOOD, headers={"x-api-key": "a"})
        assert again.status_code == 200 and again.json()["cached"] is True
        assert await r.llen(store.QUEUE) == 0

        # invalid -> 422 + quarantined; malformed JSON too
        bad = await c.post("/v1/reports", json={**GOOD, "patient_age_years": 999}, headers={"x-api-key": "a"})
        assert bad.status_code == 422 and bad.json()["quarantine_id"]
        junk = await c.post("/v1/reports", content=b"{not json", headers={"x-api-key": "a"})
        assert junk.status_code == 422
        q = (await c.get("/v1/quarantine")).json()
        assert len(q) == 2 and {x["reason"] for x in q} == {"schema_validation", "malformed_json"}

        # feedback: disagree busts the cache
        fb = await c.post("/v1/feedback", json={"job_id": job_id, "verdict": "disagree",
                                                "corrected_seriousness": "non_serious"},
                          headers={"x-api-key": "a"})
        assert fb.status_code == 200
        assert (await c.get("/v1/feedback/stats")).json()["disagree"] == 1
        redo = await c.post("/v1/reports", json=GOOD, headers={"x-api-key": "a"})
        assert redo.status_code == 202  # cache was invalidated

        # rate limit
        for _ in range(config.RATE_LIMIT_PER_MIN + 1):
            last = await c.post("/v1/reports", json={**GOOD, "patient_age_years": 1}, headers={"x-api-key": "spam"})
        assert last.status_code == 429 and "retry-after" in last.headers

        assert (await c.get("/readyz")).status_code == 200
        assert "pv_reports_total" in (await c.get("/metrics")).text


def test_flow():
    asyncio.run(_flow())


async def _reaper():
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    await r.hset(store.job_key("j1"), mapping={"status": "processing", "started_at": 1.0})
    await r.lpush(store.PROCESSING, "j1")
    assert await store.requeue_stale(r, {}) == 1
    assert await r.lrange(store.QUEUE, 0, -1) == ["j1"]


def test_reaper_requeues_stale():
    asyncio.run(_reaper())
