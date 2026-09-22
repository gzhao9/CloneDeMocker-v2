# COLLAB — cross-machine coordination board / 跨机协作留言板

Coordination between the two machines pushing to this repository.
双方协作留言板。

- **A** — `daynell`, owns `data/druid-37.0.0/`
- **B** — `Caralll`, owns `data/cloudstack/` (CloudStack master, runId `ad6456be`)

---

## RULES — read this section first, always / 规则（先读这一段）

1. **Read only `## ACTIVE` below.** Never read `COLLAB_ARCHIVE.md` unless you
   need history for a specific question — it exists for traceability, not for
   routine reading. / **只读下面的 `## ACTIVE`**，归档文件不要例行读取。
2. **Entry format** — one entry per message, body **≤ 10 lines**:
   ```
   ### [A-003] 2026-09-22 12:40 · A → B · OPEN
   body...
   - read-by-B:
   - done:
   ```
   Id is `<author><seq>`. Status is one of `OPEN` / `READ` / `DONE`.
3. **Mark what you read.** When you act on an entry addressed to you, fill in
   `read-by-<you>: <timestamp>` and set status to `READ`. When the entry needs
   nothing further from anyone, set `DONE` and fill `done: <timestamp>`. /
   读过并处理后填 `read-by-*` 并改状态；彻底了结时改 `DONE`。
4. **Archive, don't delete.** Whenever you update this file, first move every
   entry that is `DONE` **and** ≥ 24 h old into `COLLAB_ARCHIVE.md` (append at
   the end, keep the text verbatim). Also archive the oldest `DONE`/`READ`
   entries if `## ACTIVE` exceeds **6 entries**, even when newer than 24 h. /
   **归档而非删除**：每次更新本文件时，先把 `DONE` 且超过 24 小时的条目原文追加到
   `COLLAB_ARCHIVE.md`；活跃区超过 6 条时，即使不足 24 小时也归档最旧的。
5. **Keep it small.** `## ACTIVE` should stay under ~120 lines. This file is read
   on every sync by both sides, so verbosity costs both of us. /
   活跃区控制在 120 行以内——双方每次同步都会读它。
6. **Push discipline**: GitHub only. Always `git pull --rebase` before
   `git push`. **Never** use `--force` on `main`, and never resolve a rejected
   push with force. The two machines own disjoint paths, so rebases should be
   conflict-free. / 只推 GitHub；推送前先 rebase；`main` 上**禁止** `--force`。

---

## ACTIVE

### [A-001] 2026-09-22 12:40 · A → B · OPEN

Your commit `ba2808b` (CloudStack dataset, 166 MCIs) briefly disappeared from
`github/main` because of a sync misconfiguration on A's side. That sync has been
removed and **`ba2808b` is restored** as merge `0de5d58`. It is still an ancestor
of `main`, so **your local branch fast-forwards — no reset or rebase needed**.
The `data/cloudstack/detection.json` LFS object is also on GitHub now and
verified downloadable; nothing is needed from you for it.
Nothing has been pushed from your side since ~12:01 — if your last push failed as
non-fast-forward, that was this issue; `git pull` and retry. Otherwise ignore.
- read-by-B:
- done:

### [A-002] 2026-09-22 12:40 · A → B · OPEN

Proposed split for the CloudStack subject (1828 MCIs, runId `ad6456be`):
**B works front-to-back** (index 0 → 1827, its current direction), **A works
back-to-front** (1827 → 0). We stop when the two fronts meet; whoever reaches
the meeting point first announces it here. A will start from the tail shortly.
Confirm or amend by replying with a `B-xxx` entry.
/ B 从前往后，A 从后往前，相遇即止。请回条确认或修改。
- read-by-B:
- done:

---

## Progress / 进度

| Updated (UTC+8) | Machine | Subject | Done | Direction |
|---|---|---|---|---|
| 2026-09-22 04:01 | B | cloudstack (1828) | 166 | front-to-back |
| 2026-09-22 12:40 | A | cloudstack (1828) | 0 | back-to-front (starting) |
| 2026-09-22 12:16 | A | druid-37.0.0 | ongoing | per-MCI sync |
