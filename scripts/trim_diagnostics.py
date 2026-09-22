"""Strip Maven download noise out of harness diagnostics before they land in data/.

Why this exists: `studio.canonical_store.entry_from_agent_result` stores the harness's raw
Maven output. Measured over 166 CloudStack MCIs that is 60 KB per MCI, of which 77% is
`Progress (2): 3.9/5.3 MB | 635/812 kB`-style download chatter. Projected over a full
1828-MCI run the results file reaches ~113 MB, past GitHub's 100 MB hard limit, so the batch
would become unpushable partway through. Trimming brings it to ~32 KB per MCI (~60 MB full
scale) while keeping every line anyone would actually read.

What is kept: every `[ERROR]` / `[WARNING]` line, build verdicts, `Tests run:` summaries,
dependency-resolution failures, compilation failures, and the last lines of each stream so a
failure keeps its surrounding context. What is dropped: progress bars and download/upload
chatter.

Usable two ways:

    # as a library, on one entry dict, before canonical_store.merge()
    from scripts.trim_diagnostics import trim_entry
    trim_entry(entry)

    # as a CLI, rewriting an existing results file in place
    python scripts/trim_diagnostics.py data/<project>/refactoring/<setup>/refactoring-results.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

NOISE = re.compile(r"^(Progress \(|Downloading from |Downloaded from |Uploading |Uploaded )")
KEEP = re.compile(
    r"\[ERROR\]|\[WARNING\]|BUILD (SUCCESS|FAILURE)|Tests run:|"
    r"Could not (find|resolve|collect)|Compilation (failure|error)|T E S T S"
)

MAX_KEEP = 200
TAIL = 60


def trim_text(text: Any) -> Any:
    """Drop progress noise from one diagnostics string, keeping signal plus a tail."""
    if not isinstance(text, str):
        return text
    lines = [line for line in text.splitlines() if not NOISE.match(line.strip())]
    signal = [line for line in lines if KEEP.search(line)][-MAX_KEEP:]
    seen = set(signal)
    tail = [line for line in lines[-TAIL:] if line not in seen]
    return "\n".join(signal + tail)


def trim_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Trim both harness sides of one canonical entry, in place, and return it."""
    harness = entry.get("harness") or {}
    for side in ("baseline", "candidate"):
        section = harness.get(side)
        if isinstance(section, dict) and isinstance(section.get("diagnostics"), list):
            section["diagnostics"] = [trim_text(item) for item in section["diagnostics"]]
    return entry


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1])
    document = json.loads(path.read_text(encoding="utf-8"))
    before = len(json.dumps(document, ensure_ascii=False))

    results = document.get("results", {})
    for entry in results.values():
        trim_entry(entry)

    text = json.dumps(document, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    after = len(text)
    count = len(results) or 1
    print(f"{path}")
    print(f"  {before/1e6:.1f} MB -> {after/1e6:.1f} MB  ({1 - after/before:.1%} smaller, "
          f"{after/count/1024:.0f} KB per MCI over {count} entries)")


if __name__ == "__main__":
    main()
