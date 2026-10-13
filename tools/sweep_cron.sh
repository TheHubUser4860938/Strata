#!/usr/bin/env bash
# Memory-curation cron entry: the PRESSURE-DRIVEN sweep (the Letta/MemGPT sleep-time pattern -
# the store speaks before the wall, and a background pass consolidates instead of the queue
# silently growing).  Cheap by design: it asks the server two numbers; when neither is hot it
# exits without touching the engine at all.
#
#   pressure  - the store sits at >=80% of its max_memories budget (GET /memory "pressure")
#   pending   - journaled-but-uncurated turns; more than a handful means the loop fell behind
#
# The sweep itself is idle-gated (curate_loop --sweep waits for quiet slots), so a cron tick
# that lands mid-thought never steals engine time from a live answer.
#
# Install on THIS dev box (no cron daemon - systemd user timer, every 30 min):
#   units at ~/.config/systemd/user/strata-sweep.{service,timer} - INSTALLED + ENABLED
#   2026-10-12 (first tick drained 11 pending turns -> 13 FILL, queue 0).  The unit also sets
#   Environment=STRATA_USER_NAME=<name>: a real name classifies USR best and does not belong in git.
#   Check: systemctl --user list-timers strata-sweep.timer ; force: systemctl --user start strata-sweep.service ;
#   remove: systemctl --user disable --now strata-sweep.timer
# Install where cron DOES exist (every 30 min, mail-quiet):
#   (crontab -l 2>/dev/null | grep -v sweep_cron.sh; \
#    echo '*/30 * * * * ~/Projects/Strata-MAM-cartrige-mod/tools/sweep_cron.sh >> ~/Projects/Strata-MAM-cartrige-mod/tools/state/cron.log 2>&1') | crontab -
# Remove: crontab -l | grep -v sweep_cron.sh | crontab -
set -u
cd "$(dirname "$0")/.."
URL=${STRATA_URL:-http://127.0.0.1:8005}
PENDING_MAX=${PENDING_MAX:-5}

nums=$(curl -sf --max-time 5 "$URL/memory" 2>/dev/null | python3 -c \
  'import json,sys; s=json.load(sys.stdin)["stats"]; print(int(bool(s.get("pressure"))), s["turns_pending"])' 2>/dev/null) \
  || exit 0   # server down or memory off: nothing to do, no cron mail
read -r pressure pending <<<"$nums"

if [ "${pressure:-0}" = "1" ] || [ "${pending:-0}" -gt "$PENDING_MAX" ]; then
    echo "$(date '+%F %T') sweep: pressure=$pressure pending=$pending"
    python3 tools/curate_loop.py --sweep --batches 2 --tag cron >> tools/state/cron.log 2>&1
else
    echo "$(date '+%F %T') idle: pending=$pending"
fi
