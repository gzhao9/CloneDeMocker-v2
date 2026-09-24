"""Exact per-call token usage from the API, for rows whose calls were logged (modelCallLog).

Each stored row's `usage` sums the refactoring calls *and* the audit call (validation), and a
failed row records only its last call. The pair and measured runs log every call's response
id with an audit flag, so this reads each response back (GET, no tokens spent) and writes,
per row, refactoring usage and audit usage separately.

    python baseline_v1/usage_by_calls.py --project kiota-java-1.10.0 --setup CloneDeMocker-measured+Terra-5.6
Output: validation/results/usage-<project>-<setup>.json  {mciId: {"refactoring": {...}, "audit": {...}, "missing": n}}
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from baseline_v1 import run_pair  # noqa: E402

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")


def usage_of(client, rid: str) -> dict | None:
    try:
        u = client.responses.retrieve(rid).usage
    except Exception:  # noqa: BLE001 - expired or unknown id
        return None
    return {"input_tokens": u.input_tokens,
            "cached_input_tokens": getattr(u.input_tokens_details, "cached_tokens", 0) or 0,
            "output_tokens": u.output_tokens,
            "reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--setup", required=True)
    args = parser.parse_args()
    run_pair.load_env()
    import os
    from openai import OpenAI
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, timeout=60, max_retries=3)
    rows = json.loads((REPO / "data" / args.project / "refactoring" / args.setup / "refactoring-results.json")
                      .read_text(encoding="utf-8"))["results"]
    calls = [(mci, c) for mci, v in rows.items() for c in ((v.get("timings") or {}).get("modelCallLog") or [])]
    with ThreadPoolExecutor(max_workers=3) as pool:
        got = list(pool.map(lambda mc: usage_of(client, mc[1]["responseId"]), calls))
    out: dict = {}
    for (mci, call), u in zip(calls, got):
        slot = out.setdefault(mci, {"refactoring": dict.fromkeys(FIELDS, 0), "audit": dict.fromkeys(FIELDS, 0),
                                    "calls": 0, "missing": 0})
        slot["calls"] += 1
        if u is None:
            slot["missing"] += 1
            continue
        side = slot["audit" if call.get("audit") else "refactoring"]
        for k in FIELDS:
            side[k] += u[k]
    dest = REPO / "validation" / "results" / f"usage-{args.project}-{args.setup}.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"{len(out)} rows, {len(calls)} calls, missing {sum(v['missing'] for v in out.values())} -> {dest.name}")


if __name__ == "__main__":
    main()
