"""Backfill usageRefactoring / usageAudit on pair-run rows written before they were recorded.

Rows from rounds 2+3 (and the measured re-run) keep every call's response id in
timings.modelCallLog, with the audit call flagged. This reads each call's usage back from the
API (GET, no tokens) and writes the split onto rows that lack it.

    python baseline_v1/backfill_usage_split.py prefetch                  # fetch, cache; safe while lanes run
    python baseline_v1/backfill_usage_split.py apply --project dubbo-3.3.6  # write + publish (stop lanes writing it first)

Cache: validation/results/usage-calls.jsonl (resumable).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from baseline_v1 import drive, run_pair  # noqa: E402
from baseline_v1.usage_by_calls import FIELDS, usage_of  # noqa: E402

CACHE = REPO / "validation" / "results" / "usage-calls.jsonl"
PAIR_SETUPS = ("CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6", "CloneDeMocker-measured+Terra-5.6")


def datasets(project: str | None):
    for path in sorted((REPO / "data").glob("*/refactoring/*/refactoring-results.json")):
        if path.parent.name in PAIR_SETUPS and (project is None or path.parts[-4] == project):
            yield path


def cached() -> dict:
    have = {}
    if CACHE.is_file():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                have[row["id"]] = row["usage"]
    return have


def prefetch() -> None:
    run_pair.load_env()
    import os
    from openai import OpenAI
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, timeout=60, max_retries=3)
    have = cached()
    ids = set()
    for path in datasets(None):
        for row in json.loads(path.read_text(encoding="utf-8"))["results"].values():
            if "usageRefactoring" in row:
                continue
            ids.update(c["responseId"] for c in (row.get("timings") or {}).get("modelCallLog") or []
                       if str(c.get("responseId", "")).startswith("resp_"))
    todo = sorted(ids - set(have))
    print(f"{len(ids)} calls needed, {len(todo)} to fetch", flush=True)
    lock = threading.Lock()

    def fetch(rid: str) -> None:
        u = usage_of(client, rid)
        with lock:
            with CACHE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": rid, "usage": u}) + "\n")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, todo))
    print("prefetch done", flush=True)


def apply(project: str) -> None:
    have = cached()
    for path in datasets(project):
        data = json.loads(path.read_text(encoding="utf-8"))
        filled = incomplete = 0
        for row in data["results"].values():
            log = (row.get("timings") or {}).get("modelCallLog")
            if "usageRefactoring" in row or not log:
                continue
            split = {"refactoring": dict.fromkeys(FIELDS, 0), "audit": dict.fromkeys(FIELDS, 0)}
            missing = 0
            for call in log:
                u = call.get("usage") or have.get(call.get("responseId"))
                if u is None:
                    missing += 1
                    continue
                side = split["audit" if call.get("audit") else "refactoring"]
                for k in FIELDS:
                    side[k] += u.get(k, 0) or 0
            row["usageRefactoring"], row["usageAudit"] = split["refactoring"], split["audit"]
            if missing:
                row["usageSplitMissingCalls"] = missing
                incomplete += 1
            filled += 1
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline=chr(10))
        print(f"{project} {path.parent.name}: filled {filled} rows ({incomplete} with calls not retrievable)")
    drive.SETUPS = PAIR_SETUPS
    print("publish:", drive.sync("US", project, "backfill usageRefactoring/usageAudit"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prefetch", "apply"))
    parser.add_argument("--project")
    args = parser.parse_args()
    prefetch() if args.mode == "prefetch" else apply(args.project)
