"""Drive the V1/V2 pair runs unattended: one lane = a queue of projects run in order.

    python baseline_v1/drive.py --lane L1 kiota-java-1.10.0 dubbo-3.3.6
    python baseline_v1/drive.py --lane L2 spring-security-7.1.1 druid-37.0.0

Each lane is a supervisor: it runs the lane's worker in a child process and restarts it if it
dies, giving up after 3 restarts in 10 minutes (a crash that fast is not something a restart
fixes). The worker walks the queue; per project it opens a detection run from data/<project>/
(or reuses the one recorded in pair-runids.json), runs every MCI through V2 then V1, and
publishes every SYNC_EVERY MCIs and at the end of each project.

Publishing touches only this lane's own dataset directories and never another machine's
paths. Two lanes share one working tree, so pushes are serialised with a lock file. A lane
that cannot publish keeps running: the rows are on disk and the next sync carries them.

Anything skipped or failing goes to validation/results/pair-issues.log, for the morning.

Several lanes on one project, on one host: give each the same direction and its own slice,

    python baseline_v1/drive.py --lane L1 --reverse --slice 0/3 ... cloudstack
    python baseline_v1/drive.py --lane L2 --reverse --slice 1/3 ... cloudstack
    python baseline_v1/drive.py --lane L3 --reverse --slice 2/3 ... cloudstack

Slice K/N takes the MCIs whose crc32(id) % N == K, so the lanes never pick the same MCI. Slice 0
keeps the workspace `pair-<project>`; the others get `pair-<project>-<lane>`. Row writes on the
host are serialised by baseline_v1/rowlock.py. `--start 0.5` begins the walk half-way in, for a
machine joining a project that others already walk from both ends.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from baseline_v1.rowlock import RowLock  # noqa: E402

RESULTS = REPO / "validation" / "results"
RUNIDS = RESULTS / "pair-runids.json"
ISSUES = RESULTS / "pair-issues.log"
LOCK = REPO / ".git" / "pair-sync.lock"
REMOTE = "github"
SYNC_EVERY = 10
SETUPS = ("CloneDeMocker+Luna-5.6", "CloneDeMocker-V1+Luna-5.6")
PROJECT_ROOTS = {
    "kiota-java-1.10.0": r"D:\Java_projects\Microsoft\kiota-java-1.10.0",
    "druid-37.0.0": r"D:\Java_projects\Apache\druid-37.0.0",
    "dubbo-3.3.6": r"D:\Java_projects\Apache\dubbo-3.3.6",
    "spring-security-7.1.1": r"D:\Java_projects\Spring\spring-security-7.1.1",
    "spring-integration-7.1.1": r"D:\Java_projects\Spring\spring-integration-7.1.1",
}


def stamp() -> str:
    return datetime.now().strftime("%m-%d %H:%M:%S")


def issue(lane: str, message: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with ISSUES.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp()} [{lane}] {message}\n")


def git(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


class Lock:
    def __enter__(self):
        while True:
            try:
                os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL))
                return self
            except FileExistsError:
                if time.time() - LOCK.stat().st_mtime > 900:      # a lane died holding it
                    LOCK.unlink(missing_ok=True)
                time.sleep(3)

    def __exit__(self, *exc):
        LOCK.unlink(missing_ok=True)


def sync(lane: str, project: str, note: str) -> bool:
    paths = [f"data/{project}/refactoring/{s}" for s in SETUPS
             if (REPO / "data" / project / "refactoring" / s).exists()]
    paths += [f"data/{project}/{f}" for f in ("detection.json", "detection-meta.json")
              if (REPO / "data" / project / f).exists()]
    if not paths:
        return True
    # Build the commit on top of the remote with a private index and push it. The working tree,
    # the local branch and the other lane's half-written files are never touched: an earlier
    # `pull --rebase --autostash` stashed both lanes' unpublished rows and failed to re-apply
    # them, leaving the lanes writing into truncated files.
    files = [p for d in paths for p in ([d] if (REPO / d).is_file() else
             [f.relative_to(REPO).as_posix() for f in (REPO / d).rglob("*") if f.is_file()])]
    index = REPO / ".git" / f"pair-index-{lane}"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}

    def plumb(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600, env=env)

    with Lock():
        for attempt in range(1, 6):
            if git("fetch", "-q", REMOTE, "main").returncode != 0:
                time.sleep(5 * attempt)
                continue
            # The remote-tracking ref, not FETCH_HEAD: any concurrent `git fetch` (the per-MCI
            # routing check runs one outside this lock) rewrites FETCH_HEAD, and reading it
            # mid-write returned nothing. An empty base made read-tree fail silently, and the
            # commit that followed held only this lane's files -- f5a8435b deleted 3482 files.
            base = git("rev-parse", "--verify", f"{REMOTE}/main^{{commit}}").stdout.strip()
            base_tree = git("rev-parse", "--verify", f"{base}^{{tree}}").stdout.strip() if base else ""
            index.unlink(missing_ok=True)
            if not base or not base_tree or plumb("read-tree", base).returncode != 0:
                issue(lane, f"{project}: could not read the remote tree ({base!r}); not publishing")
                time.sleep(5 * attempt)
                continue
            ok = True
            with RowLock():                    # another lane on this host may be recording a row
                for rel in files:
                    merge_with_remote(base, rel)
                    blob = git("hash-object", "-w", "--", rel).stdout.strip()
                    if not blob or plumb("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}").returncode != 0:
                        ok = False
                        break
            tree = plumb("write-tree").stdout.strip() if ok else ""
            if not tree:
                issue(lane, f"{project}: building the commit failed; not publishing")
                time.sleep(5 * attempt)
                continue
            if tree == base_tree:
                index.unlink(missing_ok=True)
                return True                                   # nothing new to publish
            # This publisher only adds and updates files. Any deletion means the index was not
            # built from the remote tree, so refuse rather than push it.
            deleted = git("diff-tree", "-r", "--name-only", "--diff-filter=D", base_tree, tree).stdout.split()
            if deleted:
                issue(lane, f"{project}: REFUSED a commit that would delete {len(deleted)} files "
                            f"(e.g. {deleted[:3]}); not publishing")
                index.unlink(missing_ok=True)
                return False
            commit = plumb("commit-tree", tree, "-p", base, "-m",
                           f"pair {project}: {note} (V2 vs V1, gpt-5.6-luna)").stdout.strip()
            if not commit:
                time.sleep(3 * attempt)
                continue
            pushed = subprocess.run(["git", "push", "-q", REMOTE, f"{commit}:refs/heads/main"], cwd=REPO,
                                    capture_output=True, text=True, timeout=600,
                                    env={**os.environ, "CLONEDEMOCKER_ALLOW_PUSH": "1"})
            if pushed.returncode == 0:
                index.unlink(missing_ok=True)
                # Move local main (and the default index) onto what was just pushed. Without
                # this, local main fell 351 commits behind and every published file showed as a
                # pending change in the IDE. A mixed reset never writes the working tree.
                subprocess.run(["git", "reset", "-q", commit], cwd=REPO, capture_output=True)
                return True
            time.sleep(3 * attempt)
        index.unlink(missing_ok=True)
    issue(lane, f"{project}: push failed 5 times ({note}); rows are on disk, next sync retries")
    return False


def merge_with_remote(base: str, rel: str) -> None:
    """Fold rows another machine published into this file before it replaces the remote copy.

    Two machines share CloudStack's pair datasets. Writing this host's copy over the remote one
    would not merge, it would replace -- dropping every row the other host added since. Rows
    present only remotely are adopted; for rows on both sides this host's copy wins (it is the
    one that just ran them). The local file is updated too, so it stays a superset.
    """
    if not (rel.endswith("refactoring-results.json") or rel.endswith("refactoring-results.csv")):
        return
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=REPO, capture_output=True)
    if shown.returncode != 0 or not shown.stdout:
        return
    remote_text = shown.stdout.decode("utf-8", "replace")
    path = REPO / rel
    if rel.endswith(".json"):
        local = json.loads(path.read_text(encoding="utf-8"))
        remote = json.loads(remote_text).get("results", {})
        missing = {k: v for k, v in remote.items() if k not in local["results"]}
        if missing:
            local["results"].update(missing)
            local["totalMcis"] = len(local["results"])
            path.write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8", newline=chr(10))
    else:
        import csv, io
        mine = list(csv.DictReader(path.open(encoding="utf-8")))
        theirs = list(csv.DictReader(io.StringIO(remote_text)))
        seen = {r.get("mciId") for r in mine}
        extra = [r for r in theirs if r.get("mciId") not in seen]
        if extra and mine:
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(mine[0].keys()), extrasaction="ignore")
                writer.writeheader()
                writer.writerows(mine + extra)


ROUTE: dict = {"reverse": False, "round1": None, "slice": (0, 1), "start": 0.0}
ROUND1_SETUP = "CloneDeMocker+Terra-5.6"
_remote_cache: dict = {"at": 0.0}


def _remote_rows(rel: str) -> dict:
    shown = subprocess.run(["git", "show", f"{REMOTE}/main:{rel}"], cwd=REPO, capture_output=True)
    if shown.returncode != 0 or not shown.stdout:
        return {}
    return json.loads(shown.stdout.decode("utf-8", "replace")).get("results", {})


# Modules CloudStack builds only when the `noredist` property is set (profiles in pom.xml and
# plugins/pom.xml). Only C has graded them, with CLONEDEMOCKER_MAVEN_ARGS=-Dnoredist.
NOREDIST_MODULES = ("vmware-base", "plugins/api/vmware-sioc", "plugins/backup/veeam", "plugins/hypervisors/vmware",
                    "plugins/network-elements/cisco-vnmc", "plugins/network-elements/nsx",
                    "plugins/network-elements/netris", "plugins/network-elements/juniper-contrail",
                    "plugins/network-elements/tungsten", "plugins/database/mysql-ha")


def make_should_run(project: str):
    """Decide per MCI, on the remote's current data, whether this lane runs it.

    --round1-failures skip: leave MCIs whose round-1 verdict is not SUCCESS to C (A and B).
    --round1-failures only: run only those (C). An MCI round 1 has not graded yet counts as
    not-failed... no longer: round 1 is complete, so a missing verdict goes to C. Any MCI the remote already has in both pair
    datasets was done by another machine and is skipped.
    """
    def refresh() -> None:
        if time.time() - _remote_cache["at"] < 120:
            return
        git("fetch", "-q", REMOTE, "main")
        base = f"data/{project}/refactoring"
        _remote_cache.update({
            "at": time.time(),
            "round1": _remote_rows(f"{base}/{ROUND1_SETUP}/refactoring-results.json") if ROUTE["round1"] else {},
            "v2": _remote_rows(f"{base}/{SETUPS[0]}/refactoring-results.json"),
            "v1": _remote_rows(f"{base}/{SETUPS[1]}/refactoring-results.json"),
        })

    def should_run(mci_id: str) -> bool:
        k, n = ROUTE["slice"]
        if n > 1 and zlib.crc32(mci_id.encode("utf-8")) % n != k:
            return False                                  # another lane on this host has it
        try:
            refresh()
        except Exception:  # noqa: BLE001 - offline: fall back to the last view
            pass
        if mci_id in _remote_cache.get("v2", {}) and mci_id in _remote_cache.get("v1", {}):
            return False
        verdict = (_remote_cache.get("round1", {}).get(mci_id) or {}).get("classification")
        # Round 1 is complete, so an MCI without a verdict was never gradable there (e.g.
        # Volume::4 crashed B's interpreter six times): it goes to C, not to A and B.
        failed = verdict != "SUCCESS"
        # Round 1 graded CloudStack's noredist-gated modules (vmware, nsx, tungsten, ...) only on
        # C, with CLONEDEMOCKER_MAVEN_ARGS=-Dnoredist. Without it those modules are not in the
        # reactor and the MCI fails in seconds as ENVIRONMENT_NOT_READY (46 such pairs on A,
        # 2026-09-24). Send them where round 1 could build them.
        scope = str((_remote_cache.get("round1", {}).get(mci_id) or {}).get("scope") or "")
        gated = any(module in scope for module in NOREDIST_MODULES)
        if ROUTE["round1"] == "skip":
            return not failed and not gated
        if ROUTE["round1"] == "only":
            if gated and not failed:
                return True
            if verdict == "ENVIRONMENT_NOT_READY" and ROUTE.get("inherit_env"):
                # User decision 2026-09-24: an MCI whose untouched baseline could not be
                # established in round 1 keeps that verdict for V2 and V1 instead of paying up to
                # 3600 s per baseline again. Written as rows flagged inheritedFromRound1.
                from baseline_v1 import run_pair
                run_pair.inherit_round1(project, mci_id, _remote_cache["round1"][mci_id], run_pair.HARNESS_V2)
                run_pair.inherit_round1(project, mci_id, _remote_cache["round1"][mci_id], run_pair.HARNESS_V1)
                return False
            return failed
        return True

    return should_run


def run_id_for(project: str) -> str:
    known = json.loads(RUNIDS.read_text(encoding="utf-8")) if RUNIDS.is_file() else {}
    if project in known:
        return known[project]
    from studio.detection_service import DetectionService
    run_id = DetectionService(REPO).restore_from_data(PROJECT_ROOTS[project])["runId"]
    known = json.loads(RUNIDS.read_text(encoding="utf-8")) if RUNIDS.is_file() else {}
    known[project] = run_id
    RUNIDS.write_text(json.dumps(known, indent=2), encoding="utf-8")
    return run_id


def worker(lane: str, projects: list[str]) -> None:
    from baseline_v1 import run_pair
    run_pair.load_env()
    run_pair.LANES_ON_HOST = ROUTE["slice"][1]
    run_pair.LANE = lane
    for project in projects:
        try:
            run_id = run_id_for(project)
        except Exception as error:  # noqa: BLE001
            issue(lane, f"{project}: cannot open a detection run ({type(error).__name__}: {error}); skipped")
            continue
        count = {"n": 0}

        def after(proj: str, mci_id: str) -> None:
            count["n"] += 1
            if count["n"] % SYNC_EVERY == 0:
                sync(lane, proj, f"{count['n']} MCIs")

        for attempt in range(2):                     # a second pass retries tool errors once
            k, n = ROUTE["slice"]
            summary = run_pair.run_project(run_id, project, after_mci=after,
                                           should_run=make_should_run(project), reverse=ROUTE["reverse"],
                                           start=ROUTE["start"], workspace_suffix=lane if k else "")
            sync(lane, project, "pass end")
            if not summary["remaining"]:
                break
        if summary["remaining"]:
            issue(lane, f"{project}: {summary['remaining']} MCIs still missing after two passes")
        run_pair.log(f"[{lane}] project {project} finished")
    run_pair.log(f"[{lane}] lane finished")


def supervise(lane: str, projects: list[str]) -> None:
    log = (RESULTS / f"pair-lane-{lane}.log").open("a", encoding="utf-8")
    restarts: list[float] = []
    while True:
        roots = [a for name in projects if name in PROJECT_ROOTS for a in ("--root", f"{name}={PROJECT_ROOTS[name]}")]
        route = ((["--reverse"] if ROUTE["reverse"] else []) + (["--round1-failures", ROUTE["round1"]] if ROUTE["round1"] else [])
                 + (["--inherit-round1-env"] if ROUTE.get("inherit_env") else [])
                 + ["--slice", "{}/{}".format(*ROUTE["slice"]), "--start", str(ROUTE["start"])])
        child = subprocess.Popen([sys.executable, __file__, "--lane", lane, "--worker", *roots, *route, *projects],
                                 cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        code = child.wait()
        if code == 0:
            return
        now = time.time()
        restarts = [t for t in restarts if now - t < 600] + [now]
        if len(restarts) > 3:
            issue(lane, f"worker died {len(restarts)} times in 10 minutes (last exit {code}); lane STOPPED")
            return
        issue(lane, f"worker exited with {code}; restarting")
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", action="append", default=[],
                        help="NAME=PATH, where this host keeps a project (e.g. on Linux)")
    parser.add_argument("--reverse", action="store_true", help="walk the MCI list from the end")
    parser.add_argument("--round1-failures", choices=("skip", "only"), default=None,
                        help="skip: leave round-1 failures to C; only: run just those (C)")
    parser.add_argument("--inherit-round1-env", action="store_true",
                        help="with --round1-failures only: MCIs round 1 graded ENVIRONMENT_NOT_READY "
                             "keep that verdict for V2 and V1 (flagged inheritedFromRound1), not re-run")
    parser.add_argument("--slice", default="0/1",
                        help="K/N: this lane takes MCIs with crc32(id) %% N == K (N lanes on one host)")
    parser.add_argument("--start", type=float, default=0.0,
                        help="0..1: begin the walk this far into the MCI list, wrapping around")
    parser.add_argument("projects", nargs="+")
    args = parser.parse_args()
    ROUTE["reverse"] = args.reverse
    ROUTE["round1"] = args.round1_failures
    ROUTE["inherit_env"] = args.inherit_round1_env
    k, _, n = args.slice.partition("/")
    ROUTE["slice"] = (int(k), int(n or 1))
    if not 0 <= ROUTE["slice"][0] < ROUTE["slice"][1]:
        parser.error("--slice K/N needs 0 <= K < N")
    ROUTE["start"] = args.start
    for pair in args.root:
        name, _, path = pair.partition("=")
        PROJECT_ROOTS[name] = path
    (worker if args.worker else supervise)(args.lane, args.projects)
