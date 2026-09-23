"""Run the CloudStack MCI batch, publishing each completed MCI to GitHub as it lands.

One MCI at a time: run it, merge the (diagnostics-trimmed) result into data/cloudstack/,
commit, and push. A teammate agent pushes to the same branch from another machine, so every
push is preceded by a rebase and retried — a rejection here is routine, not an error.

Resumable in two independent ways, which is what makes a multi-day run survivable:
  * the batch's own per-MCI JSON under .clonedemocker/runs/<run>/full-batch-no-pit/ is the
    source of truth for "was this MCI run" (a file existing means skip), and
  * data/ is rebuilt by merging, so a lost push costs nothing but a re-push.

Usage:
    python scripts/run_cloudstack_synced.py               # forward, from the first unfinished
    python scripts/run_cloudstack_synced.py --reverse     # backward, from the last unfinished
    python scripts/run_cloudstack_synced.py --stop-after 50
    python scripts/run_cloudstack_synced.py --board-every 25
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import board  # noqa: E402
from scripts import publish_cloudstack as publish_module  # noqa: E402
from scripts.publish_cloudstack import publish  # noqa: E402
from scripts.trim_diagnostics import trim_entry  # noqa: E402
from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402

# The detection run and the batch output live in the original working copy; this clean clone is
# the publishing side. Keeping them separate means a re-clone never costs us batch progress.
SOURCE_REPO = Path(r"C:\Users\lixin\CloneDeMocker-v2")
RUN_ID = "ad6456bed97547a197a4c9353766c0f6"
BATCH_DIR = SOURCE_REPO / ".clonedemocker" / "runs" / RUN_ID / "full-batch-no-pit"
PROPOSALS = SOURCE_REPO / ".clonedemocker" / "runs" / RUN_ID / "refactoring"
WORKSPACE_ID = "cloudstack-full-no-pit"
PROJECT = "cloudstack"
MODEL = "gpt-5.6-terra"

GIT_NAME = "Caralll"
GIT_EMAIL = "lixinyi0823@gmail.com"
BOARD = REPO / "COLLAB.md"
SECTION = "## Section: agent-cloudstack-master"


def safe_name(index: int, mci_id: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9_.-]+", "_", mci_id).strip("_")
    return f"{index:04d}-{compact[:120]}.json"


def git(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    command = ["git", "-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}", *args]
    # encoding is explicit: text=True decodes with the locale codec, which is GBK on this
    # host, and COLLAB.md is UTF-8 -- `git show origin/main:COLLAB.md` would raise
    # UnicodeDecodeError and kill the board sync.
    return subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                          encoding="utf-8", errors="replace", check=check, timeout=timeout)


def push_with_rebase(message: str, paths: list[str], attempts: int = 6,
                     push: bool = True) -> bool:
    """Commit the given paths and push, rebasing onto the teammate's work on rejection.

    Returns False rather than raising: a failed push must never abort the batch, because the
    next MCI's push carries the same content forward anyway.

    With push=False the commit is still made, so per-MCI history is preserved and a later
    single push publishes the whole run at once. This is the mode to use while the other agent
    is still force-pushing main, since anything pushed into that would be silently destroyed.
    """
    if not push:
        git("add", "--", *paths, check=False)
        if not git("diff", "--cached", "--quiet", check=False).returncode:
            return True  # nothing staged
        git("commit", "-q", "-m", message, check=False)
        return True

    # publish() regenerates the dataset from the batch output after any rebase, so the published
    # counts never depend on which side a conflict on the generated results file resolved to.
    # Picking a side here is what silently dropped four MCIs earlier in this run.
    return publish(message=message, attempts=attempts, quiet=False)


def verify_landed(path: str) -> bool:
    git("fetch", "origin", "main", check=False)
    listed = git("ls-tree", "origin/main", "--name-only", "--", path, check=False)
    return bool(listed.stdout.strip())


def _launch_failed(result: dict) -> bool:
    """True when the harness never actually ran Maven, rather than running it and failing."""
    harness = result.get("harness") or {}
    for side in ("baseline", "candidate"):
        section = harness.get(side) or {}
        for item in (section.get("diagnostics") or []):
            if isinstance(item, str) and ("WinError 2" in item
                                          or "cannot find the file specified" in item):
                return True
    return False


def sync_board() -> None:
    """Read the board every publish cycle: surface A's new entries, then stamp them received.

    Detection reads origin's copy rather than ours, because ours only catches up when a push
    races and forces a rebase -- on a quiet stretch our copy can be hours stale, which is
    exactly when a message would sit unseen. Printing is the point: stdout is what the
    session's watcher greps to decide a human-or-model reply is needed.

    Per A-005 the runner never replies. Stamping says received, not understood.
    """
    git("fetch", "-q", "origin", "main", check=False, timeout=120)
    upstream = git("show", "origin/main:COLLAB.md", check=False, timeout=60).stdout
    theirs = {eid for eid, _ in board.unread_in(upstream)} if upstream else set()
    mine = {eid for eid, _ in board.unread_from_them()}

    # Both transports at once, deliberately. A-029 cut A's read path over to collab/inbox and
    # kept A's *write* path dual until B confirms from its running process; C has not moved at
    # all. Reading both is how B honours that without needing everyone to switch on one commit.
    inbox = {eid for eid, _ in board.unread_inbox()}
    inbox_first = dict(board.unread_inbox())

    for entry_id in sorted(theirs | mine | inbox):
        body = board.entry_text(upstream, entry_id) or board.entry_text(board._read(), entry_id)
        first = next((line for line in body.splitlines()[2:] if line.strip()), "") if body else ""
        first = first or inbox_first.get(entry_id, "")
        via = "inbox" if entry_id in inbox else "board"
        print(f"    BOARD: unread {entry_id} via {via} -- {first[:110]}", flush=True)
    # Only entries already in our copy can be stamped; the rest get stamped once a rebase
    # brings them in, and stay printed until then so they are not silently lost.
    board.mark_read(sorted(mine))
    # Deliberately no receipt for inbox entries: filing is a model session's act, not the
    # runner's. Recording it here marked C-008 read before any session had seen it.


def update_board(done: int, total: int, counts: dict[str, int], started: float) -> None:
    board.progress(done, total, counts, (time.time() - started) / 3600)


def write_status(done: int, total: int) -> None:
    """A-029's collab/status/<agent>.md: alive, where, and stuck-or-not, in six lines.

    B inferred in B-031 that A had stalled, from a row count that had not moved. A was
    grading at full rate and could not push -- two states the repo could not tell apart.
    Rewritten in place, never appended: this is a gauge, not a log, and single-writer so it
    cannot conflict.
    """
    path = REPO / "collab" / "status" / "B.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    unpushed = len(git("log", "--oneline", "origin/main..HEAD",
                       check=False, timeout=60).stdout.split(chr(10))) - 1
    path.write_text(
        f"# B — rewritten in place by the runner. Only B writes this file.\n"
        f"updated:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"position: {done}/{total} of B's own queue\n"
        f"unpushed: {max(unpushed, 0)} commit(s) ahead of origin/main\n"
        f"host:     {publish_module.PLATFORM}\n"
        f"pid:      {os.getpid()}\n",
        encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reverse", action="store_true",
                        help="walk the MCI list back to front (for a second worker)")
    parser.add_argument("--stop-after", type=int, default=0, help="0 = no limit")
    parser.add_argument("--board-every", type=int, default=25,
                        help="update COLLAB.md every N completed MCIs (0 = never)")
    parser.add_argument("--no-push", dest="push", action="store_false", default=True,
                        help="commit each MCI locally but do not push (use while the other "
                             "agent may still force-push main)")
    args = parser.parse_args()

    os.chdir(REPO)
    for line in (SOURCE_REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

    service = DetectionService(SOURCE_REPO)
    _, raw = service.load_raw_detection(RUN_ID)
    instances = service._indexed_instances(raw)
    total = len(instances)
    agent = RefactoringAgent(service)

    order = list(range(total, 0, -1)) if args.reverse else list(range(1, total + 1))
    counts: dict[str, int] = {}
    for path in BATCH_DIR.glob("0*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("result"):
            key = canonical_store.classify_agent_result(record["result"])
            counts[key] = counts.get(key, 0) + 1

    started = time.time()
    processed = 0
    reported: set[str] = set()   # one NOTE per distinct fault, never a stream
    streak_module, streak = None, 0

    published = publish_module._existing_results()
    print(f"    {len(published)} MCIs already published by all agents combined", flush=True)

    sync_board()   # A-005: read the board on purpose at start

    for index in order:
        instance = instances[index - 1]
        mci_id = instance["id"]
        result_path = BATCH_DIR / safe_name(index, mci_id)
        if result_path.is_file():
            continue
        # Another agent may already have graded this one. B decides what to run from its own
        # batch directory, which knows nothing about A's tail or C's Linux pass, so without
        # this B spends a full generation on an MCI that is already settled and then discards
        # the result at publish time (the producedBy guard keeps theirs). Cheap to check, and
        # it is what makes an interleaved split affordable rather than wasteful.
        owner = (published.get(mci_id) or {}).get("producedBy")
        if owner and owner != "B":
            print(f"[{index}/{total}] SKIP {mci_id} — already graded by {owner}", flush=True)
            continue

        print(f"[{index}/{total}] START {mci_id}", flush=True)
        item_started = time.time()
        try:
            result = agent.run(
                run_id=RUN_ID, selected_mci_ids=[mci_id], model=MODEL, user_instruction="",
                run_pit=False, api_profile="default", use_mock=False, max_retries=2,
                sequence_selection=None, use_cache=True, progress_callback=None,
                workspace_id=WORKSPACE_ID,
            )
            record = {"index": index, "total": total, "mciId": mci_id, "runPit": False,
                      "elapsedSeconds": round(time.time() - item_started, 2),
                      "completedAt": datetime.now(timezone.utc).isoformat(),
                      "mavenArgs": os.environ.get("MAVEN_ARGS", ""), "result": result}
        except Exception as error:  # noqa: BLE001 - a tool fault must not end the batch
            record = {"index": index, "total": total, "mciId": mci_id, "runPit": False,
                      "elapsedSeconds": round(time.time() - item_started, 2),
                      "completedAt": datetime.now(timezone.utc).isoformat(),
                      "mavenArgs": os.environ.get("MAVEN_ARGS", ""),
                      "error": f"{type(error).__name__}: {error}"}
            print(f"[{index}/{total}] TOOL ERROR {record['error'][:120]}", flush=True)
            kind = record["error"].split(":")[0]
            if kind not in reported:
                reported.add(kind)
                board.post_note(
                    f"B's runner raised `{kind}` on `{mci_id}` (index {index}). The batch "
                    f"continues -- the MCI is left unrecorded and will be retried on resume."
                    "\n\n```\n" + record["error"][:400] + "\n```",
                    urgent=True)

        # A tool exception is not a verdict about the MCI, so its record must not land in the
        # batch directory: "done" is "a file exists here", so writing it there would retire the
        # MCI permanently -- absent from the dataset and never retried on resume, invisible in
        # both directions. It goes to tool-errors/ instead, where it stays auditable and the
        # MCI is simply picked up again next run. (Raised by A-006, which flagged the mirror
        # image of this on their side: a placeholder result reported as MODEL_DECLINED.)
        target = result_path if record.get("result") else (
            BATCH_DIR / "tool-errors" / result_path.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")
        os.replace(temporary, target)

        result = record.get("result")
        if not result:
            continue

        # A broken invocation is not a verdict. If Maven itself could not be launched
        # (WinError 2 = mvn.cmd not on PATH), the harness reports "compilation did not pass"
        # and the MCI is filed as ENVIRONMENT_NOT_READY -- indistinguishable from a genuinely
        # unbuildable module, and never retried, because done means a result file exists.
        # Two MCIs were lost this way during a one-minute window when the runner was started
        # without the Maven environment. Quarantine instead, so the next run picks them up.
        if _launch_failed(result):
            quarantine = BATCH_DIR / "tool-errors" / result_path.name
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(result_path, quarantine)
            print(f"[{index}/{total}] QUARANTINED: Maven could not be launched; "
                  f"not recording a verdict for {mci_id}", flush=True)
            if "launch" not in reported:
                reported.add("launch")
                board.post_note(
                    f"B detected that Maven could not be launched at all (`WinError 2`) while "
                    f"grading `{mci_id}`. The harness reports that as a failed compilation, so "
                    f"such an MCI is filed as `ENVIRONMENT_NOT_READY` and never retried -- "
                    f"indistinguishable from a genuinely unbuildable module. B now quarantines "
                    f"these instead. **If your runner can start without its build environment, "
                    f"you have the same silent data-loss path.**",
                    urgent=True)
            continue

        classification = canonical_store.classify_agent_result(result)
        counts[classification] = counts.get(classification, 0) + 1
        processed += 1

        entry = trim_entry(canonical_store.entry_from_agent_result(mci_id, result))
        diff_lookup = {}
        proposal_id = result.get("proposalId")
        if proposal_id:
            diff = PROPOSALS / proposal_id / "changes.diff"
            if diff.is_file() and diff.stat().st_size:
                diff_lookup[mci_id] = diff
        canonical_store.merge(project=PROJECT, repository_root=REPO, entries=[entry],
                              detection_source=None, diff_lookup=diff_lookup, model=MODEL,
                              harness=canonical_store.HARNESS_CLONEDEMOCKER, use_mock=False)

        # A runs the same 1828 MCIs, so a module that starts failing here will fail
        # there too -- worth one NOTE, not a per-MCI stream.
        modules = (result.get("harness") or {}).get("candidate") or {}
        scope = str((modules or {}).get("scope") or entry.get("scope") or "")
        if classification == "ENVIRONMENT_NOT_READY":
            streak = streak + 1 if scope == streak_module else 1
            streak_module = scope
            if streak == 8 and scope and scope not in reported:
                reported.add(scope)
                board.post_note(
                    f"Eight consecutive `ENVIRONMENT_NOT_READY` in scope `{scope}` on B's side. "
                    f"Most recent: `{mci_id}`, reason: {(result.get('reason') or '')[:160]}\n\n"
                    f"Since you run the same 1828 MCIs, expect the same there. If it is a missing "
                    f"non-redistributable jar you happen to have, installing it unlocks these for "
                    f"both of us; if it is a subject test that cannot pass on Windows, it belongs "
                    f"in the environment bucket rather than FAILED_* when we report.",
                    urgent=False)
        else:
            streak_module, streak = None, 0

        published = publish_module._existing_results()

        # Read the board on every publish cycle, not just at startup. Doing it only at startup
        # meant a message posted one minute into a multi-day run sat unread for the whole run.
        sync_board()

        done = sum(counts.values())
        write_status(done, total)
        paths = [f"data/{PROJECT}", "COLLAB.md"]
        if args.board_every and processed % args.board_every == 0:
            update_board(done, total, counts, started)
            paths.append("COLLAB.md")

        elapsed = record["elapsedSeconds"]
        print(f"[{index}/{total}] {classification} {elapsed:.0f}s "
              f"tokens={(result.get('usage') or {}).get('total_tokens', 0)}", flush=True)
        push_with_rebase(
            f"sync completed MCI {mci_id} ({classification}) to CloudStack 24.0.0-SNAPSHOT dataset",
            paths, push=args.push,
        )

        if args.stop_after and processed >= args.stop_after:
            print(f"stopping after {processed} MCIs as requested", flush=True)
            break

    update_board(sum(counts.values()), total, counts, started)
    push_with_rebase("Update CloudStack progress on the collaboration board",
                     ["COLLAB.md", f"data/{PROJECT}"], push=args.push)
    print(f"done: processed {processed} this session; totals {counts}", flush=True)


if __name__ == "__main__":
    main()
