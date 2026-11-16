"""JSON logs on stdout with trace_id/span_id so a log line jumps straight to its Jaeger trace."""
import json
import logging
import sys
import time

from . import config


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        try:
            from opentelemetry import trace
            ctx = trace.get_current_span().get_span_context()
            if ctx.is_valid:
                d["trace_id"] = format(ctx.trace_id, "032x")
                d["span_id"] = format(ctx.span_id, "016x")
        except Exception:
            pass
        d.update(getattr(record, "ctx", {}))  # logger.info("x", extra={"ctx": {"job_id": ...}})
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


def setup_logging(service: str) -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(config.LOG_LEVEL)
    logging.getLogger("uvicorn.access").disabled = True  # we log requests ourselves
    logging.getLogger("httpx").setLevel(logging.WARNING)
