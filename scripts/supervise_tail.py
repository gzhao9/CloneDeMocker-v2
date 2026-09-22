"""Keep the CloudStack tail runner alive across a multi-day run.

An agent session watching the batch is not a resilience strategy: the watch expires, the
session ends, and nobody is awake at 04:00. This supervisor is a detached process that
restarts the runner whether or not anyone is looking.

Two deliberate limits:

  * **It gives up after 3 restarts inside 10 minutes.** Dying that fast means something a
    restart cannot fix, and a night spent crash-looping is worse than a night stopped — it
    burns tokens and fills the dataset with half-finished work. On giving up it writes
    `STOPPED` into its log and a marker file, so the condition is visible rather than silent.
  * **It never runs git.** The runner already shares the working tree with whatever session
    is open, and that sharing is exactly what produces stuck rebases and stale locks. Adding
    a third writer would make the problem it exists to survive more likely, so the audit
    trail here is a plain log file.

Usage:
    python scripts/supervise_tail.py            # runs until the batch finishes
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
RUNNER = REPO / "scripts" / "run_cloudstack_tail.py"
RUN_LOG = REPO / "validation" / "results" / "cloudstack-tail-run.log"
SUP_LOG = REPO / "validation" / "results" / "cloudstack-tail-supervisor.log"
STOPPED = REPO / "validation" / "results" / "cloudstack-tail-SUPERVISOR-STOPPED"

POLL_SECONDS = 60
MAX_RESTARTS = 3
WINDOW_SECONDS = 600
DONE_MARKERS = ("nothing left in the tail", "session done")


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with SUP_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def batch_finished() -> bool:
    try:
        tail = RUN_LOG.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return False
    return any(marker in tail for marker in DONE_MARKERS)


def start() -> subprocess.Popen:
    handle = RUN_LOG.open("a", encoding="utf-8")
    return subprocess.Popen([str(PYTHON), str(RUNNER)], cwd=REPO,
                            stdout=handle, stderr=subprocess.STDOUT)


def main() -> None:
    STOPPED.unlink(missing_ok=True)
    restarts: list[float] = []
    process = start()
    log(f"supervisor up; runner pid {process.pid}")

    while True:
        time.sleep(POLL_SECONDS)

        if process.poll() is None:
            continue

        if batch_finished():
            log("runner exited after finishing the tail; supervisor done")
            return

        now = time.time()
        restarts = [t for t in restarts if now - t < WINDOW_SECONDS] + [now]
        if len(restarts) > MAX_RESTARTS:
            log(f"STOPPED: {len(restarts)} restarts within {WINDOW_SECONDS // 60} min — "
                f"a restart is not going to fix this, so not crash-looping overnight")
            STOPPED.write_text(
                f"{datetime.now().isoformat(timespec='seconds')} "
                f"supervisor gave up after {len(restarts)} rapid restarts\n", encoding="utf-8")
            return

        process = start()
        log(f"runner had exited (code {process.returncode}); restarted as pid {process.pid} "
            f"[{len(restarts)} restart(s) in the last {WINDOW_SECONDS // 60} min]")


if __name__ == "__main__":
    sys.exit(main())
