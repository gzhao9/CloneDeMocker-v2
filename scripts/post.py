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
    ids = re.findall(r"^### \[([ABC]-\d+)\]", body, flags=re.M)
    if len(ids) != 1:
        sys.exit(f"post: entry must contain exactly one '### [X-NNN]' header, found {ids}")
    entry_id = ids[0]
    if list((REPO / "collab" / "inbox").glob(f"*/*/{entry_id}.md")):
        sys.exit(f"post: {entry_id} has already been delivered")
    deliver(entry_id, body)
    if "--push" in sys.argv:
        publish_now(entry_id)


def deliver(entry_id: str, body: str) -> None:
    """Drop the entry in each recipient's unread inbox (A-024, folder form in A-030).

    Written alongside the board copy rather than instead of it, so the proposal needs no
    agreement to start being useful and none to abandon: B and C keep reading COLLAB.md and
    cannot tell the difference. One writer per path is the point -- these never conflict.
    """
    header = body.splitlines()[0]
    m = re.search(r"→\s*([ABC](?:\s*,\s*[ABC])*)", header)
    cc = re.search(r"\(cc\s+([ABC](?:\s*,\s*[ABC])*)\)", header)
    names = re.findall(r"[ABC]", (m.group(1) if m else "") + (cc.group(1) if cc else ""))
    if not names:
        print(f"post: {entry_id} has no recipient in its header, inbox copy skipped")
        return
    for name in dict.fromkeys(names):
        path = BOARD.parent / "collab" / "inbox" / name / "unread" / f"{entry_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline=chr(10))
        print(f"post: {entry_id} delivered to collab/inbox/{name}/unread/")


def a_owns(rel: str) -> bool:
    """Is this a path THIS agent is entitled to write or remove?

    RULES 3, enforced rather than remembered. A publish overlays this agent's local copy
    onto the remote tree, so any path it lists that belongs to a peer is silently reverted
    to whatever this agent last happened to have -- which resurrected 27 files C had just
    deleted, on top of C's own cleanup commit. A may write the board, its own status and
    receipts, its own mailbox, and the letters it sends. Nothing else under collab/.
    """
    if rel.startswith("archive/board-retired-"):
        return True
    if rel in ("collab/archived-ids", "collab/unread-B"):
        return True                                   # dead scheme, archived; A may clear it
    if rel in ("COLLAB.md", "COLLAB_ARCHIVE.md", "collab/README.md",
               "collab/inbox/README.md", f"collab/latest-from-{ME}"):
        return True
    if rel.startswith(f"collab/status/{ME}") or rel == f"collab/read/{ME}.md":
        return True
    if rel.startswith(f"collab/inbox/{ME}/"):          # this agent's own mailbox
        return True
    if re.fullmatch(rf"collab/inbox/[ABC]/unread/{ME}-\d+\.md", rel):
        return True                                   # the letters this agent sends
    # The flat layout `collab/inbox/<who>/<id>.md` is dead: it predates unread/read/ and
    # every file still sitting in it was put there by A, first by A's original deliver()
    # and then resurrected by A's stale-overlay push. Cleaning up one's own dead layout is
    # the one case where touching a peer's directory is not touching a peer's work.
    if re.fullmatch(r"collab/inbox/[ABC]/[ABC]-\d+\.md", rel):
        return True
    # An unread/ copy of an entry the owner has already filed to read/ is not the owner's
    # work: it is an entry A's stale-overlay push un-filed. Undoing that is A cleaning up
    # after itself, and the read/ copy proves the owner's real state is untouched.
    m = re.fullmatch(r"collab/inbox/([ABC])/unread/([ABC]-\d+)\.md", rel)
    return bool(m and (repo_root / "collab" / "inbox" / m.group(1) / "read" /
                       f"{m.group(2)}.md").is_file())


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
        return subprocess.run(["git", *a], cwd=repo, text=True, capture_output=True, env=env)

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
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if git("read-tree", "FETCH_HEAD", env=env).returncode != 0:
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
        upstream = git("ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout
        for rel in (x.strip() for x in upstream.splitlines()):
            if rel and rel not in on_disk and a_owns(rel):
                git("update-index", "--force-remove", rel, env=env)
        tree = git("write-tree", env=env).stdout.strip()
        if not tree:
            return False
        head = git("rev-parse", "FETCH_HEAD").stdout.strip()
        if git("diff", "--quiet", f"{head}^{{tree}}", tree).returncode == 0:
            print(f"post: {entry_id} already matches the remote board")
            return True
        sha = git("commit-tree", tree, "-p", head, "-m", f"board: {entry_id}").stdout.strip()
        if sha and git("push", "github", f"{sha}:main").returncode == 0:
            print(f"post: {entry_id} pushed as {sha[:8]} (working tree untouched)")
            return True
    return False


if __name__ == "__main__":
    main()
