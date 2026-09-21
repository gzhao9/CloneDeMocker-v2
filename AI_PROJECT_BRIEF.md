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
- `studio/`: Python API/UI backend and the operational pipeline (formerly
  `app/`, renamed 2026-09-18 for clarity -- it's the interactive product, not
  a generic "app"). `detection_service.py` invokes detection;
  `refactoring_agent.py` chooses an MCI and drives the staged pipeline;
  `harness.py` compiles and tests the target project; `model_provider.py` has
  real and mock providers. Three modules support the staged pipeline:
  `prompts/` (the five stage prompts plus the shared edit protocol),
  `payloads.py` (whitelist payload construction and the pre-send verbatim
  assertion) and `source_map.py` (maps the detector's normalized text back to
  real source offsets); `mechanical.py` performs the model-free branch.
- `studio/web/`: browser UI. Start with `start-ui.ps1` on Windows or
  `start-ui.sh` on Unix-like systems.
- `validation/`: separate pilot/real-project validation tooling, organized by
  pipeline stage (collection / analysis / `report_builders/` / `notes/`), not
  by paper RQ number -- RQ boundaries may shift, directory structure shouldn't
  have to follow. Do not merge it into `studio/`. `run_pilot.py` orchestrates,
  `scoped_harness.py` limits Maven/PIT to the relevant module, and
  `reset_workspace.py` restores a shared validation copy. Every script here
  must run standalone from the command line -- nothing in this project's
  workflow may depend on an AI coding assistant session being present.
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
  project (for example `D:\\Java_projects\\Apache\\.clonedemocker-workspaces`),
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

- `studio/refactoring_agent.py::_model_input()` no longer sends the same
  test-method source three times; `_strip_duplicate_source()` removes the
  sequence-level `testMethodRawCode` and the nested
  `rawStatementInfo[*].locationContext.methodRawCode` copies before building
  the model payload. The full detector JSON is untouched for UI/analysis.
- `_validate_replacements()` rejects a "replacement" whose content is
  byte-identical to the original file, so a model echoing the source back no
  longer counts as a successful edit.
- `studio/harness.py` parses PIT's `mutations.xml` (now requested via
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

## Staged refactoring pipeline (2026-09-20)

`RefactoringAgent` no longer sends one model call per MCI. It now runs the
paper's two steps as actual execution structure: one encapsulation call per
MCI, then one integration call per sequence, routed across the five prompts in
`studio/prompts/`. The "no shared stubbing" branch runs in `mechanical.py`
without any model call (94/94 of Dubbo's cases, reproducibly). Measured on
Dubbo 3.3.6: 471 calls instead of 123, but total input grows only 19% and the
largest single payload halves, because each call sees one test method rather
than every source file of the MCI.

Two things this replaced, both worth not reintroducing:

- The detector's `testMethodRawCode` / `testMockLines` are a **normalized
  view**, not source text: class-level indentation stripped, CRLF instead of
  the files' LF, wrapped statements joined with the whitespace deleted, and a
  trailing comment sometimes moved in front of its statement. None of Dubbo's
  94 test methods can be found byte-for-byte in their own file. Anything that
  becomes an `oldString` must therefore come from `source_map.py`, never from
  the detector. The old single-call path avoided this only because
  `_strip_duplicate_source()` happened to drop the method text.
- Payload construction is a **whitelist** (`payloads.py`), not a blacklist.
  The old filter removed two known-harmful fields and let everything else
  through, so 631 of 1587 code strings (40%) in a payload were not verbatim.
  `verbatim_failures()` now asserts before every send that each code string
  occurs in the file it names, and refuses to send otherwise.

Java 17 and Maven 3.9.9 are on `PATH` here, and Dubbo 3.3.6, cloudstack,
druid and dubbo-3.2.0 are checked out under `D:\\Java_projects\\Apache\\`. The
staged pipeline has been verified against all 123 MCIs of the real detector
output for payload construction, location and the model-free branch, but has
not yet been run end-to-end against a live model or a full Maven build.

## Scoped verification and the shared workspace (2026-09-20)

Verification no longer builds the whole reactor for a change that touches one
or two test files. `ProjectHarness.validate()` takes a `BuildScope`, and
`RefactoringAgent` computes it from the selected MCI before the baseline runs,
so compile, test and PIT all carry `-pl <modules> -am`. The scope is recorded
on the evidence (`HarnessEvidence.scope`) and shown in the UI, because once
narrowed "tests passed" no longer means the whole project passed.

Three things that had to change together, and should not be undone singly:

- **The pre-flight baseline is gone.** It ran from its own `/api/baseline/*`
  endpoints before the user had selected an MCI, so it could not know which
  modules were involved and always compiled all 124 Dubbo modules. Scoping is
  only possible once the MCI is known, which is why the baseline now lives
  inside `run()` and nowhere else.
- **A batch shares one workspace** (`workspace_id`). `_copy_project` excludes
  `target/`, so a copy per MCI meant a cold compile per MCI — N MCIs, N cold
  compiles. Only the first now pays it.
- **Each MCI restores the files it touched**, in a `finally` around `_run()`.
  Without this the shared workspace would hand the next MCI the previous one's
  edits as its baseline, and the MCIs would stop being independent.

`-pl X -am` builds what X depends on, not what depends on X. That is a blind
spot when X publishes a test-jar, which three Dubbo modules consume
(`dubbo-rest-jaxrs`, `dubbo-rest-spring`, `dubbo-rpc-triple`), so
`_has_test_jar_consumer()` adds `-amd` for those cases.

PIT remains opt-in (`run_pit`, default off; the UI's `#run-pit` checkbox), and
`classify_transition()` treats `NOT_RUN` as neither pass nor fail, so skipping
it for a large batch leaves that tier honestly unverified rather than silently
green.

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

## Resumable runs and the canonical dataset (2026-09-20)

Two things make a long batch survivable, and they work together.

`studio/canonical_store.py` merges a run into `data/<project>/` **by mciId**, keeping every
MCI this run did not touch. A 99-MCI batch therefore need not finish in one sitting: stop at
the 60th, run the remaining 39 later, and the two splice into one dataset. The UI exposes this
as "Save report to data/" (`/api/refactoring/export`); `validation/export_canonical.py` shares
the same module so the CLI and the UI cannot drift on which entries are kept or replaced.
Until this existed, UI results lived only in the server process and a restart lost them.

`studio/verification_ledger.py` records verifications that passed, keyed on source fingerprint
plus scope plus PIT setting plus pipeline generation. The baseline is the big win: MCIs in one
module verify over byte-identical sources, so 40 MCIs in one module previously meant 40
identical baseline runs. Candidate entries additionally key on the patch fingerprint.

Three properties of the ledger are deliberate and should not be relaxed:

- **Only passing verifications are recorded.** A failure is usually environmental (an
  unresolved dependency, a busy port), and recording it would make one transient fault
  permanent.
- **Every reused tier is stamped** into the result as `verificationReused: {baseline, candidate}`
  with the timestamp it came from. When the paper says something passed verification, the
  report must be able to separate what this run measured from what it replayed — the
  environment may have moved and a recorded "compilation passed" cannot detect that.
- **`reuse_verification=False` forces a full re-verification**, so a clean measurement is always
  available.

Per-phase timings feed RQ3: `HarnessEvidence.durations` carries compile/test/pit seconds and
`RefactoringAgent.run` returns `timings` with generation, baseline, candidate and total. PIT is
its own entry rather than folded into refactoring time, since it often outlasts compile and
test combined.

## data/ layout: one directory per harness+model (2026-09-21)

```
data/<project>/
  detection.json, detection-meta.json      shared by every setup
  refactoring/<harness>+<model>/           e.g. CloneDeMocker+Terra-5.6
    setup.json                             label, harness, model -- the directory name is shorthand
    refactoring-results.{json,csv}, diffs/, cctr.{json,csv}
```

`studio/canonical_store.py` picks the directory (`setup_label`, `MODEL_DISPLAY_NAMES`), so a later
Codex+model or pricier-model run lands beside the current one instead of overwriting it. Debug-stub
results go to `<harness>+MockProvider`, never into a real model's directory; that is why the agent
result now carries `useMock`. CCTR is per setup because it is computed from that setup's diffs.

Detection is saved to `data/<project>/` automatically the first time a project is detected, and the
UI then offers to reuse it (confirmation dialog on "Scan") instead of rescanning.
`DetectionService.restore_from_data` rebuilds an ordinary run directory from it, so refactoring
needs no second code path. An existing saved detection is never overwritten by a fresh detect,
because stored results are keyed by its MCI numbers. `detection-meta.json` records scan/detect
seconds; Dubbo's were reconstructed from the cached run's file mtimes (`timingSource` says so).

## Detector language level and the first Gradle subject (2026-09-21)

Spring Security 7.1.1 (Gradle, `D:\Java_projects\Spring\`) is the first Gradle project run.
JavaParser's default Java 11 level silently skipped 230 of its 4418 files (instanceof patterns,
text blocks, records, switch expressions), 30 of them Mockito test classes. The detector now uses
JavaParser 3.28.2 at `JAVA_25`, retries a failed file at `JAVA_8` (only `_` as an identifier
separates them), passes the same configuration to the source-root `JavaParserTypeSolver`, and
prints `[INFO] Parse failures / 解析失败文件: N/total`. Dubbo's saved MCIs are reproduced exactly.

Detection never compiles the subject, Maven or Gradle. "Resolve dependencies" only asks the build
for the test classpath. The Maven variant was broken until now: every reactor module wrote the same
`-Dmdep.outputFile` and the last one won (13 jars for Dubbo), so enabling it changed nothing. It now
collects each module's `Dependencies classpath:` from the output with `-fae`. With it, Dubbo's
unresolved simple type names drop 115 -> 9 and 18 MCI ids change from simple to qualified names
(two MCIs gain one member each); `data/dubbo-3.3.6/` still holds the no-resolution result.

The harness's Gradle path now mirrors Maven (`studio/gradle_support.py`): Gradle reports its own
project dirs, `configuration: 'tests'` consumers (the `-amd` counterpart, closed transitively) and
toolchain availability; `:p:testClasses` / `:p:test --tests X --rerun` / an init-script-injected
gradle-pitest-plugin writing to `build/pit-reports`; English javac output via `JAVA_TOOL_OPTIONS`
(a `-D` on gradlew does not reach the compiler JVM). `--rerun` matters: Spring enables the build
cache, which would otherwise replay test reports. Preflight probes Gradle the same way and blocks
on a missing toolchain. JDK 25 is at `D:\soft\jdk-25.0.4.1+1`, registered in
`~/.gradle/gradle.properties`. Verified end to end on `core`/DaoAuthenticationProviderTests:
compile 286s (core's test output has 13 downstream consumers), test 6s, PIT 75s.

Two things the first Spring batch exposed. Tests need not live in `src/test`: saml2 keeps its
OpenSAML 5 tests in `src/opensaml5Test`, run only by an `opensaml5Test` task, so `:p:test --tests X`
left them unexecuted and the baseline gate failed (6 MCIs). Discovery now also reports each Test
task's source sets; the harness picks the task and `<sourceSet>Classes` from where the class file
lives, and collects `test-results/*/`. Separately, overlong generic MCI ids broke Windows MAX_PATH
on export; `safe_mci_filename` hashes names over 125 characters (Dubbo's names are unchanged).
On this host a proxy's fake-ip DNS resolves any name (198.18.x.x), so
`NimbusReactiveJwtDecoderTests.decodeWhenInvalidUrl` fails at baseline; 5 MCIs are affected and
correctly land in ENVIRONMENT_NOT_READY.
MAX_PATH struck a third time: inside a `batch-<32 hex>` workspace, Gradle report paths for Spring's
long test class names reach 263 characters. Gradle shortens the file name and `rglob` lists it, but
it cannot be opened without the `\?\` prefix, so tests that ran yielded no result and the MCI was
misfiled as ENVIRONMENT_NOT_READY (never as a false SUCCESS: the gate requires fresh results).
`harness._report_files` now prefixes every report path it reads, deletes or stats.
That misfiling then outlived its fix: the verification ledger recorded any run whose compile and test
*status* passed, so the empty-result baselines were replayed on the next batch. `VerificationLedger`
now records and serves only evidence with at least one non-skipped test result (checked on read too,
so the three bad records already on disk are ignored).

The report-path fix covered only one of several places that hit MAX_PATH inside a batch copy, which
sits ~43 characters deeper than the project. Our own reads/writes of workspace files, and Gradle's
test-task lookup (`test_tasks_for`), used plain paths: past 260 characters `is_file()` silently
returned False and `write_text` raised Errno 2, so two Spring Security MCIs (saml2 opensaml5Test,
oauth2-authorization-server) ended as tool ERRORs absent from the export. All of these now go
through `studio/long_paths.long_path`; `workspace` itself stays plain for Gradle commands, evidence
and ledger keys.
