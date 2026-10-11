#!/usr/bin/env bash
# spikes/window/00_copy_and_patch.sh - the D2/D3 copy venue, resumable.
#
#   00_copy_and_patch.sh [COPY_PATH]        # copy + patch + verify (default COPY below)
#
# Starts fine IN PARALLEL with the engine loading (disk work only). Safe to re-run after
# interruption: the file copy is skipped when the target already has the exact source size,
# and the row-writes are idempotent pwrites (170 B each, deduplicated by dst row).
set -u
DEVROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$DEVROOT" || exit 1
SRC="Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"
OUT="${1:-Strata-data/ple-copies/q8-shard2-d2.gguf}"
PLAN="spikes/window/d2_plan.json"
PY="${PY:-$HOME/Strata-data/venvs/spikeC/bin/python}"
[ -f "$PLAN" ] || { echo "missing $PLAN - run: $PY spikes/window/build_d2_plan.py"; exit 1; }
mkdir -p "$(dirname "$OUT")"
if [ -f "$OUT" ] && [ "$(stat -c%s "$OUT")" = "$(stat -c%s "$SRC")" ]; then
  echo "[copy] $OUT already complete ($(du -h "$OUT" | cut -f1)) - skipping"
else
  echo "[copy] copying 54.4 GB (expect 10-25 min on this NVMe) ..."
  t0=$(date +%s)
  cp "$SRC" "$OUT.part" && mv "$OUT.part" "$OUT" || { rm -f "$OUT.part"; echo "copy failed"; exit 1; }
  echo "[copy] done in $(( $(date +%s) - t0 )) s"
fi
echo "[patch] applying $(python3 -c "import json;print(len(json.load(open('$PLAN'))['writes']))") row-writes ..."
"$PY" spikes/ple-overlay/mk_ple_copy.py --plan "$PLAN" --apply --out "$OUT"
echo "[verify] re-reading every written row + 100 unwritten sample rows ..."
"$PY" spikes/ple-overlay/mk_ple_copy.py --plan "$PLAN" --verify --out "$OUT" || exit 1
echo "[venue] switching the dev config (engine picks it up at NEXT boot):"
"$PY" spikes/window/set_ple_venue.py --copy "$OUT"
