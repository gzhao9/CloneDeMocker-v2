"""Collaboration board monitor and autonomous responder helper for Agent C (Remedy).

Monitors COLLAB.md for incoming entries from peers (A and B), logs events,
stamps receipts (recv-C / read-by-C for NOTEs), and alerts on pending REQs.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "COLLAB.md"
ARCHIVE = REPO / "COLLAB_ARCHIVE.md"
LOG_DIR = REPO / "logs"
ALERT_LOG = LOG_DIR / "collab_alerts.log"
MONITOR_LOG = LOG_DIR / "collab_monitor.log"

ME = "C"
PEERS = ("A", "B")
REMOTE = "origin"
BRANCH = "main"

ENTRY_RE = re.compile(r"^### \[([AB])-(\d+)\]\s+([^\n]+)", re.M)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%m-%d %H:%M:%S")
    formatted = f"{timestamp} [C-Monitor] {msg}"
    print(formatted, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with MONITOR_LOG.open("a", encoding="utf-8") as f:
        f.write(formatted + "\n")


def alert(msg: str) -> None:
    log(f"⚠️  ALERT: {msg}")
    with ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now_utc()} {msg}\n")


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=timeout
    )


def fetch_remote() -> bool:
    try:
        res = git("fetch", REMOTE, BRANCH, timeout=90)
        return res.returncode == 0
    except Exception as e:
        log(f"Fetch failed: {e}")
        return False


def get_active_blocks(text: str) -> list[tuple[str, str, str, str]]:
    """Return list of (entry_id, author, header, full_block) in ## ACTIVE."""
    start = text.find("## ACTIVE")
    if start == -1:
        return []
    end = text.find("\n## Section:", start)
    if end == -1:
        end = len(text)
    active_text = text[start:end]

    results = []
    blocks = re.split(r"(?=^### \[)", active_text, flags=re.M)
    for block in blocks:
        m = re.match(r"^### \[([ABC])-(\d+)\]\s+([^\n]+)", block)
        if not m:
            continue
        author = m.group(1)
        seq = m.group(2)
        header = m.group(3)
        entry_id = f"{author}-{seq}"
        results.append((entry_id, author, header, block.strip()))
    return results


def check_and_process_board(auto_stamp: bool = True) -> list[str]:
    """Check COLLAB.md for unread messages targeting C.
    
    Returns list of unhandled REQs requiring attention.
    """
    if not BOARD.is_file():
        return []

    text = BOARD.read_text(encoding="utf-8")
    active_entries = get_active_blocks(text)
    
    pending_reqs = []
    modified = False

    for entry_id, author, header, block in active_entries:
        if author not in PEERS:
            continue

        # Check if addressed to or cc'd to C
        # e.g. "B → C", "B -> C", "cc C", "(cc C)"
        is_for_c = ("→ C" in header or "-> C" in header or "cc C" in header)
        if not is_for_c:
            continue

        is_req = "· REQ" in header
        is_note = "· NOTE" in header or "· REQ-ANSWER" in header

        # Check recv-C
        recv_slot = f"- recv-{ME}:"
        recv_match = re.search(rf"- recv-{ME}:(.*)", block)
        has_recv = recv_match and recv_match.group(1).strip()

        # Check read-by-C
        read_slot = f"- read-by-{ME}:"
        read_match = re.search(rf"- read-by-{ME}:(.*)", block)
        has_read = read_match and read_match.group(1).strip()

        # If not received, stamp receipt
        if not has_recv and auto_stamp:
            stamp_time = now_utc()
            i = text.find(f"### [{entry_id}]")
            end = text.find("- done:", i)
            if i != -1 and end != -1:
                at = text.rfind(recv_slot, i, end)
                if at != -1:
                    cut = at + len(recv_slot)
                    text = text[:cut] + f" {stamp_time}" + text[cut:]
                    modified = True
                    log(f"Stamped receipt for {entry_id}")

        if is_note and not has_read and auto_stamp:
            # Automatic read receipt for one-way NOTE
            stamp_time = now_utc()
            i = text.find(f"### [{entry_id}]")
            end = text.find("- done:", i)
            if i != -1 and end != -1:
                at = text.rfind(read_slot, i, end)
                if at != -1:
                    cut = at + len(read_slot)
                    text = text[:cut] + f" {stamp_time}" + text[cut:]
                    modified = True
                    log(f"Auto-marked NOTE as read: {entry_id} ({header})")

        elif is_req and not has_read:
            pending_reqs.append((entry_id, header))
            alert(f"Pending REQ from {author}: [{entry_id}] {header}")

    if modified:
        BOARD.write_text(text, encoding="utf-8", newline="\n")

    return pending_reqs


def sync_board_if_modified() -> bool:
    """Commit and push COLLAB.md if changed locally."""
    diff = git("diff", "--name-only", "COLLAB.md")
    if "COLLAB.md" not in diff.stdout:
        return True

    log("COLLAB.md modified locally, committing...")
    git("add", "COLLAB.md")
    commit_res = git("commit", "-m", f"C: update board receipts at {now_utc()}")
    if commit_res.returncode != 0:
        log(f"Commit COLLAB.md failed: {commit_res.stderr.strip()}")
        return False

    # Push with rebase
    for attempt in range(1, 4):
        push_res = git("push", REMOTE, BRANCH)
        if push_res.returncode == 0:
            log("Successfully pushed COLLAB.md update")
            return True
        log(f"Push COLLAB.md rejected (attempt {attempt}/3), rebasing...")
        pull_res = git("pull", "--rebase", REMOTE, BRANCH)
        if pull_res.returncode != 0:
            git("rebase", "--abort")
            time.sleep(3 * attempt)
            continue
    return False


def run_daemon(interval: int = 180) -> None:
    log(f"Starting collaboration monitor daemon (interval: {interval}s)...")
    while True:
        try:
            log("Checking for upstream peer messages...")
            fetch_remote()
            reqs = check_and_process_board(auto_stamp=True)
            if reqs:
                log(f"Currently {len(reqs)} active REQs awaiting C's action: {[r[0] for r in reqs]}")
            else:
                log("No pending REQs for C. Board up to date.")
            sync_board_if_modified()
        except Exception as e:
            log(f"Error in monitor cycle: {e}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="COLLAB.md monitor for Agent C")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous background loop")
    parser.add_argument("--interval", type=int, default=180, help="Check interval in seconds (default: 180)")
    parser.add_argument("--check", action="store_true", help="Run once and report status")
    args = parser.parse_args()

    if args.daemon:
        run_daemon(interval=args.interval)
    else:
        log("Performing one-shot board check...")
        fetch_remote()
        reqs = check_and_process_board(auto_stamp=True)
        if reqs:
            print("\n" + "=" * 60)
            print("PENDING REQS REQUIRING C'S RESPONSE:")
            for entry_id, header in reqs:
                print(f"  [{entry_id}] {header}")
            print("=" * 60 + "\n")
        else:
            print("No pending REQs found for C.")
        sync_board_if_modified()


if __name__ == "__main__":
    main()
