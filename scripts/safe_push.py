"""Publish named files (or file a letter as read) from a checkout that runners write into.

The pair of scripts/safe_pull.py. One short command instead of a hand-typed plumbing sequence:
build the commit on github/main with a private index, add exactly the listed paths, refuse any
deletion except moving your own letter unread/ -> read/, push, and realign local main. The
working tree is never written, so a running lane is never disturbed.

    python scripts/safe_push.py -m "why" path [path ...]      # publish these files as they are on disk
    python scripts/safe_push.py --file-read B-073 [E-004 ...]  # move your letters unread/ -> read/
Your letter is AGENT_ID (default A).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REMOTE = "github"
ME = os.environ.get("AGENT_ID", "A")


def git(*args: str, env: dict | None = None, check: bool = False) -> subprocess.CompletedProcess:
    done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, encoding="utf-8", errors="replace",
                          env={**os.environ, **(env or {})})
    if check and done.returncode:
        sys.exit(f"safe_push: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done


def attempt(paths: list[str], letters: list[str], message: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        idx = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if git("fetch", "-q", REMOTE, "main").returncode:
            return False
        base = git("rev-parse", "--verify", f"{REMOTE}/main^{{commit}}").stdout.strip()
        if not base or git("read-tree", base, env=idx).returncode:
            return False
        allowed_deletes = set()
        for letter in letters:
            unread = f"collab/inbox/{ME}/unread/{letter}.md"
            # The blob is moved as is (its id), never decoded: a text round-trip through the
            # console codec once archived an empty letter (B-074, GBK on Windows).
            blob = git("rev-parse", "--verify", "-q", f"{base}:{unread}").stdout.strip()
            if not blob:
                print(f"safe_push: {unread} is not on {REMOTE}/main; skipped")
                continue
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{unread.replace('/unread/', '/read/')}", env=idx)
            git("update-index", "--force-remove", unread, env=idx)
            allowed_deletes.add(unread)
        for rel in paths:
            blob = git("hash-object", "-w", "--", rel).stdout.strip()
            if not blob:
                sys.exit(f"safe_push: cannot read {rel}")
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=idx)
        tree = git("write-tree", env=idx).stdout.strip()
        deleted = set(git("diff-tree", "-r", "--diff-filter=D", "--name-only", f"{base}^{{tree}}", tree).stdout.split())
        if deleted - allowed_deletes:
            sys.exit(f"safe_push: refused, the commit would delete {sorted(deleted - allowed_deletes)[:3]}")
        if git("diff", "--quiet", f"{base}^{{tree}}", tree).returncode == 0:
            print("safe_push: nothing to publish")
            return True
        sha = git("commit-tree", tree, "-p", base, "-m", message).stdout.strip()
        if not sha or git("push", "-q", REMOTE, f"{sha}:refs/heads/main",
                          env={"CLONEDEMOCKER_ALLOW_PUSH": "1"}).returncode:
            return False
        git("reset", "-q", sha)
        for letter in letters:        # mirror the move on disk, so a later publish cannot re-add it
            unread = REPO / "collab" / "inbox" / ME / "unread" / f"{letter}.md"
            read = REPO / "collab" / "inbox" / ME / "read" / f"{letter}.md"
            git("checkout", "HEAD", "--", read.relative_to(REPO).as_posix())
            unread.unlink(missing_ok=True)
        print(f"safe_push: pushed {sha[:8]}")
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("-m", "--message")
    parser.add_argument("--file-read", nargs="+", default=[], metavar="ID")
    args = parser.parse_args()
    if not args.paths and not args.file_read:
        sys.exit("safe_push: give paths to publish or --file-read IDs")
    message = args.message or (f"{ME}: file {', '.join(args.file_read)} as read" if not args.paths else None)
    if not message:
        sys.exit("safe_push: -m is required when publishing files")
    paths = [Path(p).resolve().relative_to(REPO).as_posix() for p in args.paths]
    for n in range(5):
        if attempt(paths, args.file_read, message):
            return
        time.sleep(3 * (n + 1))
    sys.exit("safe_push: push failed 5 times (the remote kept moving); run it again")


if __name__ == "__main__":
    main()
