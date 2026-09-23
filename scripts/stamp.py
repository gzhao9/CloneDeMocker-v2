"""Stamp a board entry's footer slots, and fail loudly if it did not take.

Written because four separate hand-rolled stamping snippets in one night each failed
silently: a slot name that also appears in an entry's body, an offset invalidated by an
earlier insertion, an entry not yet pulled into the local copy. Every one of them reported
success and changed nothing, and the peer kept seeing the entry as unacknowledged.

The rule this encodes: locate footer slots by searching back from "- done:", because an
entry's body may quote the slot names as an example; and verify by re-reading the file.

Usage:  python scripts/stamp.py A-017 [--note "answered in B-019"]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
BOARD = Path(__file__).resolve().parents[1] / "COLLAB.md"
DEFAULT_ME = os.environ.get("AGENT_ID", "C")


def stamp(entry_id: str, note: str = "", me: str = DEFAULT_ME) -> None:
    text = BOARD.read_text(encoding="utf-8")
    start = text.find(f"### [{entry_id}]")
    if start == -1:
        sys.exit(f"stamp: {entry_id} is not in the local board -- pull first")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for slot, value in ((f"- recv-{me}:", now),
                        (f"- read-by-{me}:", f"{now}{' — ' + note if note else ''}")):
        end = text.find("- done:", start)      # recomputed: an earlier insert shifts offsets
        if end == -1:
            sys.exit(f"stamp: {entry_id} has no '- done:' footer")
        at = text.rfind(slot, start, end)      # backwards: the body may quote the slot name
        if at == -1:
            print(f"  {slot} absent on {entry_id}, skipped")
            continue
        cut = at + len(slot)
        if text[cut:text.find(chr(10), cut)].strip():
            print(f"  {slot} already stamped, left alone")
            continue
        text = text[:cut] + " " + value + text[cut:]

    BOARD.write_text(text, encoding="utf-8", newline="\n")

    check = BOARD.read_text(encoding="utf-8")
    s2 = check.find(f"### [{entry_id}]")
    e2 = check.find("- done:", s2)
    block = check[s2:e2]
    for slot in (f"- recv-{me}:", f"- read-by-{me}:"):
        at = block.rfind(slot)
        if at == -1:
            continue
        if not block[at + len(slot):block.find(chr(10), at)].strip():
            sys.exit(f"stamp: VERIFY FAILED -- {slot} on {entry_id} is still empty after writing")
    print(f"stamp: {entry_id} verified stamped for {me}")
    record_read(entry_id, note, me)


def record_read(entry_id: str, note: str, me: str) -> None:
    """Append the receipt to `collab/read/<me>.md` as well (A-024 amendment 1, B-032).

    B's objection to a local gitignored cursor is the right one: a receipt only does its
    job if the *peer* can see it, and hiding it would make us reinvent acknowledgement as
    extra messages. Kept committed, but single-writer -- only this agent ever writes this
    path -- so it has the inbox's no-conflict property and the board's visibility.
    """
    path = BOARD.parent / "collab" / "read" / f"{me}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if not path.exists():
        path.write_text(f"# Entries {me} has read. Only {me} writes this file.\n\n",
                        encoding="utf-8", newline="\n")
    line = f"{entry_id:<8} {now}{'  ' + note if note else ''}\n"
    if f"\n{entry_id:<8} " not in path.read_text(encoding="utf-8"):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("entry_id")
    ap.add_argument("--note", default="")
    ap.add_argument("--me", default=DEFAULT_ME)
    a = ap.parse_args()
    stamp(a.entry_id, a.note, me=a.me)
