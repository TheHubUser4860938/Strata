#!/usr/bin/env bash
# dev-down.sh - stop the memory-core dev instance (:8005) and bring production back (:8004).
# Note: production comes back detached and WITHOUT --open (no browser window); the engine log
# still goes to ~/Strata/strata-iq3_s.log. The first request after the restart re-reads prompt
# history (RAM checkpoints are gone) - expect ~1-2 min on long sessions.
set -u
DEVROOT="$(cd "$(dirname "$0")" && pwd)"
PRODROOT="${PRODROOT:-$HOME/Strata}"
SRV='serve/[s]erver.py --engine strata --config'

echo "[dev-down 1/3] Stopping dev (:8005) ..."
pkill -TERM -f "$SRV $DEVROOT/strata-dev.json" 2>/dev/null
pkill -TERM -f "$DEVROOT/[e]ngine/strata" 2>/dev/null
for _ in $(seq 1 60); do
  pgrep -f "$DEVROOT/[e]ngine/strata" >/dev/null || pgrep -f "$SRV $DEVROOT/strata-dev.json" >/dev/null || break
  sleep 1
done
curl -s -m 2 http://127.0.0.1:8005/health >/dev/null 2>&1 && echo "  WARNING: :8005 still answers" || echo "  dev down"

echo "[dev-down 2/3] Starting production (:8004) ..."
cd "$PRODROOT" || exit 1
nohup .venv/bin/python serve/server.py --engine strata --config "$PRODROOT/strata-iq3_s.json" --port 8004 >> "$PRODROOT/dev-cycle-server.out" 2>&1 &
echo "  server pid $!"

echo "[dev-down 3/3] Waiting for production health (up to 7 min) ..."
for i in $(seq 1 420); do
  if curl -sf -m 3 http://127.0.0.1:8004/health >/dev/null 2>&1; then
    echo "  production health OK after ${i}s - back home on :8004"; exit 0
  fi
  sleep 1
done
echo "  ERROR: :8004 not healthy after 420 s. Check ~/Strata/strata-iq3_s.log and dev-cycle-server.out."
exit 1
