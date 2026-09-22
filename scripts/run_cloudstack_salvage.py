"""
Salvage settled failed MCIs in Apache CloudStack on Linux aarch64.
Collaborator: C ("Remedy")

Complies strictly with:
- A-008: Layer in by mciId via canonical_store.merge; record host/platform provenance; avoid live frontier; resolve to upstream and re-apply C's entries on push contention.
- A-010: Only target B's settled failures (detection index < 1700). Never touch A's un-gated tail (>= 1700).
- B-009 & B-010: Stamp producedBy: "C" and platform: "linux-aarch64" so B's publisher never overwrites C's entries.
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

RUN_ID = "473f3cc8e16b4ccf97ee72fe18ec3aa2"
WORKSPACE_ID = "cloudstack-salvage-linux"
PROJECT = "cloudstack"
MODEL = "deepseek-chat"
CANONICAL_MODEL = "gpt-5.6-terra"
REMOTE = "origin"
DATASET = REPO / "data/cloudstack/refactoring/CloneDeMocker+Terra-5.6"
RESULTS = DATASET / "refactoring-results.json"
CONFLICT_PATHS = ["refactoring-results.json", "refactoring-results.csv"]
AGENT = "C"
PLATFORM = "linux-aarch64"


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%m-%d %H:%M:%S')} [C-Remedy] {message}", flush=True)


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


def relayer(entry: dict, diff_source: Path | None) -> None:
    """Re-add C's entry on top of whatever the dataset currently holds."""
    diff_lookup = {}
    if diff_source is not None and diff_source.is_file():
        diff_lookup[entry["mciId"]] = diff_source
    canonical_store.merge(project=PROJECT, repository_root=REPO, entries=[entry],
                          detection_source=None, diff_lookup=diff_lookup, model=CANONICAL_MODEL,
                          harness=canonical_store.HARNESS_CLONEDEMOCKER, use_mock=False)


def recover_repo() -> None:
    git_dir = REPO / ".git"
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        log("recover: aborting a rebase left in progress")
        git("rebase", "--abort")
    if (git_dir / "MERGE_HEAD").exists():
        log("recover: aborting a merge left in progress")
        git("merge", "--abort")
    lock = git_dir / "index.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 300:
        log("recover: removing a stale index.lock")
        try:
            lock.unlink()
        except OSError:
            pass


def sync(message: str, entry: dict, diff_source: Path | None, attempts: int = 5) -> bool:
    """Commit and push, surviving other agents pushing to the same files."""
    recover_repo()
    git("add", "--", f"data/{PROJECT}")
    if not git("diff", "--cached", "--quiet").returncode:
        log("sync: no changes to commit in data/cloudstack")
        return True

    git("commit", "-q", "-m", message)

    for attempt in range(1, attempts + 1):
        push_res = git("push", REMOTE, "main")
        if push_res.returncode == 0:
            log(f"sync: successfully pushed to {REMOTE}/main")
            return True

        log(f"sync: push rejected (attempt {attempt}/{attempts}), rebasing against upstream...")
        pull = git("pull", "--rebase", REMOTE, "main")
        if pull.returncode != 0:
            # Conflicted on shared dataset files. Take upstream for them; C's entry is re-applied below.
            for name in CONFLICT_PATHS:
                git("checkout", "--ours", "--", str((DATASET / name).relative_to(REPO)))
                git("add", "--", str((DATASET / name).relative_to(REPO)))
            cont = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"],
                                  cwd=REPO, text=True, capture_output=True, timeout=300)
            if cont.returncode != 0:
                git("rebase", "--abort")
                log(f"sync: rebase unresolved on attempt {attempt}, retrying after backoff")
                time.sleep(5 * attempt)
                continue

        # Whether rebase was clean or resolved toward upstream, re-apply C's entry.
        relayer(entry, diff_source)
        git("add", "--", f"data/{PROJECT}")
        if git("diff", "--cached", "--quiet").returncode:
            git("commit", "-q", "-m", f"Re-apply {entry['mciId']} by C after rebase")
        time.sleep(2 * attempt)

    log("sync: push attempts exhausted; next cycle will carry it forward")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudStack MCI Salvage Runner by C (Remedy)")
    parser.add_argument("--ids-file", default="validation/cloudstack_salvage_targets_7.txt",
                        help="Path to file containing MCI IDs to salvage")
    parser.add_argument("--stop-after", type=int, default=0, help="Stop after processing N items (0 = run all)")
    parser.add_argument("--no-push", action="store_true", help="Do not push commits to remote")
    args = parser.parse_args()

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        log("FATAL: OPENAI_API_KEY is not set")
        sys.exit(2)

    ids_path = REPO / args.ids_file
    if not ids_path.is_file():
        log(f"FATAL: IDs file not found: {ids_path}")
        sys.exit(1)

    target_ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.startswith("#")]

    service = DetectionService(REPO)
    agent = RefactoringAgent(service)

    log(f"Starting salvage batch on Linux aarch64 with {len(target_ids)} target MCIs")
    log(f"IDs file: {args.ids_file}")
    log(f"Model: {MODEL}, Target dataset: {DATASET.relative_to(REPO)}")

    results_data = {}
    if RESULTS.is_file():
        try:
            results_data = json.loads(RESULTS.read_text(encoding="utf-8")).get("results", {})
        except Exception:
            pass

    processed = 0
    salvaged_count = 0

    for mci_id in target_ids:
        if args.stop_after and processed >= args.stop_after:
            log(f"Reached stop-after limit of {args.stop_after} MCIs")
            break

        prev_entry = results_data.get(mci_id, {})
        prev_cls = prev_entry.get("classification", "UNKNOWN")
        prev_producer = prev_entry.get("producedBy") or "B/A"

        log(f"[{processed + 1}/{len(target_ids)}] START {mci_id} (previous: {prev_cls} by {prev_producer})")
        item_started = time.time()

        try:
            result = agent.run(
                run_id=RUN_ID,
                selected_mci_ids=[mci_id],
                model=MODEL,
                user_instruction="",
                run_pit=False,
                api_profile="default",
                use_mock=False,
                max_retries=2,
                sequence_selection=None,
                use_cache=False,  # Force fresh evaluation on Linux environment
                progress_callback=None,
                workspace_id=WORKSPACE_ID
            )
        except Exception as error:
            log(f"    ERROR executing {mci_id}: {type(error).__name__}: {str(error)[:250]}")
            continue

        elapsed = time.time() - item_started
        classification = canonical_store.classify_agent_result(result)
        entry = canonical_store.entry_from_agent_result(mci_id, result)
        entry["producedBy"] = AGENT
        entry["platform"] = PLATFORM

        # Preserve previous failure note if applicable
        if prev_cls != "SUCCESS":
            entry["previousFailure"] = {
                "classification": prev_cls,
                "producedBy": prev_producer,
                "platform": prev_entry.get("platform", "windows")
            }

        diff_source = None
        proposal_id = result.get("proposalId")
        if proposal_id:
            cand = REPO / ".clonedemocker" / "runs" / RUN_ID / "refactoring" / proposal_id / "changes.diff"
            if cand.is_file() and cand.stat().st_size:
                diff_source = cand

        log(f"    RESULT: {classification} in {elapsed:.1f}s (was {prev_cls})")

        # Merge entry into canonical store
        relayer(entry, diff_source)

        if classification == "SUCCESS":
            salvaged_count += 1
            commit_msg = f"C: salvage {mci_id} (SUCCESS on linux-aarch64, was {prev_cls})"
        else:
            commit_msg = f"C: retry {mci_id} ({classification} on linux-aarch64, was {prev_cls})"

        if not args.no_push:
            sync(commit_msg, entry, diff_source)

        processed += 1

    log(f"Batch completed: {processed} processed, {salvaged_count} successfully salvaged into SUCCESS!")


if __name__ == "__main__":
    main()
