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
import dataclasses
import json
import re
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


def mutation_matrix(workspace: Path, since: float, modules: tuple[str, ...] = ()) -> dict[str, str]:
    """{mutant key: "STATUS|test;test"} from the fresh mutations.xml of the scoped modules only.

    Only reports under the scoped modules' own directories count. Reading every fresh report in the
    shared workspace picked up other modules' reports: a report written a second before `since` was
    taken, so kiota's azure baseline also held okHttp's mutants (E-013)."""
    matrix: dict[str, str] = {}
    base_dir = long_path(workspace)
    wanted = [tuple(Path(m).parts) for m in modules]
    for report in base_dir.rglob("mutations.xml"):
        if "pit-reports" not in report.parts or report.stat().st_mtime < since:
            continue
        rel = Path(str(report)).relative_to(base_dir).parts
        if wanted and not any(rel[:len(w)] == w and "pit-reports" in rel[len(w):len(w) + 3] for w in wanted):
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


def split(value: str):
    status, _, killers = value.partition("|")
    return status, set(filter(None, killers.split(";")))


def unstable_mutants(first: dict[str, str], second: dict[str, str]) -> dict[str, str]:
    """Mutants whose status or killing tests differ between two baseline runs of the same code."""
    return {k: f"{first.get(k, '-')} || {second.get(k, '-')}" for k in set(first) | set(second)
            if first.get(k) != second.get(k)}


def compare(base: dict[str, str], cand: dict[str, str], unstable: dict | None = None,
            killers: bool = True) -> dict:
    """Candidate vs baseline, mutant by mutant. Mutants that differed between two baseline runs
    (`unstable`) and any change into or out of TIMED_OUT are counted apart: under host load they
    move without any code change (kiota getRetryAfter:150, dubbo nacos TIMED_OUT -> SURVIVED)."""
    unstable = unstable or {}

    # PIT counts these as detected. KILLED <-> TIMED_OUT under host load is not a lost kill: kiota's
    # okHttp L1 flipped one mutant KILLED -> TIMED_OUT on an identical re-run.
    detected = {"KILLED", "TIMED_OUT", "MEMORY_ERROR", "RUN_ERROR"}
    lost, gained, changed, killers_lost, missing = [], [], {}, {}, 0
    timeout_flips, skipped = {}, 0
    for key, value in base.items():
        if key not in cand:
            missing += 1
            continue
        (bs, bk), (cs, ck) = split(value), split(cand[key])
        if key in unstable:
            skipped += value != cand[key]
            continue
        if bs != cs and "TIMED_OUT" in (bs, cs):
            timeout_flips[key] = f"{bs}->{cs}"
        elif bs in detected and cs not in detected:
            lost.append(key)
        elif bs not in detected and cs in detected:
            gained.append(key)
        elif bs != cs and bs in detected:
            changed[key] = f"{bs}->{cs}"
        elif killers and bs == cs == "KILLED" and bk - ck:
            killers_lost[key] = sorted(bk - ck)
    return {"killedLost": sorted(lost), "killedGained": sorted(gained), "detectedStatusChanged": changed,
            "killersLost": killers_lost, "timeoutFlips": timeout_flips, "unstableChangesSkipped": skipped,
            "unstableMutants": len(unstable), "mutantsOnlyInBaseline": missing,
            "mutantsOnlyInCandidate": len(set(cand) - set(base))}


def candidate_from_diff(base: dict[str, str], diff: dict) -> dict[str, str]:
    cand = {k: v for k, v in base.items() if k not in set(diff.get("missing") or [])}
    cand.update(diff.get("changed") or {})
    return cand


def matrix_diff(base: dict[str, str], cand: dict[str, str]) -> dict:
    """The candidate matrix as a delta from the baseline, so any comparison can be recomputed offline."""
    return {"changed": {k: v for k, v in cand.items() if base.get(k) != v},
            "missing": sorted(k for k in base if k not in cand)}


def skip_failing_tests_in_pom(pom: Path) -> None:
    """Set skipFailingTests on the workspace's pitest-maven plugin. pitest-maven 1.30.0 has no user
    property for it, so -DskipFailingTests is ignored; dubbo-remoting-netty4 has 12 tests that pass in
    surefire but fail under PIT's runner, and without this PIT aborts the whole module."""
    if not pom.is_file():
        return
    ns = "http://maven.apache.org/POM/4.0.0"
    ET.register_namespace("", ns)
    tree = ET.parse(pom)
    project = tree.getroot()
    found = [pl for pl in project.iter(f"{{{ns}}}plugin")
             if (pl.find(f"{{{ns}}}artifactId") is not None and pl.find(f"{{{ns}}}artifactId").text == "pitest-maven")]
    if not found:
        # ensure_pit_junit5_support adds the plugin only for JUnit 5 projects; CloudStack (JUnit 4) had
        # none, so skip/skipFailingTests went nowhere and PIT ran in every upstream module again.
        build = project.find(f"{{{ns}}}build")
        if build is None:
            build = ET.SubElement(project, f"{{{ns}}}build")
        plugins = build.find(f"{{{ns}}}plugins")
        if plugins is None:
            plugins = ET.SubElement(build, f"{{{ns}}}plugins")
        plugin = ET.SubElement(plugins, f"{{{ns}}}plugin")
        ET.SubElement(plugin, f"{{{ns}}}groupId").text = "org.pitest"
        ET.SubElement(plugin, f"{{{ns}}}artifactId").text = "pitest-maven"
        found = [plugin]
    for plugin in found[:1]:
        config = plugin.find(f"{{{ns}}}configuration")
        if config is None:
            config = ET.SubElement(plugin, f"{{{ns}}}configuration")
        changed = False
        # skip follows a property that is true in the root and false only in the scoped modules
        # (scoped_pit), so `-pl X -am` builds the upstream modules but runs PIT in X alone. Without it,
        # CloudStack plugins share com.cloud.* with core/server, PIT ran (and failed) upstream first,
        # and the scoped plugins never got a report (B-083).
        for name, value in (("skipFailingTests", "true"), ("skip", "${" + PIT_SKIP_PROPERTY + "}")):
            flag = config.find(f"{{{ns}}}{name}")
            if flag is None:
                flag = ET.SubElement(config, f"{{{ns}}}{name}")
            if flag.text != value:
                flag.text, changed = value, True
        changed |= set_pom_property(tree, ns, "true")
        changed |= skip_tests_outside_scope(project, ns)
        if changed:
            tree.write(pom, encoding="utf-8", xml_declaration=True)
        return


def skip_tests_outside_scope(project, ns: str) -> bool:
    """Bind surefire's skipTests to the same property, so `-pl X -am test` tests X alone. The -Dtest package
    filter also matched upstream modules sharing com.cloud.* roots, and server's MySQL-only DAO tests failed
    engine/orchestration's baseline on every host (D-018). The upstream modules still compile."""
    build = project.find(f"{{{ns}}}build")
    if build is None:
        build = ET.SubElement(project, f"{{{ns}}}build")
    plugins = build.find(f"{{{ns}}}plugins")
    if plugins is None:
        plugins = ET.SubElement(build, f"{{{ns}}}plugins")
    surefire = next((pl for pl in plugins.findall(f"{{{ns}}}plugin")
                     if (pl.findtext(f"{{{ns}}}artifactId") or "") == "maven-surefire-plugin"), None)
    if surefire is None:
        surefire = ET.SubElement(plugins, f"{{{ns}}}plugin")
        ET.SubElement(surefire, f"{{{ns}}}groupId").text = "org.apache.maven.plugins"
        ET.SubElement(surefire, f"{{{ns}}}artifactId").text = "maven-surefire-plugin"
    config = surefire.find(f"{{{ns}}}configuration")
    if config is None:
        config = ET.SubElement(surefire, f"{{{ns}}}configuration")
    flag = config.find(f"{{{ns}}}skipTests")
    if flag is None:
        flag = ET.SubElement(config, f"{{{ns}}}skipTests")
    value = "${" + PIT_SKIP_PROPERTY + "}"
    if flag.text == value:
        return False
    flag.text = value
    return True


PIT_SKIP_PROPERTY = "cloneDeMockerPitSkip"


def set_pom_property(tree, ns: str, value: str) -> bool:
    project = tree.getroot()
    props = project.find(f"{{{ns}}}properties")
    if props is None:
        props = ET.SubElement(project, f"{{{ns}}}properties")
    prop = props.find(f"{{{ns}}}{PIT_SKIP_PROPERTY}")
    if prop is None:
        prop = ET.SubElement(props, f"{{{ns}}}{PIT_SKIP_PROPERTY}")
    if prop.text == value:
        return False
    prop.text = value
    return True


class scoped_pit:
    """While active, the scoped Maven modules set cloneDeMockerPitSkip=false; their POMs are restored after."""

    def __init__(self, ws_dir: Path, project_root: Path, modules: tuple[str, ...]):
        self.root_dir = Path(str(long_path(ws_dir)))
        # No module (whole project) means the root itself: PIT runs everywhere, as before.
        self.poms = [(long_path(ws_dir / m / "pom.xml"), long_path(project_root / m / "pom.xml")) for m in modules or (".",)]
        self.poms = [(w, o) for w, o in self.poms if w.is_file() and o.is_file()]

    def __enter__(self):
        ns = "http://maven.apache.org/POM/4.0.0"
        ET.register_namespace("", ns)
        for pom, _ in self.poms:
            tree = ET.parse(pom)
            if set_pom_property(tree, ns, "false"):
                tree.write(pom, encoding="utf-8", xml_declaration=True)
        return self

    def __exit__(self, *exc):
        ns = "http://maven.apache.org/POM/4.0.0"
        ET.register_namespace("", ns)
        for pom, original in self.poms:
            if Path(str(pom)).parent == self.root_dir:
                # The root POM holds the workspace's pitest setup: reset the property, keep the rest.
                tree = ET.parse(pom)
                if set_pom_property(tree, ns, "true"):
                    tree.write(pom, encoding="utf-8", xml_declaration=True)
            else:
                shutil.copyfile(original, pom)


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


def failing_test_classes(ws_dir: Path, modules: tuple[str, ...], since: float, tail: str) -> list[str]:
    """Test classes that failed, errored or crashed the fork in the scoped modules' fresh surefire reports."""
    found: set[str] = set()
    for module in modules or (".",):
        reports = long_path(ws_dir / module / "target" / "surefire-reports")
        if not reports.is_dir():
            continue
        for report in reports.glob("TEST-*.xml"):
            if report.stat().st_mtime < since:
                continue
            try:
                root = ET.parse(report).getroot()
            except (OSError, ET.ParseError):
                continue
            for case in root.iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    found.add(case.attrib.get("classname") or root.attrib.get("name", ""))
    # A crashed fork writes no XML for its test; surefire names it under "Crashed tests:".
    lines = (tail or "").splitlines()
    for i, line in enumerate(lines):
        if "Crashed tests:" in line:
            for follow in lines[i + 1:]:
                m = re.fullmatch(r"\[ERROR\]\s+([\w$]+(?:\.[\w$]+)+)\s*", follow)
                if not m:
                    break
                found.add(m.group(1))
    return sorted(c for c in found if c)


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
    full_matrix = harness.pit_full_matrix
    if (ws.dir / "pom.xml").is_file():
        with scoped_pit(ws.dir, ws.root, scope.modules):
            evidence = harness.validate(ws.dir, run_pit=True, scope=scope).as_dict()
    else:
        evidence = harness.validate(ws.dir, run_pit=True, scope=scope).as_dict()
    matrix = mutation_matrix(ws.dir, started, scope.modules) if evidence.get("pitStatus") == "PASSED" else {}
    slimmed = slim(evidence) or {}
    # The harness's own mutant summary reads every fresh report in the workspace, so it can include other
    # modules (E-013). Score and counts come from the module-scoped matrix instead; its mutant list is dropped.
    slimmed.pop("mutants", None)
    if any(slimmed.get(k) not in ("PASSED", "NOT_RUN", None) for k in ("compileStatus", "testStatus", "pitStatus")):
        # Keep the tail of the build output on failure: surefire fork crashes (druid indexing-service, CloudStack
        # server) could not be told from host problems without it (C-027).
        text = "\n".join(str(d) for d in evidence.get("diagnostics") or [])
        slimmed["diagnosticsTail"] = text[-12000:]
    if matrix:
        statuses = [value.split("|", 1)[0] for value in matrix.values()]
        slimmed["mutationScore"] = statuses.count("KILLED") / len(statuses)
        slimmed["mutationCounts"] = {s: statuses.count(s) for s in sorted(set(statuses))}
    return {"host": HOST, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "evidence": slimmed,
            "matrix": matrix, "fullMatrix": full_matrix, "seconds": round(time.time() - started, 1)}


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
    parser.add_argument("--single-baseline", action="store_true",
                        help="run each module's baseline once: no repeat run, so no unstable-mutant filter (owner, E-016)")
    parser.add_argument("--slice", default="0/1", help="K/N: only modules with crc32(scope) %% N == K")
    parser.add_argument("--kill-first", action="append", default=[], metavar="MODULE",
                        help="module scope (substring) that runs without fullMutationMatrix: each mutant stops at its "
                             "first kill, so killersLost is not measured there (A-098: spring-security config never "
                             "finished a full-matrix baseline in 6 h)")
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
    harness.pit_in_reactor = True
    ws = Workspace(project_root, args.project, harness, suffix)
    scopes = {}
    for module in modules:
        dirs = module_dirs(module)
        scopes[module] = BuildScope(modules=dirs, pit_classes=package_patterns(project_root, dirs, "main"),
                                    pit_tests=package_patterns(project_root, dirs, "test"))
    base_path = REPO / "data" / args.project / OUT_DIR / f"baseline-{HOST}{suffix}.json"
    baselines = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    for module, stored in baselines.items():
        if module in scopes and stored.get("excludedTests"):
            scopes[module] = dataclasses.replace(scopes[module], excluded_tests=tuple(stored["excludedTests"]))
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

    def matrix_mode(module: str) -> ProjectHarness:
        harness.pit_full_matrix = not any(part in module for part in args.kill_first)
        return harness

    def baseline(module: str) -> dict:
        stored = baselines.get(module)
        incomplete = stored is not None and (stored.get("evidence") or {}).get("compileStatus") == "PASSED" and (
            (stored.get("evidence") or {}).get("pitStatus") != "PASSED")      # tests or PIT failed (D-010, D-011)
        wanted = not any(part in module for part in args.kill_first)
        if stored is not None and stored.get("fullMatrix", True) != wanted:
            # A baseline made in the other mode cannot anchor these runs (killer sets differ by mode, D-015).
            run_pair.log(f"  PIT-LAYERS {args.project} baseline {module}: stored in the other matrix mode; re-running")
            stored = None
        if stored is None or (incomplete and not stored.get("retriedOnRestart")):
            retry = stored is not None
            ws.restore()
            started = time.time()
            record = validate(matrix_mode(module), ws, scopes[module])
            if tests_failed(record) and (ws.dir / "pom.xml").is_file():
                # Tests that fail on the untouched code twice (a database the host lacks, a fork-crashing test;
                # C-027, C-029) are left out of this module's test and PIT phases, in the baseline and every layer.
                failing = failing_test_classes(ws.dir, scopes[module].modules, started - 1,
                                               (record.get("evidence") or {}).get("diagnosticsTail", ""))
                new = [c for c in failing if c not in scopes[module].excluded_tests]
                if new:
                    scopes[module] = dataclasses.replace(
                        scopes[module], excluded_tests=tuple(sorted(set(scopes[module].excluded_tests) | set(new))))
                    run_pair.log(f"  PIT-LAYERS {args.project} baseline {module}: excluding {len(new)} test classes "
                                 f"failing on the original code: {', '.join(new)[:300]}")
                    ws.restore()
                    record = validate(matrix_mode(module), ws, scopes[module])
            if scopes[module].excluded_tests:
                record["excludedTests"] = list(scopes[module].excluded_tests)
            if retry:
                record["retriedOnRestart"] = True
            baselines[module] = record
            save(base_path, baselines)
            e = record["evidence"] or {}
            run_pair.log(f"  PIT-LAYERS {args.project} baseline {module}: compile {e.get('compileStatus')} "
                         f"test {e.get('testStatus')} pit {e.get('pitStatus')} score {e.get('mutationScore')} "
                         f"mutants {len(record['matrix'])} {record['seconds']}s")
        record = baselines[module]
        if record.get("matrix") and "unstable" not in record and not args.single_baseline:
            # A second run of the untouched code marks the mutants that move on their own.
            ws.restore()
            again = validate(matrix_mode(module), ws, scopes[module])
            if again.get("matrix"):
                record["unstable"] = unstable_mutants(record["matrix"], again["matrix"])
                record["repeatSeconds"] = again["seconds"]
                save(base_path, baselines)
                run_pair.log(f"  PIT-LAYERS {args.project} baseline {module}: repeat run, "
                             f"{len(record['unstable'])} of {len(record['matrix'])} mutants unstable")
                recompare(module)
        return record

    def recompare(module: str) -> None:
        """Re-derive stored layer comparisons of this module under the current rules, from their diffs."""
        base = baselines[module]
        for s in setups:
            d = data[s]
            touched = False
            for key, run in d["out"]["runs"].items():
                if key.rsplit("#L", 1)[0] == module and run.get("matrixDiff"):
                    run["comparison"] = compare(base["matrix"], candidate_from_diff(base["matrix"], run["matrixDiff"]),
                                                base.get("unstable"), killers=base.get("fullMatrix", True))
                    touched = True
            if touched:
                save(d["out_path"], d["out"])

    def run_layer(d: dict, module: str, mcis: list[str]) -> dict:
        ws.apply(layer_contents(mcis, d["diffs"], project_root))
        try:
            record = validate(matrix_mode(module), ws, scopes[module])
        finally:
            ws.restore()
        record["mcis"] = mcis
        matrix = record.pop("matrix")
        base = baseline(module)
        base_matrix = base["matrix"]
        record["comparison"] = compare(base_matrix, matrix, base.get("unstable"),
                                       killers=base.get("fullMatrix", True) and record.get("fullMatrix", True)) if matrix else None
        record["matrixDiff"] = matrix_diff(base_matrix, matrix) if matrix else None
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
