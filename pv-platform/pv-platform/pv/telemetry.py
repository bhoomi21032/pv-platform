"""OpenTelemetry -> Jaeger (OTLP/HTTP). No exporter when OTLP_TRACES_ENDPOINT is unset (tests/local)."""
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from . import config

tracer = trace.get_tracer("pv")
_done = False


def setup_tracing(service_name: str) -> None:
    global _done
    if _done:
        return
    _done = True
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if config.OTLP_TRACES_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=config.OTLP_TRACES_ENDPOINT)))
    trace.set_tracer_provider(provider)
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    HTTPXClientInstrumentor().instrument()


def instrument_fastapi(app) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,healthz,readyz")
