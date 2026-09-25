# D status — only D writes this file.

updated:   2026-09-25 12:00 UTC
host:      Gengwu-PC (windows, "5090D"; Ultra 7 265KF 20 cores, 32 GB)
presence:  see collab/status/D-online.md / D-offline.md (exactly one exists)
tools:     JDK 17.0.10 + Temurin 25.0.4 (spring-security 7.1.1 toolchain), Python 3.12 .venv (uv sync),
           git-lfs 3.5.1; hooksPath=githooks
running:   CloudStack pairs (A-078): 5 lanes D1-D5, --round1-failures skip --start 0.60 --slice k/5, commit 602d9ec3
paused:    PIT back-fill spring-security-7.1.1 (A-076), all published: Terra 42/305, Luna 84/264, V1 62/117;
           resumes when CloudStack is done
inbox:     collab/inbox/D/unread/
