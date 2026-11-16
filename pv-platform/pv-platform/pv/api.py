"""FastAPI intake service. Validates -> quarantines bad input -> caches/dedupes -> enqueues -> returns 202."""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from opentelemetry.propagate import inject
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from . import config, metrics, store
from .logging_setup import setup_logging
from .schemas import AEReport, FeedbackIn, format_errors
from .telemetry import instrument_fastapi, setup_tracing, tracer

log = logging.getLogger("pv.api")
QUIET = {"/metrics", "/healthz", "/readyz"}


def client_id(request: Request) -> str:
    return request.headers.get("x-api-key") or (request.client.host if request.client else "unknown")


async def rate_limit(request: Request) -> None:
    allowed, retry_after = await store.rate_limit_hit(request.app.state.redis, client_id(request),
                                                      config.RATE_LIMIT_PER_MIN)
    if not allowed:
        metrics.REPORTS.labels("rate_limited").inc()
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(retry_after)})


async def _depth_gauge(r) -> None:
    while True:
        try:
            metrics.QUEUE_DEPTH.set(await r.llen(store.QUEUE))
        except Exception:
            pass
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "redis", None) is None:  # tests inject a fake before startup
        app.state.redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    task = asyncio.create_task(_depth_gauge(app.state.redis))
    yield
    task.cancel()
    await app.state.redis.aclose()


def create_app() -> FastAPI:
    setup_logging("pv-api")
    setup_tracing("pv-api")
    app = FastAPI(title="PV intake & triage", version="1.0", lifespan=lifespan)
    instrument_fastapi(app)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        t0, status = time.perf_counter(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            if request.url.path not in QUIET:
                route = request.scope.get("route")
                path = route.path if route else "unmatched"  # template, not raw URL -> bounded cardinality
                dt = time.perf_counter() - t0
                metrics.HTTP_REQS.labels(request.method, path, str(status)).inc()
                metrics.HTTP_LAT.labels(path).observe(dt)
                log.info("request", extra={"ctx": {"method": request.method, "path": path, "status": status,
                                                   "ms": round(dt * 1000, 1), "client": client_id(request)}})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz(request: Request):
        try:
            await request.app.state.redis.ping()
        except Exception:
            return JSONResponse({"ok": False, "redis": "down"}, status_code=503)
        return {"ok": True}

    @app.get("/metrics")
    async def prom():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/reports", status_code=202, dependencies=[Depends(rate_limit)])
    async def submit(request: Request):
        r, client = request.app.state.redis, client_id(request)
        raw = await request.body()
        if len(raw) > config.MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")

        # --- D1: validate, else quarantine (never silently drop, never reach the LLM) ---
        with tracer.start_as_current_span("validate"):
            try:
                data = json.loads(raw)
            except ValueError:
                return await _reject(r, client, "malformed_json", [{"field": "<body>", "type": "json_invalid",
                                                                     "msg": "body is not valid JSON"}], raw)
            if not isinstance(data, dict):
                return await _reject(r, client, "not_an_object", [{"field": "<body>", "type": "model_type",
                                                                    "msg": "expected a JSON object"}], raw)
            try:
                report = AEReport.model_validate(data)
            except ValidationError as e:
                return await _reject(r, client, "schema_validation", format_errors(e), raw)

        ckey = store.cache_key_for(report)
        cached = await store.get_cached(r, ckey)
        if cached:
            job_id = await store.create_done_job_from_cache(r, cached, ckey)
            metrics.REPORTS.labels("cache_hit").inc()
            return JSONResponse({"job_id": job_id, "status": "done", "cached": True}, status_code=200)

        if await r.llen(store.QUEUE) >= config.MAX_QUEUE_DEPTH:  # load shedding beats unbounded latency
            metrics.REPORTS.labels("shed").inc()
            raise HTTPException(503, "queue full, retry later", headers={"Retry-After": "5"})

        with tracer.start_as_current_span("enqueue"):
            carrier: dict = {}
            inject(carrier)  # trace context rides along in the job -> worker span joins this trace
            job_id, deduped = await store.enqueue(r, report, ckey, carrier)
        metrics.REPORTS.labels("deduped" if deduped else "accepted").inc()
        return {"job_id": job_id, "status": "queued", "deduped": deduped, "poll": f"/v1/reports/{job_id}"}

    async def _reject(r, client, reason, errors, raw):
        qid = await store.quarantine(r, client, reason, errors, raw)
        metrics.REPORTS.labels("quarantined").inc()
        log.warning("report quarantined", extra={"ctx": {"quarantine_id": qid, "reason": reason}})
        return JSONResponse({"detail": "report failed validation and was quarantined",
                             "quarantine_id": qid, "errors": errors}, status_code=422)

    @app.get("/v1/reports/{job_id}")
    async def get_report(job_id: str, request: Request):
        job = await store.get_job(request.app.state.redis, job_id)
        if not job:
            raise HTTPException(404, "unknown job_id (or expired)")
        return job

    @app.get("/v1/quarantine")
    async def get_quarantine(request: Request, limit: int = 50):
        return await store.list_quarantine(request.app.state.redis, min(limit, 200))

    @app.post("/v1/feedback", dependencies=[Depends(rate_limit)])
    async def feedback(fb: FeedbackIn, request: Request):
        r = request.app.state.redis
        job = await r.hgetall(store.job_key(fb.job_id))
        if not job:
            raise HTTPException(404, "unknown job_id (or expired)")
        rec = {**fb.model_dump(mode="json"), "at": time.time(), "client": client_id(request)}
        await store.save_feedback(r, rec, job, job.get("cache_key"))
        metrics.FEEDBACK.labels(fb.verdict).inc()
        return {"ok": True}

    @app.get("/v1/feedback/stats")
    async def fb_stats(request: Request):
        return await store.feedback_stats(request.app.state.redis)

    web = Path(__file__).resolve().parent.parent / "web"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")  # keep LAST
    return app


app = create_app()
