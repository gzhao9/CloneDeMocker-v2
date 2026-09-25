# collab/ — how the three agents talk

**Messages are files.** There is no board. `COLLAB.md` and `COLLAB_ARCHIVE.md` were
retired at the 2026-09-23 cutover and deleted from the tree; git history still has every
word of them if anyone ever needs it (`git show 454cfeaa:COLLAB_ARCHIVE.md`).

```
collab/inbox/<recipient>/unread/<id>.md    written by the sender, once
collab/inbox/<recipient>/read/<id>.md      moved there by the recipient
collab/status/<agent>.md                   where that agent's runner is
collab/status/<agent>-session.md           what its model session is doing, "as of"
```

## Rules

1. **Read your own `unread/`. When you have acted on an entry, move it to `read/`.**
   The file's location *is* the read state — no cursor, no stamp, nothing to reconstruct. /
   读自己的 `unread/`，处理完 `mv` 进 `read/`；**文件位置就是已读状态**。
2. **The sender writes the file once and never touches it again.** Moving it is the
   recipient's act. One writer per path is what makes conflicts impossible. /
   发送方只写一次、此后永不再动。
3. ⚠️ **Only the owner of a path may write or delete it, and "I cannot see it" is not
   evidence that it was deleted** — it usually means a peer pushed something you have not
   pulled. A publish overlays your local copy onto the remote tree, so listing a path you
   do not own silently reverts the owner's work. This cost 58 files once and 27 again. /
   **只有路径的拥有者能写或删它**；"我看不到"不构成删除的证据。
4. **Ask with `fetch`, act with `pull`.** A fetch plus a listing of your `unread/` answers
   "is there anything for me" without opening a file or spending a token. / 问用 fetch，
   做才 pull。
5. **Tag every entry `REQ` or `NOTE`; body ≤ 10 lines; the asker closes.** Filing *is* the
   acknowledgement — never write an entry that only says thanks or agreed. /
   每条打 `REQ`/`NOTE`；正文 ≤10 行；由提问方关闭；归档就是回执。
6. **`cc` is not automatic.** Copy a third agent only when it must *act*. /
   **抄送不是默认**。
7. **Push discipline**: GitHub only, `pull --rebase` before `push`, **never `--force` on
   `main`**. Use `scripts/post.py --push`: it builds the commit on the remote tip through a
   temporary index and never touches the working tree, so a runner's uncommitted files
   cannot block it and it cannot disturb the runner. / 只推 GitHub；禁止 `--force`。
8. **A filing by a 24/7 runner means received, not understood.** A `REQ` may wait hours
   for the other side's model session. Post it and carry on; never block. /
   脚本归档只代表收到，不要阻塞等待。

## Channels and cost (2026-09-25, set by A's owner)

9. **Default is the board, sent with one command:** `AGENT_ID=<you> python scripts/post.py entry.md --push`
   (Windows PowerShell: `$env:AGENT_ID="E"; python scripts/post.py entry.md --push`).
   It delivers to every recipient in the header (A–E), pushes through a private index, and
   realigns your local `main`. Set `AGENT_ID` to your own letter, or it acts as A.
10. **Watch your inbox with a background script, not with your model session.** A loop that
    runs `git fetch -q github main && git ls-tree --name-only github/main collab/inbox/<you>/unread/`
    every 5 min and prints only when the list changes costs no tokens until mail arrives.
11. **Before reading mail or restarting a lane, run `python scripts/safe_pull.py`.** It is a pull
    that skips the live results files (see A-070), so your disk shows the current board and code.
12. **Direct session messages (`SendMessage`) only when urgent or a peer seems cut off.** They are
    real-time but not archived. Anything decided that way gets one line on the board afterwards.
    Never message B's session directly; B is reached only through the board.
13. **Publish anything else with one command too:** `python scripts/safe_push.py -m "why" <paths>`,
    and file your letters with `python scripts/safe_push.py --file-read <IDs>` (AGENT_ID = you).
    Same private-index path as the lanes; it refuses deletions and realigns local `main`. Do not
    hand-type plumbing sequences. They cost tokens on every call, and they caused the losses.
