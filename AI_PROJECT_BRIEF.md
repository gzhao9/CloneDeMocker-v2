# CloneDeMocker-v2: AI quick brief

Read this file before opening code. It is a compact handoff for the current
research/tooling version.

## Purpose

CloneDeMocker detects duplicated Mockito setup logic (mock-clone instances,
MCIs) in Java test code, then asks an LLM to refactor a selected MCI and
validates that refactoring.  The project is being prepared for a new FSE
submission; `CloneDeMocker_FSE.pdf` is the paper and
`CloneDeMocker-ASE-Comments.pdf` is related ASE review material in the
transfer package.

## Layout and execution path

- `DETECTION/`: Maven/Java detector. Flow: mock-logic extraction -> frequent
  stub-set mining -> MCI formation. Its CLI/export JSON feeds the Python app.
- `app/`: Python API/UI backend and the operational pipeline.
  `detection_service.py` invokes detection; `refactoring_agent.py` chooses an
  MCI, prepares the model payload, applies the edit; `harness.py` compiles and
  tests the target project; `model_provider.py` has real and mock providers.
- `app/web/`: browser UI. Start with `start-ui.ps1` on Windows or
  `start-ui.sh` on Unix-like systems.
- `validation/`: separate pilot/real-project validation tooling. Do not merge
  it into `app/`. `run_pilot.py` orchestrates, `scoped_harness.py` limits
  Maven/PIT to the relevant module, and `reset_workspace.py` restores a shared
  validation copy.
- `tests/`: Python tests. Run: `uv run --with pytest python -m pytest tests/ -q`.

## Current state that must be preserved

- This checkout intentionally contains uncommitted additions/modifications and
  staged removals of legacy `DATA/` and `REFACTORING/` material. The transfer
  archive is a snapshot of the working tree, not a clean Git release. Do not
  restore or commit those removals without the project owner's decision.
- Recent fixes cover Windows long paths, keeping `\\\\?\\` paths out of
  subprocess `cwd`, LF source writes (to satisfy Spotless), and a one-hour
  harness timeout.
- For real Java projects, isolated workspaces must be placed beside the target
  project (for example `C:\\Java_projects\\Apache\\.clonedemocker-workspaces`),
  not beneath a tool path containing Chinese characters: Maven/JDK native path
  encoding otherwise can cause silent compilation failures on Windows.
- A real non-mock validation succeeded for Dubbo 3.3.6, MCI
  `org.apache.dubbo.remoting.ChannelHandler::1` in netty4. The refactoring
  extracted duplicated `Mockito.mock(ChannelHandler.class)` calls to a private
  helper; compilation, tests, and PIT passed.

## Credibility fixes landed (2026-09-14)

Following a Codex design review (see `项目接力.md` and the "必改项" list it
produced), these are done and covered by `tests/test_refactoring_agent.py`
and `tests/test_harness.py` (11 tests, all passing via
`uv run --with pytest python -m pytest tests/ -q`):

- `app/refactoring_agent.py::_model_input()` no longer sends the same
  test-method source three times; `_strip_duplicate_source()` removes the
  sequence-level `testMethodRawCode` and the nested
  `rawStatementInfo[*].locationContext.methodRawCode` copies before building
  the model payload. The full detector JSON is untouched for UI/analysis.
- `_validate_replacements()` rejects a "replacement" whose content is
  byte-identical to the original file, so a model echoing the source back no
  longer counts as a successful edit.
- `app/harness.py` parses PIT's `mutations.xml` (now requested via
  `-DoutputFormats=XML`) into per-status counts, a mutation score, and a
  mutant-identity -> status map; `mutation_regressed()` fails a candidate if
  any mutant killed in the baseline is not killed in the candidate (stronger
  than comparing the old aggregate `pitStatus`). Reports are filtered by
  mtime so a shared/reused workspace doesn't pick up a stale report from an
  earlier MCI.
- `_collect_test_identities()` replaces the old aggregate test-count
  comparison with a `"class#method" -> PASSED/FAILED/ERROR/SKIPPED` map, so
  two differently-broken test runs with the same totals are no longer read
  as equivalent.
- `_goal_check()` is a lightweight (no re-detection) check that the MCI's
  shared/duplicated mock statements actually occur fewer times in the
  patched files than before, instead of trusting the model's own summary.
- `validation/run_pilot.py`: every MCI now restores the shared workspace to
  the unmodified baseline after it finishes, success or failure, so results
  are independent of MCI order; it gained a repair loop (mirrors the product
  agent's, up to two attempts, only for compile/test-equivalence failures,
  not for a missed goal-check or mutant regression), a `--direct-llm-baseline`
  mode and a `--no-repair` flag for the planned RQ4 three-arm comparison, and
  richer per-MCI metadata (`firstPassClassification`, `repairRounds`,
  `promptHash`, per-attempt `responseId`/usage).

Deliberately **not** done yet: unifying the product (`RefactoringAgent.run`)
and validation (`run_pilot.py`) execution paths into one shared engine. They
still each implement their own (structurally similar) repair loop. This was
judged lower priority than the credibility fixes above given the two-week
FSE timeline; revisit after the Dubbo validation run if there's time.

This sandbox has no Java/Maven on `PATH`, so none of the above could be
exercised end-to-end against a real Maven project here — only unit-tested.
Run `validation/run_pilot.py` for real once Dubbo 3.3.6 is available.

## Configuration and safety

- Real API mode needs `OPENAI_API_KEY` in the process environment. A local
  `.env` may exist for `run_pilot.py`, but is deliberately excluded from this
  transfer package and `server.py` does not auto-load it.
- `--use-mock` / the UI debug checkbox is completely local and makes no API
  request.
- The tool needs Python/uv, a compatible JDK/Maven environment for detection,
  and network access or locally available Maven dependencies when validating a
  target Java project.

## Read next only if needed

Start with `README.md`; then read `validation/README.md` for validation work.
`项目接力.md` in the transfer package is the longer Chinese session record.
