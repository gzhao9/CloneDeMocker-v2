# CloneDeMocker environment and data checklist

## What every machine must download

1. **Git and Git LFS** — clone the repository, then run `git lfs pull`.
2. **uv** — installs the project's Python 3.11 and all locked Python packages. Do not install a separate Python for this project.
3. **JDK 17** — both `java` and `javac` must be available; set `JAVA_HOME`.
4. **A Java build tool** — Maven 3.9+ for Maven projects, or the target project's `mvnw`/`gradlew` wrapper.
5. **Network access on first setup** — Astral/GitHub for managed Python, PyPI for Python packages, and Maven Central or the target project's configured repositories for Java dependencies.
6. **An OpenAI key only for real-model runs** — Debug/Mock mode does not need one.

Run `setup.cmd` on Windows or `bash setup.sh` on macOS/Linux. Then run the checker:

```powershell
.\check-env.cmd
```

For a real target and PIT readiness:

```powershell
.\check-env.cmd --project-root "D:\Java_projects\Apache\cloudstack-4.23.0.0" `
  --data-project cloudstack-4.23.0.0 `
  --require-pit
```

Every failed check prints the unmet condition and its corrective command. Warnings describe optional checks that were not requested.

## What must be saved under data/

The UI now writes every completed MCI incrementally. The manual **Save report to data/** button remains available as an idempotent retry.

```text
data/<project>/
├── detection.json
├── detection-meta.json
└── refactoring/<setup>/
    ├── setup.json
    ├── refactoring-results.json
    ├── refactoring-results.csv
    ├── diffs/<mci>.diff
    └── pit/
        ├── <mci>.json
        ├── baselines/.../mutations.xml
        └── candidates/.../mutations.xml
```

The UI's PIT option stores the PIT summary in `refactoring-results.json`. To retain the full baseline/candidate evidence and original `mutations.xml`, run PIT replay against an already saved setup:

```powershell
uv run --locked --python 3.11 python validation/pit_replay.py pit cloudstack-4.23.0.0
```

Verify that raw PIT evidence really reached `data/`:

```powershell
.\check-env.cmd --project-root "D:\Java_projects\Apache\cloudstack-4.23.0.0" `
  --data-project cloudstack-4.23.0.0 `
  --require-pit `
  --verify-pit-output
```

`validation/results/` is temporary run output and is ignored by Git. `data/` is the canonical, reviewable dataset that should be committed and pushed.
