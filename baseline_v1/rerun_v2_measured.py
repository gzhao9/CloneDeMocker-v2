"""Re-run CloneDeMocker V2 on one project with every model call measured, for the Codex comparison.

Same conditions as the Codex runs: this host, the same checkout, the same build environment,
gpt-5.6-terra, and the full 4-check validation (run_pit=True). Every call is timed and its
response id kept, with the audit call flagged, so refactoring tokens and audit tokens can be
separated exactly afterwards (usage_by_calls.py). Proposal cache off, as in the pair runs.

Results go to data/<project>/refactoring/CloneDeMocker-measured+Terra-5.6/, next to round 1.

    python baseline_v1/rerun_v2_measured.py --project kiota-java-1.10.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402
from baseline_v1 import drive, run_pair  # noqa: E402

HARNESS = "CloneDeMocker-measured"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    args = parser.parse_args()
    run_pair.load_env()
    canonical_store.MODEL_DISPLAY_NAMES.setdefault("gpt-5.6-terra", "Terra 5.6")
    label = canonical_store.setup_label(HARNESS, args.model)
    drive.SETUPS = (canonical_store.setup_dirname(label),)
    service = DetectionService(REPO)
    run_id = drive.run_id_for(args.project)
    _, raw = service.load_raw_detection(run_id)
    path = canonical_store.setup_directory(REPO, args.project, label) / "refactoring-results.json"
    done = set(json.loads(path.read_text(encoding="utf-8"))["results"]) if path.is_file() else set()
    base = RefactoringAgent._openai_provider("default")
    for mci in [i["id"] for i in service._indexed_instances(raw)]:
        if mci in done:
            continue
        provider = run_pair.TimedProvider(base)
        agent = run_pair.PairV2Agent(service, provider=provider)
        started = time.time()
        try:
            result = agent.run(run_id, [mci], args.model, user_instruction="", run_pit=True, api_profile="default",
                               use_mock=False, sequence_selection=None, max_retries=2, use_cache=False,
                               progress_callback=None, workspace_id=f"measured-{args.project}")
        except Exception as error:  # noqa: BLE001
            run_pair.log(f"  MEASURED {args.project} {mci}: TOOL ERROR {type(error).__name__}: {str(error)[:200]}")
            continue
        result = provider.stamp(result)
        verdict = run_pair.record(args.project, mci, result, run_id, args.model, HARNESS)
        t = result["timings"]
        cand = (result.get("harness") or {}).get("candidate") or {}
        run_pair.log(f"  MEASURED {args.project} {mci}: {verdict} model {t['modelSeconds']}s ({len(provider.calls)} calls) "
                     f"audit {t['auditSeconds']}s pit {cand.get('pitStatus')} total {time.time() - started:.0f}s")
        drive.sync("MS", args.project, "measured V2 re-run")


if __name__ == "__main__":
    main()
