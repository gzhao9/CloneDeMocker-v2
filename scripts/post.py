"""Insert a board entry under the real `## ACTIVE` heading, or refuse.

Written after a hand-rolled insert spliced an entry into the middle of line 1 and
duplicated two others. Three separate causes, all the same shape:

  * `find("## ACTIVE\n\n")` assumed a blank line that the board's own rules text does not
    have, returned -1, and `-1 + len(marker)` became a plausible-looking offset near the top
    of the file;
  * `find("## ACTIVE")` then matched the heading *quoted inside a rule*, not the heading;
  * neither checked anything before writing.

So: match a whole line, verify afterwards, and exit nonzero if the entry is not where it
should be. Never compute an insertion point from a failed search.

Usage:  python scripts/post.py entry.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "COLLAB.md"


def main() -> None:
    body = Path(sys.argv[1]).read_text(encoding="utf-8").rstrip() + "\n"
    ids = re.findall(r"^### \[([ABC]-\d+)\]", body, flags=re.M)
    if len(ids) != 1:
        sys.exit(f"post: entry must contain exactly one '### [X-NNN]' header, found {ids}")
    entry_id = ids[0]

    lines = BOARD.read_text(encoding="utf-8").split("\n")
    heads = [i for i, l in enumerate(lines) if l.strip() == "## ACTIVE"]
    if len(heads) != 1:
        sys.exit(f"post: expected exactly one '## ACTIVE' heading line, found {len(heads)}")
    if re.search(rf"^### \[{re.escape(entry_id)}\]", "\n".join(lines), flags=re.M):
        sys.exit(f"post: {entry_id} is already on the board")

    at = heads[0] + 1
    lines[at:at] = [""] + body.split("\n")
    BOARD.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    check = BOARD.read_text(encoding="utf-8")
    if not re.search(rf"^### \[{re.escape(entry_id)}\]", check, flags=re.M):
        sys.exit(f"post: VERIFY FAILED -- {entry_id} is not on the board after writing")
    dupes = [k for k in set(re.findall(r"^### \[([ABC]-\d+)\]", check, flags=re.M))
             if len(re.findall(rf"^### \[{re.escape(k)}\]", check, flags=re.M)) > 1]
    if dupes:
        sys.exit(f"post: VERIFY FAILED -- duplicate entries after writing: {dupes}")
    print(f"post: {entry_id} inserted and verified")


if __name__ == "__main__":
    main()
