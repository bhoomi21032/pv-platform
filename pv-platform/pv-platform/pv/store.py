"""Redis data layer: queue, jobs, cache, in-flight dedupe, rate limit, quarantine, feedback.

Queue is a reliable-queue pattern: BLMOVE queue -> processing, LREM on completion, and a reaper
that re-queues jobs stuck in `processing` (worker crash). Delivery is at-least-once; processing is idempotent.
"""
import hashlib
import json
import time
import uuid

from . import config
from .schemas import AEReport, QuarantineRecord, TriageResult

QUEUE = "pv:queue"
PROCESSING = "pv:processing"
DLQ = "pv:dlq"
QUARANTINE = "pv:quarantine"
FEEDBACK = "pv:feedback"


def job_key(job_id: str) -> str:
    return f"pv:job:{job_id}"


def cache_key_for(report: AEReport) -> str:
    canon = json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex


# ---------- rate limit (fixed window, per client) ----------
async def rate_limit_hit(r, client: str, limit: int, window_s: int = 60) -> tuple[bool, int]:
    """Returns (allowed, retry_after_s). Fixed window: simple + cheap, allows 2x burst at a boundary."""
    now = int(time.time())
    window = now // window_s
    key = f"pv:rl:{client}:{window}"
    async with r.pipeline(transaction=False) as p:
        p.incr(key)
        p.expire(key, window_s + 1)
        count, _ = await p.execute()
    return count <= limit, window_s - (now % window_s)


# ---------- quarantine (D1) ----------
async def quarantine(r, client: str, reason: str, errors: list[dict], raw: bytes) -> str:
    rec = QuarantineRecord(id=new_id(), received_at=time.time(), client=client, reason=reason,
                           errors=errors, raw=raw[:10_000].decode("utf-8", "replace"))
    async with r.pipeline(transaction=True) as p:
        p.lpush(QUARANTINE, rec.model_dump_json())
        p.ltrim(QUARANTINE, 0, 999)
        await p.execute()
    return rec.id


async def list_quarantine(r, limit: int = 50) -> list[dict]:
    return [json.loads(x) for x in await r.lrange(QUARANTINE, 0, limit - 1)]


# ---------- jobs ----------
async def get_cached(r, ckey: str) -> dict | None:
    v = await r.get(f"pv:cache:{ckey}")
    return json.loads(v) if v else None


async def create_done_job_from_cache(r, cached: dict, ckey: str) -> str:
    job_id = new_id()
    now = time.time()
    async with r.pipeline(transaction=True) as p:
        p.hset(job_key(job_id), mapping={
            "status": "done", "created_at": now, "completed_at": now, "cached": 1,
            "cache_key": ckey, "result": json.dumps(cached)})
        p.expire(job_key(job_id), config.JOB_TTL_S)
        await p.execute()
    return job_id


async def enqueue(r, report: AEReport, ckey: str, carrier: dict) -> tuple[str, bool]:
    """Returns (job_id, deduped). Dedupe: identical report already in flight -> reuse its job."""
    job_id = new_id()
    won = await r.set(f"pv:inflight:{ckey}", job_id, nx=True, ex=120)
    if not won:
        existing = await r.get(f"pv:inflight:{ckey}")
        if existing:
            return existing, True
    async with r.pipeline(transaction=True) as p:
        p.hset(job_key(job_id), mapping={
            "status": "queued", "created_at": time.time(), "attempts": 0, "cached": 0,
            "cache_key": ckey, "report": report.model_dump_json(), "carrier": json.dumps(carrier)})
        p.expire(job_key(job_id), config.JOB_TTL_S)
        p.lpush(QUEUE, job_id)
        await p.execute()
    return job_id, False


async def get_job(r, job_id: str) -> dict | None:
    h = await r.hgetall(job_key(job_id))
    if not h:
        return None
    out = {"job_id": job_id, "status": h.get("status"), "cached": h.get("cached") == "1",
           "created_at": float(h["created_at"]) if "created_at" in h else None}
    if h.get("result"):
        out["result"] = json.loads(h["result"])
    if h.get("error"):
        out["error"] = h["error"]
    if h.get("completed_at") and h.get("created_at"):
        out["duration_s"] = round(float(h["completed_at"]) - float(h["created_at"]), 3)
    if h.get("feedback"):
        out["feedback"] = json.loads(h["feedback"])
    return out


async def complete_job(r, job_id: str, ckey: str, result: TriageResult) -> float:
    now = time.time()
    payload = result.model_dump_json()
    async with r.pipeline(transaction=True) as p:
        p.hset(job_key(job_id), mapping={"status": "done", "completed_at": now, "result": payload})
        if result.source == "llm":  # never cache degraded (fallback) answers
            p.set(f"pv:cache:{ckey}", payload, ex=config.CACHE_TTL_S)
        p.delete(f"pv:inflight:{ckey}")
        p.lrem(PROCESSING, 1, job_id)
        await p.execute()
    return now


async def fail_or_retry(r, job_id: str, err: str) -> str:
    """Bug/Redis-level failures only (LLM failures never raise: they fall back). Returns 'retried'|'dead'."""
    attempts = await r.hincrby(job_key(job_id), "attempts", 1)
    async with r.pipeline(transaction=True) as p:
        p.lrem(PROCESSING, 1, job_id)
        if attempts < config.MAX_JOB_ATTEMPTS:
            p.hset(job_key(job_id), mapping={"status": "queued", "error": err[:300]})
            p.lpush(QUEUE, job_id)
            status = "retried"
        else:
            p.hset(job_key(job_id), mapping={"status": "failed", "error": err[:300]})
            p.lpush(DLQ, job_id)
            status = "dead"
        await p.execute()
    return status


async def requeue_stale(r, suspects: dict[str, float]) -> int:
    """Reaper. Re-queue jobs whose worker died. `suspects` = in-memory first-seen times for jobs that were
    claimed but never marked 'processing' (crash between BLMOVE and HSET)."""
    now, n = time.time(), 0
    for job_id in await r.lrange(PROCESSING, 0, -1):
        h = await r.hmget(job_key(job_id), "status", "started_at")
        status, started = h[0], h[1]
        stale = False
        if status in (None, "done", "failed"):
            await r.lrem(PROCESSING, 1, job_id)
            suspects.pop(job_id, None)
            continue
        if status == "processing" and started and now - float(started) > config.VISIBILITY_TIMEOUT_S:
            stale = True
        elif status == "queued":
            first = suspects.setdefault(job_id, now)
            stale = now - first > config.VISIBILITY_TIMEOUT_S
        if stale:
            async with r.pipeline(transaction=True) as p:
                p.lrem(PROCESSING, 1, job_id)
                p.hset(job_key(job_id), "status", "queued")
                p.lpush(QUEUE, job_id)
                await p.execute()
            suspects.pop(job_id, None)
            n += 1
    return n


# ---------- feedback ----------
async def save_feedback(r, fb: dict, job: dict, ckey: str | None) -> None:
    async with r.pipeline(transaction=True) as p:
        p.lpush(FEEDBACK, json.dumps(fb))
        p.ltrim(FEEDBACK, 0, 9999)
        p.hset(job_key(fb["job_id"]), "feedback", json.dumps(fb))
        if fb["verdict"] == "disagree" and ckey:
            p.delete(f"pv:cache:{ckey}")  # a disputed answer must not be served from cache again
        await p.execute()


async def feedback_stats(r) -> dict:
    items = [json.loads(x) for x in await r.lrange(FEEDBACK, 0, 9999)]
    agree = sum(1 for i in items if i["verdict"] == "agree")
    return {"total": len(items), "agree": agree, "disagree": len(items) - agree,
            "agreement_rate": round(agree / len(items), 3) if items else None}
