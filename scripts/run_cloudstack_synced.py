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
    return subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                          check=check, timeout=timeout)


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


def update_board(done: int, total: int, counts: dict[str, int], started: float) -> None:
    """Prepend a dated progress entry to our own section. Only our section is touched, so the
    teammate's edits to theirs never conflict with ours."""
    if not BOARD.is_file():
        return
    text = BOARD.read_text(encoding="utf-8")
    if SECTION not in text:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    success = counts.get("SUCCESS", 0)
    graded = sum(v for k, v in counts.items() if k != "ENVIRONMENT_NOT_READY")
    rate = f"{success / done:.1%}" if done else "n/a"
    graded_rate = f"{success / graded:.1%}" if graded else "n/a"
    hours = (time.time() - started) / 3600
    entry = (
        f"### {stamp} — progress {done}/{total} ({done/total:.1%})\n\n"
        f"CloudStack 24.0.0-SNAPSHOT batch, PIT off. "
        f"SUCCESS {success}/{done} = {rate} overall, {graded_rate} excluding "
        f"ENVIRONMENT_NOT_READY.\n\n"
        f"Breakdown: " + ", ".join(f"`{k}` {v}" for k, v in sorted(counts.items())) + ".  \n"
        f"This session has been running {hours:.1f} h.\n\n"
    )
    head, _, rest = text.partition(SECTION)
    lines = rest.split("\n")
    # Keep the section heading and its subject paragraph, insert the new entry above older ones.
    cut = next((i for i, line in enumerate(lines) if line.startswith("### ")), len(lines))
    merged = head + SECTION + "\n".join(lines[:cut]) + entry + "\n".join(lines[cut:])
    BOARD.write_text(merged, encoding="utf-8", newline="\n")


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

    for index in order:
        instance = instances[index - 1]
        mci_id = instance["id"]
        result_path = BATCH_DIR / safe_name(index, mci_id)
        if result_path.is_file():
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

        temporary = result_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")
        os.replace(temporary, result_path)

        result = record.get("result")
        if not result:
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

        done = sum(counts.values())
        paths = [f"data/{PROJECT}"]
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
