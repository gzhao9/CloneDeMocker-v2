"""Keep the CloudStack batch running unattended, and say so on the board when it intervenes.

The model session that started the batch is not always awake, and the harness caps a watch at
five minutes — so "an agent is watching" is not a resilience strategy overnight. This is: a
detached loop that notices the runner is gone and starts it again, which works whether or not
anyone is looking.

Restarting is safe because the batch is resumable by construction: an MCI counts as done when
its JSON exists under .clonedemocker/runs/<run>/full-batch-no-pit/, so a restarted runner skips
everything finished and redoes at most the one that was in flight.

Two things it deliberately does not do:
  * restart forever in a tight loop — repeated fast deaths mean something is broken that
    restarting cannot fix, so after MAX_FAST_RESTARTS it stops and posts an urgent NOTE rather
    than burning a night on a crash loop, and
  * stay quiet about it — every restart is recorded on the board, because a run whose gaps are
    invisible cannot be trusted later.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import board  # noqa: E402

SCRATCH = Path(r"C:\Users\lixin\AppData\Local\Temp\claude"
               r"\c--Users-lixin-CloneDeMocker-v2\52f58a04-1bdc-498c-9eae-b41ef885bb8b\scratchpad")
PID_FILE = SCRATCH / "synced.pid"
RUN_LOG = SCRATCH / "live-run.stdout.log"
ERR_LOG = SCRATCH / "live-run.stderr.log"
SUP_LOG = SCRATCH / "supervisor.log"

PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
RUNNER = REPO / "scripts" / "run_cloudstack_synced.py"

POLL_SECONDS = 60
FAST_WINDOW = 600        # two deaths inside ten minutes is a crash loop, not bad luck
MAX_FAST_RESTARTS = 3


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {message}"
    with SUP_LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def alive(pid: int) -> bool:
    if not pid:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         text=True, capture_output=True).stdout
    return str(pid) in out


def read_pid() -> int:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def tail(path: Path, lines: int = 12) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(unavailable)"


def start() -> int:
    with RUN_LOG.open("a", encoding="utf-8") as out, ERR_LOG.open("a", encoding="utf-8") as err:
        process = subprocess.Popen(
            [str(PYTHON), "-u", str(RUNNER), "--board-every", "25"],
            cwd=str(REPO), stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    PID_FILE.write_text(str(process.pid))
    return process.pid


def main() -> None:
    log(f"supervisor up, watching pid {read_pid()}")
    restarts: list[float] = []

    while True:
        time.sleep(POLL_SECONDS)
        pid = read_pid()
        if alive(pid):
            continue

        now = time.time()
        restarts = [t for t in restarts if now - t < FAST_WINDOW] + [now]
        recent_err, recent_out = tail(ERR_LOG, 12), tail(RUN_LOG, 6)

        if len(restarts) > MAX_FAST_RESTARTS:
            log(f"runner died {len(restarts)} times in {FAST_WINDOW}s — giving up")
            try:
                board.post_note(
                    f"B's batch runner died {len(restarts)} times within {FAST_WINDOW // 60} "
                    f"minutes and the supervisor has stopped restarting it. Something is broken "
                    f"that a restart does not fix, so B's half of the run is **stalled** until "
                    f"the session is next active.\n\nLast stderr:\n```\n{recent_err[-600:]}\n```"
                    f"\n\nYour tail is unaffected — the two halves share only the results file, "
                    f"and B's last publish is complete and pushed. If you reach B's frontier "
                    f"before B resumes, just keep going past it.",
                    urgent=True)
            except Exception as error:  # noqa: BLE001 - never let the notice kill the supervisor
                log(f"board post failed: {error}")
            return

        new_pid = start()
        log(f"runner was gone; restarted as {new_pid} (restart {len(restarts)})")
        try:
            board.post_note(
                f"B's batch runner exited unexpectedly and was restarted automatically as pid "
                f"{new_pid}. The batch is resumable — an MCI counts as done once its result file "
                f"exists — so at most the one in flight is redone and nothing published is lost."
                f"\n\nLast output before the exit:\n```\n{recent_out}\n```",
                urgent=False)
        except Exception as error:  # noqa: BLE001
            log(f"board post failed: {error}")


if __name__ == "__main__":
    main()
