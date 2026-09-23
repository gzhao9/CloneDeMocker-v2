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
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

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
            base = git("rev-parse", "FETCH_HEAD").stdout.strip()
            index.unlink(missing_ok=True)
            plumb("read-tree", base)
            for rel in files:
                blob = git("hash-object", "-w", "--", rel).stdout.strip()
                plumb("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}")
            tree = plumb("write-tree").stdout.strip()
            if tree == git("rev-parse", f"{base}^{{tree}}").stdout.strip():
                index.unlink(missing_ok=True)
                return True                                   # nothing new to publish
            commit = plumb("commit-tree", tree, "-p", base, "-m",
                           f"pair {project}: {note} (V2 vs V1, gpt-5.6-luna)").stdout.strip()
            if git("push", "-q", REMOTE, f"{commit}:refs/heads/main").returncode == 0:
                index.unlink(missing_ok=True)
                return True
            time.sleep(3 * attempt)
        index.unlink(missing_ok=True)
    issue(lane, f"{project}: push failed 5 times ({note}); rows are on disk, next sync retries")
    return False


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
            summary = run_pair.run_project(run_id, project, after_mci=after)
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
        child = subprocess.Popen([sys.executable, __file__, "--lane", lane, "--worker", *roots, *projects],
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
    parser.add_argument("projects", nargs="+")
    args = parser.parse_args()
    for pair in args.root:
        name, _, path = pair.partition("=")
        PROJECT_ROOTS[name] = path
    (worker if args.worker else supervise)(args.lane, args.projects)
