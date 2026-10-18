"""PR4 (audit 09 finding 5, Grok 2026-10-18): the sweep_cron.sh cross-process exclusion.

flock is the mechanism the script relies on; this test pins the mechanism itself: a second
NON-BLOCKING holder of the same lockfile must fail with the script's sentinel exit code (9)
while the first runs, and the lock must be free again once the holder exits. Without the
sentinel contract the script's skip-report would fire on real sweep failures (or miss real
collisions).

    python3 tools/test_sweep_lock.py

No GPU, no server, no Postgres - pure flock(1) semantics.
"""
import os
import subprocess
import sys
import tempfile
import time


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        lock = os.path.join(d, "sweep.lock")
        a = subprocess.Popen(["flock", "-n", "-E", "9", lock, "sleep", "2"])
        time.sleep(0.3)  # a holds the lock
        b = subprocess.run(["flock", "-n", "-E", "9", lock, "true"], capture_output=True)
        assert b.returncode == 9, f"contended holder exited {b.returncode}, want the sentinel 9"
        rc_a = a.wait()
        assert rc_a == 0, f"first holder exited {rc_a}, want 0"
        c = subprocess.run(["flock", "-n", "-E", "9", lock, "true"], capture_output=True)
        assert c.returncode == 0, "lock not free after the holder exited"
    print("test_sweep_lock: 3/3 OK (contention -> 9, holder -> 0, release -> free)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
