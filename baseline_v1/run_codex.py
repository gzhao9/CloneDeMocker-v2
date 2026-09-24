"""Run OpenAI Codex CLI on one MCI, then judge its result with V2's own validation.

Comparison baseline: an off-the-shelf coding agent given the same MCI, the same model and the
same source, versus CloneDeMocker. Fairness rules:

  * Same input: Codex gets what CloneDeMocker's detector hands our pipeline -- mocked class,
    each test file and method, the duplicated mock statements -- in a neutral task description
    that carries none of our prompts.
  * Same model and effort: gpt-5.6-terra, reasoning effort medium, API-key auth from the
    project's .env (the key is passed to the Codex process only, never printed or written).
  * Same source: a git worktree of the checkout round 1 used; Codex may build and run tests.
  * Same judge: Codex's edited files go through RefactoringAgent's own pipeline (baseline,
    candidate compile+test, audit, classification) with max_retries=0 -- no repair on our side.

Recorded per MCI: Codex wall time, its token usage (from --json events), the files it changed,
and V2's verdict and timings, under data/<project>/refactoring/Codex+Terra-5.6/.

    python baseline_v1/run_codex.py --project cloudstack --mci "java.sql.Connection::1"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402
from baseline_v1 import drive, run_pair  # noqa: E402

HARNESS_CODEX = "Codex"
drive.PROJECT_ROOTS.setdefault("cloudstack", r"D:\Java_projects\Apache\cloudstack")
WORK = Path(r"D:\Java_projects\_codex")


def env_with_key() -> dict:
    run_pair.load_env()
    key = os.environ.get("OPENAI_API_KEY", "")
    env = {**os.environ, "OPENAI_API_KEY": key, "CODEX_API_KEY": key}
    return env


def task_prompt(instance: dict, root: Path) -> str:
    lines = [
        f"This Java project's tests contain duplicated mock setup for `{instance['mockedClass']}`: "
        f"the same mocking statements are repeated across the test methods listed below (a mock clone).",
        "",
        "Refactor the test code to remove this duplication -- for example by extracting a reusable "
        "helper method or a shared mock field -- and make every listed test method use it.",
        "Requirements: keep every test's behaviour identical (same tests, same assertions, same outcome); "
        "change test sources only, never production code or build files; keep the project compiling. "
        "You may build the project and run the affected tests (Maven) to check your change.",
        "",
        "Duplicated mock usages:",
    ]
    for n, seq in enumerate(instance["sequences"], 1):
        path = Path(seq["filePath"])
        rel = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
        stmts = [s["code"] for s in (seq.get("rawStatementInfo") or {}).values() if s.get("isMockRelated")]
        lines.append(f"{n}. {rel} :: {seq['testMethodName']}  (mock variable `{seq.get('variableName', '')}`)")
        lines += [f"     {s}" for s in stmts]
    shared = instance.get("sharedStatements") or []
    if shared:
        lines += ["", "Statements shared by all of them:"] + [f"     {s}" for s in shared]
    return "\n".join(lines)


class CodexAgent(RefactoringAgent):
    """RefactoringAgent whose 'generation' is the file contents Codex left in its worktree."""

    codex_files: dict = {}
    codex_new: dict = {}

    def _generate_staged(self, provider, model, project_root, instances, files, user_instruction="", progress=None):
        edits = [{"path": p.as_posix(), "oldString": files[p], "newString": c}
                 for p, c in self.codex_files.items() if p in files and c != files[p]]
        new = [{"path": p.as_posix(), "content": c} for p, c in self.codex_new.items()]
        return {"canRefactor": bool(edits or new), "reason": "Codex changed no MCI file",
                "edits": edits, "newFiles": new, "summary": "Codex CLI"}, [], []

    def _write_cache(self, key, value):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--mci", required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--sandbox", default="workspace-write")
    args = parser.parse_args()

    project_root = Path(drive.PROJECT_ROOTS.get(args.project) or rf"D:\Java_projects\Apache\{args.project}")
    service = DetectionService(REPO)
    run_id = drive.run_id_for(args.project) if args.project in drive.PROJECT_ROOTS else \
        service.restore_from_data(str(project_root))["runId"]
    _, raw = service.load_raw_detection(run_id)
    instance = next(i for i in service._indexed_instances(raw) if i["id"] == args.mci)

    safe = canonical_store.safe_mci_filename(args.mci)
    tree = WORK / f"{args.project}-{safe}"
    WORK.mkdir(parents=True, exist_ok=True)
    if tree.exists():
        subprocess.run(["git", "-C", str(project_root), "worktree", "remove", "--force", str(tree)])
    subprocess.run(["git", "-C", str(project_root), "worktree", "add", "--detach", str(tree), "HEAD"],
                   check=True, capture_output=True)

    prompt = task_prompt(instance, project_root)
    log_path = WORK / f"{args.project}-{safe}.codex.jsonl"
    m2 = Path.home() / ".m2"
    import shutil
    command = [shutil.which("codex") or "codex", "exec", "--json", "--skip-git-repo-check", "-m", args.model,
               "-c", 'model_reasoning_effort="medium"',
               "-s", args.sandbox, "-c", "sandbox_workspace_write.network_access=true",
               "--add-dir", str(m2), "-C", str(tree), "-"]
    started = time.time()
    with log_path.open("w", encoding="utf-8") as out:
        proc = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace",
                              stdout=out, stderr=subprocess.STDOUT, env=env_with_key())
    wall = round(time.time() - started, 2)

    usage = {}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("usage"):
            for k, v in event["usage"].items():
                if isinstance(v, (int, float)):
                    usage[k] = usage.get(k, 0) + v

    changed = subprocess.run(["git", "-C", str(tree), "diff", "--name-only"], capture_output=True, text=True).stdout.split()
    added = subprocess.run(["git", "-C", str(tree), "ls-files", "--others", "--exclude-standard"],
                           capture_output=True, text=True).stdout.split()
    diff = subprocess.run(["git", "-C", str(tree), "diff"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    (WORK / f"{args.project}-{safe}.codex.diff").write_text(diff, encoding="utf-8")
    CodexAgent.codex_files = {Path(p): (tree / p).read_text(encoding="utf-8") for p in changed if p.endswith(".java")}
    CodexAgent.codex_new = {Path(p): (tree / p).read_text(encoding="utf-8") for p in added if p.endswith(".java")}
    out_of_scope = [p for p in changed if not p.endswith(".java") or "/src/test/" not in p]

    agent = CodexAgent(service, provider=RefactoringAgent._openai_provider("default"))
    result = agent.run(run_id, [args.mci], args.model, user_instruction="", run_pit=False, api_profile="default",
                       use_mock=False, sequence_selection=None, max_retries=0, use_cache=False,
                       progress_callback=None, workspace_id=f"codex-{args.project}")
    entry = canonical_store.entry_from_agent_result(args.mci, result)
    entry.update({"codexExit": proc.returncode, "codexWallSeconds": wall, "codexUsage": usage,
                  "codexChangedFiles": changed, "codexNewFiles": added, "codexOutOfScope": out_of_scope,
                  "codexLog": log_path.name, "model": args.model})
    diff_file = REPO / ".clonedemocker" / "runs" / run_id / "refactoring" / (result.get("proposalId") or "_") / "changes.diff"
    canonical_store.MODEL_DISPLAY_NAMES.setdefault("gpt-5.6-terra", "Terra 5.6")
    canonical_store.merge(project=args.project, repository_root=REPO, entries=[entry], detection_source=None,
                          diff_lookup={args.mci: diff_file} if diff_file.is_file() else {},
                          model=args.model, harness=HARNESS_CODEX, use_mock=False)
    print(json.dumps({"mci": args.mci, "verdict": entry["classification"], "codexExit": proc.returncode,
                      "codexWallSeconds": wall, "usage": usage, "changed": changed, "added": added,
                      "outOfScope": out_of_scope, "validationReason": entry.get("validationReason", "")[:200],
                      "validationTotalSeconds": entry.get("totalSeconds")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
