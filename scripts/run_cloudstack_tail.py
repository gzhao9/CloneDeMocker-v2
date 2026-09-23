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
    """Write a log line, never raising.

    The stdout this inherits is a GBK pipe on this host, so a single character it cannot
    encode -- an emoji, or any of the board's Chinese text quoted into a message -- raised
    UnicodeEncodeError out of `print` and killed a multi-day run mid-sync, leaving a
    half-written dataset behind. A logger that can stop the run is worse than a lossy one.
    """
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(encoding, "replace").decode(encoding, "replace"), flush=True)
    except OSError:
        pass


def peek_messages() -> list[dict]:
    """Check for messages without pulling, so reading is not gated on writing.

    Publishing is batched at BATCH_PUSH for a good reason (three workers pushing every row
    starved each other on rebases), but that made the batch boundary the *only* moment A
    looked at the board -- roughly every 20 minutes, and on 2026-09-23 not at all for three
    hours, because a broken sync stopped the pull that the check rode on. Reading has none
    of writing's contention: a fetch takes no lock on the working tree, needs no clean tree,
    costs no tokens, and cannot conflict. So fetch and read the remote's copy directly,
    leaving the working tree untouched, and do it every MCI.
    """
    if git("fetch", REMOTE, "main", timeout=120).returncode != 0:
        return []                                   # a racing fetch, or offline; next MCI retries
    watched = ["COLLAB.md", "collab/inbox/A", "collab/read"]
    if not git("diff", "--quiet", "HEAD", "FETCH_HEAD", "--", *watched).returncode:
        return []                                   # nothing addressed here has moved

    # Read from the inbox, not the board (A-024, agreed in B-032, cut over here). Reading is
    # the half that can switch unilaterally: B keeps writing both, so nothing breaks if B has
    # not cut over yet, and A stops parsing a 60 KB file to find out whether anyone wrote to
    # it. Writing stays dual until B confirms its *running* process reads the inbox -- B-033
    # is explicit that an edit to a publisher is not an effect until the process restarts.
    listing = git("ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", "collab/inbox/A")
    if listing.returncode == 0 and listing.stdout.strip():
        seen = read_receipts()
        unread = []
        for path in sorted(listing.stdout.split("\n")):
            entry_id = Path(path).stem
            if not re.fullmatch(r"[BC]-\d+", entry_id) or entry_id in seen:
                continue
            body = git("show", f"FETCH_HEAD:{path}").stdout or ""
            first = next((l.strip() for l in body.splitlines()
                          if l.strip() and not l.startswith(("###", "- "))), "")
            unread.append({"id": entry_id, "author": entry_id[0], "target": "A",
                           "summary": first[:120]})
        report_unread(unread)
        return unread

    shown = git("show", "FETCH_HEAD:COLLAB.md")
    return check_collab_messages(shown.stdout) if shown.returncode == 0 else []


def write_status(**fields: object) -> None:
    """Overwrite `collab/status/A.md` with what A is doing right now. No history.

    B-031 asserted A had paused. A had not -- A was grading at full rate and could not
    push, which no peer could tell apart from a stopped runner. This answers "is A alive,
    where is A, is A stuck" without reading the board, the log or the dataset, and answers
    it in six lines that are rewritten in place rather than appended, so the cost of asking
    never grows. Single-writer, like everything else here, so it cannot conflict.
    """
    path = REPO / "collab" / "status" / "A.md"
    behind = git("rev-list", "--count", "HEAD..FETCH_HEAD").stdout.strip() or "?"
    ahead = git("rev-list", "--count", "FETCH_HEAD..HEAD").stdout.strip() or "?"
    lines = [f"# A — status at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             "", f"unpushed commits : {ahead}    (>0 and growing means A cannot publish)",
             f"behind remote    : {behind}"]
    lines += [f"{k:<17}: {v}" for k, v in fields.items()]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError:
        pass


def read_receipts() -> set[str]:
    """Ids A has already processed, from A's own single-writer receipts file (B-032)."""
    path = REPO / "collab" / "read" / "A.md"
    try:
        return set(re.findall(r"^([ABC]-\d+)\s", path.read_text(encoding="utf-8"), flags=re.M))
    except OSError:
        return set()


def check_collab_messages(content: str | None = None) -> list[dict]:
    """Report any unread entries addressed to or cc'ing A, in `content` or the local board."""
    if content is None:
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

    report_unread(unread)
    return unread


def report_unread(unread: list[dict]) -> None:
    """Log the unread entries and leave them in a file a session can read without git."""
    if not unread:
        try:
            UNREAD_ALERT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return
    log(f"[COLLAB ALERT] Found {len(unread)} unread message(s) for A:")
    lines_for_file = []
    for item in unread:
        log(f"    * [{item['id']}] {item['author']} -> {item['target']}: {item['summary']}")
        lines_for_file.append(f"[{item['id']}] {item['author']} -> {item['target']}\n"
                              f"  {item['summary']}\n")
    try:
        UNREAD_ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
        UNREAD_ALERT_FILE.write_text("\n".join(lines_for_file), encoding="utf-8")
    except OSError:
        pass


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
    # Never publish a results file that does not parse. A conflicted copy was committed and
    # pushed once, and every agent that pulled it then read the corpus as empty.
    if not done_ids():
        log("    sync: results file does not parse — refusing to commit it")
        return False
    git("add", "--", f"data/{PROJECT}", "COLLAB.md", "collab")
    if not git("diff", "--cached", "--quiet").returncode:
        return True

    git("commit", "-q", "-m", message)

    for attempt in range(1, attempts + 1):
        pull = git("pull", "--rebase", REMOTE, "main")
        if pull.returncode == 0:
            check_collab_messages()
        else:
            # Resolve every conflicted path, not a fixed list of two. A and B can collide on
            # any file under the dataset -- `diffs/<mci>.diff` most often, because both hosts
            # may run the same MCI and write the same path with different content. Leaving
            # one of those unresolved made `rebase --continue` fail every attempt, which read
            # in the log as "rebase unresolved" and stopped A publishing for hours.
            # Keep resolving until the rebase is finished, not just until the first conflict
            # is. A rebase replays *every* unpushed commit, and when A is several board
            # entries behind, each one conflicts with the entries B pushed meanwhile. The
            # earlier version resolved one, called `rebase --continue`, and read the *next*
            # commit's conflict as a failure -- so it aborted the whole rebase and retried
            # from scratch, five times, forever. Eight commits took eight rounds to land.
            cont = None
            for _ in range(len(batch) + 40):
                unresolved = [p for p in git("diff", "--name-only", "--diff-filter=U")
                              .stdout.split("\n") if p.strip()]
                if not unresolved:
                    break
                for path in unresolved:
                    if path.endswith("COLLAB.md"):
                        merge_board()      # keeps both sides' entries and stamps
                        continue
                    git("checkout", "--ours", "--", path)  # upstream wins; relayer re-adds ours
                    git("add", "--", path)
                cont = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"],
                                      cwd=REPO, text=True, capture_output=True, timeout=300)
                if cont.returncode != 0 and "empty" in (cont.stdout + cont.stderr).lower():
                    # Taking upstream wholesale can leave nothing to commit; that is a
                    # resolved rebase, not a failed one.
                    cont = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--skip"],
                                          cwd=REPO, text=True, capture_output=True, timeout=300)
            if cont is None:
                # The pull failed with nothing conflicted -- an unstaged file refusing the
                # rebase, most often. Say so, instead of calling it a conflict.
                why = (pull.stderr or pull.stdout).strip().split("\n")[-1][:160]
                log(f"    sync: pull failed with nothing to resolve ({why}), retrying")
                time.sleep(5 * attempt)
                continue
            if cont.returncode != 0:
                git("rebase", "--abort")
                why = (cont.stderr or cont.stdout).strip().split("\n")[-1][:160]
                log(f"    sync: rebase unresolved on attempt {attempt} ({why}), retrying")
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
    high_water = 0

    while True:
        done = done_ids()
        if not done:
            log("results file unreadable (conflict markers?) — stopping rather than re-running")
            break
        # A *readable* but truncated results file is the dangerous one: the existing guard
        # above only catches a file that fails to parse. When a crash mid-sync left 25 rows
        # where there had been 1090, the worklist read that as "almost nothing is done" and
        # began re-running the whole corpus from the tail, overwriting the dataset as it
        # went. The count only ever grows, so a drop means damage, not progress.
        if len(done) < high_water - 5:
            log(f"results file shrank {high_water} -> {len(done)} — stopping; restore it "
                f"from the remote before restarting")
            break
        high_water = max(high_water, len(done))
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
        peek_messages()          # every MCI: a fetch, no pull, no working-tree change
        write_status(position=processed, total=len(ordered), current=mci_id,
                     done=len(done), pending=len(pending))
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
