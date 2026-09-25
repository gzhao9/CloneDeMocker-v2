# AGENTS.md — rules for any AI agent working in this repository

This file is for every agent, whatever tool runs it (Claude Code, Antigravity, Codex, ...).
It holds the operating rules that were learned the hard way. How agents talk to each other
is in [collab/README.md](collab/README.md); read that too.

## 1. One-time setup per clone

- **Remote: GitHub only** (`git@github.com:gzhao9/CloneDeMocker-v2.git`). The old local
  gitea is retired. Never add it back or push to it.
- **Enable the repository's git hooks:** `git config core.hooksPath githooks`.
  `githooks/pre-push` refuses any push that deletes a file, unless the same content reappears
  at another path in that push (a move, such as inbox `unread/` → `read/`). Additions and edits
  always pass. To delete something on purpose, run `ALLOW_DELETE=1 git push ...`.
- Use the project's `.venv` Python (`.venv/bin/python`, or `.venv\Scripts\python` on Windows).
  The system Python lacks the openai SDK.

## 2. Git: the working tree lags behind HEAD. Never commit what `git status` lists

The lanes' automatic syncs and the private-index publishers (`post.py`, `safe_push.py`,
`publish.sh`) move `HEAD` to the pushed commit but never rewrite the working tree. After a few
hours, `git status` or the IDE's Source Control view shows hundreds of "deleted" and "modified"
files. They are only files you have not pulled yet. They are not your changes.

- **Never `git add -A`, `git commit -a`, or click Commit or "Discard all changes" in the IDE.**
  Committing that state deletes other agents' published files; this lost 58 files once and
  27 again. Discarding it overwrites a running lane's unpublished rows.
- To bring the working tree up to date, run `python scripts/safe_pull.py` (A-070). C's
  lane also runs `logs/c-automation/sync_worktree.py` after every publish. Neither one
  overwrites a live results file.
- To publish your own files, use `python scripts/safe_push.py -m "why" <paths>`. Never pull
  with `--autostash`, and never push with `--force`.

- **`git fetch` updates only the remote-tracking ref.** A check that fetches and then reads the
  working tree, or `git status`, learns nothing about what arrived. Ask the ref itself:
  `git ls-tree -r --name-only github/main -- <path>`. A watcher that fetched and then looked at
  disk left three letters unseen on the remote for nine hours, one of them a liveness check.
- **A running lane keeps the code it started with.** `drive.py`'s supervisor respawns each
  worker from `__file__`, so a `.py` you update on disk takes effect at the **next respawn**, not
  when you save it. Workers that had run 14 hours were still executing the previous day's code
  while the disk held six newer files. Updating the file is not deploying it: either restart the
  worker or say plainly that the change is not live yet.

## 3. Running and stopping lanes

- On Linux (gwz-pc), CloudStack needs `CLONEDEMOCKER_MAVEN_ARGS=-Dnoredist` in the launch
  environment. Without it, the vmware/nsx/netris/tungsten/contrail modules drop out of the
  reactor. The rows then come out as false `FAILED_SYNTACTIC_VALIDITY`.
- **Model names:** round 1 is `gpt-5.6-terra`, and the V1/V2 pairs are `gpt-5.6-luna`. The
  API key has **no** access to `deepseek-chat` (404). A runner that still names it fails every
  MCI that needs the model.
- **Before a relaunch**, `git diff --stat origin/main -- '*.py'` shows code files on disk
  that are older than the remote (see section 2). Update them first.
- **Stopping:**
  - `scripts/run_cloudstack_salvage.py` stops cleanly on SIGINT or SIGTERM. It ends its
    Maven/surefire children, restores the workspace, and exits.
  - For other lanes, use SIGINT (Ctrl-C). A shell starts background jobs with SIGINT
    ignored, so check that the process actually exited.
  - To pause without losing anything, `kill -STOP -- -<pgid>` freezes the lane, and
    `kill -CONT -- -<pgid>` resumes it.
  - Never `kill -9` a lane.
- **After a hard kill or a reboot:** a leftover `.clonedemocker-restore.json` journal in the
  workspace is replayed before the next MCI. To be sure, diff the workspace against the
  pristine source checkout before relaunching.
- **Known environment trap:** some CloudStack tests call `sudo` (for example
  `sudo umount`). With nobody there to authenticate, they wait until the 1-hour test timeout
  and end as `ENVIRONMENT_NOT_READY`.

## 4. Secrets

`.env` holds API keys. Never commit it. Never print a key in a log, a letter, or a commit
message. Never put credentials in a remote URL.

## 5. Traps that corrupt data without failing

None of these raise. Each one was found only after it had already written something wrong.

- **An index-formatted glob rolls over.** `safe_name()` numbers files `%04d`, so the batch
  reaches `1000-*.json` and a glob of `0*.json` silently stops seeing new work. It cost 68 rows
  their `producedBy`/`platform` stamps, and two letters asserting to peers the opposite of the
  truth. Match `[0-9]*`, and when a count looks too low, suspect the pattern before the data.
- **Never decode a letter or a data file to move it.** Read the blob id and write that
  (`git rev-parse <ref>:<path>` → `update-index --cacheinfo`). A text round-trip through the
  Windows console codec (GBK) archived one letter as an empty file.
- **Regenerating a shared file drops fields you do not know about.** A publisher that rebuilt
  its own rows deleted a peer's `resampleOutcome` on every run, about once every two minutes.
  Carry unknown keys forward (`entry.setdefault(k, v)` from the stored row) instead of writing
  only what you produce.
- **Windows: `CREATE_NO_WINDOW` and `DETACHED_PROCESS` are mutually exclusive.** Passing both
  gives every child its own visible console window; `DETACHED_PROCESS` wins. Pass
  `CREATE_NO_WINDOW` alone.
- **Check which command produced which line.** Running `git ls-tree <remote>` and a local `ls`
  in one block and reading the joined output as one source is how a letter that had never been
  pushed was reported as delivered.
