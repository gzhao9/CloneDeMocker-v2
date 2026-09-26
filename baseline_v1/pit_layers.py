"""PIT by layers (owner's design, E-008 / A-084): every SUCCESS diff of a setup, stacked, whole-module PIT.

Per project and setup, the SUCCESS rows' published diffs are grouped by the row's module scope and
stacked in detection order with `git apply --3way`. A diff that does not apply on top of the ones
already stacked moves, whole, to the next layer; nothing is rewritten and no model is called.

PIT covers the whole module, not the MCIs' test classes: every test of the module runs, PIT mutates
the module's own production packages and uses its own test packages, with fullMutationMatrix so the
XML names every killing test. The baseline runs once per module on the original code (shared by
all setups); each layer then runs once on the same scope with the layer's diffs applied. A layer is
compared with the baseline mutant by mutant: kills lost or gained, and tests that stopped killing a
mutant. If a layer with several MCIs fails to compile or its tests fail, the first MCI is kept and
the rest move to a new layer at the end, so every MCI is still measured. A run whose tests fail is
repeated once first: one flaky test in a 3642-test module (D-010) must not void a baseline or split
a layer. A stored baseline that compiled but whose tests or PIT failed is retried once when the tool restarts.

`--slice K/N` runs only the modules with crc32(scope) % N == K, so several processes can share one
project on one host (E-012). Each slice has its own workspace (pitlayers-<project>-<K>) and files
(<host>-<K>.json, baseline-<host>-<K>.json).

Output (one file per host; hosts never write the same file):
    data/<project>/pit-layers/baseline-<host>.json            {scope: evidence + matrix}
    data/<project>/refactoring/<setup>/pit-layers/<host>.json {"plan": {...}, "runs": {"<scope>#L<k>": {...}}}

    python baseline_v1/pit_layers.py --project kiota-java-1.10.0 --root kiota-java-1.10.0=<checkout>
        [--setups all|a,b] [--max-layer 1] [--threads 4] [--noredist only|skip] [--plan-only] [--no-publish]
Resumable: a baseline or (module, layer) already in this host's files is skipped, and the stored plan
is reused, so a restart does not reshuffle layers.
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
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio.canonical_store import write_retrying  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.harness import BuildScope, ProjectHarness, ensure_pit_junit5_support  # noqa: E402
from studio.long_paths import long_path  # noqa: E402
from studio.refactoring_agent import RefactoringAgent, _workspace_root  # noqa: E402
from baseline_v1 import drive, run_pair  # noqa: E402
from baseline_v1.pit_from_diff import SETUPS_BY_PROJECT, publish, slim  # noqa: E402

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


def module_dirs(scope: str) -> tuple[str, ...]:
    """"2 module(s): a, b" -> ("a", "b"); anything else is the whole project -> ()."""
    _, sep, names = scope.partition("module(s): ")
    return tuple(n.strip() for n in names.split(",") if n.strip()) if sep else ()


def gated(module: str) -> bool:
    return any(name in module for name in drive.NOREDIST_MODULES)


def package_patterns(root: Path, modules: tuple[str, ...], kind: str) -> tuple[str, ...]:
    """PIT globs ("pkg.*") covering every Java package under <module>/src/<kind>/java, minimal set."""
    packages = set()
    for module in modules or (".",):
        base = root / module / "src" / kind / "java"
        if base.is_dir():
            for source in long_path(base).rglob("*.java"):
                rel = Path(str(source)).parent.relative_to(long_path(base))
                if rel.parts:
                    packages.add(".".join(rel.parts))
    kept: list[str] = []
    for name in sorted(packages):
        if not any(name == k or name.startswith(k + ".") for k in kept):
            kept.append(name)
    return tuple(f"{k}.*" for k in kept)


def mutation_matrix(workspace: Path, since: float) -> dict[str, str]:
    """{mutant key: "STATUS|test;test"} from every fresh mutations.xml (fullMutationMatrix)."""
    matrix: dict[str, str] = {}
    for report in long_path(workspace).rglob("mutations.xml"):
        if "pit-reports" not in report.parts or report.stat().st_mtime < since:
            continue
        try:
            root = ET.parse(report).getroot()
        except (OSError, ET.ParseError):
            continue
        for m in root.findall("mutation"):
            key = "|".join(m.findtext(t) or "" for t in ("mutatedClass", "mutatedMethod", "lineNumber", "mutator"))
            status = (m.attrib.get("status") or m.findtext("status") or "UNKNOWN").upper()
            killers = sorted(t for t in (m.findtext("killingTests") or m.findtext("killingTest") or "").split("|") if t)
            matrix[key] = status + "|" + ";".join(killers)
    return matrix


def compare(base: dict[str, str], cand: dict[str, str]) -> dict:
    def split(value: str):
        status, _, killers = value.partition("|")
        return status, set(filter(None, killers.split(";")))

    lost, gained, killers_lost, missing = [], [], {}, 0
    for key, value in base.items():
        if key not in cand:
            missing += 1
            continue
        (bs, bk), (cs, ck) = split(value), split(cand[key])
        if bs == "KILLED" and cs != "KILLED":
            lost.append(key)
        elif bs != "KILLED" and cs == "KILLED":
            gained.append(key)
        elif bs == cs == "KILLED" and bk - ck:
            killers_lost[key] = sorted(bk - ck)
    return {"killedLost": sorted(lost), "killedGained": sorted(gained), "killersLost": killers_lost,
            "mutantsOnlyInBaseline": missing, "mutantsOnlyInCandidate": len(set(cand) - set(base))}


def skip_failing_tests_in_pom(pom: Path) -> None:
    """Set skipFailingTests on the workspace's pitest-maven plugin. pitest-maven 1.30.0 has no user
    property for it, so -DskipFailingTests is ignored; dubbo-remoting-netty4 has 12 tests that pass in
    surefire but fail under PIT's runner, and without this PIT aborts the whole module."""
    if not pom.is_file():
        return
    ns = "http://maven.apache.org/POM/4.0.0"
    ET.register_namespace("", ns)
    tree = ET.parse(pom)
    for plugin in tree.getroot().iter(f"{{{ns}}}plugin"):
        artifact = plugin.find(f"{{{ns}}}artifactId")
        if artifact is None or artifact.text != "pitest-maven":
            continue
        config = plugin.find(f"{{{ns}}}configuration")
        if config is None:
            config = ET.SubElement(plugin, f"{{{ns}}}configuration")
        flag = config.find(f"{{{ns}}}skipFailingTests")
        if flag is None:
            flag = ET.SubElement(config, f"{{{ns}}}skipFailingTests")
        if flag.text != "true":
            flag.text = "true"
            tree.write(pom, encoding="utf-8", xml_declaration=True)
        return


class Workspace:
    """One copy of the project for all runs; a layer's files are written in and restored afterwards."""

    def __init__(self, project_root: Path, project: str, harness: ProjectHarness, suffix: str = ""):
        self.root = project_root
        self.dir = _workspace_root(project_root, f"pitlayers-{project}{suffix}")
        if not self.dir.is_dir():
            RefactoringAgent._copy_project(project_root, self.dir)
        ensure_pit_junit5_support(self.dir, harness.maven_repo_local)
        if harness.pit_skip_failing_tests:
            skip_failing_tests_in_pom(self.dir / "pom.xml")
        self.written: list[str] = []

    def apply(self, contents: dict[str, str]) -> None:
        self.restore()
        for rel, text in contents.items():
            target = long_path(self.dir / rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            self.written.append(rel)

    def restore(self) -> None:
        for rel in self.written:
            source, target = long_path(self.root / rel), long_path(self.dir / rel)
            if source.is_file():
                shutil.copyfile(source, target)
            else:
                target.unlink(missing_ok=True)
        self.written = []


def tests_failed(record: dict) -> bool:
    e = record.get("evidence") or {}
    return e.get("compileStatus") == "PASSED" and e.get("testStatus") != "PASSED"


def validate(harness: ProjectHarness, ws: Workspace, scope: BuildScope) -> dict:
    """One run; if it compiles but its tests fail, once more (flaky tests, D-010)."""
    first = _validate(harness, ws, scope)
    if not tests_failed(first):
        return first
    second = _validate(harness, ws, scope)
    second["retriedAfter"] = {"testStatus": (first["evidence"] or {}).get("testStatus"), "seconds": first["seconds"]}
    return second


def _validate(harness: ProjectHarness, ws: Workspace, scope: BuildScope) -> dict:
    started = time.time()
    evidence = harness.validate(ws.dir, run_pit=True, scope=scope).as_dict()
    matrix = mutation_matrix(ws.dir, started - 1) if evidence.get("pitStatus") == "PASSED" else {}
    return {"host": HOST, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "evidence": slim(evidence),
            "matrix": matrix, "seconds": round(time.time() - started, 1)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--root", action="append", default=[], help="NAME=PATH of this host's checkout")
    parser.add_argument("--setups", default="all")
    parser.add_argument("--max-layer", type=int, default=0, help="stop after this layer (0 = all)")
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="PIT threads")
    parser.add_argument("--timeout-hours", type=float, default=6.0, help="limit per build command")
    parser.add_argument("--noredist", choices=("only", "skip"), default=None,
                        help="CloudStack: run only / skip the -Dnoredist-gated modules (C / D)")
    parser.add_argument("--publish-every", type=int, default=10, help="also publish after this many runs")
    parser.add_argument("--plan-only", action="store_true", help="print the layers and stop; nothing is built")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--slice", default="0/1", help="K/N: only modules with crc32(scope) %% N == K")
    args = parser.parse_args()
    k_slice, _, n_slice = args.slice.partition("/")
    k_slice, n_slice = int(k_slice), int(n_slice or 1)
    if not 0 <= k_slice < n_slice:
        parser.error("--slice K/N needs 0 <= K < N")
    suffix = f"-{k_slice}" if n_slice > 1 else ""

    def mine(module: str) -> bool:
        return n_slice == 1 or zlib.crc32(module.encode("utf-8")) % n_slice == k_slice
    for pair in args.root:
        name, _, path = pair.partition("=")
        drive.PROJECT_ROOTS[name] = path
    run_pair.load_env()
    project_root = Path(drive.PROJECT_ROOTS[args.project])
    setups = SETUPS_BY_PROJECT[args.project] if args.setups == "all" else args.setups.split(",")
    order = detection_order(args.project)
    base = REPO / "data" / args.project / "refactoring"

    data = {}
    for setup in setups:
        directory = base / setup
        rows = json.loads((directory / "refactoring-results.json").read_text(encoding="utf-8-sig"))["results"]
        out_path = directory / OUT_DIR / f"{HOST}{suffix}.json"
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
            if not mine(module):
                continue
            diffs[mci] = path.read_text(encoding="utf-8")
            bymod[module].append(mci)
        for module, mcis in bymod.items():
            if module not in out["plan"]:
                out["plan"][module] = plan_layers(mcis, diffs, project_root)
        data[setup] = {"out_path": out_path, "out": out, "diffs": diffs, "modules": list(bymod)}
        plan = {m: out["plan"][m] for m in bymod}
        placed = sum(len(layer) for layers in plan.values() for layer in layers)
        first = sum(len(layers[0]) for layers in plan.values() if layers)
        run_pair.log(f"PIT-LAYERS {args.project} [{setup}]: {len(diffs)} diffs, {len(plan)} modules, "
                     f"layer 1 holds {first}, {sum(len(v) for v in plan.values())} (module, layer) runs, "
                     f"deepest {max((len(v) for v in plan.values()), default=0)}, unplaced {len(diffs) - placed}")
    modules = sorted({m for d in data.values() for m in d["modules"]})
    run_pair.log(f"PIT-LAYERS {args.project}: {len(modules)} module baselines")
    if args.plan_only:
        return

    ProjectHarness.TIMEOUT_SECONDS = int(args.timeout_hours * 3600)
    harness = ProjectHarness()
    harness.pit_full_matrix = True
    harness.pit_threads = args.threads
    harness.pit_skip_failing_tests = True
    ws = Workspace(project_root, args.project, harness, suffix)
    scopes = {}
    for module in modules:
        dirs = module_dirs(module)
        scopes[module] = BuildScope(modules=dirs, pit_classes=package_patterns(project_root, dirs, "main"),
                                    pit_tests=package_patterns(project_root, dirs, "test"))
    base_path = REPO / "data" / args.project / OUT_DIR / f"baseline-{HOST}{suffix}.json"
    baselines = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    pending = 0

    def save(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, ensure_ascii=False, indent=1)
        write_retrying(lambda: path.write_text(text, encoding="utf-8", newline="\n"))

    def flush() -> None:
        nonlocal pending
        if not args.no_publish:
            paths = [data[s]["out_path"] for s in setups if data[s]["out_path"].is_file()]
            publish(paths + ([base_path] if base_path.is_file() else []), args.project)
        pending = 0

    def baseline(module: str) -> dict:
        stored = baselines.get(module)
        incomplete = stored is not None and (stored.get("evidence") or {}).get("compileStatus") == "PASSED" and (
            (stored.get("evidence") or {}).get("pitStatus") != "PASSED")      # tests or PIT failed (D-010, D-011)
        if stored is None or (incomplete and not stored.get("retriedOnRestart")):
            retry = stored is not None
            ws.restore()
            record = validate(harness, ws, scopes[module])
            if retry:
                record["retriedOnRestart"] = True
            baselines[module] = record
            save(base_path, baselines)
            e = record["evidence"] or {}
            run_pair.log(f"  PIT-LAYERS {args.project} baseline {module}: compile {e.get('compileStatus')} "
                         f"test {e.get('testStatus')} pit {e.get('pitStatus')} score {e.get('mutationScore')} "
                         f"mutants {len(record['matrix'])} {record['seconds']}s")
        return baselines[module]

    def run_layer(d: dict, module: str, mcis: list[str]) -> dict:
        ws.apply(layer_contents(mcis, d["diffs"], project_root))
        try:
            record = validate(harness, ws, scopes[module])
        finally:
            ws.restore()
        record["mcis"] = mcis
        matrix = record.pop("matrix")
        record["comparison"] = compare(baseline(module)["matrix"], matrix) if matrix else None
        return record

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
                if (baseline(module)["evidence"] or {}).get("pitStatus") != "PASSED":
                    d["out"]["runs"][key] = {"host": HOST, "mcis": mcis, "skipped": "baseline PIT did not pass"}
                    save(d["out_path"], d["out"])
                    continue
                record = run_layer(d, module, mcis)
                e = record["evidence"] or {}
                if len(mcis) > 1 and (e.get("compileStatus") != "PASSED" or e.get("testStatus") != "PASSED"):
                    failed_on = [e.get("compileStatus"), e.get("testStatus")]
                    layers[k - 1] = mcis[:1]               # keep the first, measure the rest in a later layer
                    layers.append(mcis[1:])
                    record = run_layer(d, module, mcis[:1]) | {"spilled": mcis[1:], "spilledOn": failed_on}
                d["out"]["runs"][key] = record
                save(d["out_path"], d["out"])
                e, c = record["evidence"] or {}, record.get("comparison") or {}
                run_pair.log(f"  PIT-LAYERS {args.project} [{setup}] {key} ({len(record['mcis'])} MCIs): "
                             f"compile {e.get('compileStatus')} test {e.get('testStatus')} pit {e.get('pitStatus')} "
                             f"score {e.get('mutationScore')} lost {len(c.get('killedLost') or [])} "
                             f"gained {len(c.get('killedGained') or [])} killersLost {len(c.get('killersLost') or {})} "
                             f"{record['seconds']}s" + (f" spilled {len(record['spilled'])}" if record.get("spilled") else ""))
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
