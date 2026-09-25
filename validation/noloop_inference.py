"""Infer V2 + Terra without the repair loop from round 1. An analysis, not a dataset.

Owner's decision (2026-09-26): the no-loop ablation is inferred, not run, and is not raw data.
Round 1 ran V2 with Terra and up to two repair rounds, and every row records `repairRounds`:

  * repairRounds == 0: the first attempt was the whole run; without the loop the verdict is the same.
  * repairRounds >= 1: the first attempt failed; without the loop it stays failed. Round 1 did not
    record which gate that first attempt failed, so these count as "failed first attempt".

Round 1 is read from github/main (C is still re-running CloudStack's round-1 failures, so re-run
this when that is done). spring-integration has no Terra round 1 and is not covered.

    python validation/noloop_inference.py          # writes reports/noloop-terra.{md,json}
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUND1 = "CloneDeMocker+Terra-5.6"
PROJECTS = ("kiota-java-1.10.0", "dubbo-3.3.6", "druid-37.0.0", "spring-security-7.1.1", "cloudstack")


def round1_rows(project: str) -> dict:
    shown = subprocess.run(["git", "show", f"github/main:data/{project}/refactoring/{ROUND1}/refactoring-results.json"],
                           cwd=REPO, capture_output=True)
    if shown.returncode != 0:
        sys.exit(f"{project}: no round-1 Terra results on github/main")
    return json.loads(shown.stdout.decode("utf-8-sig"))["results"]


def infer(project: str) -> dict:
    rows = round1_rows(project)
    with_loop = Counter(r.get("classification") for r in rows.values())
    no_loop = Counter(r.get("classification") if not r.get("repairRounds") else "FAILED_FIRST_ATTEMPT"
                      for r in rows.values())
    repaired = [m for m, r in rows.items() if r.get("repairRounds")]
    return {
        "project": project, "mcis": len(rows),
        "successWithLoop": with_loop["SUCCESS"], "successNoLoop": no_loop["SUCCESS"],
        "rescuedByLoop": sum(1 for m in repaired if rows[m].get("classification") == "SUCCESS"),
        "failedDespiteLoop": sum(1 for m in repaired if rows[m].get("classification") != "SUCCESS"),
        "cacheHitRows": sum(1 for r in rows.values() if r.get("cacheHit")),
        "withLoop": dict(with_loop), "noLoop": dict(no_loop), "repairedMcis": repaired,
    }


def main() -> None:
    subprocess.run(["git", "fetch", "-q", "github", "main"], cwd=REPO)
    results = [infer(p) for p in PROJECTS]
    total = {k: sum(r[k] for r in results) for k in ("mcis", "successWithLoop", "successNoLoop", "rescuedByLoop",
                                                      "failedDespiteLoop", "cacheHitRows")}
    head = subprocess.run(["git", "rev-parse", "--short", "github/main"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    out = REPO / "reports"
    out.mkdir(exist_ok=True)
    (out / "noloop-terra.json").write_text(json.dumps({
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": f"github/main {head}",
        "round1Setup": ROUND1, "projects": results, "total": total}, ensure_ascii=False, indent=2), encoding="utf-8")

    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "-"
    lines = [
        "# V2 + Terra without the repair loop (inferred from round 1)", "",
        f"Source: `{ROUND1}` rows on github/main {head}. Nothing was re-run. A row with repairRounds 0 keeps its "
        "verdict; a row that needed repair counts as a failed first attempt. spring-integration has no Terra round 1.", "",
        "| Project | MCIs | SUCCESS with loop | SUCCESS without loop | Rescued by loop | Failed despite loop | Cache-hit rows |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results + [{"project": "**Total**", **total}]:
        lines.append(f"| {r['project']} | {r['mcis']} | {r['successWithLoop']} ({pct(r['successWithLoop'], r['mcis'])}) | "
                     f"{r['successNoLoop']} ({pct(r['successNoLoop'], r['mcis'])}) | {r['rescuedByLoop']} | "
                     f"{r['failedDespiteLoop']} | {r['cacheHitRows']} |")
    lines += ["", "Cache-hit rows replayed an earlier stored answer in round 1; they are counted like the rest.",
              "CloudStack's round 1 is still being corrected by C (A-052); re-run this script when that is done."]
    (out / "noloop-terra.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
