"""
Distills a run_pilot.py report (plus its underlying detection data) into one
canonical, git-trackable dataset per project instead of committing a fresh,
fully-duplicated .clonedemocker/runs/{uuid}/ snapshot or a new timestamped
validation/results/pilot-*.json for every invocation.

Writes/overwrites in place:
    data/<project>/detection.json            latest full detection output
    data/<project>/refactoring-results.json  one record per MCI, keyed by mciId,
                                              overwriting only the MCIs this report
                                              touched -- MCIs from a prior export not
                                              present in this report are kept as-is
    data/<project>/diffs/<mciId>.diff        one diff file per MCI with a resolvable
                                              proposalId, overwritten on re-export

Usage:
    uv run python validation/export_canonical.py --project dubbo \
        --report validation/results/pilot-merged-redesign-round1-20260916.json \
        --detection-run-id acc8c0a744b04cdbbb36ad05a7509789 \
        [--proposal-overrides path/to/overrides.json]

--proposal-overrides is a {mciId: proposalId} JSON map for results whose
report-recorded proposalId doesn't resolve to a real changes.diff (e.g. entries
recovered from a crashed run's log, reconstructed without full harness detail --
see pilot-merged-redesign-round1-20260916.json's spliceNote).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# 合并规则只保留一份实现：UI 的导出接口与这个脚本共用 studio.canonical_store，
# 否则两条路各写一套，迟早在"哪些 MCI 被保留、哪些被覆盖"上悄悄分叉。
# One implementation of the merge rules: the UI export endpoint and this script share
# studio.canonical_store, or the two paths would each grow their own and drift on which MCIs
# are kept and which are overwritten.
sys.path.insert(0, str(REPO_ROOT))
from studio.canonical_store import safe_mci_filename  # noqa: E402


def find_diff(proposal_id: str) -> Path | None:
    matches = list((REPO_ROOT / ".clonedemocker" / "runs").glob(f"*/refactoring/{proposal_id}/changes.diff"))
    return matches[0] if matches else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--report", required=True, help="run_pilot.py report JSON to distill")
    parser.add_argument("--detection-run-id", required=True,
                         help="runId whose mock-clone-instances.json has full project coverage")
    parser.add_argument("--proposal-overrides", default=None)
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    overrides = (json.loads(Path(args.proposal_overrides).read_text(encoding="utf-8"))
                 if args.proposal_overrides else {})

    out_dir = REPO_ROOT / "data" / args.project
    diffs_dir = out_dir / "diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)

    detection_src = REPO_ROOT / ".clonedemocker" / "runs" / args.detection_run_id / "mock-clone-instances.json"
    if not detection_src.is_file():
        sys.exit(f"detection data not found: {detection_src}")
    shutil.copyfile(detection_src, out_dir / "detection.json")

    existing_path = out_dir / "refactoring-results.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8"))["results"] if existing_path.is_file() else {}

    results_by_id = dict(existing)
    copied = missing = 0
    for result in report.get("results", []):
        mci_id = result["mciId"]
        proposal_id = overrides.get(mci_id, result.get("proposalId"))
        entry = dict(result)
        if proposal_id:
            diff_path = find_diff(proposal_id)
            if diff_path:
                shutil.copyfile(diff_path, diffs_dir / safe_mci_filename(mci_id))
                entry["diffFile"] = f"diffs/{safe_mci_filename(mci_id)}"
                copied += 1
            else:
                missing += 1
                print(f"  WARNING: no changes.diff found for {mci_id} (proposalId={proposal_id})")
        results_by_id[mci_id] = entry

    success = sum(1 for r in results_by_id.values() if r.get("classification") == "SUCCESS")
    canonical = {
        "project": args.project,
        "lastUpdated": report.get("generatedAt"),
        "sourceReport": Path(args.report).name,
        "model": report.get("model"),
        "totalMcis": report.get("totalMcis"),
        "mciSuccessRate": success / len(results_by_id) if results_by_id else None,
        "results": results_by_id,
    }
    (out_dir / "refactoring-results.json").write_text(
        json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_columns = ["mciId", "classification", "firstPassClassification", "repairRounds",
                   "goalAchieved", "mutationRegressed", "reclassifiedReason", "diffFile"]
    with (out_dir / "refactoring-results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        for mci_id, entry in results_by_id.items():
            writer.writerow({"mciId": mci_id, **entry})

    print(f"wrote {out_dir / 'detection.json'}")
    print(f"wrote {out_dir / 'refactoring-results.csv'}")
    print(f"wrote {out_dir / 'refactoring-results.json'} "
          f"({len(results_by_id)} MCIs total, {copied} diffs copied this export, {missing} missing)")


if __name__ == "__main__":
    main()
