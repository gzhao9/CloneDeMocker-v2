"""Run this machine's back-to-front half of the CloudStack MCI list, unattended.

Walks the 1828 MCIs of runId ad6456be from the tail down, one at a time, merging
each result into data/cloudstack/ and pushing it. The other machine walks the same
list from the front, so both write the same dataset files and every push races it.

Two properties make a multi-day unattended run survivable:

  * **The worklist is derived, never stored.** What is left is recomputed from
    refactoring-results.json on every iteration, so a crash, a restart, or the
    other machine finishing something first all resolve themselves. Nothing has
    to be reconciled by hand.
  * **Conflicts always resolve toward upstream, then re-apply ours.** Taking a
    side is what loses data: the other machine lost four MCIs that way. After any
    rebase — conflicted or not — this re-merges only this machine's entry through
    canonical_store.merge, which leaves entries it was not given untouched. Both
    sides therefore survive regardless of how the conflict resolved, so the push
    path never has to be right about who won.

Usage:
    python scripts/run_cloudstack_tail.py                 # until the tail is done
    python scripts/run_cloudstack_tail.py --stop-after 50
    python scripts/run_cloudstack_tail.py --no-push
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402

# The detection restored from data/cloudstack/, with file paths already remapped from the
# other machine's checkout to this one's. Reused rather than restored per run: restoring
# copies a 165 MB detection file every time.
RUN_ID = "bf09daadb15140fb81972020b2190a25"
WORKSPACE_ID = "cloudstack-tail-no-pit"
PROJECT = "cloudstack"
MODEL = "gpt-5.6-terra"
REMOTE = "github"
DATASET = REPO / "data/cloudstack/refactoring/CloneDeMocker+Terra-5.6"
RESULTS = DATASET / "refactoring-results.json"
CONFLICT_PATHS = ["refactoring-results.json", "refactoring-results.csv"]
# MCIs whose run died inside the tooling. Kept out of the dataset on purpose: a tool
# exception classifies as MODEL_DECLINED, which would read as "the model refused" in the
# results and quietly overstate that category. Recorded here instead so the derived
# worklist still advances past them and they stay auditable.
SKIPPED = REPO / "validation/cloudstack_tail_skipped.json"


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%m-%d %H:%M:%S')} {message}", flush=True)


def git(*args: str, check: bool = False, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, text=True, capture_output=True,
                          check=check, timeout=timeout)


def load_env() -> None:
    for candidate in (REPO / ".env", REPO.parent / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def done_ids() -> set[str]:
    try:
        return set(json.loads(RESULTS.read_text(encoding="utf-8")).get("results", {}))
    except (OSError, json.JSONDecodeError):
        # A conflicted or half-written file must not be read as "nothing is done" — that would
        # re-run the whole list. Treat it as unknown and let the caller stop.
        return set()


def skipped_ids() -> dict[str, str]:
    try:
        return json.loads(SKIPPED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_skip(mci_id: str, reason: str) -> None:
    records = skipped_ids()
    records[mci_id] = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {reason}"
    SKIPPED.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def relayer(entry: dict, diff_source: Path | None) -> None:
    """Re-add this machine's entry on top of whatever the dataset currently holds."""
    diff_lookup = {}
    if diff_source is not None and diff_source.is_file():
        diff_lookup[entry["mciId"]] = diff_source
    canonical_store.merge(project=PROJECT, repository_root=REPO, entries=[entry],
                          detection_source=None, diff_lookup=diff_lookup, model=MODEL,
                          harness=canonical_store.HARNESS_CLONEDEMOCKER, use_mock=False)


def sync(message: str, entry: dict, diff_source: Path | None, attempts: int = 5) -> bool:
    """Commit and push, surviving the other machine pushing to the same files."""
    git("add", "--", f"data/{PROJECT}")
    if not git("diff", "--cached", "--quiet").returncode:
        return True

    git("commit", "-q", "-m", message)

    for attempt in range(1, attempts + 1):
        if git("push", REMOTE, "main").returncode == 0:
            return True

        pull = git("pull", "--rebase", REMOTE, "main")
        if pull.returncode != 0:
            # Conflicted on the shared dataset files. Take upstream for them; ours is re-applied
            # below, so nothing of this machine's is riding on this choice. During a rebase
            # --ours is upstream, the inverse of a merge — the trap that cost the other machine
            # four MCIs.
            for name in CONFLICT_PATHS:
                git("checkout", "--ours", "--", str((DATASET / name).relative_to(REPO)))
                git("add", "--", str((DATASET / name).relative_to(REPO)))
            cont = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"],
                                  cwd=REPO, text=True, capture_output=True, timeout=300)
            if cont.returncode != 0:
                git("rebase", "--abort")
                log(f"    sync: rebase unresolved on attempt {attempt}, retrying")
                time.sleep(5 * attempt)
                continue

        # Whether the rebase was clean or resolved toward upstream, re-apply our own entry.
        relayer(entry, diff_source)
        git("add", "--", f"data/{PROJECT}")
        if git("diff", "--cached", "--quiet").returncode:
            git("commit", "-q", "-m", f"Re-apply {entry['mciId']} after rebase")
        time.sleep(2 * attempt)

    log("    sync: giving up for now; the next MCI carries it forward")
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-after", type=int, default=0, help="0 = until the tail is done")
    parser.add_argument("--no-push", dest="push", action="store_false", default=True)
    args = parser.parse_args()

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        log("OPENAI_API_KEY is not set")
        sys.exit(2)

    service = DetectionService(REPO)
    _, raw = service.load_raw_detection(RUN_ID)
    instances = service._indexed_instances(raw)
    by_id = {item["id"]: item for item in instances}
    ordered = [item["id"] for item in instances]
    agent = RefactoringAgent(service)

    log(f"tail runner: {len(ordered)} MCIs total, walking back to front")
    processed = 0
    counts: dict[str, int] = {}
    started = time.time()

    while True:
        done = done_ids()
        if not done:
            log("results file unreadable (conflict markers?) — stopping rather than re-running")
            break
        skipped = skipped_ids()
        remaining = [m for m in reversed(ordered) if m not in done and m not in skipped]
        if not remaining:
            log(f"nothing left in the tail ({len(skipped)} skipped after tool errors)")
            break

        mci_id = remaining[0]
        index = ordered.index(mci_id) + 1
        log(f"[{len(done)}/{len(ordered)}] START {mci_id} (index {index})")
        item_started = time.time()

        try:
            result = agent.run(run_id=RUN_ID, selected_mci_ids=[mci_id], model=MODEL,
                               user_instruction="", run_pit=False, api_profile="default",
                               use_mock=False, max_retries=2, sequence_selection=None,
                               use_cache=True, progress_callback=None, workspace_id=WORKSPACE_ID)
        except Exception as error:  # noqa: BLE001 - one bad MCI must not end a multi-day run
            log(f"    TOOL ERROR {type(error).__name__}: {str(error)[:200]}")
            # Deliberately not merged into the dataset: classify_agent_result maps a
            # placeholder like this to MODEL_DECLINED, so recording it would report a tooling
            # crash as the model refusing to refactor. The skip list keeps the run moving
            # without putting a fact in the results that is not one.
            record_skip(mci_id, f"{type(error).__name__}: {str(error)[:200]}")
            git("add", "--", str(SKIPPED.relative_to(REPO)))
            git("commit", "-q", "-m", f"Skip {mci_id} after a tool error")
            continue

        elapsed = time.time() - item_started
        entry = canonical_store.entry_from_agent_result(mci_id, result)
        classification = canonical_store.classify_agent_result(result)
        counts[classification] = counts.get(classification, 0) + 1
        processed += 1

        diff_source = None
        proposal_id = result.get("proposalId")
        if proposal_id:
            candidate = REPO / ".clonedemocker" / "runs" / RUN_ID / "refactoring" / proposal_id / "changes.diff"
            if candidate.is_file() and candidate.stat().st_size:
                diff_source = candidate

        relayer(entry, diff_source)
        log(f"    {classification} {elapsed:.0f}s "
            f"tokens={(result.get('usage') or {}).get('total_tokens', 0)}")

        if args.push:
            sync(f"sync completed MCI {mci_id} ({classification}) to CloudStack "
                 f"24.0.0-SNAPSHOT dataset", entry, diff_source)

        if args.stop_after and processed >= args.stop_after:
            log(f"stopping after {processed} as requested")
            break

    hours = (time.time() - started) / 3600
    log(f"session done: {processed} MCIs in {hours:.1f} h; {counts}")


if __name__ == "__main__":
    main()
