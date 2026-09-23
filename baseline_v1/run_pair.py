"""Run the V1-vs-V2 comparison pair on one project, MCI by MCI.

For each MCI, on one shared isolated workspace:

  1. V2, CloneDeMocker with its harness: `RefactoringAgent.run` with exactly the arguments the
     previous V2 round used (scripts/run_cloudstack_tail.py): user_instruction="",
     run_pit=False, max_retries=2, use_cache=True.
  2. V1, the original notebook with no harness: `V1RefactoringAgent.run`, max_retries=0,
     use_cache=False.

One detection, two candidates. Validation is V2's, unchanged; nothing re-implements it. The
saving is the baseline: V2 records it in the verification ledger, keyed by source
fingerprint + build scope + harness generation, and V1's run reads the same key and replays
that evidence instead of compiling and testing the untouched project a second time. The two
candidates still each get their own full compile + test, since their code differs.

Refactoring time. `generationSeconds` covers only V2's first two phases, and `totalSeconds`
includes compile+test (validation). Every model call here is timed at the call site, tagged
as refactoring or audit, and stored in `timings`:
  modelSeconds     every refactoring call, repair calls included (the paper's two phases
                   plus the harness loop), audit excluded
  auditSeconds     the LLM-judgment check, which is validation
  modelCallLog     one row per call: seconds, responseId, audit flag

Results land in data/<project>/refactoring/<setup>/ through V2's canonical_store:
  CloneDeMocker+Luna-5.6        V2
  CloneDeMocker-V1+Luna-5.6     V1

Usage:
    python baseline_v1/run_pair.py --run-id <detection run> --project kiota-java-1.10.0 --limit 1
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.model_provider import ModelProvider, ModelResult  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402
from baseline_v1.v1_agent import V1RefactoringAgent  # noqa: E402

canonical_store.MODEL_DISPLAY_NAMES.setdefault("gpt-5.6-luna", "Luna 5.6")
HARNESS_V2 = canonical_store.HARNESS_CLONEDEMOCKER
HARNESS_V1 = "CloneDeMocker-V1"
LOG = REPO / "validation" / "results" / "pair-run.log"


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_env() -> None:
    for candidate in (REPO / ".env", REPO.parent / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class TimedProvider(ModelProvider):
    """Times each model call where it is made. The audit is told apart by its caller."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        audit = any(frame.function == "_audit_refactoring" for frame in inspect.stack()[1:8])
        started = time.perf_counter()
        result = self.inner.generate(instructions, input_text, model)
        self.calls.append({"seconds": round(time.perf_counter() - started, 3),
                           "responseId": result.response_id, "audit": audit})
        return result

    def stamp(self, result: dict) -> dict:
        timings = dict(result.get("timings") or {})
        timings["modelSeconds"] = round(sum(c["seconds"] for c in self.calls if not c["audit"]), 2)
        timings["auditSeconds"] = round(sum(c["seconds"] for c in self.calls if c["audit"]), 2)
        timings["modelCallLog"] = list(self.calls)
        return {**result, "timings": timings}


def record(project: str, mci_id: str, result: dict, run_id: str, model: str, harness: str) -> str:
    entry = canonical_store.entry_from_agent_result(mci_id, result)
    entry["host"] = socket.gethostname()
    if harness == HARNESS_V1 and "V1 output could not be written back" in (entry.get("validationReason") or ""):
        # V1 answered but its answer cannot become code: count it against V1's output, not as
        # the model declining. Flagged so it can be reported separately.
        entry["classification"] = "FAILED_SYNTACTIC_VALIDITY"
        entry["v1WriteBackFailed"] = True
    diff = REPO / ".clonedemocker" / "runs" / run_id / "refactoring" / (result.get("proposalId") or "_") / "changes.diff"
    lookup = {mci_id: diff} if diff.is_file() and diff.stat().st_size else {}
    canonical_store.merge(project=project, repository_root=REPO, entries=[entry], detection_source=None,
                          diff_lookup=lookup, model=model, harness=harness, use_mock=False)
    return entry["classification"]


def done_ids(project: str, model: str, harness: str) -> set[str]:
    label = canonical_store.setup_label(harness, model)
    path = canonical_store.setup_directory(REPO, project, label) / "refactoring-results.json"
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))["results"])
    except (OSError, ValueError, KeyError):
        return set()


def run_project(run_id: str, project: str, model: str = "gpt-5.6-luna", limit: int = 0,
                after_mci=None) -> dict:
    """Run every MCI of one detection run through V2 then V1. Resumable: MCIs already in both
    datasets are skipped. `after_mci(project, mci_id)` runs after each MCI (the driver syncs there)."""
    service = DetectionService(REPO)
    _, raw = service.load_raw_detection(run_id)
    ordered = [item["id"] for item in service._indexed_instances(raw)]
    base = RefactoringAgent._openai_provider("default")
    workspace = f"pair-{project}"

    v2_done = done_ids(project, model, HARNESS_V2)
    v1_done = done_ids(project, model, HARNESS_V1)
    todo = [m for m in ordered if m not in v2_done or m not in v1_done]
    if limit:
        todo = todo[:limit]
    log(f"pair run: {project}, {len(ordered)} MCIs, {len(todo)} to do, model {model}")
    errors = 0
    for mci_id in todo:
        for name, agent_cls, harness, kwargs, done in (
            ("V2", RefactoringAgent, HARNESS_V2, {"max_retries": 2, "use_cache": True}, v2_done),
            ("V1", V1RefactoringAgent, HARNESS_V1, {"max_retries": 0, "use_cache": False}, v1_done),
        ):
            if mci_id in done:
                continue
            provider = TimedProvider(base)
            agent = agent_cls(service, provider=provider)
            started = time.time()
            try:
                result = agent.run(run_id, [mci_id], model, user_instruction="", run_pit=False,
                                   api_profile="default", use_mock=False, sequence_selection=None,
                                   progress_callback=None, workspace_id=workspace, **kwargs)
            except Exception as error:  # noqa: BLE001 - record nothing rather than a false verdict
                errors += 1
                log(f"  {name} {mci_id}: TOOL ERROR {type(error).__name__}: {str(error)[:200]}")
                continue
            result = provider.stamp(result)
            verdict = record(project, mci_id, result, run_id, model, harness)
            t = result["timings"]
            log(f"  {name} {mci_id}: {verdict}  model {t['modelSeconds']}s ({len(provider.calls)} calls), "
                f"audit {t['auditSeconds']}s, total {time.time() - started:.0f}s, "
                f"baseline {'reused' if (result.get('verificationReused') or {}).get('baseline') else 'run'}"
                + (f"  | {result.get('validationReason', '')[:120]}" if verdict != "SUCCESS" else ""))
        if after_mci:
            after_mci(project, mci_id)
    log(f"pair run: {project} pass finished ({errors} tool errors)")
    return {"todo": len(todo), "errors": errors,
            "remaining": len([m for m in ordered if m not in done_ids(project, model, HARNESS_V2)
                              or m not in done_ids(project, model, HARNESS_V1)])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, help="detection run to refactor from")
    parser.add_argument("--project", required=True, help="dataset name under data/, e.g. kiota-java-1.10.0")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--limit", type=int, default=0, help="0 = every MCI")
    args = parser.parse_args()
    load_env()
    run_project(args.run_id, args.project, args.model, args.limit)


if __name__ == "__main__":
    main()
