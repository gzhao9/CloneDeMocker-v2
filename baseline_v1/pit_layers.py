"""PIT by layers (owner's design, E-008): every SUCCESS diff of a setup, stacked, one PIT run per layer.

Per project and setup, the SUCCESS rows' published diffs are grouped by the row's module scope and
stacked in detection order with `git apply --3way`. A diff that does not apply on top of the ones
already stacked moves, whole, to the next layer; nothing is rewritten and no model is called. Each
(module, layer) is then validated like one multi-MCI run: `RefactoringAgent.run` with every MCI of the
layer selected, so the scope is the union of their test classes, and baseline and candidate PIT run
on that same scope. The baseline comes from the verification ledger when the same scope ran before.

Layer 1 of every setup runs first, then layer 2, and so on, so each project has layer-1 data early.
It publishes after every layer (and every --publish-every runs inside one).

A candidate that does not compile is almost always two MCIs adding the same helper. The layer's
first MCI is kept and the others move to a new layer at the end, so every MCI is still measured.

Output, one file per dataset and host (hosts never write the same file):
    data/<project>/refactoring/<setup>/pit-layers/<host>.json
    {"plan": {module: [[mci, ...], ...]}, "runs": {"<module>#L<k>": {...}}}

    python baseline_v1/pit_layers.py --project kiota-java-1.10.0 --root kiota-java-1.10.0=<checkout>
        [--setups all|a,b] [--max-layer 1] [--noredist only|skip] [--plan-only] [--no-publish]
Resumable: a (module, layer) already in this host's file is skipped. The plan is stored with the runs
and reused, so a restart does not reshuffle layers.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.harness import mutation_regressed  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402
from baseline_v1 import drive, run_pair  # noqa: E402
from baseline_v1.pit_from_diff import SETUPS_BY_PROJECT, DiffReplayAgent, publish, slim  # noqa: E402

HOST = socket.gethostname()
OUT_DIR = "pit-layers"


def targets(diff_text: str) -> list[str]:
    return [line[4:].strip().removeprefix("b/") for line in diff_text.splitlines()
            if line.startswith("+++ ") and not line.startswith("+++ /dev/null")]


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def detection_order(project: str) -> dict[str, int]:
    run, raw = DetectionService(REPO).load_raw_detection(drive.run_id_for(project))
    ids = [f"{cls}::{i + 1}" for cls, values in raw.get("detectedMockClones", {}).items() for i in range(len(values))]
    return {mci: n for n, mci in enumerate(ids)}


class Stack:
    """A throwaway git repo holding the pristine copies of the files one module's diffs touch."""

    def __init__(self, project_root: Path, files: set[str]):
        self.dir = Path(tempfile.mkdtemp(prefix="pitlayers-"))
        git("init", "-q", cwd=self.dir)
        for rel in files:
            source = project_root / rel
            if source.is_file():
                target = self.dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8").replace("\r\n", "\n"),
                                  encoding="utf-8", newline="\n")
        git("add", "-A", cwd=self.dir)
        git("-c", "user.name=pit", "-c", "user.email=pit@local", "commit", "-qm", "base", "--allow-empty", cwd=self.dir)

    def push(self, diff_text: str) -> bool:
        """Stack one diff; on failure leave the stack exactly as it was."""
        patch = self.dir / ".change.diff"
        patch.write_text(diff_text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        done = git("apply", "--3way", "--whitespace=nowarn", ".change.diff", cwd=self.dir)
        patch.unlink()
        if done.returncode == 0:
            # Commit, so that a later diff's failure resets to this diff, not to the pristine base.
            git("add", "-A", cwd=self.dir)
            git("-c", "user.name=pit", "-c", "user.email=pit@local", "commit", "-qm", "stacked", "--allow-empty",
                cwd=self.dir)
            return True
        git("reset", "-q", "--hard", cwd=self.dir)
        git("clean", "-qfd", cwd=self.dir)
        return False

    def contents(self, files: set[str]) -> dict[str, str]:
        return {rel: (self.dir / rel).read_text(encoding="utf-8") for rel in files if (self.dir / rel).is_file()}

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def plan_layers(mcis: list[str], diffs: dict[str, str], project_root: Path) -> list[list[str]]:
    """Layers for one module: stack in the given order, spill what does not apply to the next layer."""
    layers: list[list[str]] = []
    todo = list(mcis)
    while todo:
        stack = Stack(project_root, {rel for m in todo for rel in targets(diffs[m])})
        placed, left = [], []
        for m in todo:
            (placed if stack.push(diffs[m]) else left).append(m)
        stack.close()
        if not placed:                       # does not apply even alone; pit_from_diff has the same rule
            layers.append([])
            break
        layers.append(placed)
        todo = left
    return layers


def layer_contents(mcis: list[str], diffs: dict[str, str], project_root: Path) -> dict[str, str]:
    files = {rel for m in mcis for rel in targets(diffs[m])}
    stack = Stack(project_root, files)
    try:
        for m in mcis:
            if not stack.push(diffs[m]):
                raise RuntimeError(f"{m} no longer stacks on its layer")
        return stack.contents(files)
    finally:
        stack.close()


def module_of(row: dict) -> str:
    return str(row.get("scope") or "unknown")


def gated(module: str) -> bool:
    return any(name in module for name in drive.NOREDIST_MODULES)


def run_layer(service, run_id, project, project_root, model, mcis, patched) -> dict:
    started = time.time()
    record: dict = {"host": HOST, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mcis": mcis}
    try:
        DiffReplayAgent.patched = patched
        agent = DiffReplayAgent(service, provider=RefactoringAgent._openai_provider("default"))
        result = agent.run(run_id, mcis, model, user_instruction="", run_pit=True, api_profile="default",
                           use_mock=False, sequence_selection=None, max_retries=0, use_cache=False,
                           progress_callback=None, workspace_id=f"pitlayers-{project}")
        harness = result.get("harness") or {}
        baseline, candidate = harness.get("baseline") or {}, harness.get("candidate") or {}
        record.update({
            "classificationWithPit": canonical_store.classify_agent_result(result),
            "baseline": slim(baseline), "candidate": slim(candidate),
            "mutationScoreDelta": harness.get("mutationScoreDelta"),
            "mutationRegressed": harness.get("mutationRegressed"),
            "killedLost": sorted(k for k, s in (baseline.get("mutants") or {}).items()
                                 if s == "KILLED" and (candidate.get("mutants") or {}).get(k) != "KILLED"),
            "identityRegressed": mutation_regressed(baseline, candidate),
            "verificationReused": result.get("verificationReused") or {},
        })
    except Exception as error:  # noqa: BLE001 - record and keep going
        record["error"] = f"{type(error).__name__}: {str(error)[:300]}"
    record["seconds"] = round(time.time() - started, 1)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--root", action="append", default=[], help="NAME=PATH of this host's checkout")
    parser.add_argument("--setups", default="all")
    parser.add_argument("--max-layer", type=int, default=0, help="stop after this layer (0 = all)")
    parser.add_argument("--noredist", choices=("only", "skip"), default=None,
                        help="CloudStack: run only / skip the -Dnoredist-gated modules (C / D)")
    parser.add_argument("--publish-every", type=int, default=10, help="also publish after this many runs")
    parser.add_argument("--plan-only", action="store_true", help="print the layers and stop; nothing is built")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    for pair in args.root:
        name, _, path = pair.partition("=")
        drive.PROJECT_ROOTS[name] = path
    run_pair.load_env()
    os.environ.setdefault("OPENAI_API_KEY", "unused")
    project_root = Path(drive.PROJECT_ROOTS[args.project])
    setups = SETUPS_BY_PROJECT[args.project] if args.setups == "all" else args.setups.split(",")
    order = detection_order(args.project)
    base = REPO / "data" / args.project / "refactoring"

    data = {}
    for setup in setups:
        directory = base / setup
        rows = json.loads((directory / "refactoring-results.json").read_text(encoding="utf-8"))["results"]
        model = json.loads((directory / "setup.json").read_text(encoding="utf-8")).get("model") or "gpt-5.6-terra"
        out_path = directory / OUT_DIR / f"{HOST}.json"
        out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() else {"plan": {}, "runs": {}}
        diffs, bymod = {}, defaultdict(list)
        for mci in sorted(rows, key=lambda m: order.get(m, len(order))):
            row = rows[mci]
            path = directory / (row.get("diffFile") or "")
            if row.get("classification") != "SUCCESS" or not row.get("diffFile") or not path.is_file():
                continue
            module = module_of(row)
            if args.noredist == "only" and not gated(module) or args.noredist == "skip" and gated(module):
                continue
            diffs[mci] = path.read_text(encoding="utf-8")
            bymod[module].append(mci)
        for module, mcis in bymod.items():
            if module not in out["plan"]:
                out["plan"][module] = plan_layers(mcis, diffs, project_root)
        data[setup] = {"model": model, "out_path": out_path, "out": out, "diffs": diffs, "modules": list(bymod)}
        plan = {m: out["plan"][m] for m in bymod}
        placed = sum(len(layer) for layers in plan.values() for layer in layers)
        first = sum(len(layers[0]) for layers in plan.values() if layers)
        run_pair.log(f"PIT-LAYERS {args.project} [{setup}]: {len(diffs)} diffs, {len(plan)} modules, "
                     f"layer 1 holds {first}, {sum(len(v) for v in plan.values())} (module, layer) runs, "
                     f"deepest {max((len(v) for v in plan.values()), default=0)}, unplaced {len(diffs) - placed}")
    if args.plan_only:
        return

    service = DetectionService(REPO)
    run_id = drive.run_id_for(args.project)
    pending = 0

    def save(d) -> None:
        d["out_path"].parent.mkdir(parents=True, exist_ok=True)
        d["out_path"].write_text(json.dumps(d["out"], ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    def flush() -> None:
        nonlocal pending
        if not args.no_publish:
            publish([data[s]["out_path"] for s in setups if data[s]["out_path"].is_file()], args.project)
        pending = 0

    k = 0
    while True:
        k += 1
        if args.max_layer and k > args.max_layer:
            break
        more = False
        for setup in setups:
            d = data[setup]
            for module in d["modules"]:
                layers = d["out"]["plan"][module]
                if len(layers) < k or not layers[k - 1]:
                    continue
                more = True
                key = f"{module}#L{k}"
                if key in d["out"]["runs"]:
                    continue
                mcis = layers[k - 1]
                patched = layer_contents(mcis, d["diffs"], project_root)
                record = run_layer(service, run_id, args.project, project_root, d["model"], mcis, patched)
                cand = record.get("candidate") or {}
                if (len(mcis) > 1 and cand.get("compileStatus") == "FAILED"
                        and (record.get("baseline") or {}).get("compileStatus") == "PASSED"):
                    record["spilled"] = mcis[1:]           # keep the first, measure the rest later
                    layers[k - 1] = mcis[:1]
                    layers.append(mcis[1:])
                    record = run_layer(service, run_id, args.project, project_root, d["model"], mcis[:1],
                                       layer_contents(mcis[:1], d["diffs"], project_root)) | {"spilled": mcis[1:]}
                d["out"]["runs"][key] = record
                save(d)
                c = record.get("candidate") or {}
                run_pair.log(f"  PIT-LAYERS {args.project} [{setup}] {key} ({len(record['mcis'])} MCIs): "
                             f"{record.get('classificationWithPit') or record.get('error')} pit {c.get('pitStatus')} "
                             f"delta {record.get('mutationScoreDelta')} lost {len(record.get('killedLost') or [])} "
                             f"{record['seconds']}s")
                pending += 1
                if pending >= args.publish_every:
                    flush()
        if pending:
            flush()
        run_pair.log(f"PIT-LAYERS {args.project}: layer {k} done")
        if not more:
            break
    run_pair.log(f"PIT-LAYERS {args.project} on {HOST}: done")


if __name__ == "__main__":
    main()
