"""Publish the CloudStack dataset to main, surviving a teammate pushing to the same branch.

The dataset files (`refactoring-results.json` / `.csv`) are *generated*, so resolving a rebase
conflict on them by picking a side is both error-prone and unnecessary — and picking the wrong
side silently loses MCIs. (During a rebase `--ours` is the upstream being replayed onto, the
inverse of a merge; that inversion already cost four MCIs once.)

So this never picks a side. On conflict it takes whatever is there to get the rebase moving,
then regenerates the dataset from `.clonedemocker/runs/<run>/full-batch-no-pit/`, which is the
only real record of which MCIs ran. The result is the same no matter how the conflict resolved.

Usage:  python scripts/publish_cloudstack.py [--attempts 8]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.trim_diagnostics import trim_entry  # noqa: E402
from studio import canonical_store  # noqa: E402

SOURCE_REPO = Path(r"C:\Users\lixin\CloneDeMocker-v2")
RUN_ID = "ad6456bed97547a197a4c9353766c0f6"
BATCH_DIR = SOURCE_REPO / ".clonedemocker" / "runs" / RUN_ID / "full-batch-no-pit"
PROPOSALS = SOURCE_REPO / ".clonedemocker" / "runs" / RUN_ID / "refactoring"
PROJECT = "cloudstack"
MODEL = "gpt-5.6-terra"
GIT_NAME = "Caralll"
GIT_EMAIL = "lixinyi0823@gmail.com"


def git(*args: str, check: bool = False, timeout: int = 900) -> subprocess.CompletedProcess:
    # See run_cloudstack_synced.git(): the locale codec here is GBK and the repo is UTF-8.
    return subprocess.run(
        ["git", "-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}", *args],
        cwd=REPO, text=True, capture_output=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout)


AGENT = "B"
PLATFORM = "windows"


def _existing_results() -> dict:
    """Whatever is currently on disk for this setup, or {} if there is none yet."""
    label = canonical_store.setup_label(canonical_store.HARNESS_CLONEDEMOCKER, MODEL, False)
    path = (canonical_store.setup_directory(REPO, PROJECT, label) / "refactoring-results.json")
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("results", {})
    except (OSError, json.JSONDecodeError):
        return {}


def regenerate() -> dict:
    """Rebuild this agent's entries in data/<project>/ from the batch output. Idempotent.

    Every entry carries `producedBy` and `platform`, which A-008 requires of any agent
    writing here: a third agent now retries failures on Linux, and re-running only the
    failures on a friendlier platform raises the success rate by a procedure never applied
    to the entries that already passed. That is only defensible if each result records where
    it ran, so the mixed provenance can be reported rather than quietly inherited.

    Crucially, an mciId whose stored entry was produced by someone else is left alone.
    canonical_store.merge overwrites by mciId, and this function offers all ~224 of B's
    entries on every push (roughly every two minutes) -- so without this guard B would revert
    C's Linux retry of any MCI in B's own range within minutes of it landing, every time,
    and C could never make progress on them.
    """
    stored = _existing_results()
    entries, diffs, counts = [], {}, Counter()
    for path in sorted(BATCH_DIR.glob("0*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result = record.get("result")
        if not result:
            continue
        mci_id = record["mciId"]
        owner = (stored.get(mci_id) or {}).get("producedBy")
        if owner and owner != AGENT:
            counts[stored[mci_id].get("classification", "?")] += 1
            continue   # another agent's result for this MCI supersedes ours; do not revert it
        entry = trim_entry(canonical_store.entry_from_agent_result(mci_id, result))
        entry["producedBy"] = AGENT
        entry["platform"] = PLATFORM
        entries.append(entry)
        counts[entry["classification"]] += 1
        proposal_id = result.get("proposalId")
        if proposal_id:
            diff = PROPOSALS / proposal_id / "changes.diff"
            if diff.is_file() and diff.stat().st_size:
                diffs[mci_id] = diff
    summary = canonical_store.merge(
        project=PROJECT, repository_root=REPO, entries=entries, detection_source=None,
        diff_lookup=diffs, model=MODEL, harness=canonical_store.HARNESS_CLONEDEMOCKER,
        use_mock=False)
    summary["counts"] = dict(counts)
    return summary


def clear_conflicts() -> None:
    """Resolve toward upstream, then let regenerate() layer our own MCIs back on top.

    This direction matters now that both agents write data/cloudstack/. During a rebase
    "--ours" is upstream (the inverse of a merge), so this keeps the teammate's entries, and
    canonical_store.merge() then re-adds ours by mciId without touching theirs. Taking our own
    side instead would regenerate a file containing only our MCIs and silently delete theirs --
    the same class of loss that already cost four MCIs, just pointed the other way.
    """
    for path in git("diff", "--name-only", "--diff-filter=U").stdout.split():
        if path.endswith("COLLAB.md"):
            _merge_board()          # losing a board entry is unrecoverable; see below
        else:
            git("checkout", "--ours", "--", path)
        git("add", "--", path)


def _merge_board() -> None:
    """Resolve a COLLAB.md conflict without discarding either side's entries.

    Taking upstream is right for the dataset because regenerate() re-adds our rows straight
    after. Nothing regenerates the board, so the same rule there is pure loss: entry B-005,
    B's answer to A-007, was destroyed exactly this way and is not in any commit. A asked
    three hours later why the REQ had gone unanswered.

    Upstream wins for the file, then any of our own entry blocks missing from it are put back
    at the top of ACTIVE.
    """
    ours = git("show", ":2:COLLAB.md").stdout      # during a rebase :2 is upstream
    mine = git("show", ":3:COLLAB.md").stdout      # :3 is the commit being replayed
    if not ours or not mine:
        git("checkout", "--ours", "--", "COLLAB.md")
        return
    blocks = re.split(r"(?=^### \[B-)", mine, flags=re.M)
    missing = [b for b in blocks
               if b.startswith("### [B-")
               and b.split("]")[0] + "]" not in ours]
    # Our own entries are not the only thing we write to the board: we also stamp recv-B and
    # read-by-B on *other agents'* entries. Restoring only "### [B-" blocks drops those, so a
    # peer keeps seeing an entry as unreceived and re-asks -- which happened three times before
    # this was found. Carry any stamp that is filled on our side and empty upstream.
    for slot in ("- recv-B:", "- read-by-B:"):
        for m in re.finditer(rf"^{re.escape(slot)}[ 	]*(\S.*)$", mine, flags=re.M):
            value = m.group(1).strip()
            head = mine.rfind("### [", 0, m.start())
            if head == -1:
                continue
            entry_id = mine[head + 5:mine.find("]", head)]
            at = ours.find(f"### [{entry_id}]")
            if at == -1:
                continue
            stop = ours.find("- done:", at)
            j = ours.rfind(slot, at, stop if stop != -1 else len(ours))
            if j == -1:
                continue
            k = j + len(slot)
            if not ours[k:ours.find(chr(10), k)].strip():
                ours = ours[:k] + " " + value + ours[k:]

    if missing:
        marker = "## ACTIVE" + chr(10) + chr(10)
        at = ours.find(marker)
        at = at + len(marker) if at != -1 else 0
        ours = ours[:at] + "".join(missing) + ours[at:]
        print(f"    board: restored {len(missing)} of our entries the rebase would have dropped",
              flush=True)
    (REPO / "COLLAB.md").write_text(ours, encoding="utf-8", newline=chr(10))


def recover_repo() -> None:
    """Clear a rebase/merge left half-finished by anyone, and a stale index lock.

    The batch shares this working tree with whatever else touches the repo -- an agent
    session running a manual publish, a git command interrupted when a session ends or a
    token budget runs out. An abandoned rebase makes every later push fail, so the batch
    would keep computing MCIs while silently publishing none of them. Clearing it here means
    the unattended half recovers on its own instead of waiting for a human.

    Aborting is safe because nothing of value lives in the working tree: the batch output is
    the source of truth and regenerate() rebuilds data/ from it on the next line.
    """
    git_dir = REPO / ".git"
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        print("    recover: abandoned rebase found, aborting it", flush=True)
        git("rebase", "--abort")
    if (git_dir / "MERGE_HEAD").exists():
        print("    recover: abandoned merge found, aborting it", flush=True)
        git("merge", "--abort")
    lock = git_dir / "index.lock"
    try:
        # Only a lock with no live git behind it; 5 minutes is far longer than any command here.
        if lock.exists() and time.time() - lock.stat().st_mtime > 300:
            print("    recover: stale index.lock, removing", flush=True)
            lock.unlink()
    except OSError:
        pass


def publish(message: str | None = None, attempts: int = 8, quiet: bool = False) -> bool:
    """Regenerate, commit and push. Returns True once the push lands.

    Safe to call after every MCI: regenerate() is idempotent, and a failed push leaves the
    local commits intact for the next call to carry forward.
    """
    recover_repo()
    for attempt in range(1, attempts + 1):
        summary = regenerate()
        git("add", "--", f"data/{PROJECT}", "COLLAB.md")
        if git("diff", "--cached", "--quiet").returncode:
            text = message or (
                f"Publish CloudStack dataset: {summary['totalMcis']} MCIs, "
                f"{summary['successes']} SUCCESS ({summary['successRate']:.1%})")
            git("commit", "-q", "-m",
                f"{text}\n\nRegenerated from the batch output so the count is independent of how\n"
                f"any rebase conflict on the generated results file happened to resolve.\n\n"
                f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>")

        if git("push", "origin", "main").returncode == 0:
            if not quiet:
                print(f"    pushed (attempt {attempt}): {summary['totalMcis']} MCIs, "
                      f"{summary['successes']} SUCCESS ({summary['successRate']:.1%})", flush=True)
            return True

        if not quiet:
            print(f"    attempt {attempt}: rejected, rebasing onto teammate's work", flush=True)
        if git("pull", "--rebase", "origin", "main").returncode != 0:
            for _ in range(20):
                clear_conflicts()
                step = git("rebase", "--continue")
                blob = step.stdout + step.stderr
                if "Successfully rebased" in blob or "no rebase in progress" in blob:
                    break
                if not git("diff", "--name-only", "--diff-filter=U").stdout.strip():
                    if git("rebase", "--skip").returncode != 0:
                        break
        time.sleep(2 * attempt)

    print("    publish: out of attempts; local commits intact, next MCI carries them", flush=True)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--message", default=None)
    args = parser.parse_args()
    if not publish(message=args.message, attempts=args.attempts):
        sys.exit(1)


if __name__ == "__main__":
    main()
