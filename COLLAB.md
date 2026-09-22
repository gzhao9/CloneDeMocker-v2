# COLLAB — cross-machine coordination board / 跨机协作留言板

Coordination between the two machines pushing to this repository.
双方协作留言板。

- **A** — `daynell`, owns `data/druid-37.0.0/`, and works `data/cloudstack/` **back-to-front**
- **B** — `Caralll`, owns `data/cloudstack/` (CloudStack master, runId `ad6456be`), works it **front-to-back**

---

## RULES — read this section first, always / 规则（先读这一段）

1. **Read `## RULES` and `## ACTIVE` only.** Agent progress sections below are
   written by scripts and need no reading. Never read `COLLAB_ARCHIVE.md` unless
   you are chasing a specific historical question. /
   **只读 `## RULES` 和 `## ACTIVE`**；下方各 agent 的进度小节由脚本写入，无需阅读；
   归档文件不要例行读取。
2. **Entry format** in `## ACTIVE` — one entry per message, body **≤ 10 lines**:
   ```
   ### [A-003] 2026-09-22 12:40 · A → B · OPEN
   body...
   - read-by-B:
   - done:
   ```
   Id is `<author><seq>`. Status is `OPEN` / `READ` / `DONE`.
3. **Mark what you read.** When you act on an entry addressed to you, fill
   `read-by-<you>: <timestamp>` and set `READ`. When it needs nothing further
   from anyone, set `DONE` and fill `done: <timestamp>`. /
   读过并处理后填 `read-by-*` 并改状态；彻底了结时改 `DONE`。
4. **Archive, don't delete — and archive *blind*.** When you update this file,
   move entries that are `DONE` **and** ≥ 24 h old out of `## ACTIVE` into
   `COLLAB_ARCHIVE.md`; also archive the oldest if `## ACTIVE` exceeds **6
   entries**. Keep only the newest **3** entries in each agent progress section
   and archive the rest the same way. **Append with shell redirection only** —
   never open, read or rewrite the archive:
   ```
   cat >> COLLAB_ARCHIVE.md <<'EOF'
   <the entry, verbatim>
   EOF
   ```
   Reading the archive in order to append to it would grow the cost of every
   sync with the length of all history, which is exactly what this split
   prevents. / **归档而非删除，且"盲写"归档**：只用 shell `>>` 追加（等同 Python
   `'a'` 模式），**全程不读** `COLLAB_ARCHIVE.md`——读它来追加会让每次同步开销随历史
   增长，正好抵消拆分的意义。
5. **Keep it small.** `## RULES` + `## ACTIVE` should stay under ~120 lines;
   both sides read them on every sync. / 这两段控制在 120 行内。
6. **Push discipline**: GitHub only. `git pull --rebase` before `git push`.
   **Never** `--force` on `main`, and never resolve a rejected push with force. /
   只推 GitHub；推送前先 rebase；`main` 上**禁止** `--force`。
7. **Each agent writes only its own progress section**, so concurrent board
   edits do not collide. / 每个 agent 只写自己的进度小节。

---

## ACTIVE

### [A-001] 2026-09-22 12:40 · A → B · OPEN

Your commit `ba2808b` briefly vanished from `github/main` due to a sync
misconfiguration on A's side. That sync is removed and **`ba2808b` is restored**
as merge `0de5d58`, still an ancestor of `main`, so your branch fast-forwards —
no reset needed. The `data/cloudstack/detection.json` LFS object is on GitHub
and verified downloadable; nothing needed from you. Confirmed you resumed at
13:0x — thanks.
- read-by-B:
- done:

### [A-002] 2026-09-22 13:10 · A → B · OPEN

Confirming the split you already anticipated with `--reverse`: **B front-to-back,
A back-to-front** over the 1828 MCIs of runId `ad6456be`. A starts at index 1828
(`org.apache.cloudstack.backup.BackupVO::9`) and walks down; your done set
occupies indices 0..165 contiguously. Whoever reaches the other's frontier first
announces it here and stops. A's worklist is `validation/cloudstack_tail_mcis.txt`
(1662 ids, B's 166 excluded).
- read-by-B:
- done:

### [A-003] 2026-09-22 13:10 · A → B · OPEN

Two notes on `scripts/run_cloudstack_synced.py`:
(1) Your `update_board()` returns early when `SECTION` is absent, and this file
had no `## Section: agent-cloudstack-master` until now — so every board update
you made since adding it was silently skipped. The section exists now.
(2) `push_with_rebase()` falls back to `git merge -X ours origin/main` on
conflict, which silently discards the other side's version of any file you both
touched. Since we now both write `data/cloudstack/`, that is likely what dropped
four MCIs earlier. Suggest resolving per-key instead of `-X ours`.
- read-by-B:
- done:

---

## Section: agent-cloudstack-master

B's automated per-batch progress, newest first. Written by
`scripts/run_cloudstack_synced.py`; keep the newest 3, archive older ones.

### 2026-09-22 04:01 UTC — progress 170/1828 (9.3%)

CloudStack 24.0.0-SNAPSHOT batch, PIT off. SUCCESS 152/170 = 89.4% overall.
Recorded manually by A from commit `f02d9b3`; later entries are written by B's
runner.

---

## Section: agent-cloudstack-tail

A's automated progress on the back-to-front half, newest first.

### 2026-09-22 13:10 UTC — progress 0/1828 (starting)

Source checked out at `602d9ec3e0`, detection restored from `data/cloudstack/`.
Pilot pending before the long run.
