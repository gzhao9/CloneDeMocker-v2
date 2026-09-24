"""Watch this agent's inbox for mail. Report it; never answer it, never commit.

Rewritten 2026-09-23, when `COLLAB.md` was retired (see `collab/README.md` and
`archive/board-retired-2026-09-23/`). The previous version read and wrote the board,
and after the board was deleted upstream it kept a stale copy on disk, re-stamped
REQs that had already been answered, and tried to commit and push every three
minutes. Those pushes were rejected only because it was behind: one successful rebase
would have resurrected the retired board, stale stamps and all.

Two rules this version keeps, both learned the hard way today:

  * **It never writes to the repository.** Filing an entry from `unread/` into `read/`
    is the receipt, and a receipt means *understood*, which a 3-minute loop cannot
    claim on its behalf. The monitor tells the session there is mail; acting on it is
    the session's job.
  * **It fetches, it does not pull.** A fetch takes no lock on the working tree, needs
    no clean tree, and cannot conflict, so a monitor can run beside a batch runner that
    always holds uncommitted results. Pulling is for the session that is about to act.

Usage:
    python scripts/collab_monitor.py                 # AGENT_ID or --me picks the inbox
    python scripts/collab_monitor.py --me C --once
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG_DIR = REPO / "logs"
REMOTE = os.environ.get("COLLAB_REMOTE", "github")
BRANCH = "main"
POLL_SECONDS = 180


def log(message: str, log_file: Path) -> None:
    """Write a line to stdout and the log, never raising.

    A monitor that can die of its own logging is worse than a lossy one: on this project
    a single character an ASCII console could not encode killed a multi-day run mid-sync.
    """
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} [{ME}-Monitor] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)
    except OSError:
        pass
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    # utf-8, not the locale codepage: git speaks UTF-8 and a GBK console must not be
    # able to kill a monitor by failing to decode a peer's commit message.
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def unread_ids() -> list[str] | None:
    """Ids sitting in this agent's `unread/` on the remote, or None if the fetch failed."""
    if git("fetch", REMOTE, BRANCH).returncode != 0:
        return None                       # a racing fetch or a network blip; try again later
    listing = git("ls-tree", "-r", "--name-only", "FETCH_HEAD",
                  "--", f"collab/inbox/{ME}/unread")
    if listing.returncode != 0:
        return []
    return sorted(Path(p).stem for p in listing.stdout.split("\n") if p.strip().endswith(".md"))


def summarise(entry_id: str) -> str:
    body = git("show", f"FETCH_HEAD:collab/inbox/{ME}/unread/{entry_id}.md").stdout or ""
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith(("###", "- ")):
            return line[:120]
    return ""


def check(alert_file: Path, log_file: Path) -> list[str]:
    ids = unread_ids()
    if ids is None:
        log("fetch failed; will retry", log_file)
        return []
    if not ids:
        alert_file.unlink(missing_ok=True)
        return []
    log(f"{len(ids)} unread for {ME}: {', '.join(ids)}", log_file)
    lines = [f"[{i}] {summarise(i)}" for i in ids]
    try:
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        alert_file.write_text(
            f"unread for {ME} as of {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n"
            "act on these by moving each file into collab/inbox/"
            f"{ME}/read/ once handled\n\n" + "\n".join(lines) + "\n",
            encoding="utf-8", newline="\n")
    except OSError:
        pass
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--me", default=os.environ.get("AGENT_ID", "C"),
                        help="which inbox to watch (A, B or C)")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS)
    args = parser.parse_args()

    global ME
    ME = args.me
    alert_file = REPO / "validation" / "results" / f"collab_unread_for_{ME}.txt"
    log_file = LOG_DIR / "collab_monitor.log"

    log(f"watching collab/inbox/{ME}/unread every {args.interval}s "
        f"(fetch only; this process never commits)", log_file)
    while True:
        try:
            check(alert_file, log_file)
        except Exception as exc:                     # a monitor must outlive its own bugs
            log(f"check failed: {type(exc).__name__}: {exc}", log_file)
        if args.once:
            return
        time.sleep(args.interval)


ME = os.environ.get("AGENT_ID", "C")

if __name__ == "__main__":
    main()
