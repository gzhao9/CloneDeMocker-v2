"""Add PIT (the fourth check) to finished rows, from the published diffs, on any host.

Rounds 1-3 and the Codex runs were validated without PIT (except kiota). This runs the same
harness with run_pit=True on each SUCCESS row's candidate, rebuilt from the row's published
`diffs/<mci>.diff` on top of the untouched project checkout. Nothing is generated: no model
call, and the LLM audit is skipped (it is not part of the PIT measurement, and re-calling it
would spend tokens). The row's verdict is never changed.

Per MCI, every refactoring round of the project runs back to back, so the baseline PIT (the
verification ledger) and the warm workspace are shared by all of them. A project is assigned to
one host. Output is a separate file per dataset and host, so hosts never write the same file and
the running lanes are never touched:
    data/<project>/refactoring/<setup>/pit/<host>.json   {mciId: {...PIT evidence...}}
The PIT fields are folded into the rows once, after every lane has finished.

    python baseline_v1/pit_from_diff.py --project dubbo-3.3.6 \\
        --root dubbo-3.3.6=D:\\Java_projects\\Apache\\dubbo-3.3.6 [--setups all] [--limit 5]
Resumable: rows already in any host's pit/*.json for that dataset are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402
from baseline_v1 import drive, run_pair  # noqa: E402

HOST = socket.gethostname()
KEEP = ("compileStatus", "testStatus", "pitStatus", "mutationScore", "mutants", "killedMutants",
        "survivedMutants", "noCoverageMutants", "timedOutMutants", "durations", "commands", "scope")

# Every refactoring round of a project. Round 1 = V2 terra; rounds 2/3 = V2 and V1 on luna;
# Codex only on kiota and dubbo. spring-integration's round-1 directory carries a wrong
# "deepseek-chat" label (the calls were terra).
SETUPS_BY_PROJECT = {
    "kiota-java-1.10.0": ["CloneDeMocker+Terra-5.6", "CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6",
                          "Codex+Terra-5.6", "Codex-plan+Terra-5.6"],
    "dubbo-3.3.6": ["CloneDeMocker+Terra-5.6", "CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6",
                    "Codex+Terra-5.6", "Codex-plan+Terra-5.6"],
    "druid-37.0.0": ["CloneDeMocker+Terra-5.6", "CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6"],
    "spring-security-7.1.1": ["CloneDeMocker+Terra-5.6", "CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6"],
    "spring-integration-7.1.1": ["CloneDeMocker+deepseek-chat", "CloneDeMocker+Luna-5.6",
                                 "CloneDeMocker-V1+Luna-5.6"],
    "cloudstack": ["CloneDeMocker+Terra-5.6", "CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6"],
}


class DiffReplayAgent(RefactoringAgent):
    """Generation = the published diff applied to the original files; no model, no audit."""

    patched: dict = {}

    def _generate_staged(self, provider, model, project_root, instances, files, user_instruction="", progress=None):
        edits, new = [], []
        for rel, content in self.patched.items():
            path = Path(rel)
            if path in files:
                if content != files[path]:
                    edits.append({"path": path.as_posix(), "oldString": files[path], "newString": content})
            else:
                new.append({"path": path.as_posix(), "content": content})
        return {"canRefactor": bool(edits or new), "reason": "empty diff", "edits": edits,
                "newFiles": new, "summary": "replayed published diff"}, [], []

    @staticmethod
    def _audit_refactoring(*args, **kwargs):
        return {"status": "SKIPPED", "risk": "SKIPPED", "reason": "PIT back-fill: audit not re-run"}

    def _write_cache(self, key, value):
        return None


def apply_diff(project_root: Path, diff_text: str) -> dict[str, str]:
    """Return {relative path: patched content (LF)} for every file the diff touches."""
    targets = [line[4:].strip().removeprefix("b/") for line in diff_text.splitlines()
               if line.startswith("+++ ") and not line.startswith("+++ /dev/null")]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for rel in targets:
            source = project_root / rel
            if source.is_file():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
                (work / rel).write_text(text, encoding="utf-8", newline="\n")
        (work / "change.diff").write_text(diff_text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        # Plain first: --recount ignores hunk counts, so in a multi-file diff it swallows the
        # next file's header as a removed line (E-003: 88 of 467 dubbo diffs). It stays only as
        # a fallback for a diff whose counts are off.
        for extra in ([], ["--recount"]):
            done = subprocess.run(["git", "apply", "--whitespace=nowarn", *extra, "change.diff"],
                                  cwd=work, capture_output=True, text=True)
            if done.returncode == 0:
                break
        if done.returncode != 0:
            raise RuntimeError(f"diff does not apply: {done.stderr.strip()[:300]}")
        return {rel: (work / rel).read_text(encoding="utf-8") for rel in targets if (work / rel).is_file()}


def slim(evidence: dict | None) -> dict | None:
    return {k: evidence.get(k) for k in KEEP if k in evidence} if evidence else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--setups", default="all", help="comma list of dataset directories, or 'all'")
    parser.add_argument("--root", action="append", default=[], help="NAME=PATH of this host's checkout")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many MCIs")
    parser.add_argument("--publish-every", type=int, default=5, help="publish after this many MCIs")
    parser.add_argument("--no-publish", action="store_true", help="trial run: keep the files local")
    args = parser.parse_args()
    for pair in args.root:
        name, _, path = pair.partition("=")
        drive.PROJECT_ROOTS[name] = path
    run_pair.load_env()
    project_root = Path(drive.PROJECT_ROOTS[args.project])
    setups = SETUPS_BY_PROJECT[args.project] if args.setups == "all" else args.setups.split(",")
    base = REPO / "data" / args.project / "refactoring"
    data = {}
    for setup in setups:
        directory = base / setup
        rows = json.loads((directory / "refactoring-results.json").read_text(encoding="utf-8"))["results"]
        model = json.loads((directory / "setup.json").read_text(encoding="utf-8")).get("model") or "gpt-5.6-terra"
        out_path = directory / "pit" / f"{HOST}.json"
        out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() else {}
        done = set(out)
        if (directory / "pit").is_dir():
            for other in (directory / "pit").glob("*.json"):
                done |= set(json.loads(other.read_text(encoding="utf-8")))
        data[setup] = {"dir": directory, "rows": rows, "model": model, "out_path": out_path, "out": out, "done": done}
    mcis: list[str] = []
    for setup in setups:
        for mci, row in data[setup]["rows"].items():
            if row.get("classification") == "SUCCESS" and mci not in data[setup]["done"] and mci not in mcis:
                mcis.append(mci)
    if args.limit:
        mcis = mcis[:args.limit]
    service = DetectionService(REPO)
    run_id = drive.run_id_for(args.project)
    run_pair.log(f"PIT-DIFF {args.project} on {HOST}: {len(mcis)} MCIs x {len(setups)} setups")
    pending = 0
    for mci in mcis:
        for setup in setups:
            d = data[setup]
            row = d["rows"].get(mci)
            if not row or row.get("classification") != "SUCCESS" or mci in d["done"]:
                continue
            d["out"][mci] = run_one(service, run_id, args.project, project_root, setup, d, mci, row)
            d["out_path"].parent.mkdir(parents=True, exist_ok=True)
            d["out_path"].write_text(json.dumps(d["out"], ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        pending += 1
        if pending >= args.publish_every and not args.no_publish:
            publish([data[s]["out_path"] for s in setups if data[s]["out_path"].is_file()], args.project)
            pending = 0
    if pending and not args.no_publish:
        publish([data[s]["out_path"] for s in setups if data[s]["out_path"].is_file()], args.project)
    run_pair.log(f"PIT-DIFF {args.project} on {HOST}: done")


def run_one(service, run_id, project, project_root, setup, d, mci, row) -> dict:
    diff_path = d["dir"] / "diffs" / canonical_store.safe_mci_filename(mci)
    started = time.time()
    record = {"host": HOST, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "verdict": row.get("classification")}
    try:
        DiffReplayAgent.patched = apply_diff(project_root, diff_path.read_text(encoding="utf-8"))
        agent = DiffReplayAgent(service, provider=RefactoringAgent._openai_provider("default"))
        result = agent.run(run_id, [mci], d["model"], user_instruction="", run_pit=True, api_profile="default",
                           use_mock=False, sequence_selection=None, max_retries=0, use_cache=False,
                           progress_callback=None, workspace_id=f"pitdiff-{project}")
        harness = result.get("harness") or {}
        record.update({
            "classificationWithPit": canonical_store.classify_agent_result(result),
            "baseline": slim(harness.get("baseline")), "candidate": slim(harness.get("candidate")),
            "mutationScoreDelta": harness.get("mutationScoreDelta"),
            "mutationRegressed": harness.get("mutationRegressed"),
            "verificationReused": result.get("verificationReused") or {},
        })
    except Exception as error:  # noqa: BLE001 - record the failure, keep going
        record["error"] = f"{type(error).__name__}: {str(error)[:300]}"
    record["seconds"] = round(time.time() - started, 1)
    cand = record.get("candidate") or {}
    run_pair.log(f"  PIT-DIFF {project} {mci} [{setup}]: {record.get('classificationWithPit') or record.get('error')} "
                 f"pit {cand.get('pitStatus')} score {cand.get('mutationScore')} {record['seconds']}s")
    return record


def publish(paths: list[Path], project: str) -> None:
    """Push this host's PIT files alone, on the remote tip, with a private index (never deletes)."""
    rels = [path.relative_to(REPO).as_posix() for path in paths]

    def git(*a, env=None):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True,
                              env={**os.environ, **(env or {})})

    for attempt in range(5):
        with tempfile.TemporaryDirectory() as tmp:
            idx = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if git("fetch", "-q", drive.REMOTE, "main").returncode == 0:
                base = git("rev-parse", "--verify", f"{drive.REMOTE}/main^{{commit}}").stdout.strip()
                if base and git("read-tree", base, env=idx).returncode == 0:
                    for rel in rels:
                        blob = git("hash-object", "-w", "--", rel).stdout.strip()
                        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=idx)
                    tree = git("write-tree", env=idx).stdout.strip()
                    if git("diff-tree", "-r", "--diff-filter=D", "--name-only", f"{base}^{{tree}}", tree).stdout.strip():
                        run_pair.log("  PIT-DIFF publish refused: the tree would delete files")
                        return
                    sha = git("commit-tree", tree, "-p", base, "-m", f"PIT back-fill {project} on {HOST}").stdout.strip()
                    if sha and git("push", "-q", drive.REMOTE, f"{sha}:refs/heads/main",
                                   env={"CLONEDEMOCKER_ALLOW_PUSH": "1"}).returncode == 0:
                        git("reset", "-q", sha)
                        return
        time.sleep(5 * (attempt + 1))
    run_pair.log(f"  PIT-DIFF publish failed 5 times; {rels} stay on disk, the next publish carries them")


if __name__ == "__main__":
    main()
