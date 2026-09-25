"""`git pull` for a checkout that runners are writing into.

A plain pull refuses (or, with --autostash, once stashed live rows away) because the lanes keep
the shared results files dirty. Those results files are one big JSON per dataset that every
host writes, so a merge would conflict on every pull. This brings everything else up to date
and leaves the live files to the lanes' own sync (which merges them row by row):

  1. fetch, then move local main + index to github/main (mixed reset: the working tree is untouched);
  2. overwrite code and board paths from github/main: nobody edits these in place while a
     lane runs, so the remote copy is the current one;
  3. restore files that exist on the remote but not on disk (peers' diffs, results, letters);
  4. delete board letters on disk that the remote has filed away (unread/ copies of read/ ones).

What is left in `git status` afterwards is this host's own unpublished work.

    python scripts/safe_pull.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REMOTE = "github"
CODE_AND_BOARD = ("baseline_v1", "scripts", "studio", "tests", "collab", "archive")


def git(*args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if check and done.returncode:
        sys.exit(f"safe_pull: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def main() -> None:
    git("fetch", "-q", REMOTE, "main")
    git("reset", "-q", f"{REMOTE}/main")
    present = [p for p in CODE_AND_BOARD if git("ls-tree", "--name-only", "HEAD", p, check=False).strip()]
    if present:
        git("checkout", "HEAD", "--", *present)
    missing = [p for p in git("ls-files", "-d").splitlines() if p]
    for start in range(0, len(missing), 200):
        git("checkout", "HEAD", "--", *missing[start:start + 200])
    # A modified file whose content equals an earlier committed version of it is a stale copy
    # (a peer pushed a newer one), not local work: take the remote's.
    stale = []
    for line in git("status", "--porcelain").splitlines():
        if not line.startswith(" M "):
            continue
        rel = line[3:].strip().strip('"')
        blob = git("hash-object", "--", rel, check=False).strip()
        history = git("log", "--format=%H", "-n", "200", "HEAD", "--", rel, check=False).split()
        if blob and any(git("rev-parse", f"{c}:{rel}", check=False).strip() == blob for c in history[1:]):
            stale.append(rel)
    if stale:
        git("checkout", "HEAD", "--", *stale)
    tracked = set(git("ls-files", "collab").splitlines())
    removed = 0
    for path in (REPO / "collab" / "inbox").glob("*/unread/*.md"):
        rel = path.relative_to(REPO).as_posix()
        if rel not in tracked and rel.replace("/unread/", "/read/") in tracked:
            path.unlink()
            removed += 1
    left = [line for line in git("status", "--porcelain").splitlines() if line]
    print(f"safe_pull: at {git('rev-parse', '--short', 'HEAD').strip()}; restored {len(missing)} missing, "
          f"refreshed {len(stale)} stale copies, removed {removed} stale letters; {len(left)} local changes remain (this host's unpublished work)")


if __name__ == "__main__":
    main()
