# CloneDeMocker v2

<div align="right"><b>English</b> | <a href="README_zh.md">简体中文</a></div>

CloneDeMocker is a local tool for detecting and refactoring duplicated mock setup in Java unit tests. This README starts with reproducible setup and day-to-day usage; architecture and research details are linked near the end.

## First-time setup

### 1. Install the base tools

| Tool | Requirement | Purpose |
|---|---|---|
| Git | Current stable release | Get the source |
| uv | Current stable release | Install the pinned Python and dependencies |
| JDK | 17 | Run the detector and validate Java projects |
| Maven | 3.9+ | Build Maven projects; a target project's Maven Wrapper may be used instead |
| Gradle | Version required by the target | Needed only for Gradle projects; prefer the target project's Wrapper |

The project standardizes on Python 3.11. Do not create a virtual environment manually or run `studio` with the system Python. `uv` uses `.python-version`, `pyproject.toml`, and `uv.lock` to create `.venv` and install the locked environment.

Install uv on Windows:

```powershell
winget install --id=astral-sh.uv -e
```

For macOS/Linux, follow the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### 2. Install project dependencies automatically

On Windows, use the `.cmd` wrapper so common PowerShell `ExecutionPolicy` settings cannot block startup:

```powershell
.\setup.cmd
```

On macOS/Linux:

```bash
bash setup.sh
```

The setup script uses repository-local uv cache and Python directories, obtains Python 3.11, runs `uv sync --locked`, verifies the core Python imports, and checks Java and Maven.

## Start the Web UI

Windows:

```powershell
.\start-ui.cmd
```

macOS/Linux:

```bash
./start-ui.sh
```

If an archive or shared filesystem removed executable permissions, run `bash start-ui.sh` instead. Open <http://127.0.0.1:8765> after startup.

The launcher performs a fast lock and environment check on every run. An already-synchronized environment is not downloaded again.

## Basic workflow

1. Open the Web UI and select a Java project root.
2. Run the environment check for the JDK, build tool, and path.
3. Scan for Mock Clone Instances (MCIs).
4. Select a candidate and generate a refactoring proposal.
5. Review its unified or side-by-side diff.
6. After compilation, tests, and optional PIT validation pass, accept or discard it.

For a first run, enable Debug/Mock mode in the UI; it does not call an external model. For a real model, create a Git-ignored `.env` in the repository root:

```dotenv
OPENAI_API_KEY=your-key
```

The launchers read `.env` from this repository or its parent without replacing variables already present in the process environment.

## Dependency and version policy

- `pyproject.toml` is the source of truth for direct dependencies and the Python range.
- `uv.lock` pins the complete transitive environment and is committed to Git.
- `.python-version` selects Python 3.11 consistently across machines.
- `studio/requirements.txt` is a compatibility list for tools that cannot consume `pyproject.toml`; it is not the recommended install path.

The Web UI does not need plotting dependencies. To run scripts under `validation/report_builders/`, install the locked `reports` extra:

```powershell
.\setup.cmd -IncludeReports
```

On macOS/Linux, use `bash setup.sh --reports`.

When adding or upgrading a dependency:

```powershell
uv add <package>
uv lock
uv sync --locked
```

Commit both `pyproject.toml` and `uv.lock`. Do not install a required package only into a personal environment with `pip`.

## Troubleshooting

### `uv.lock`, Python version, or `StrEnum` errors

Do not start with the system Python. Run `setup.cmd` and then `start-ui.cmd`. If an old Python created `.venv`, remove that local directory and rerun setup; `.venv` is not committed.

### PowerShell says scripts are disabled or unsigned

Run `start-ui.cmd`. It applies `ExecutionPolicy Bypass` only to that launcher process and does not change the machine-wide policy.

### `start-ui.sh: Permission denied`

Run `bash start-ui.sh`. A normal Git clone preserves the executable bit; ZIP and shared-drive transfers may require `chmod +x setup.sh start-ui.sh`.

### Permission denied in the uv cache

Use the supplied setup and launch scripts. They keep both the uv cache and managed Python under the repository-local `.uv-cache` and `.uv-python` directories.

### The UI opens, but Java scanning or validation fails

Confirm JDK 17 with `java -version`, then run `mvn -version` to see which Java Maven actually uses. For Gradle projects, keep and use the target's `gradlew` / `gradlew.bat` where possible.

## Headless experiments

Example batch validation:

```powershell
uv run --locked --python 3.11 python -m validation.run_pilot `
  --project-root "D:\Java_projects\Apache\dubbo-3.3.6" `
  --limit 2 `
  --use-mock
```

See [validation/README.md](validation/README.md) for the complete experiment protocol, PIT options, workspace reset, and reporting commands.

## Project overview

CloneDeMocker has three stages: a Java static detector mines repeated Mockito stubbing patterns; a Python agent produces constrained minimal patches; and a validation harness runs compilation, regression tests, and optional PIT mutation tests in an isolated workspace. The Web UI supports manual review, while `validation/` runs batch experiments.

| Path | Purpose |
|---|---|
| `studio/` | Python backend, Web UI, refactoring agent, and validation harness |
| `DETECTION/` | Java/Maven static detector |
| `validation/` | Batch experiments, analysis, and report tooling |
| `tests/` | Python regression tests |
| `data/` | Canonical, Git-trackable experiment data |

See [AI_PROJECT_BRIEF.md](AI_PROJECT_BRIEF.md) for the fuller architecture and terminology map.

## Validation and development

```powershell
uv run --locked --python 3.11 python -m unittest discover -s tests -v
```

Licensed under Apache License 2.0.
