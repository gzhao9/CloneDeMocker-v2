"""Re-judge already-produced candidates with the full 4-check validation (PIT included).

Codex's kiota results were first judged with run_pit=False, i.e. 3 of the 4 checks, while
round 1 on kiota ran PIT. This re-runs RefactoringAgent's own validation with run_pit=True on
the exact files each candidate produced -- read back from its proposal's candidate-files/ and
manifest.json -- so no model or agent is called again. The row is replaced; the original
agent fields (codex*, model call log) are kept, and `revalidatedWithPit` records the source.

    python baseline_v1/revalidate_pit.py --project kiota-java-1.10.0 --setup Codex+Terra-5.6
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402
from baseline_v1 import drive, run_pair  # noqa: E402


class ReplayAgent(RefactoringAgent):
    """Generation = the candidate files a previous run produced; nothing is generated."""

    files_now: dict = {}
    files_new: dict = {}

    def _generate_staged(self, provider, model, project_root, instances, files, user_instruction="", progress=None):
        edits = [{"path": p.as_posix(), "oldString": files[p], "newString": c}
                 for p, c in self.files_now.items() if p in files and c != files[p]]
        new = [{"path": p.as_posix(), "content": c} for p, c in self.files_new.items()]
        return {"canRefactor": bool(edits or new), "reason": "no candidate files", "edits": edits,
                "newFiles": new, "summary": "replayed candidate"}, [], []

    def _write_cache(self, key, value):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--setup", required=True, help="dataset directory, e.g. Codex+Terra-5.6")
    parser.add_argument("--model", default="gpt-5.6-terra")
    args = parser.parse_args()
    run_pair.load_env()
    drive.SETUPS = (args.setup,)
    service = DetectionService(REPO)
    run_id = drive.run_id_for(args.project)
    directory = REPO / "data" / args.project / "refactoring" / args.setup
    rows = json.loads((directory / "refactoring-results.json").read_text(encoding="utf-8"))["results"]
    harness_label = json.loads((directory / "setup.json").read_text(encoding="utf-8"))["harness"]
    for mci, row in rows.items():
        if row.get("revalidatedWithPit"):
            continue
        proposal = next((REPO / ".clonedemocker" / "runs").glob(f"*/refactoring/{row['proposalId']}"), None)
        if proposal is None or not (proposal / "manifest.json").is_file():
            run_pair.log(f"  PIT-REVALIDATE {args.project} {mci}: no candidate files for {row.get('proposalId')}; skipped")
            continue
        manifest = json.loads((proposal / "manifest.json").read_text(encoding="utf-8"))
        now, new = {}, {}
        for rel, original_hash in manifest.items():
            content = (proposal / "candidate-files" / rel).read_text(encoding="utf-8")
            (new if original_hash is None else now)[Path(rel)] = content
        ReplayAgent.files_now, ReplayAgent.files_new = now, new
        agent = ReplayAgent(service, provider=RefactoringAgent._openai_provider("default"))
        result = agent.run(run_id, [mci], args.model, user_instruction="", run_pit=True, api_profile="default",
                           use_mock=False, sequence_selection=None, max_retries=0, use_cache=False,
                           progress_callback=None, workspace_id=f"pit-{args.project}")
        entry = canonical_store.entry_from_agent_result(mci, result)
        keep = {k: v for k, v in row.items() if k.startswith("codex") or k in ("host",)}
        entry.update(keep)
        entry.update({"model": row.get("model") or args.model, "revalidatedWithPit": row.get("proposalId")})
        diff_file = REPO / ".clonedemocker" / "runs" / run_id / "refactoring" / (result.get("proposalId") or "_") / "changes.diff"
        canonical_store.merge(project=args.project, repository_root=REPO, entries=[entry], detection_source=None,
                              diff_lookup={mci: diff_file} if diff_file.is_file() else {}, model=args.model,
                              harness=harness_label, use_mock=False)
        cand = (entry.get("harness") or {}).get("candidate") or {}
        run_pair.log(f"  PIT-REVALIDATE {args.project} {mci} [{args.setup}]: {entry['classification']} "
                     f"pit {cand.get('pitStatus')} score {cand.get('mutationScore')} regressed {entry.get('mutationRegressed')}")
        drive.sync("PIT", args.project, f"PIT re-validation of {args.setup}")


if __name__ == "__main__":
    main()
