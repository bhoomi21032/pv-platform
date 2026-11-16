#!/usr/bin/env bash
# Scaling experiment: same load, 1/2/4/8 worker containers. Needs: docker compose, `pip install locust`.
# Usage: scripts/run_scaling.sh [users=80] [duration_s=150]
set -euo pipefail
USERS=${1:-80}; DUR=${2:-150}; WINDOW=$((DUR - 30))   # drop the first 30 s ramp-up from the metric window
rm -f results/scaling.csv
# High limit so the rate limiter is not the bottleneck in THIS experiment (it has its own test: see docs/SCALING.md)
export RATE_LIMIT_PER_MIN=100000 WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-4}
docker compose up -d --build --scale worker=1
sleep 8
curl -s -XPOST localhost:9000/admin/reset >/dev/null
for N in 1 2 4 8; do
  echo "=== workers=$N (concurrency=$WORKER_CONCURRENCY each, users=$USERS) ==="
  docker compose up -d --no-recreate --scale worker=$N
  sleep 6
  docker compose exec -T redis redis-cli FLUSHALL >/dev/null    # empty queue + cache between runs
  sleep 2
  locust -f load/locustfile.py --headless -u "$USERS" -r 20 -t "${DUR}s" --host http://localhost:8000 \
         --csv "results/locust_w$N" --only-summary 2>&1 | tail -n 25
  python scripts/prom_snapshot.py --window "$WINDOW" --label "workers=$N" --csv results/scaling.csv
  sleep 20   # let the queue drain before the next run
done
echo; echo "Results -> results/scaling.csv (+ results/locust_w*_stats.csv)"
