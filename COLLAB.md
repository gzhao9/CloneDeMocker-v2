# COLLAB — cross-machine coordination board / 跨机协作留言板

Two machines run CloneDeMocker against different subjects and push to the same
GitHub repository. This file is the coordination board between them: append
status, hand-offs and questions here rather than relying on commit messages.

两台机器分别跑不同的被测项目，推同一个 GitHub 仓库。这个文件是双方的留言板：
状态、交接、疑问写在这里，不要只靠 commit message 传递。

- **Machine A / A 机** — `daynell`, subject: `data/druid-37.0.0/`
- **Machine B / B 机** — `Caralll`, subject: `data/cloudstack/` (master-branch CloudStack, runId `ad6456be`)

---

## 2026-09-22 12:35 (UTC+8) — Machine A

**Your commit `ba2808b` is back on `main`, and the sync problem that dropped it
has been fixed. You can pull and resume pushing. /
你的提交 `ba2808b` 已恢复到 `main`，导致它丢失的同步问题已修复，可以 pull 之后继续推送。**

It was restored as merge commit `659d655` — merged, not cherry-picked, so
`ba2808b` is still an ancestor of `main` and **your local branch fast-forwards;
no reset or rebase needed**. /
用 merge 恢复（非 cherry-pick），`ba2808b` 仍是 `main` 的祖先，**本地分支直接快进即可，
不需要 reset 或 rebase**。

### Action needed from Machine B / 需要 B 机处理

**The LFS blob for `data/cloudstack/detection.json` (184 MB) is missing on
GitHub — it returns HTTP 404.** The commit and its LFS pointer are on `main`,
but the object itself never finished uploading, so `git lfs pull` or a fresh
clone cannot materialise the file. Most likely the commit push succeeded while
`git lfs push` did not.

Please re-push the object from the machine that still has it:

```
git lfs push github --all
```

Machine A cannot supply this file — it has never been downloadable from here,
and without it Machine A cannot start on the CloudStack subject.

`data/cloudstack/detection.json` 的 LFS 大文件在 GitHub 上是 404，提交和指针都在，
但文件本体没上传成功。请从还有该文件的机器补推；A 机这边拿不到，没有它就无法开工。

### Proposed working agreement / 提议的协作约定

Proposed by Machine A — **please confirm or amend by appending to this file.**
A 机提议，**请在本文件追加确认或修改。**

1. **Push target**: GitHub only, and never `--force` on `main`. Recommend
   enabling GitHub branch protection on `main` with force-push disabled. /
   只推 GitHub，`main` 上不允许 `--force`；建议开启分支保护禁用强推。
2. **Concurrent pushes**: always `git pull --rebase` before `git push`; never
   resolve a rejected push with `--force`. The two machines touch disjoint paths
   (`data/druid-37.0.0/**` vs `data/cloudstack/**`), so rebases should be
   conflict-free. / 推送前先 rebase，被拒绝时**不要**用 `--force` 解决。
3. **MCI work split** — for the shared CloudStack subject (1828 MCIs, runId
   `ad6456be`): **Machine B works front-to-back** (index 0 → 1827, its current
   direction), **Machine A works back-to-front** (1827 → 0). Each side records
   its high-water mark below so we can see where the two fronts meet. /
   **B 机从前往后跑，A 机从后往前跑**，各自在下方登记进度，便于看到两端在哪里汇合。

### Progress log / 进度登记

| Time (UTC+8) | Machine | Subject | Done | Note |
|---|---|---|---|---|
| 2026-09-22 04:01 | B | cloudstack (1828) | 166 front-to-back | 152 SUCCESS, 11 ENV_NOT_READY, 2 FAILED_BEHAV, 1 FAILED_SYNTACTIC |
| 2026-09-22 12:35 | A | cloudstack (1828) | 0 back-to-front | blocked: needs `detection.json` LFS object before it can start |

### Open question for Machine B / 给 B 机的问题

Is your agent still running? Nothing has been pushed from your side since
`ba2808b` at ~12:01. If your last `git push` was rejected as non-fast-forward,
that was the problem above — it is fixed now, so `git pull` and retry. If you
were simply mid-batch, ignore this. /
你那边的 agent 还在跑吗？12:01 之后没有新推送。如果最后一次 push 报了
non-fast-forward 被拒，就是上面这个问题，现已修复，`git pull` 后重试即可。
