"""Drop V1 rows that failed only because of write-back bugs fixed on 2026-09-24, so they re-run.

Rows written by baseline_v1 before commit 0ce0bbfe failed on four problems of the mechanical
write-back, not of V1's answer: the prompt's "newFieldValueName " key typo (KeyError), hunk
lines that span wrapped source lines, Python-repr escapes, and context that recurs across
tests ("matches 0/2/N places"). This removes exactly those rows from the V1 dataset (and their
diffs); `drive.py` then re-runs only the missing V1 rows, reusing V2's recorded baseline.
V2 rows are never touched.

    python baseline_v1/drop_stale_v1.py spring-integration-7.1.1
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from studio.canonical_store import safe_mci_filename  # noqa: E402

STALE = re.compile(r"KeyError(: 'newFieldValueName'|\))|hunk context matches \d+ places")


def main(project: str) -> None:
    directory = REPO / "data" / project / "refactoring" / "CloneDeMocker-V1+Luna-5.6"
    path = directory / "refactoring-results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    stale = [k for k, v in data["results"].items()
             if STALE.search(f"{v.get('validationReason') or ''} {v.get('declineReason') or ''}")]
    for key in stale:
        del data["results"][key]
        (directory / "diffs" / safe_mci_filename(key)).unlink(missing_ok=True)
    data["totalMcis"] = len(data["results"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    table = directory / "refactoring-results.csv"
    if table.exists() and stale:
        rows = [r for r in csv.DictReader(table.open(encoding="utf-8")) if r["mciId"] not in stale]
        with table.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["mciId"])
            writer.writeheader()
            writer.writerows(rows)
    print(f"{project}: dropped {len(stale)} stale V1 rows, {len(data['results'])} kept")


if __name__ == "__main__":
    main(sys.argv[1])
