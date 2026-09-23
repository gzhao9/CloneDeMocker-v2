"""COLLAB.md access for the batch runner, implementing the protocol agreed in A-005/B-004.

The split that matters: B is a 24/7 script plus an intermittent model session, and only the
script runs while nobody is watching. So this module does exactly the mechanical half —

  * pull the board and notice entries from A that carry no `read-by-B` yet,
  * stamp them received (the protocol is explicit that the stamp means *received*, not
    understood — a script cannot understand anything), and surface them on stdout so the
    session's monitor wakes the model, which is what actually answers them,
  * post a `NOTE` when a fault it can *detect* occurs, once per distinct condition.

and deliberately not the other half: it never posts a `REQ`, never answers one, and never
opens an entry to agree or acknowledge. Under A-005 `read-by` is the acknowledgement, so a
"thanks, noted" entry is pure token cost for both sides — the courtesy loop the protocol
exists to kill.

Archive discipline follows A's rule: overflow is appended to COLLAB_ARCHIVE.md with open("a")
and the archive is never read back, so sync cost stays flat as history grows.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "COLLAB.md"
ARCHIVE = REPO / "COLLAB_ARCHIVE.md"
INBOX = REPO / "collab" / "inbox"
READ_DIR = REPO / "collab" / "read"

# Peers, plural: with a third agent on the board, filtering on a single THEM made
# every C -> B entry invisible to the runner. C-001 only reached B because a model
# session read the board by hand.
ME = "B"
PEERS = ("A", "C")
THEM = "A"   # retained for the outgoing header only
MY_SECTION = "## Section: agent-cloudstack-master"
MAX_ACTIVE_MINE = 3
KEEP_PROGRESS = 3
ENTRY = re.compile(r"^### \[([AB])-(\d+)\]", re.M)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _read() -> str:
    return BOARD.read_text(encoding="utf-8") if BOARD.is_file() else ""


def _write(text: str) -> None:
    BOARD.write_text(text, encoding="utf-8", newline="\n")


def _section(text: str, heading: str) -> tuple[int, int]:
    """Character span of a section's body, or (-1, -1)."""
    start = text.find(heading)
    if start == -1:
        return -1, -1
    body = text.index("\n", start) + 1
    nxt = text.find("\n## ", body)
    return body, (nxt if nxt != -1 else len(text))


def _archive(blocks: list[str]) -> None:
    if not blocks:
        return
    with ARCHIVE.open("a", encoding="utf-8", newline="\n") as handle:
        for block in blocks:
            handle.write("\n" + block.rstrip() + "\n")


def entry_text(text: str, entry_id: str) -> str | None:
    """The block for one entry id inside an arbitrary board text."""
    i = text.find(f"### [{entry_id}]")
    if i == -1:
        return None
    j = text.find("\n### [", i + 1)
    return text[i:(j if j != -1 else len(text))].strip()


def unread_in(text: str) -> list[tuple[str, str]]:
    """(id, text) for A's entries in `text` whose read-by-B footer is still blank.

    Takes the board text rather than reading from disk, so the runner can inspect origin's
    copy — which is what it actually needs. Our working copy only catches up when a push races
    and forces a rebase, so on a quiet stretch it can sit hours behind, which is precisely when
    a message would go unseen.
    """
    start, end = _section(text, "\n## ACTIVE")
    if start == -1:
        return []
    out = []
    for block in re.split(r"(?=^### \[)", text[start:end], flags=re.M):
        match = ENTRY.match(block)
        if not match or match.group(1) not in PEERS:
            continue
        footer = re.search(rf"- read-by-{ME}:(.*)", block)
        if footer and not footer.group(1).strip():
            out.append((f"{match.group(1)}-{match.group(2)}", block.strip()))
    return out


def unread_from_them() -> list[tuple[str, str]]:
    """unread_in() against our own working copy."""
    return unread_in(_read())


def receipted() -> set[str]:
    """Entry ids already in collab/read/<me>.md. Only this agent writes that file."""
    path = READ_DIR / f"{ME}.md"
    if not path.is_file():
        return set()
    return {m.group(1) for m in re.finditer(r"^([ABC]-\d+)\b", path.read_text(encoding="utf-8"),
                                            flags=re.M)}


def unread_inbox() -> list[tuple[str, str]]:
    """Unread entries from collab/inbox/<me>/, the transport A-024 agreed and A-029 cut over to.

    Read straight off the working tree: the inbox is single-writer per path, so a pull brings
    peers' files in without conflict and there is no upstream-vs-ours skew to reason about --
    which is the whole reason COLLAB.md needed unread_in() to read origin's copy instead.
    """
    if not INBOX.is_dir():
        return []
    done = receipted()
    out = []
    for path in sorted((INBOX / ME).glob("*.md")) if (INBOX / ME).is_dir() else []:
        if path.name == "README.md":
            continue
        entry_id = path.stem
        if entry_id in done or entry_id.startswith(f"{ME}-"):
            continue
        body = path.read_text(encoding="utf-8")
        first = next((l for l in body.splitlines()[2:] if l.strip()), "")
        out.append((entry_id, first))
    return out


def record_receipt(ids: list[str], note: str = "") -> None:
    """Append machine receipts to collab/read/<me>.md.

    B-032's amendment 1: read state stays *visible* to peers, because asker-closes runs on
    "has the peer read this", not "have I read this" -- but single-writer, so it cannot
    conflict the way in-place stamps did.
    """
    if not ids:
        return
    READ_DIR.mkdir(parents=True, exist_ok=True)
    path = READ_DIR / f"{ME}.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else \
        f"# Entries {ME} has read. Only {ME} writes this file.\n"
    have = receipted()
    lines = [l for l in existing.splitlines() if l.strip()]
    stamp = now().replace(" UTC", "")
    added = [f"{i:<8} {stamp}" + (f"  {note}" if note else "") for i in ids if i not in have]
    if added:
        path.write_text("\n".join(lines + added) + "\n", encoding="utf-8", newline="\n")


def mark_read(ids: list[str]) -> None:
    """Stamp a machine receipt in recv-<me>. Never touches read-by-<me>.

    D2: prose in read-by ("received, unread") is invisible to every scanner, so a REQ nobody
    could answer looked identical to an answered one -- which is why A-007 sat for hours.
    Slots are located by searching back from "- done:", because an entry's body may quote
    these same slot names as a worked example.
    """
    if not ids:
        return
    text = _read()
    stamp = now()
    for entry_id in ids:
        i = text.find(f"### [{entry_id}]")
        if i == -1:
            continue
        end = text.find("- done:", i)
        if end == -1:
            continue
        j = text.rfind(f"- recv-{ME}:", i, end)
        if j == -1:
            anchor = text.rfind(f"- read-by-{ME}:", i, end)
            if anchor == -1:
                continue
            text = text[:anchor] + f"- recv-{ME}: {stamp}" + chr(10) + text[anchor:]
            continue
        k = j + len(f"- recv-{ME}:")
        if not text[k:text.find(chr(10), k)].strip():
            text = text[:k] + f" {stamp}" + text[k:]
    _write(text)


def post_note(body: str, re_id: str | None = None, urgent: bool = False) -> str | None:
    """Post a one-way NOTE. Never a REQ: a script cannot hold up its end of a request."""
    text = _read()
    start, end = _section(text, "\n## ACTIVE")
    if start == -1:
        return None
    used = [int(n) for k, n in ENTRY.findall(text) if k == ME]
    entry_id = f"{ME}-{max(used, default=0) + 1:03d}"
    header = f"### [{entry_id}] {now()} · {ME} → {THEM} · NOTE"
    if re_id:
        header += f" · re: {re_id}"
    block = (f"{header}\n\n{'⚠️ ' if urgent else ''}{body.strip()}\n\n"
             f"_Detected and posted by B's runner; no reply needed. If this needs a decision, "
             f"open a REQ and B's next active session will answer._\n"
             f"- read-by-{THEM}:\n- done:\n\n")
    text = text[:start] + block + text[start:]
    _write(text)
    _trim_active()
    return entry_id


def _trim_active() -> None:
    """Retire our own oldest ACTIVE entries. A's entries are theirs to retire."""
    text = _read()
    start, end = _section(text, "\n## ACTIVE")
    if start == -1:
        return
    blocks = re.split(r"(?=^### \[)", text[start:end], flags=re.M)
    entries = [b for b in blocks if ENTRY.match(b)]
    mine = [b for b in entries if ENTRY.match(b).group(1) == ME]
    if len(mine) <= MAX_ACTIVE_MINE:
        return
    drop = mine[MAX_ACTIVE_MINE:]
    _archive(drop)
    kept = "".join(b for b in blocks if b not in drop)
    _write(text[:start] + kept + text[end:])


def progress(done: int, total: int, counts: dict[str, int], hours: float) -> None:
    """Write a progress snapshot into B's own section, keeping the newest few."""
    text = _read()
    start, end = _section(text, MY_SECTION)
    if start == -1:
        text = text.rstrip() + f"\n\n---\n\n{MY_SECTION}\n\n"
        _write(text)
        start, end = _section(_read(), MY_SECTION)
        text = _read()
    success = counts.get("SUCCESS", 0)
    graded = sum(v for k, v in counts.items() if k != "ENVIRONMENT_NOT_READY")
    entry = (f"### {now()} — progress {done}/{total} ({done/total:.1%})\n\n"
             f"SUCCESS {success}/{done} = {success/done:.1%} overall"
             + (f", {success/graded:.1%} excluding ENVIRONMENT_NOT_READY" if graded else "")
             + ".  \n"
             + "Breakdown: " + ", ".join(f"`{k}` {v}" for k, v in sorted(counts.items()))
             + f". Session running {hours:.1f} h.\n\n")
    body = text[start:end]
    blocks = re.split(r"(?=^### )", body, flags=re.M)
    preamble = "".join(b for b in blocks if not b.startswith("### "))
    snapshots = [b for b in blocks if b.startswith("### ")]
    _archive(snapshots[KEEP_PROGRESS - 1:])
    _write(text[:start] + preamble + entry + "".join(snapshots[:KEEP_PROGRESS - 1]) + text[end:])
