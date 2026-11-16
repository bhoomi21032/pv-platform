#!/usr/bin/env bash
# Failure injection against the llm-stub (see docs/INCIDENT_LOG.md for the drills).
#   chaos.sh slow      LLM latency ~5 s (exceeds the 3 s timeout)
#   chaos.sh errors    50% HTTP 503
#   chaos.sh down      100% HTTP 503
#   chaos.sh hang      every call hangs
#   chaos.sh garbage   50% malformed JSON output
#   chaos.sh heal      back to normal
set -euo pipefail
STUB=${STUB:-http://localhost:9000}
set_cfg() { curl -s -XPOST "$STUB/admin/config" -H 'content-type: application/json' -d "$1"; echo; }
case "${1:-}" in
  slow)    set_cfg '{"latency_ms_mean": 5000, "latency_ms_jitter": 500}' ;;
  errors)  set_cfg '{"error_rate": 0.5}' ;;
  down)    set_cfg '{"error_rate": 1.0}' ;;
  hang)    set_cfg '{"hang_rate": 1.0}' ;;
  garbage) set_cfg '{"malformed_rate": 0.5}' ;;
  heal)    curl -s -XPOST "$STUB/admin/reset"; echo ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
