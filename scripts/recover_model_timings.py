"""Recover per-call model timings from the API, without re-running anything.

Why: `generationSeconds` covers only the initial Encapsulation + Integration calls. The
repair loop (validation failed -> error fed back to the model) is not timed at all, and
`totalSeconds` mixes in baseline/candidate compile+test, which is validation, not
refactoring (paper Fig. 2). Every call's response id is kept in the local
`proposal.json` (`stageLog[*].responseId` per phase call, top-level `responseId` for the
last call, i.e. the repair call when there was one), and the Responses API stores each
response server-side for ~30 days with `created_at` / `completed_at`. So the exact model
time per call can be read back with a GET.

Safe to run beside a live batch:
  * read-only against the API (GET /responses/{id}); no generation, no tokens spent;
  * never runs git and never writes under data/ -- output goes to validation/results/;
  * low concurrency with backoff on 429, so it does not starve the runner's own calls;
  * resumable: ids already fetched are skipped on the next run.

Usage (from the repo root, same .env as the batch):
    python scripts/recover_model_timings.py [--workers 3]

Output: validation/results/model-timings-<hostname>.json, keyed by proposalId:
    {proposalId: {"calls": [{"id", "stage", "variant", "attempt", "created", "completed",
                              "seconds", "status"}], "phaseSeconds", "repairSeconds",
                   "missing": [...]}}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(os.environ.get("CLONEDEMOCKER_REPO") or Path(__file__).resolve().parents[1])
OUT = REPO / "validation" / "results" / f"model-timings-{socket.gethostname()}.json"
RAW = REPO / "validation" / "results" / f"model-timings-{socket.gethostname()}.calls.jsonl"


def load_env() -> None:
    for candidate in (REPO / ".env", REPO.parent / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


EXPECTED_MODEL = "gpt-5.6-terra"
models: dict[str, str | None] = {}
fromcache: dict[str, str] = {}


def collect() -> dict[str, list[dict]]:
    """proposalId -> the calls it made, in order, from every local proposal.json."""
    found: dict[str, list[dict]] = {}
    models.clear()
    for path in glob.glob(str(REPO / ".clonedemocker" / "runs" / "*" / "refactoring" / "*" / "proposal.json")):
        try:
            proposal = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = proposal.get("proposalId") or Path(path).parent.name
        models[pid] = proposal.get("model")
        calls, seen = [], set()
        source = proposal
        cache = proposal.get("cache") or {}
        if cache.get("hit") and cache.get("key"):
            # A cache replay made no calls of its own; its answer came from the run that first
            # produced it. That run's calls are stored in the cache entry, so time those.
            original = None
            for folder in sorted((REPO / ".clonedemocker").glob("proposal-cache*")):
                entry = folder / f"{cache['key']}.json"
                if entry.is_file():
                    try:
                        original = json.loads(entry.read_text(encoding="utf-8")).get("response") or {}
                    except (OSError, json.JSONDecodeError):
                        original = None
                    break
            if not original:
                continue
            source = original
            models[pid] = original.get("model") or models[pid]
            fromcache[pid] = cache["key"]
        proposal = source
        for step in proposal.get("stageLog") or []:
            rid = step.get("responseId")
            if rid and rid.startswith("resp_") and rid not in seen:
                seen.add(rid)
                calls.append({"id": rid, "stage": step.get("stage"), "variant": step.get("variant"),
                              "attempt": step.get("attempt")})
        last = proposal.get("responseId")
        # A cache replay records a "cache-..." placeholder, not a call: nothing to time.
        if last and last.startswith("resp_") and last not in seen:
            # Not one of the phase calls, so it came after them: the repair call.
            calls.append({"id": last, "stage": "REPAIR", "variant": None,
                          "attempt": proposal.get("repairAttemptsUsed")})
        if calls:
            found[pid] = calls
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    load_env()
    from openai import OpenAI, RateLimitError, NotFoundError
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, max_retries=0, timeout=60)

    proposals = collect()
    done: dict[str, dict] = {}
    if RAW.is_file():
        for line in RAW.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["id"]] = row
    todo = [c["id"] for calls in proposals.values() for c in calls if c["id"] not in done]
    print(f"{len(proposals)} proposals, {sum(map(len, proposals.values()))} calls, "
          f"{len(done)} already fetched, {len(todo)} to fetch", flush=True)

    lock = threading.Lock()
    RAW.parent.mkdir(parents=True, exist_ok=True)

    def fetch(rid: str) -> None:
        for attempt in range(6):
            try:
                r = client.responses.retrieve(rid)
                row = {"id": rid, "created": r.created_at, "completed": getattr(r, "completed_at", None),
                       "status": r.status}
                break
            except RateLimitError:
                time.sleep(2 ** attempt)
            except NotFoundError:
                row = {"id": rid, "created": None, "completed": None, "status": "NOT_FOUND"}
                break
            except Exception as exc:  # network blips: back off, then record the failure
                if attempt == 5:
                    row = {"id": rid, "created": None, "completed": None, "status": f"ERROR {type(exc).__name__}"}
                    break
                time.sleep(2 ** attempt)
        else:
            row = {"id": rid, "created": None, "completed": None, "status": "RATE_LIMITED"}
        with lock:
            done[rid] = row
            with RAW.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            if len(done) % 200 == 0:
                print(f"  fetched {len(done)}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(fetch, todo))

    result = {}
    for pid, calls in proposals.items():
        out, phase, repair, missing = [], 0.0, 0.0, []
        for c in calls:
            row = done.get(c["id"], {})
            secs = (row["completed"] - row["created"]) if row.get("created") and row.get("completed") else None
            out.append({**c, "created": row.get("created"), "completed": row.get("completed"),
                        "seconds": secs, "status": row.get("status")})
            if secs is None:
                missing.append(c["id"])
            elif c["stage"] == "REPAIR":
                repair += secs
            else:
                phase += secs
        # A call we could not time must never read as 0 s: a proposal with any missing call
        # gets null totals, and one produced by another model is kept out of the numbers.
        excluded = models.get(pid) != EXPECTED_MODEL
        complete = not missing and not excluded
        result[pid] = {"model": models.get(pid), "excluded": excluded, "calls": out,
                       "fromCacheKey": fromcache.get(pid),
                       "phaseSeconds": round(phase, 2) if complete else None,
                       "repairSeconds": round(repair, 2) if complete else None,
                       "missing": missing}
    OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    bad = sum(1 for r in done.values() if r.get("created") is None)
    print(f"wrote {OUT.name}: {len(result)} proposals, {len(done)} calls, {bad} without timing", flush=True)


if __name__ == "__main__":
    sys.exit(main())
