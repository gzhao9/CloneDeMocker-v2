# D status — only D writes this file.

updated:   2026-09-25 13:16 UTC
host:      Gengwu-PC (windows, "5090D"; Ultra 7 265KF 20 cores, 32 GB)
presence:  see collab/status/D-online.md / D-offline.md (exactly one exists)
tools:     JDK 17.0.10 + Temurin 25.0.4 (spring-security 7.1.1 toolchain), Python 3.12 .venv (uv sync),
           git-lfs 3.5.1; hooksPath=githooks
running:   CloudStack pairs (A-078): baseline_v1/autoscale.py --initial 7 --max 12, --claim lanes L1..,
           --round1-failures skip --start 0.60, commit 602d9ec3 (5 slice lanes measured 51-54 MCIs/h)
paused:    PIT back-fill spring-security-7.1.1 (A-076), all published: Terra 42/305, Luna 84/264, V1 62/117;
           resumes when CloudStack is done
inbox:     collab/inbox/D/unread/
