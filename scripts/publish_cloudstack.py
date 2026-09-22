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
    return subprocess.run(
        ["git", "-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}", *args],
        cwd=REPO, text=True, capture_output=True, check=check, timeout=timeout)


def regenerate() -> dict:
    """Rebuild data/<project>/ from the batch output. Idempotent."""
    entries, diffs, counts = [], {}, Counter()
    for path in sorted(BATCH_DIR.glob("0*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result = record.get("result")
        if not result:
            continue
        entry = trim_entry(canonical_store.entry_from_agent_result(record["mciId"], result))
        entries.append(entry)
        counts[entry["classification"]] += 1
        proposal_id = result.get("proposalId")
        if proposal_id:
            diff = PROPOSALS / proposal_id / "changes.diff"
            if diff.is_file() and diff.stat().st_size:
                diffs[record["mciId"]] = diff
    summary = canonical_store.merge(
        project=PROJECT, repository_root=REPO, entries=entries, detection_source=None,
        diff_lookup=diffs, model=MODEL, harness=canonical_store.HARNESS_CLONEDEMOCKER,
        use_mock=False)
    summary["counts"] = dict(counts)
    return summary


def clear_conflicts() -> None:
    conflicted = git("diff", "--name-only", "--diff-filter=U").stdout.split()
    for path in conflicted:
        # Either side is fine: regenerate() overwrites the generated files afterwards anyway.
        git("checkout", "--theirs", "--", path)
        git("add", "--", path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=8)
    args = parser.parse_args()

    for attempt in range(1, args.attempts + 1):
        summary = regenerate()
        git("add", "--", f"data/{PROJECT}", "COLLAB.md")
        if git("diff", "--cached", "--quiet").returncode:
            git("commit", "-q", "-m",
                f"Publish CloudStack dataset: {summary['totalMcis']} MCIs, "
                f"{summary['successes']} SUCCESS ({summary['successRate']:.1%})\n\n"
                f"Regenerated from the batch output so the count is independent of how any\n"
                f"rebase conflict on the generated results file happened to resolve.\n\n"
                f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>")

        if git("push", "origin", "main").returncode == 0:
            print(f"pushed on attempt {attempt}: {summary['totalMcis']} MCIs, "
                  f"{summary['successes']} SUCCESS ({summary['successRate']:.1%})")
            print(f"  {summary['counts']}")
            return

        print(f"attempt {attempt}: push rejected, rebasing onto teammate's work", flush=True)
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

    print("could not publish within the attempt budget; local commits are intact", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
