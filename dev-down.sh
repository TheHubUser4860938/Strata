#!/usr/bin/env bash
# dev-down.sh - stop the memory-core dev instance (:8005). Nothing is STARTED: production
# (:8004) comes back only when you bring it up yourself (START-HERE / your usual launcher).
# If production happens to be running, this script stops it too, so "dev-down" always leaves
# the machine engine-free - the 62 GB box fits exactly one engine (docs/MEMORY_CORE.md) and
# the old auto-restart used to surprise-run the non-dev build nobody asked for.
set -u
DEVROOT="$(cd "$(dirname "$0")" && pwd)"
PRODROOT="${PRODROOT:-$HOME/Strata}"
SRV='serve/[s]erver.py --engine strata --config'

echo "[dev-down 1/2] Stopping dev (:8005) ..."
pkill -TERM -f "$SRV $DEVROOT/strata-dev.json" 2>/dev/null
pkill -TERM -f "$DEVROOT/[e]ngine/strata" 2>/dev/null
for _ in $(seq 1 60); do
  pgrep -f "$DEVROOT/[e]ngine/strata" >/dev/null || pgrep -f "$SRV $DEVROOT/strata-dev.json" >/dev/null || break
  sleep 1
done
if curl -s -m 2 http://127.0.0.1:8005/health >/dev/null 2>&1; then
  echo "  WARNING: :8005 still answers after 60 s"
else
  echo "  dev down"
fi

echo "[dev-down 2/2] Stopping production (:8004) if it runs (nothing is restarted) ..."
pkill -TERM -f "$SRV $PRODROOT/strata-iq3_s.json" 2>/dev/null
pkill -TERM -f "$PRODROOT/[e]ngine/strata" 2>/dev/null
for _ in $(seq 1 60); do
  pgrep -f "$PRODROOT/[e]ngine/strata" >/dev/null || pgrep -f "$SRV $PRODROOT/strata-iq3_s.json" >/dev/null || break
  sleep 1
done
if curl -s -m 2 http://127.0.0.1:8004/health >/dev/null 2>&1; then
  echo "  WARNING: :8004 still answers after 60 s"
else
  echo "  no engine on :8004 - production starts only when you start it"
fi
free -h | awk 'NR==2 { print "  RAM: used " $3 ", available " $7 }'
