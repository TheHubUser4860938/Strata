#!/usr/bin/env bash
# dev-up.sh - stop production Strata (:8004) and start the memory-core dev instance (:8005).
# One engine at a time: the machine has 62 GB RAM and each engine needs ~51 GB (docs/MEMORY_CORE.md).
set -u
DEVROOT="$(cd "$(dirname "$0")" && pwd)"
PRODROOT="${PRODROOT:-$HOME/Strata}"
SRV='serve/[s]erver.py --engine strata --config'

# Politeness guard: production is someone else's terminal process. This script only stops it when
# asked (--force) or when it is already down (the coordinated flow: user closes main, then we start dev).
if [ "${1:-}" != "--force" ] && pgrep -f "$SRV $PRODROOT/strata-iq3_s.json" >/dev/null 2>&1; then
  echo "production :8004 is still running. Close the main build first (or pass --force to let this script stop it)."; exit 2
fi

echo "[dev-up 1/4] Stopping production (:8004) ..."
pkill -TERM -f "$SRV $PRODROOT/strata-iq3_s.json" 2>/dev/null
pkill -TERM -f "$PRODROOT/[e]ngine/strata" 2>/dev/null
for _ in $(seq 1 60); do
  pgrep -f "$PRODROOT/[e]ngine/strata" >/dev/null || pgrep -f "$SRV $PRODROOT/strata-iq3_s.json" >/dev/null || break
  sleep 1
done
if curl -s -m 2 http://127.0.0.1:8004/health >/dev/null 2>&1; then
  echo "  WARNING: :8004 still answers after 60 s - aborting before double-start"; exit 1
fi
free -h | awk 'NR==2 { print "  RAM after stop: used " $3 ", available " $7 }'

echo "[dev-up 2/4] Starting dev (:8005) ..."
mkdir -p "$DEVROOT/Strata-data/sessions-dev"
cd "$DEVROOT" || exit 1
# STRATA_CFG names a different run config (venue days: STRATA_CFG=$PWD/strata-venue-pg.json ./dev-up.sh);
# the default stays byte-identical to every other boot. The echo is the venue-day guard: the operator
# sees WHICH store came up instead of guessing (measured the confusion, 2026-10-16 cutover day).
CFG="${STRATA_CFG:-$DEVROOT/strata-dev.json}"
echo "  config: $(basename "$CFG")"
nohup .venv/bin/python serve/server.py --engine strata --config "$CFG" --port 8005 >> "$DEVROOT/dev-server.out" 2>&1 &
echo "  server pid $!"

echo "[dev-up 3/4] Waiting for the engine to load (weights ~51 GB; up to 7 min) ..."
for i in $(seq 1 420); do
  if curl -sf -m 3 http://127.0.0.1:8005/health >/dev/null 2>&1; then
    echo "  dev health OK after ${i}s"; 
    free -h | awk 'NR==2 { print "  RAM now: used " $3 ", available " $7 }'
    echo "[dev-up 4/4] Done. Point OpenCode/Odysseus at http://127.0.0.1:8005/v1."
    exit 0
  fi
  sleep 1
done

echo "  ERROR: :8005 not healthy after 420 s. Tails:"
tail -n 4 "$DEVROOT/strata-dev.log" 2>/dev/null
tail -n 4 "$DEVROOT/dev-server.out" 2>/dev/null
exit 1
