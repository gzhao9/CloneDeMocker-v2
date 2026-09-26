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
import hashlib
import json
import re
import shutil
import socket
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
    # Pre-configured build environment (not part of the refactoring): Codex's sandbox reports
    # the user's home as C:\, so Maven looked for C:\.m2 and failed, and PowerShell splits an
    # unquoted -Dmaven.repo.local=... at the dot. MAVEN_OPTS reaches every mvn it runs.
    repo = Path.home() / ".m2" / "repository"
    env["MAVEN_OPTS"] = (env.get("MAVEN_OPTS", "") + f" -Dmaven.repo.local={repo}").strip()
    env["GRADLE_USER_HOME"] = str(Path.home() / ".gradle")
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


def precise_prompt(instance: dict, root: Path) -> str:
    """Neutral task plus exact facts: method sources, module and test classes, the build
    environment, and output discipline. No V2 workflow, helper shape, placement or retry rule."""
    modules, classes = [], []
    for seq in instance["sequences"]:
        path = Path(seq["filePath"])
        rel = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
        module = rel.split("/src/test/")[0]
        if module not in modules:
            modules.append(module)
        if path.stem not in classes:
            classes.append(path.stem)
    lines = [task_prompt(instance, root), "",
             "Exact sources of the listed test methods (as they are now):"]
    for n, seq in enumerate(instance["sequences"], 1):
        lines += [f"--- {n}. {seq['testMethodName']} ---", (seq.get("testMethodRawCode") or "").rstrip()]
    lines += ["",
              "Environment (already configured; fixing it is not part of the task):",
              f"- Build tool: Maven. Module(s): {', '.join(modules)}. Affected test classes: {', '.join(classes)}.",
              "- The local Maven repository is preset through MAVEN_OPTS; do not pass -Dmaven.repo.local.",
              "- The shell is PowerShell: quote any -D argument that contains a comma or a dot.",
              "",
              "Working rules: read only the parts of files you need (search or line ranges), and keep "
              "command output short -- do not print whole large files or full build logs; show only "
              "errors or test summaries. Deliver the change as edits in this working tree."]
    return chr(10).join(lines)


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


def plan_prompt(instance: dict, root: Path, project: str) -> str:
    """Neutral task plus the refactoring CloneDeMocker planned for this MCI in round 1 (terra):
    the reusable helper it introduced (code) and the sites to switch over to it. The per-test
    rewrites are left to Codex."""
    diff_path = (REPO / "data" / project / "refactoring" / "CloneDeMocker+Terra-5.6" / "diffs"
                 / canonical_store.safe_mci_filename(instance["id"]))
    helper, current_file = [], None
    if diff_path.is_file():
        # New code = runs of added lines that do not directly follow removed lines (pure
        # insertions: a helper method, a shared mock field, or a whole new file). Added lines
        # right after removed ones are the call-site rewrites, which are left to Codex.
        after_removal, run = False, []

        def flush() -> None:
            if run and any(x.strip() for x in run):
                helper.append(f"// in {current_file}")
                helper.extend(run)
            run.clear()

        for raw in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.rstrip(chr(13))
            if line.startswith("+++ "):
                flush()
                current_file = line[4:].removeprefix("b/")
            elif line.startswith("--- ") or line.startswith("@@"):
                flush()
                after_removal = False
            elif line.startswith("-"):
                flush()
                after_removal = True
            elif line.startswith("+"):
                if not after_removal:
                    run.append(line[1:])
            else:
                flush()
                after_removal = False
        flush()
    lines = [task_prompt(instance, root), ""]
    if helper:
        lines += ["Planned refactoring -- follow this plan:",
                  "1. Add this reusable helper (place it as indicated):", *helper, "",
                  "2. In every listed test method, replace the duplicated mock statements with the new code "
                  "(call the helper, or use the shared mock), adapting arguments to each test; keep everything "
                  "else unchanged."]
    else:
        lines += ["(No plan is available for this MCI; choose the approach yourself.)"]
    lines += ["", "Environment (already configured; fixing it is not part of the task): Maven's local "
              "repository and Gradle's user home are preset; the shell is PowerShell, so quote -D arguments "
              "that contain a comma or a dot. Keep command output short (errors and test summaries only)."]
    return chr(10).join(lines)


def run_one(project: str, mci: str, variant: str, model: str, sandbox: str, service, run_id: str) -> dict:
    project_root = Path(drive.PROJECT_ROOTS[project])
    _, raw = service.load_raw_detection(run_id)
    instance = next(i for i in service._indexed_instances(raw) if i["id"] == mci)
    safe = canonical_store.safe_mci_filename(mci).removesuffix(".diff") + ("" if variant == "neutral" else f"-{variant}")
    # Long generic MCI ids push files deep in the worktree past MAX_PATH; name those by hash.
    short = safe if len(safe) <= 60 else "h" + hashlib.sha1(safe.encode("utf-8")).hexdigest()[:12]
    tree = WORK / f"{project}-{short}"
    WORK.mkdir(parents=True, exist_ok=True)
    if tree.exists():
        subprocess.run(["git", "-C", str(project_root), "worktree", "remove", "--force", str(tree)], capture_output=True)
    subprocess.run(["git", "-C", str(project_root), "worktree", "prune"], capture_output=True)
    subprocess.run(["git", "-C", str(project_root), "worktree", "add", "--detach", str(tree), "HEAD"],
                   check=True, capture_output=True)
    prompt = {"neutral": lambda: task_prompt(instance, project_root),
              "precise": lambda: precise_prompt(instance, project_root),
              "plan": lambda: plan_prompt(instance, project_root, project)}[variant]()
    (WORK / f"{project}-{safe}.prompt.txt").write_text(prompt, encoding="utf-8")
    log_path = WORK / f"{project}-{safe}.codex.jsonl"
    command = [shutil.which("codex") or "codex", "exec", "--json", "--skip-git-repo-check", "-m", model,
               "-c", 'model_reasoning_effort="medium"', "-s", sandbox,
               "-c", "sandbox_workspace_write.network_access=true", "-c", "shell_environment_policy.inherit=all",
               "--add-dir", str(Path.home() / ".m2"), "--add-dir", str(Path.home() / ".gradle"),
               "-C", str(tree), "-"]
    started = time.time()
    try:
        with log_path.open("w", encoding="utf-8") as out:
            proc = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace",
                                  stdout=out, stderr=subprocess.STDOUT, env=env_with_key(), timeout=1800)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    wall = round(time.time() - started, 2)
    usage: dict = {}
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
    (WORK / f"{project}-{safe}.codex.diff").write_text(diff, encoding="utf-8")
    CodexAgent.codex_files = {Path(p): (tree / p).read_text(encoding="utf-8") for p in changed if p.endswith(".java")}
    CodexAgent.codex_new = {Path(p): (tree / p).read_text(encoding="utf-8") for p in added if p.endswith(".java")}
    out_of_scope = [p for p in changed if not p.endswith(".java") or "/src/test/" not in p]
    agent = CodexAgent(service, provider=RefactoringAgent._openai_provider("default"))
    result = agent.run(run_id, [mci], model, user_instruction="", run_pit=False, api_profile="default",
                       use_mock=False, sequence_selection=None, max_retries=0, use_cache=False,
                       progress_callback=None, workspace_id=f"codex-{project}" + ("" if variant == "neutral" else f"-{variant}"))
    entry = canonical_store.entry_from_agent_result(mci, result)
    entry.update({"codexExit": code, "codexWallSeconds": wall, "codexUsage": usage,
                  "codexChangedFiles": changed, "codexNewFiles": added, "codexOutOfScope": out_of_scope,
                  "codexLog": log_path.name, "model": model, "codexPrompt": variant, "host": socket.gethostname()})
    diff_file = REPO / ".clonedemocker" / "runs" / run_id / "refactoring" / (result.get("proposalId") or "_") / "changes.diff"
    canonical_store.MODEL_DISPLAY_NAMES.setdefault("gpt-5.6-terra", "Terra 5.6")
    canonical_store.merge(project=project, repository_root=REPO, entries=[entry], detection_source=None,
                          diff_lookup={mci: diff_file} if diff_file.is_file() else {}, model=model,
                          harness=HARNESS_CODEX if variant == "neutral" else f"{HARNESS_CODEX}-{variant}", use_mock=False)
    subprocess.run(["git", "-C", str(project_root), "worktree", "remove", "--force", str(tree)], capture_output=True)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--mci", help="one MCI; omit with --all")
    parser.add_argument("--all", action="store_true", help="every MCI of the project")
    parser.add_argument("--variants", default="neutral", help="comma list of neutral, precise, plan")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--sandbox", default="workspace-write")
    args = parser.parse_args()
    # This process publishes only the Codex result directories (the pair lanes run elsewhere).
    drive.SETUPS = ("Codex+Terra-5.6", "Codex-plan+Terra-5.6", "Codex-precise+Terra-5.6")
    service = DetectionService(REPO)
    run_id = drive.run_id_for(args.project)
    _, raw = service.load_raw_detection(run_id)
    mcis = [i["id"] for i in service._indexed_instances(raw)] if args.all else [args.mci]
    for variant in args.variants.split(","):
        label = canonical_store.setup_label(HARNESS_CODEX if variant == "neutral" else f"{HARNESS_CODEX}-{variant}", args.model)
        path = canonical_store.setup_directory(REPO, args.project, label) / "refactoring-results.json"
        done = set(json.loads(path.read_text(encoding="utf-8"))["results"]) if path.is_file() else set()
        for mci in mcis:
            if mci in done:
                continue
            try:
                e = run_one(args.project, mci, variant, args.model, args.sandbox, service, run_id)
                u = e.get("codexUsage") or {}
                run_pair.log(f"  CODEX[{variant}] {args.project} {mci}: {e['classification']}  wall {e['codexWallSeconds']}s "
                             f"in {u.get('input_tokens', 0)} (cached {u.get('cached_input_tokens', 0)}) out {u.get('output_tokens', 0)}")
            except Exception as error:  # noqa: BLE001 - one MCI must not end the batch
                run_pair.log(f"  CODEX[{variant}] {args.project} {mci}: TOOL ERROR {type(error).__name__}: {str(error)[:200]}")
            drive.sync("CX", args.project, f"codex {variant}")


if __name__ == "__main__":
    main()
