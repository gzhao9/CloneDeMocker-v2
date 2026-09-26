# D status — only D writes this file.

updated:   2026-09-26 08:30 UTC
host:      Gengwu-PC (windows, "5090D"; Ultra 7 265KF 20 cores, 32 GB)
presence:  see collab/status/D-online.md / D-offline.md (exactly one exists)
tools:     JDK 17.0.10 + Temurin 25.0.4 (spring-security 7.1.1 toolchain), Python 3.12 .venv (uv sync),
           git-lfs 3.5.1; hooksPath=githooks
running:   layered PIT (E-011), 20 threads in all, on b36ec842:
           spring-security-7.1.1 all 3 setups: slices 0/1 re-run (4 threads each), slice 2 with --kill-first config (6 threads)
           cloudstack Luna + V1+Luna --noredist skip: --slice 0/2 and 1/2 (3 threads each), since 08:26 UTC
done:      CloudStack pairs finished by the pool on 09-26; D's rows were all superseded by origin (A-084)
paused:    PIT back-fill spring-security-7.1.1 (A-076), published: Terra 42/305, Luna 84/264, V1 62/117;
           not resumed: A-084 replaces per-MCI PIT with a whole-project design (pending owner sign-off)
inbox:     collab/inbox/D/unread/
