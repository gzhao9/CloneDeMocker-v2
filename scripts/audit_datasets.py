"""Audit every refactoring dataset on origin/main before results are handed in.

Checks the invariants the A/B/C collaboration put at risk, per dataset directory:
coverage against the detection (missing / unknown MCIs), provenance (model, producedBy,
platform, host), cache replays, timing gaps, resamples, and the V1 leniency flags.
Read-only: reads blobs from origin/main, writes nothing.

    python scripts/audit_datasets.py            # table
    python scripts/audit_datasets.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from studio.detection_service import DetectionService  # noqa: E402

REF = "github/main"


def show(path: str):
    out = subprocess.run(["git", "show", f"{REF}:{path}"], cwd=REPO, capture_output=True)
    return json.loads(out.stdout.decode("utf-8-sig", "replace")) if out.returncode == 0 and out.stdout else None


def tree(prefix: str) -> list[str]:
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", REF, "--", prefix], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout.split()


def audit() -> list[dict]:
    rows = []
    for path in sorted(p for p in tree("data") if p.endswith("refactoring/refactoring-results.json") is False
                       and p.endswith("/refactoring-results.json") and "/_" not in p):
        parts = path.split("/")
        project, setup = parts[1], parts[3]
        data = show(path) or {}
        results = data.get("results", {})
        detection = show(f"data/{project}/detection.json")
        ids = {i["id"] for i in DetectionService._indexed_instances(detection)} if detection else set()
        c = collections.Counter(v.get("classification") for v in results.values())
        n = len(results)
        s, e = c.get("SUCCESS", 0), c.get("ENVIRONMENT_NOT_READY", 0)
        t = lambda v: v.get("timings") or {}
        rows.append({
            "project": project, "setup": setup, "rows": n, "detected": len(ids),
            "missing": sorted(ids - set(results))[:5], "missingCount": len(ids - set(results)) if ids else None,
            "unknown": len(set(results) - ids) if ids else None,
            "actual%": round(100 * s / n, 1) if n else None,
            "exclEnv%": round(100 * s / (n - e), 1) if n - e else None,
            "classes": dict(c),
            "model": dict(collections.Counter(v.get("model") or "" for v in results.values())),
            "producedBy": dict(collections.Counter(v.get("producedBy") or "-" for v in results.values())),
            "platform": dict(collections.Counter(v.get("platform") or "-" for v in results.values())),
            "cacheHit": sum(1 for v in results.values() if v.get("cacheHit")),
            "noGeneration": sum(1 for v in results.values() if v.get("generationSeconds") is None
                                and v.get("classification") != "ENVIRONMENT_NOT_READY"),
            "modelSeconds": sum(1 for v in results.values() if "modelSeconds" in t(v)),
            "resample": sum(1 for v in results.values() if v.get("resampleOutcome")),
            "v1KeyNormalized": sum(1 for v in results.values() if v.get("v1KeyNormalized")),
            "v1FuzzyApply": sum(1 for v in results.values() if v.get("v1FuzzyApply")),
            "v1OutputUnusable": sum(1 for v in results.values() if v.get("v1OutputUnusable")),
        })
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    subprocess.run(["git", "fetch", "-q", "github", "main"], cwd=REPO)
    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        for r in report:
            print(f"== {r['project']} / {r['setup']}: {r['rows']} rows, detected {r['detected']}, "
                  f"missing {r['missingCount']}, unknown {r['unknown']}, actual {r['actual%']}%, exclEnv {r['exclEnv%']}%")
            print(f"   model {r['model']}  producedBy {r['producedBy']}  platform {r['platform']}")
            print(f"   cacheHit {r['cacheHit']}  noGeneration {r['noGeneration']}  modelSeconds {r['modelSeconds']}  "
                  f"resample {r['resample']}  V1 flags key/fuzzy/unusable {r['v1KeyNormalized']}/{r['v1FuzzyApply']}/{r['v1OutputUnusable']}")
            if r["missingCount"]:
                print(f"   missing e.g. {r['missing']}")
