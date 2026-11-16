#!/usr/bin/env python3
"""Pull headline numbers for the last N seconds from Prometheus (stdlib only).
   python scripts/prom_snapshot.py --window 120 [--label workers=4] [--csv results/scaling.csv]"""
import argparse, csv, json, os, urllib.parse, urllib.request

P = os.getenv("PROM_URL", "http://localhost:9090")


def q(expr):
    url = f"{P}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    res = json.load(urllib.request.urlopen(url, timeout=10))["data"]["result"]
    return float(res[0]["value"][1]) if res else float("nan")


def snapshot(w):
    return {
        "intake_rps": q(f'sum(rate(pv_reports_total{{result=~"accepted|cache_hit|deduped"}}[{w}s]))'),
        "intake_p95_s": q(f'histogram_quantile(0.95, sum by (le)(rate(pv_http_request_seconds_bucket{{route="/v1/reports"}}[{w}s])))'),
        "jobs_done_per_s": q(f'sum(rate(pv_jobs_total{{status="done"}}[{w}s]))'),
        "e2e_p50_s": q(f'histogram_quantile(0.5, sum by (le)(rate(pv_job_e2e_seconds_bucket[{w}s])))'),
        "e2e_p95_s": q(f'histogram_quantile(0.95, sum by (le)(rate(pv_job_e2e_seconds_bucket[{w}s])))'),
        "queue_depth_max": q(f'max_over_time(pv_queue_depth[{w}s])'),
        "fallback_share": q(f'sum(increase(pv_triage_source_total{{source="fallback"}}[{w}s])) / sum(increase(pv_triage_source_total[{w}s]))'),
        "cache_hit_ratio": q(f'sum(increase(pv_reports_total{{result="cache_hit"}}[{w}s])) / sum(increase(pv_reports_total[{w}s]))'),
        "rate_limited": q(f'sum(increase(pv_reports_total{{result="rate_limited"}}[{w}s]))'),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=120)
    ap.add_argument("--label", default="")
    ap.add_argument("--csv")
    a = ap.parse_args()
    row = {"label": a.label, **{k: round(v, 3) for k, v in snapshot(a.window).items()}}
    print(json.dumps(row, indent=1))
    if a.csv:
        new = not os.path.exists(a.csv)
        with open(a.csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
