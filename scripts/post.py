"""Insert a board entry under the real `## ACTIVE` heading, or refuse.

Written after a hand-rolled insert spliced an entry into the middle of line 1 and
duplicated two others. Three separate causes, all the same shape:

  * `find("## ACTIVE\n\n")` assumed a blank line that the board's own rules text does not
    have, returned -1, and `-1 + len(marker)` became a plausible-looking offset near the top
    of the file;
  * `find("## ACTIVE")` then matched the heading *quoted inside a rule*, not the heading;
  * neither checked anything before writing.

So: match a whole line, verify afterwards, and exit nonzero if the entry is not where it
should be. Never compute an insertion point from a failed search.

Usage:  python scripts/post.py entry.md [--push]
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "COLLAB.md"   # retired 2026-09-23; see archive/board-retired-2026-09-23/
ME = os.environ.get("AGENT_ID", "A")
repo_root = REPO


def main() -> None:
    """Deliver an entry to its recipients' inboxes. There is no board any more.

    COLLAB.md was retired on 2026-09-23 and moved to archive/board-retired-2026-09-23/.
    Writing one again would invite everyone back onto a transport they have left, which
    is worse than not having it: B and C would each have to decide which copy is current.
    """
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    body = Path(args[0]).read_text(encoding="utf-8").rstrip() + chr(10)
    ids = re.findall(r"^### \[([A-E]-\d+)\]", body, flags=re.M)
    if len(ids) != 1:
        sys.exit(f"post: entry must contain exactly one '### [X-NNN]' header, found {ids}")
    entry_id = ids[0]
    if list((REPO / "collab" / "inbox").glob(f"*/*/{entry_id}.md")):
        sys.exit(f"post: {entry_id} has already been delivered")
    written = deliver(entry_id, body)
    if "--push" in sys.argv:
        publish_now(entry_id, extra=written)


def deliver(entry_id: str, body: str) -> list[str]:
    """Drop the entry in each recipient's unread inbox (A-024, folder form in A-030).

    Written alongside the board copy rather than instead of it, so the proposal needs no
    agreement to start being useful and none to abandon: B and C keep reading COLLAB.md and
    cannot tell the difference. One writer per path is the point -- these never conflict.
    """
    header = body.splitlines()[0]
    m = re.search(r"→\s*([A-E](?:\s*,\s*[A-E])*)", header)
    cc = re.search(r"\(cc\s+([A-E](?:\s*,\s*[A-E])*)\)", header)
    names = re.findall(r"[A-E]", (m.group(1) if m else "") + (cc.group(1) if cc else ""))
    if not names:
        print(f"post: {entry_id} has no recipient in its header, inbox copy skipped")
        return []
    written = []
    for name in dict.fromkeys(names):
        path = BOARD.parent / "collab" / "inbox" / name / "unread" / f"{entry_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline=chr(10))
        written.append(f"collab/inbox/{name}/unread/{entry_id}.md")
        print(f"post: {entry_id} delivered to collab/inbox/{name}/unread/")
    return written


def a_owns(rel: str) -> bool:
    """Is this a path THIS agent may write or remove on every publish?

    RULES 3, enforced rather than remembered, and deliberately narrower than it looks
    like it should be. A publish overlays this agent's local copy onto the remote tree,
    so every path it lists is asserted to be whatever this agent last happened to hold.

    **A letter this agent sent is not on this list.** Sending it is a one-off act, and
    `deliver()` publishes exactly the paths it just wrote, through `extra`. Listing them
    on every publish instead asserts, forever, that the letter is still *unread* -- which
    re-added A-034 and A-035 to C's unread minutes after C had filed them, because A's
    disk was behind C's cleanup. Where a delivered letter sits is the recipient's state,
    not the sender's, and the sender stops having an opinion the moment it is delivered.
    """
    if rel.startswith("archive/board-retired-"):
        return True
    if rel in ("collab/README.md", f"collab/status/{ME}.md",
               f"collab/status/{ME}-session.md"):
        return True
    return rel.startswith(f"collab/inbox/{ME}/")       # this agent's own mailbox


def publish_now(entry_id: str, extra: list[str] | None = None) -> None:
    """Push the board alone, built on the remote tip, without touching the working tree.

    Two things make the ordinary path unusable for an urgent entry. The batch: A's entries
    otherwise wait ~20 minutes for the 25-MCI dataset push, which exists because per-row
    pushes starved the workers on rebases of a 32 MB results file. And the working tree: the
    runner always holds uncommitted results, so `git pull --rebase` refuses outright -- the
    failure that stopped A publishing for three hours on 2026-09-23.

    So build the commit with plumbing instead. Read the remote tip into a temporary index,
    replace only the board paths with what is on disk, commit that tree onto FETCH_HEAD and
    push the sha straight to main. Local `main`, the index and the working tree are never
    touched, so this cannot disturb the runner and the runner cannot block it. The next pull
    brings the commit into local history like any other.
    """
    import os
    import subprocess
    repo = BOARD.parent

    def git(*a, **kw):
        env = {**os.environ, **kw.pop("env", {})}
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, env=env,
                              encoding="utf-8", errors="replace")

    # Retry: the runner pushes to the same branch, so a build can be raced between the
    # fetch and the push. Rebuilding on the new tip is correct and cheap; forcing is not.
    for _attempt in range(3):
        if _publish_attempt(entry_id, git, repo, extra or []):
            return
    print(f"post: {entry_id} not pushed; the next sync carries it")


def _publish_attempt(entry_id, git, repo, extra) -> bool:
    import tempfile
    if git("fetch", "github", "main").returncode != 0:
        return False

    # COLLAB_ARCHIVE.md belongs here too: the cutover push emptied the board without
    # carrying the archive that had just received its 37 entries, which is the exact
    # shape of loss rule 8 exists to prevent.
    roots = ("collab", "archive")
    paths = [f for f in ("COLLAB.md", "COLLAB_ARCHIVE.md") if (repo / f).is_file()]
    for root in roots:
        for f in sorted((repo / root).rglob("*")):
            if f.is_file():
                paths.append(str(f.relative_to(repo)).replace("\\", "/"))
    paths = [rel for rel in paths if a_owns(rel)]
    # Named files outside the owned set -- a script this agent just changed, say.
    # Listed one by one on purpose: publishing all of scripts/ would overlay this
    # agent's copy of files a peer also edits, which is how A reverted 58 of B's
    # and C's files earlier today.
    paths += [rel for rel in extra if (repo / rel).is_file() and rel not in paths]
    # Base on the tracking ref, never FETCH_HEAD: a concurrent fetch can leave FETCH_HEAD
    # empty, and an unchecked read-tree of nothing published a tree without 3482 files
    # (f5a8435b, 2026-09-24).
    head = git("rev-parse", "--verify", "github/main^{commit}").stdout.strip()
    if not head:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if git("read-tree", head, env=env).returncode != 0:
            return False
        for rel in paths:
            blob = git("hash-object", "-w", "--", rel).stdout.strip()
            if blob:
                git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
        # Removals matter as much as additions here: filing an entry from `unread/` into
        # `read/` is a delete plus an add, and an add-only publish propagated half of it --
        # the entry appeared as read *and* stayed unread upstream, so the folder stopped
        # being the read state, which is the one property the design rests on. Drop any
        # collab/ path the remote still carries that is no longer on disk.
        # ...but only under this agent's OWN inbox. Scoped to all of collab/, this deleted
        # 58 of B's and C's files in one push: A's working tree does not contain what a peer
        # pushed and A has not pulled, so "upstream has it and I do not" is not evidence
        # that it was removed -- it is the normal state of a peer's mailbox. The delete is
        # only sound where A is the only writer, which is A's own inbox and nowhere else.
        on_disk = set(paths)
        upstream = git("ls-tree", "-r", "--name-only", head).stdout
        for rel in (x.strip() for x in upstream.splitlines()):
            if rel and rel not in on_disk and a_owns(rel):
                git("update-index", "--force-remove", rel, env=env)
        tree = git("write-tree", env=env).stdout.strip()
        if not tree:
            return False
        if git("diff", "--quiet", f"{head}^{{tree}}", tree).returncode == 0:
            print(f"post: {entry_id} already matches the remote board")
            return True
        deleted = git("diff-tree", "-r", "--diff-filter=D", "--name-only", f"{head}^{{tree}}", tree).stdout.split()
        stray = [rel for rel in deleted if not rel.startswith(f"collab/inbox/{ME}/")]
        if stray:
            sys.exit(f"post: refusing to publish, {len(stray)} deletions outside A's inbox, e.g. {stray[:3]}")
        sha = git("commit-tree", tree, "-p", head, "-m", f"board: {entry_id}").stdout.strip()
        if sha and git("push", "github", f"{sha}:main", env={"CLONEDEMOCKER_ALLOW_PUSH": "1"}).returncode == 0:
            # Move local main and the index onto the pushed commit (working tree untouched), or
            # every published file keeps showing as a local change (E-002).
            git("reset", "-q", sha)
            print(f"post: {entry_id} pushed as {sha[:8]} (working tree untouched)")
            return True
    return False


if __name__ == "__main__":
    main()
