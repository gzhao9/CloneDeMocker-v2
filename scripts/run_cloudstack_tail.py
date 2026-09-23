"""Run this machine's back-to-front half of the CloudStack MCI list, unattended.

Walks the 1828 MCIs of runId ad6456be from the tail down, one at a time, merging
each result into data/cloudstack/ and pushing it. The other machine walks the same
list from the front, so both write the same dataset files and every push races it.

Two properties make a multi-day unattended run survivable:

  * **The worklist is derived, never stored.** What is left is recomputed from
    refactoring-results.json on every iteration, so a crash, a restart, or the
    other machine finishing something first all resolve themselves. Nothing has
    to be reconciled by hand.
  * **Conflicts always resolve toward upstream, then re-apply ours.** Taking a
    side is what loses data: the other machine lost four MCIs that way. After any
    rebase — conflicted or not — this re-merges only this machine's entry through
    canonical_store.merge, which leaves entries it was not given untouched. Both
    sides therefore survive regardless of how the conflict resolved, so the push
    path never has to be right about who won.

Usage:
    python scripts/run_cloudstack_tail.py                 # until the tail is done
    python scripts/run_cloudstack_tail.py --stop-after 50
    python scripts/run_cloudstack_tail.py --no-push
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from studio import canonical_store  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.refactoring_agent import RefactoringAgent  # noqa: E402

# The detection restored from data/cloudstack/, with file paths already remapped from the
# other machine's checkout to this one's. Reused rather than restored per run: restoring
# copies a 165 MB detection file every time.
RUN_ID = "bf09daadb15140fb81972020b2190a25"
WORKSPACE_ID = "cloudstack-tail-no-pit"
PROJECT = "cloudstack"
MODEL = "gpt-5.6-terra"
REMOTE = "github"
DATASET = REPO / "data/cloudstack/refactoring/CloneDeMocker+Terra-5.6"
RESULTS = DATASET / "refactoring-results.json"
CONFLICT_PATHS = ["refactoring-results.json", "refactoring-results.csv"]
# MCIs whose run died inside the tooling. Kept out of the dataset on purpose: a tool
# exception classifies as MODEL_DECLINED, which would read as "the model refused" in the
# results and quietly overstate that category. Recorded here instead so the derived
# worklist still advances past them and they stay auditable.
SKIPPED = REPO / "validation/cloudstack_tail_skipped.json"
# MCIs to run even though they already have a result. The derived worklist skips anything
# present in the dataset, which is right in the normal case and wrong after a verdict is
# invalidated — these 69 were graded before the mvn install gate existed, so their failures
# describe this host rather than the subject. An id is removed from the file once its re-run
# lands, so a restart resumes instead of repeating.
RERUN = REPO / "validation/cloudstack_rerun_ids.txt"
BOARD = REPO / "COLLAB.md"
UNREAD_ALERT_FILE = REPO / "validation/results/collab_unread_for_A.txt"


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%m-%d %H:%M:%S')} {message}", flush=True)


def check_collab_messages() -> list[dict]:
    """Inspect COLLAB.md after a pull and report any unread entries addressed to or cc'ing A."""
    if not BOARD.is_file():
        return []
    try:
        content = BOARD.read_text(encoding="utf-8")
    except OSError:
        return []

    start = content.find("## ACTIVE")
    end = content.find("\n## Section:", start)
    if end == -1:
        end = len(content)
    active_text = content[start:end]

    unread = []
    blocks = re.split(r"(?=^### \[)", active_text, flags=re.M)
    for b in blocks:
        header_m = re.match(r"^### \[([BC]-\d+)\].*?[·\s]+([BC])\s*→\s*(.*?)(?:\n|$)", b)
        if not header_m:
            continue
        entry_id = header_m.group(1)
        author = header_m.group(2)
        target = header_m.group(3).strip()

        read_by_a = re.search(r"^- read-by-A:(.*)$", b, re.M)
        if read_by_a and not read_by_a.group(1).strip():
            summary = ""
            for line in b.splitlines():
                line = line.strip()
                if line and not line.startswith("###") and not line.startswith("- "):
                    summary = line
                    break
            unread.append({
                "id": entry_id,
                "author": author,
                "target": target,
                "summary": summary[:120]
            })

    if unread:
        log(f"🔔 [COLLAB ALERT] Found {len(unread)} unread message(s) for A on the board:")
        lines_for_file = []
        for item in unread:
            log(f"    * [{item['id']}] {item['author']} -> {item['target']}: {item['summary']}")
            lines_for_file.append(f"[{item['id']}] {item['author']} -> {item['target']}\n  {item['summary']}\n")
        try:
            UNREAD_ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
            UNREAD_ALERT_FILE.write_text("\n".join(lines_for_file), encoding="utf-8")
        except OSError:
            pass
    else:
        try:
            UNREAD_ALERT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return unread


def git(*args: str, check: bool = False, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, text=True, capture_output=True,
                          check=check, timeout=timeout)


def load_env() -> None:
    for candidate in (REPO / ".env", REPO.parent / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def done_ids() -> set[str]:
    try:
        return set(json.loads(RESULTS.read_text(encoding="utf-8")).get("results", {}))
    except (OSError, json.JSONDecodeError):
        # A conflicted or half-written file must not be read as "nothing is done" — that would
        # re-run the whole list. Treat it as unknown and let the caller stop.
        return set()


def check_environment() -> None:
    """Refuse to start without what the harness needs, instead of grading without it.

    B-012's failure mode is the one this guards: a runner that comes up with no Maven on
    PATH cannot launch it, the harness reports that as "compilation did not pass", and the
    MCI is filed as ENVIRONMENT_NOT_READY — indistinguishable from a genuinely unbuildable
    module and never retried, because an MCI counts as done once a result exists. A's own
    69 re-grades come from the same shape of problem: a missing install gate rather than a
    missing binary, recorded as a verdict either way.
    """
    if not shutil.which("mvn.cmd") and not shutil.which("mvn"):
        sys.exit("mvn is not on PATH — refusing to start rather than grade without it")

    # engine/schema runs a shell script from exec-maven-plugin during its test phase, so a
    # Maven process without bash cannot get through it: the phase dies, the candidate run
    # produces zero test results, and testStatus != PASSED is then classified
    # FAILED_BEHAVIORAL_EQUIVALENCE — an environment fault wearing the label of a
    # refactoring that changed behaviour. 61 of A's first 77 rows failed this way.
    if not shutil.which("bash"):
        for candidate in (Path(r"C:\Program Files\Git\usr\bin"), Path(r"C:\Program Files\Git\bin")):
            if (candidate / "bash.exe").is_file():
                os.environ["PATH"] = f"{candidate}{os.pathsep}{os.environ.get('PATH', '')}"
                log(f"added {candidate} to PATH so Maven can run bash")
                break
    if not shutil.which("bash"):
        sys.exit("bash is not on PATH — engine/schema's test phase needs it; refusing to start")
    # The gate's artifacts, not the gate's exit code: this is what `-pl <module> -am`
    # resolves against, and its absence is what produced the null-scope verdicts.
    installed = Path.home() / ".m2" / "repository" / "org" / "apache" / "cloudstack"
    if not installed.is_dir() or not any(installed.iterdir()):
        sys.exit(f"no CloudStack artifacts in {installed} — run mvn clean install first")
    log(f"environment ok: mvn present, artifacts installed under {installed}")


def maven_never_launched(result: dict) -> bool:
    """True when the harness never got Maven to run, so its verdict describes nothing."""
    harness = result.get("harness") or {}
    for side in ("baseline", "candidate", "after"):
        state = harness.get(side) or {}
        for line in (state.get("diagnostics") or []):
            text = str(line)
            if "WinError 2" in text or "cannot find the file specified" in text.lower():
                return True
    return False


def rerun_ids() -> list[str]:
    try:
        return [line.strip() for line in RERUN.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def drop_rerun_id(mci_id: str) -> None:
    remaining = [m for m in rerun_ids() if m != mci_id]
    RERUN.write_text(("\n".join(remaining) + "\n") if remaining else "", encoding="utf-8")


def skipped_ids() -> dict[str, str]:
    try:
        return json.loads(SKIPPED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_skip(mci_id: str, reason: str) -> None:
    records = skipped_ids()
    records[mci_id] = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {reason}"
    SKIPPED.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


# Identifies rows this machine produced. The host matters, not just the OS: A and B both
# run Windows but were not the same environment — A graded ~80 MCIs with no install gate,
# B ran for a minute with no Maven on PATH. Pooling those as one "windows" would hide
# exactly the kind of difference this field exists to expose. The other agents also skip
# rows carrying someone else's producedBy, so an unstamped row reads as unclaimed.
PRODUCED_BY = "A"
# Rows per push. One-per-MCI starved the slowest of three workers off the branch entirely.
BATCH_PUSH = 25
PLATFORM = "windows"
HOST = "daynell-win-gated"


def relayer(entry: dict, diff_source: Path | None) -> None:
    """Re-add this machine's entry on top of whatever the dataset currently holds."""
    entry = {**entry, "producedBy": PRODUCED_BY, "platform": PLATFORM, "host": HOST}
    diff_lookup = {}
    if diff_source is not None and diff_source.is_file():
        diff_lookup[entry["mciId"]] = diff_source
    canonical_store.merge(project=PROJECT, repository_root=REPO, entries=[entry],
                          detection_source=None, diff_lookup=diff_lookup, model=MODEL,
                          harness=canonical_store.HARNESS_CLONEDEMOCKER, use_mock=False)


def recover_repo() -> None:
    """Clear a working tree left mid-operation by something other than this runner.

    The failure this prevents is silent, which is what makes it the dangerous one: a
    session interrupted mid-rebase leaves the tree in that state, every later push fails,
    and the batch keeps computing MCIs while publishing none of them. Progress looks normal
    until someone checks the remote. Safe to do unconditionally — the working tree holds
    nothing this runner needs, since the dataset is rebuilt from the results file.
    """
    git_dir = REPO / ".git"
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        log("    recover: aborting a rebase left in progress")
        git("rebase", "--abort")
    if (git_dir / "MERGE_HEAD").exists():
        log("    recover: aborting a merge left in progress")
        git("merge", "--abort")
    lock = git_dir / "index.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 300:
        # Older than any live git command; whoever held it is gone.
        log("    recover: removing a stale index.lock")
        try:
            lock.unlink()
        except OSError:
            pass


def merge_board() -> None:
    """Resolve a COLLAB.md conflict without discarding either side's entries or stamps."""
    ours = git("show", ":2:COLLAB.md").stdout
    mine = git("show", ":3:COLLAB.md").stdout
    if not ours or not mine:
        git("checkout", "--ours", "--", "COLLAB.md")
        git("add", "--", "COLLAB.md")
        return
    blocks = re.split(r"(?=^### \[)", mine, flags=re.M)
    missing = []
    for b in blocks:
        if not b.startswith("### [A-"):
            continue
        entry_id = b[5:b.find("]")]
        if f"### [{entry_id}]" in ours:
            continue
        missing.append(b)
    for slot in ("- recv-A:", "- read-by-A:"):
        for m in re.finditer(rf"^{re.escape(slot)}[ \t]*(\S.*)$", mine, flags=re.M):
            value = m.group(1).strip()
            head = mine.rfind("### [", 0, m.start())
            if head == -1:
                continue
            entry_id = mine[head + 5:mine.find("]", head)]
            at = ours.find(f"### [{entry_id}]")
            if at == -1:
                continue
            stop = ours.find("- done:", at)
            j = ours.rfind(slot, at, stop if stop != -1 else len(ours))
            if j == -1:
                continue
            k = j + len(slot)
            if not ours[k:ours.find(chr(10), k)].strip():
                ours = ours[:k] + " " + value + ours[k:]

    if missing:
        marker = "## ACTIVE\n\n"
        at = ours.find(marker)
        at = at + len(marker) if at != -1 else 0
        ours = ours[:at] + "".join(missing) + ours[at:]
    (REPO / "COLLAB.md").write_text(ours, encoding="utf-8", newline="\n")
    git("add", "--", "COLLAB.md")


def sync(message: str, batch: list[tuple[dict, Path | None]], attempts: int = 5) -> bool:
    """Commit and push a batch, surviving the other machines pushing to the same files."""
    recover_repo()
    git("add", "--", f"data/{PROJECT}", "COLLAB.md")
    if not git("diff", "--cached", "--quiet").returncode:
        return True

    git("commit", "-q", "-m", message)

    for attempt in range(1, attempts + 1):
        pull = git("pull", "--rebase", REMOTE, "main")
        if pull.returncode == 0:
            check_collab_messages()
        else:
            for name in CONFLICT_PATHS:
                git("checkout", "--ours", "--", str((DATASET / name).relative_to(REPO)))
                git("add", "--", str((DATASET / name).relative_to(REPO)))
            status_out = git("status", "--porcelain").stdout
            if "COLLAB.md" in status_out:
                merge_board()
            cont = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"],
                                  cwd=REPO, text=True, capture_output=True, timeout=300)
            if cont.returncode != 0:
                git("rebase", "--abort")
                log(f"    sync: rebase unresolved on attempt {attempt}, retrying")
                time.sleep(5 * attempt)
                continue
            check_collab_messages()

        for pending_entry, pending_diff in batch:
            relayer(pending_entry, pending_diff)
        git("add", "--", f"data/{PROJECT}")
        if git("diff", "--cached", "--quiet").returncode:
            git("commit", "-q", "-m", f"Re-apply {len(batch)} MCIs after rebase")

        if git("push", REMOTE, "main").returncode == 0:
            return True

        time.sleep(2 * attempt)

    log("    sync: giving up for now; the next MCI carries it forward")
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-after", type=int, default=0, help="0 = until the tail is done")
    parser.add_argument("--no-push", dest="push", action="store_false", default=True)
    args = parser.parse_args()

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        log("OPENAI_API_KEY is not set")
        sys.exit(2)
    check_environment()
    check_collab_messages()

    service = DetectionService(REPO)
    _, raw = service.load_raw_detection(RUN_ID)
    instances = service._indexed_instances(raw)
    by_id = {item["id"]: item for item in instances}
    ordered = [item["id"] for item in instances]
    agent = RefactoringAgent(service)

    log(f"tail runner: {len(ordered)} MCIs total, walking back to front")
    processed = 0
    pending: list[tuple[dict, Path | None]] = []
    counts: dict[str, int] = {}
    started = time.time()
    # A derived worklist retries anything missing from the results, which is what makes the
    # run self-healing — but an MCI that can never be persisted would then be retried forever
    # and the remaining 1600 would never be reached. Cheap to retry (a repeat is a cache hit,
    # ~1 s and no tokens), so allow a few, then set it aside.
    attempts: dict[str, int] = {}
    MAX_ATTEMPTS = 3

    while True:
        done = done_ids()
        if not done:
            log("results file unreadable (conflict markers?) — stopping rather than re-running")
            break
        skipped = skipped_ids()
        forced = [m for m in rerun_ids() if m in by_id and m not in skipped]
        remaining = forced + [m for m in reversed(ordered)
                              if m not in done and m not in skipped and m not in forced]
        if not remaining:
            log(f"nothing left in the tail ({len(skipped)} skipped after tool errors)")
            break

        mci_id = remaining[0]
        is_forced = mci_id in forced
        attempts[mci_id] = attempts.get(mci_id, 0) + 1
        if attempts[mci_id] > MAX_ATTEMPTS:
            log(f"    {mci_id}: still absent from the results after {MAX_ATTEMPTS} runs, setting aside")
            record_skip(mci_id, f"not persisted after {MAX_ATTEMPTS} attempts")
            git("add", "--", str(SKIPPED.relative_to(REPO)))
            git("commit", "-q", "-m", f"Set aside {mci_id}: result never persisted")
            continue

        index = ordered.index(mci_id) + 1
        log(f"[{len(done)}/{len(ordered)}] START {mci_id} (index {index})")
        item_started = time.time()

        try:
            result = agent.run(run_id=RUN_ID, selected_mci_ids=[mci_id], model=MODEL,
                               user_instruction="", run_pit=False, api_profile="default",
                               use_mock=False, max_retries=2, sequence_selection=None,
                               use_cache=True, progress_callback=None, workspace_id=WORKSPACE_ID)
        except Exception as error:  # noqa: BLE001 - one bad MCI must not end a multi-day run
            log(f"    TOOL ERROR {type(error).__name__}: {str(error)[:200]}")
            # Deliberately not merged into the dataset: classify_agent_result maps a
            # placeholder like this to MODEL_DECLINED, so recording it would report a tooling
            # crash as the model refusing to refactor. The skip list keeps the run moving
            # without putting a fact in the results that is not one.
            record_skip(mci_id, f"{type(error).__name__}: {str(error)[:200]}")
            git("add", "--", str(SKIPPED.relative_to(REPO)))
            git("commit", "-q", "-m", f"Skip {mci_id} after a tool error")
            continue

        elapsed = time.time() - item_started

        if maven_never_launched(result):
            # A broken invocation is not a verdict. Recording it would file an unbuildable
            # -looking ENVIRONMENT_NOT_READY that nothing ever retries, because the worklist
            # treats any existing result as done.
            log(f"    QUARANTINED: Maven never launched for {mci_id}; not recording a verdict")
            record_skip(mci_id, "maven never launched — quarantined, needs re-run")
            git("add", "--", str(SKIPPED.relative_to(REPO)))
            git("commit", "-q", "-m", f"Quarantine {mci_id}: Maven never launched")
            continue

        entry = canonical_store.entry_from_agent_result(mci_id, result)
        classification = canonical_store.classify_agent_result(result)
        counts[classification] = counts.get(classification, 0) + 1
        processed += 1

        diff_source = None
        proposal_id = result.get("proposalId")
        if proposal_id:
            candidate = REPO / ".clonedemocker" / "runs" / RUN_ID / "refactoring" / proposal_id / "changes.diff"
            if candidate.is_file() and candidate.stat().st_size:
                diff_source = candidate

        relayer(entry, diff_source)
        if is_forced:
            # Retire it only once its replacement is in the dataset, so an interrupted
            # re-run resumes rather than leaving the invalid verdict in place unnoticed.
            drop_rerun_id(mci_id)
            git("add", "--", str(RERUN.relative_to(REPO)))
        log(f"    {'[regrade] ' if is_forced else ''}{classification} {elapsed:.0f}s "
            f"tokens={(result.get('usage') or {}).get('total_tokens', 0)}")

        # Publish in batches rather than per MCI. Three workers pushing every row against a
        # 32 MB results file made 142 commits in two hours, and the worker that never won a
        # rebase simply starved -- C sat 57 commits ahead and 580 behind, its results correct
        # but unpublished. Batching trades a crash window (up to BATCH_PUSH rows still local)
        # for contention every worker shares; the rows are on disk either way, and the next
        # sync commits `data/cloudstack` wholesale, so an interrupted batch is carried by the
        # following one rather than lost.
        pending.append((entry, diff_source))
        if args.push and len(pending) >= BATCH_PUSH:
            sync(f"sync {len(pending)} completed MCIs to CloudStack 24.0.0-SNAPSHOT dataset",
                 pending)
            pending.clear()

        if args.stop_after and processed >= args.stop_after:
            log(f"stopping after {processed} as requested")
            break

    if args.push and pending:
        sync(f"sync {len(pending)} completed MCIs to CloudStack 24.0.0-SNAPSHOT dataset",
             pending)
        pending.clear()

    hours = (time.time() - started) / 3600
    log(f"session done: {processed} MCIs in {hours:.1f} h; {counts}")


if __name__ == "__main__":
    main()
