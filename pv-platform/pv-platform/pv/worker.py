"""Queue worker. N coroutines per process; scale out with `docker compose up --scale worker=K`."""
import asyncio
import json
import logging
import signal
import time

import httpx
import redis.asyncio as aioredis
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind
from prometheus_client import start_http_server
from redis.exceptions import RedisError

from . import config, metrics, store, triage
from .llm import LLMClient
from .logging_setup import setup_logging
from .schemas import AEReport
from .telemetry import setup_tracing, tracer

log = logging.getLogger("pv.worker")


async def handle_one(r, llm: LLMClient, job_id: str) -> None:
    key = store.job_key(job_id)
    job = await r.hgetall(key)
    if not job or "report" not in job:
        await r.lrem(store.PROCESSING, 1, job_id)
        return
    ctx = extract(json.loads(job.get("carrier") or "{}"))  # continue the API's trace across the queue
    with tracer.start_as_current_span("process_job", context=ctx, kind=SpanKind.CONSUMER,
                                      attributes={"job.id": job_id}) as span:
        await r.hset(key, mapping={"status": "processing", "started_at": time.time()})
        report = AEReport.model_validate_json(job["report"])
        result, usage = await triage.run(llm, report)
        done_at = await store.complete_job(r, job_id, job["cache_key"], result)
        metrics.JOBS.labels("done").inc()
        metrics.TRIAGE_SRC.labels(result.source).inc()
        metrics.E2E.observe(done_at - float(job["created_at"]))
        span.set_attribute("triage.source", result.source)
        span.set_attribute("triage.seriousness", result.seriousness.value)
        log.info("job done", extra={"ctx": {"job_id": job_id, "source": result.source,
                                            "seriousness": result.seriousness.value,
                                            "e2e_s": round(done_at - float(job["created_at"]), 3)}})


async def worker_loop(r, llm: LLMClient, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            job_id = await r.blmove(store.QUEUE, store.PROCESSING, 1, src="RIGHT", dest="LEFT")
        except (RedisError, OSError) as e:
            log.error("redis unavailable: %s", e)
            await asyncio.sleep(1)
            continue
        if not job_id:
            continue
        try:
            await handle_one(r, llm, job_id)
        except Exception as e:  # bug-level failure; LLM problems never get here
            log.exception("job failed", extra={"ctx": {"job_id": job_id}})
            status = await store.fail_or_retry(r, job_id, repr(e))
            metrics.JOBS.labels(status).inc()


async def reaper(r, stop: asyncio.Event) -> None:
    suspects: dict[str, float] = {}
    while not stop.is_set():
        try:
            n = await store.requeue_stale(r, suspects)
            if n:
                metrics.JOBS.labels("reaped").inc(n)
                log.warning("re-queued stale jobs", extra={"ctx": {"count": n}})
        except (RedisError, OSError) as e:
            log.error("reaper: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), 15)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    setup_logging("pv-worker")
    setup_tracing("pv-worker")
    start_http_server(config.WORKER_METRICS_PORT)
    r = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=config.WORKER_CONCURRENCY * 2)) as http:
        llm = LLMClient(http)
        log.info("worker started", extra={"ctx": {"concurrency": config.WORKER_CONCURRENCY}})
        tasks = [asyncio.create_task(worker_loop(r, llm, stop)) for _ in range(config.WORKER_CONCURRENCY)]
        tasks.append(asyncio.create_task(reaper(r, stop)))
        await stop.wait()
        await asyncio.gather(*tasks, return_exceptions=True)
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
