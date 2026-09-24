"""Check that no published result was lost to another machine's push (git history audit).

A results file can lose rows without any conflict: a push of an older, shorter copy
*replaces* the newer one (this happened on 2026-09-23 when 93 rows vanished). So for every
refactoring-results.json on origin/main, collect every mciId that any commit in its history
ever added, and compare with the rows present now. Also list diff files that were deleted
and never restored. Read-only.

    python scripts/audit_overwrites.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REF = "github/main"
ADDED = re.compile(r'^\+\s*"mciId":\s*"(.*)",?\s*$')


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True).stdout.decode("utf-8", "replace")


def main() -> None:
    subprocess.run(["git", "fetch", "-q", "github", "main"], cwd=REPO)
    files = [p for p in git("ls-tree", "-r", "--name-only", REF, "--", "data").split()
             if p.endswith("/refactoring-results.json") and "/_" not in p]
    bad = 0
    for path in files:
        now = set(json.loads(git("show", f"{REF}:{path}") or "{}").get("results", {}))
        ever: set[str] = set()
        for line in git("log", REF, "-p", "--unified=0", "--format=", "--", path).splitlines():
            m = ADDED.match(line)
            if m:
                ever.add(json.loads(f'"{m.group(1)}"'))
        lost = sorted(ever - now)
        print(f"{path.split('/')[1]:26}{path.split('/')[3]:30} now {len(now):5}  ever {len(ever):5}  LOST {len(lost)}")
        for k in lost[:10]:
            print(f"      lost: {k}")
        bad += len(lost)
        base = path.rsplit("/", 1)[0] + "/diffs"
        deleted = set(git("log", REF, "--diff-filter=D", "--name-only", "--format=", "--", base).split())
        existing = set(git("ls-tree", "-r", "--name-only", REF, "--", base).split())
        gone = sorted(deleted - existing)
        if gone:
            print(f"      diff files deleted and not restored: {len(gone)}  e.g. {gone[:3]}")
    print(f"TOTAL lost rows: {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
