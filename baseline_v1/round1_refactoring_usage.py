"""Exact refactoring tokens for round-1 rows, without re-running anything.

Round 1 stored one `usage` per row that sums the refactoring calls and the audit call. The
refactoring calls' response ids are already known per proposal (stageLog + repair id, collected
by recover_model_timings.py into validation/results/model-timings-<host>.json); the audit's id
was never stored. So: read each refactoring call's usage back from the API (GET, no tokens),
sum per proposal -> exact refactoring usage; audit usage = stored row usage - refactoring usage.

    python baseline_v1/round1_refactoring_usage.py            # this host's proposals
Output: validation/results/round1-usage-<host>.json  {proposalId: {"refactoring": {...}, "calls": n, "missing": n}}
Resumable: per-call results are cached in round1-usage-<host>.calls.jsonl.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from baseline_v1 import run_pair  # noqa: E402
from baseline_v1.usage_by_calls import FIELDS, usage_of  # noqa: E402

HOST = socket.gethostname()
TIMINGS = REPO / "validation" / "results" / f"model-timings-{HOST}.json"
CALLS = REPO / "validation" / "results" / f"round1-usage-{HOST}.calls.jsonl"
OUT = REPO / "validation" / "results" / f"round1-usage-{HOST}.json"


def main() -> None:
    run_pair.load_env()
    import os
    from openai import OpenAI
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, timeout=60, max_retries=3)
    timings = json.loads(TIMINGS.read_text(encoding="utf-8"))
    wanted = {c["id"] for t in timings.values() for c in t["calls"] if str(c["id"]).startswith("resp_")}
    have = {}
    if CALLS.is_file():
        for line in CALLS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                have[row["id"]] = row["usage"]
    todo = sorted(wanted - set(have))
    print(f"{len(timings)} proposals, {len(wanted)} calls, {len(have)} cached, {len(todo)} to fetch", flush=True)
    lock = threading.Lock()

    def fetch(rid: str) -> None:
        u = usage_of(client, rid)
        with lock:
            have[rid] = u
            with CALLS.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": rid, "usage": u}) + "\n")
            if len(have) % 500 == 0:
                print(f"  {len(have)}", flush=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, todo))
    out = {}
    for pid, t in timings.items():
        acc, missing, n = dict.fromkeys(FIELDS, 0), 0, 0
        for c in t["calls"]:
            if not str(c["id"]).startswith("resp_"):
                continue
            n += 1
            u = have.get(c["id"])
            if u is None:
                missing += 1
                continue
            for k in FIELDS:
                acc[k] += u[k]
        out[pid] = {"refactoring": acc, "calls": n, "missing": missing, "fromCacheKey": t.get("fromCacheKey")}
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(out)} proposals, incomplete {sum(1 for v in out.values() if v['missing'])}")


if __name__ == "__main__":
    main()
