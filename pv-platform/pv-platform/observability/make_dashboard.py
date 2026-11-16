"""Regenerates observability/grafana/dashboards/pv.json.  python observability/make_dashboard.py"""
import json, pathlib

DS = {"type": "prometheus", "uid": "prometheus"}
panels, pid = [], 0


def panel(title, exprs, x, y, w=8, h=7, unit="short", kind="timeseries", thresholds=None, mappings=None):
    global pid
    pid += 1
    p = {"id": pid, "title": title, "type": kind, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l, "datasource": DS} for i, (e, l) in enumerate(exprs)],
         "fieldConfig": {"defaults": {"unit": unit}, "overrides": []}, "options": {}}
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": thresholds}
        p["fieldConfig"]["defaults"]["custom"] = {"thresholdsStyle": {"mode": "line"}}
    if mappings:
        p["fieldConfig"]["defaults"]["mappings"] = mappings
    panels.append(p)


q = lambda quant, metric, sel="": f'histogram_quantile({quant}, sum by (le)(rate({metric}_bucket{sel}[1m])))'
R = '{route="/v1/reports"}'
steps = lambda v: [{"color": "green", "value": None}, {"color": "red", "value": v}]

panel("Intake outcomes /s", [('sum by (result)(rate(pv_reports_total[1m]))', "{{result}}")], 0, 0, unit="reqps")
panel("Intake latency p50/p95 (SLO p95 < 300ms)", [(q(0.5, "pv_http_request_seconds", R), "p50"), (q(0.95, "pv_http_request_seconds", R), "p95")], 8, 0, unit="s", thresholds=steps(0.3))
panel("Intake 5xx ratio (SLO < 1%)", [(f'sum(rate(pv_http_requests_total{{route="/v1/reports",status=~"5.."}}[1m])) / sum(rate(pv_http_requests_total{{route="/v1/reports"}}[1m]))', "5xx")], 16, 0, unit="percentunit", thresholds=steps(0.01))
panel("Queue depth", [("max(pv_queue_depth)", "waiting")], 0, 7)
panel("Triage e2e p50/p95/p99 (SLO p95 < 10s)", [(q(0.5, "pv_job_e2e_seconds"), "p50"), (q(0.95, "pv_job_e2e_seconds"), "p95"), (q(0.99, "pv_job_e2e_seconds"), "p99")], 8, 7, unit="s", thresholds=steps(10))
panel("Jobs completed /s", [('sum by (status)(rate(pv_jobs_total[1m]))', "{{status}}")], 16, 7, unit="ops")
panel("LLM attempts by outcome /s", [('sum by (outcome)(rate(pv_llm_requests_total[1m]))', "{{outcome}}")], 0, 14)
panel("Circuit breaker", [("max(pv_llm_breaker_state)", "state")], 8, 14, w=4, kind="stat",
      mappings=[{"type": "value", "options": {"0": {"text": "CLOSED", "color": "green"}, "1": {"text": "HALF-OPEN", "color": "yellow"}, "2": {"text": "OPEN", "color": "red"}}}])
panel("Fallback share of triage", [('sum(rate(pv_triage_source_total{source="fallback"}[1m])) / sum(rate(pv_triage_source_total[1m]))', "fallback")], 12, 14, w=6, unit="percentunit")
panel("Cache hit ratio", [('sum(rate(pv_reports_total{result="cache_hit"}[1m])) / sum(rate(pv_reports_total[1m]))', "hit")], 18, 14, w=6, unit="percentunit")
panel("LLM tokens /s", [('sum by (direction)(rate(pv_llm_tokens_total[1m]))', "{{direction}}")], 0, 21, unit="short")
panel("Rate-limited + shed /s", [('sum(rate(pv_reports_total{result=~"rate_limited|shed"}[1m]))', "rejected")], 8, 21, unit="reqps")
panel("Reviewer feedback", [('sum by (verdict)(increase(pv_feedback_total[1h]))', "{{verdict}}")], 16, 21)

dash = {"uid": "pv-main", "title": "PV intake & triage", "schemaVersion": 39, "version": 1, "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"}, "panels": panels, "templating": {"list": []}, "tags": ["pv"]}
out = pathlib.Path(__file__).parent / "grafana" / "dashboards" / "pv.json"
out.write_text(json.dumps(dash, indent=1))
print("wrote", out)
