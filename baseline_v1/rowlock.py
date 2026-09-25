"""One host-wide lock around every read-modify-write of the pair results files.

Several lanes on one host share data/<project>/refactoring/<setup>/. A lane recording a row and
another lane's sync folding remote rows into the same file would each rewrite it from their own
read, and the later write would drop the earlier one's rows. Held for seconds, never across a
model call or a build.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / ".git" / "pair-rows.lock"
STALE_SECONDS = 300        # a holder that died; a real hold is a few seconds


class RowLock:
    def __enter__(self):
        while True:
            try:
                os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL))
                return self
            except FileExistsError:
                try:
                    if time.time() - LOCK.stat().st_mtime > STALE_SECONDS:
                        LOCK.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
                time.sleep(0.5)

    def __exit__(self, *exc):
        LOCK.unlink(missing_ok=True)
