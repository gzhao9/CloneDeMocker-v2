"""Regenerate COLLAB.md from collab/inbox/** and collab/read/*.md.

This is the switch commit A-026 named: once this lands, COLLAB.md stops being transport and
becomes derived output. The point is not tidiness. A generated file cannot have a merge
conflict that loses information -- the resolution is `git checkout --theirs COLLAB.md` and
run this again -- which is what A-024 was reaching for and what B got wrong twice:

  * `_merge_board()` split on `(?=^### \\[B-)`, so each chunk ran to the next B entry and
    carried A's and C's entries inside it; restoring one B block reinstated theirs, fighting
    A's archiving until ACTIVE held 11 entries with 3 duplicates;
  * an earlier hand-rolled insert computed an offset from a `find()` that returned -1 and
    spliced an entry into the middle of line 1.

Neither is expressible here: nothing is patched, the file is rebuilt from single-writer
sources every time.

Message bodies come from `collab/inbox/<recipient>/<sender>-<seq>.md` (only the sender writes
that path). Read state comes from `collab/read/<agent>.md` (only that agent writes it) and is
rendered into the entry, replacing the `- recv-X:/- read-by-X:` slots that used to be stamped
in place by whoever read the entry -- the shared-write that made stamping lossy.

Usage:
  python scripts/regen_board.py            # rebuild COLLAB.md, verify, report
  python scripts/regen_board.py --check    # verify only, write nothing (exit 1 on drift)
  python scripts/regen_board.py --backfill # first run: copy this agent's COLLAB.md entries
                                           #   into collab/inbox/, then rebuild
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "COLLAB.md"
ARCHIVE = ROOT / "COLLAB_ARCHIVE.md"
COLLAB = ROOT / "collab"
INBOX = COLLAB / "inbox"
READ = COLLAB / "read"
ARCHIVED_IDS = COLLAB / "archived-ids"

TITLE = "# COLLAB — cross-machine coordination board / 跨机协作留言板"
HEADER_RE = re.compile(r"^### \[([ABC]-\d+)\]\s*(.*)$", flags=re.M)
STAMP_RE = re.compile(r"^- (?:recv|read-by)-[ABC]:.*$\n?", flags=re.M)
DONE_RE = re.compile(r"^- done:.*$\n?", flags=re.M)
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})")
# "B → C (cc A)" / "A → B (cc C)" -- recipients are the arrow target plus any cc
ROUTE_RE = re.compile(r"·\s*([ABC])\s*→\s*([ABC])\s*(?:\(cc\s*([ABC](?:\s*,\s*[ABC])*)\))?")


def _sort_key(header: str) -> str:
    m = TS_RE.search(header)
    return f"{m.group(1)} {m.group(2)}" if m else "0000-00-00 00:00"


def _strip_footer(body: str) -> str:
    """Drop the in-place stamp slots. Read state is rendered from receipts instead."""
    return DONE_RE.sub("", STAMP_RE.sub("", body)).rstrip()


def archived_ids() -> set[str]:
    if not ARCHIVED_IDS.is_file():
        return set()
    return {ln.strip() for ln in ARCHIVED_IDS.read_text(encoding="utf-8").splitlines() if ln.strip()}


def collect_messages() -> tuple[dict[str, str], list[str]]:
    """id -> body, from every inbox file. The same id appears once per recipient."""
    bodies: dict[str, str] = {}
    warnings: list[str] = []
    for path in sorted(INBOX.rglob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8").strip()
        ids = HEADER_RE.findall(text)
        if len(ids) != 1:
            warnings.append(f"{path}: expected one entry header, found {len(ids)}")
            continue
        entry_id = ids[0][0]
        prev = bodies.get(entry_id)
        if prev is None:
            bodies[entry_id] = text
        elif _strip_footer(prev) != _strip_footer(text):
            # Copies must agree; picking one silently is how a board loses an edit.
            warnings.append(f"{entry_id}: copies differ between recipients; kept the longer")
            if len(text) > len(prev):
                bodies[entry_id] = text
    return bodies, warnings


def collect_receipts() -> dict[str, list[tuple[str, str, str]]]:
    """id -> [(reader, timestamp, note)], from each agent's own read file."""
    receipts: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    if not READ.is_dir():
        return receipts
    for path in sorted(READ.glob("*.md")):
        reader = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([ABC]-\d+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*(.*)$", line)
            if m:
                receipts[m.group(1)].append((reader, m.group(2), m.group(3).strip()))
    return receipts


def render(bodies: dict[str, str], receipts: dict, rules: str) -> str:
    archived = archived_ids()
    active = sorted((i for i in bodies if i not in archived), key=lambda i: _sort_key(bodies[i]),
                    reverse=True)
    out = [TITLE, "", rules.rstrip(), "", "## ACTIVE", ""]
    for entry_id in active:
        out.append(_strip_footer(bodies[entry_id]))
        for reader, when, note in sorted(receipts.get(entry_id, [])):
            out.append(f"- read-by-{reader}: {when}" + (f" — {note}" if note else ""))
        out.append("")
    archived_here = sorted((i for i in bodies if i in archived), reverse=True)
    if archived_here:
        out += ["## ARCHIVED — bodies in COLLAB_ARCHIVE.md / collab/inbox", "",
                ", ".join(archived_here), ""]
    return "\n".join(out).rstrip() + "\n"


def preserved_rules() -> str:
    """`## RULES` is authored, not derived, so it survives verbatim."""
    if not BOARD.is_file():
        sys.exit("regen: no COLLAB.md to take ## RULES from")
    text = BOARD.read_text(encoding="utf-8")
    lines = text.split("\n")
    starts = [i for i, l in enumerate(lines) if l.startswith("## RULES")]
    if len(starts) != 1:
        sys.exit(f"regen: expected exactly one '## RULES' heading, found {len(starts)}")
    ends = [i for i, l in enumerate(lines) if l.strip() == "## ACTIVE" and i > starts[0]]
    if not ends:
        sys.exit("regen: no '## ACTIVE' heading after '## RULES'")
    return "\n".join(lines[starts[0]:ends[0]])


def backfill(agent: str) -> int:
    """Copy this agent's own entries out of COLLAB.md into collab/inbox/<recipient>/.

    Only needed once, for entries written before the switch. Recipients come from the entry's
    own route line, so a `B → C (cc A)` entry lands in both C's and A's inbox.
    """
    written = 0
    for source in (BOARD, ARCHIVE):
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        marks = [(m.start(), m.group(1), m.group(0)) for m in HEADER_RE.finditer(text)]
        for idx, (pos, entry_id, header) in enumerate(marks):
            if not entry_id.startswith(f"{agent}-"):
                continue
            end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
            body = text[pos:end].rstrip() + "\n"
            route = ROUTE_RE.search(header)
            if not route:
                print(f"  {entry_id}: no route in header, skipped")
                continue
            targets = {route.group(2)}
            if route.group(3):
                targets |= {t.strip() for t in route.group(3).split(",")}
            for target in sorted(targets - {agent}):
                dest = INBOX / target / f"{entry_id}.md"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.is_file():
                    dest.write_text(body, encoding="utf-8", newline="\n")
                    written += 1
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; write nothing")
    ap.add_argument("--backfill", metavar="AGENT", nargs="?", const="B",
                    help="first copy AGENT's COLLAB.md entries into collab/inbox/")
    args = ap.parse_args()

    if args.backfill:
        n = backfill(args.backfill)
        print(f"regen: backfilled {n} inbox file(s) for {args.backfill}")

    rules = preserved_rules()
    bodies, warnings = collect_messages()
    if not bodies:
        sys.exit("regen: no messages found under collab/inbox -- refusing to write an empty board")
    for w in warnings:
        print(f"  warning: {w}")

    rendered = render(bodies, collect_receipts(), rules)

    if args.check:
        drift = rendered != BOARD.read_text(encoding="utf-8")
        print("regen: COLLAB.md is " + ("STALE (run without --check)" if drift else "up to date"))
        sys.exit(1 if drift else 0)

    BOARD.write_text(rendered, encoding="utf-8", newline="\n")

    # Verify against the file on disk, not the string we just built: every message present
    # exactly once, and "## RULES" not duplicated by a body that quotes the heading.
    check = BOARD.read_text(encoding="utf-8")
    found = HEADER_RE.findall(check)
    seen = [i for i, _ in found]
    dupes = {i for i in seen if seen.count(i) > 1}
    if dupes:
        sys.exit(f"regen: VERIFY FAILED -- duplicate entries after writing: {sorted(dupes)}")
    missing = sorted(set(bodies) - archived_ids() - set(seen))
    if missing:
        sys.exit(f"regen: VERIFY FAILED -- messages lost: {missing}")
    if len([l for l in check.split("\n") if l.strip() == "## ACTIVE"]) != 1:
        sys.exit("regen: VERIFY FAILED -- '## ACTIVE' is not a single heading line")
    print(f"regen: {len(seen)} active, {len(archived_ids())} archived, verified")


if __name__ == "__main__":
    main()
