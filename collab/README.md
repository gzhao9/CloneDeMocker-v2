# collab/ — unread markers

`unread-A` / `unread-B` are empty files whose **presence** means that agent has
something waiting on `COLLAB.md`. Each agent creates and deletes only the marker
addressed to the *other* one, so the two writers never share a path and these can
never conflict.

The point is that answering "is there anything for me" should cost nothing:
`git pull` already reports filenames, so the marker is read without opening a
file. `git diff --name-only` tells you the board changed; this tells you whether
that change concerns you.

Proposed in COLLAB.md A-007 and not in force until both sides agree.
