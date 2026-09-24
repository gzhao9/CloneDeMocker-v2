# Validation Pipeline Optimization Log

Running record of fixes/changes made while validating v2 against Dubbo, kept
separate from `AI_PROJECT_BRIEF.md` (which is the general handoff brief) so
the iteration history for this specific validation pass is easy to scan.
Only real correctness bugs and methodology issues go here — not routine
edits.

## 2026-09-14

- **Payload dedup, reject-unchanged, PIT mutation parsing, per-test identity,
  lightweight goal-check** (`app/harness.py`, `app/refactoring_agent.py`):
  see `AI_PROJECT_BRIEF.md` "Credibility fixes landed" section for detail.
- **`-Dmaven.repo.local` threading** (`app/harness.py`,
  `validation/scoped_harness.py`, `validation/run_pilot.py`): explicit
  `--maven-repo-local` CLI flag/config so parallel project validation doesn't
  share the default `~/.m2/repository`.
- **Bug: scoped test runs silently skipped the target module and still
  reported SUCCESS** (`validation/scoped_harness.py`,
  `validation/run_pilot.py`). Found on the first real Dubbo 3.3.6 batch:
  `-pl <module> -am` pulls dependency modules into the reactor, Maven applies
  the same `-Dtest=<class>` filter to all of them, and a dependency module
  with no matching test (e.g. `dubbo-common`) aborted the whole reactor
  before the actual target module ever ran — `-DfailIfNoTests=false` does not
  cover this (it only suppresses "no tests at all", not "no tests matching
  this specific filter"; the correct flag is
  `-Dsurefire.failIfNoSpecifiedTests=false`, now added). Compounding bug:
  `classify_transition()` only compared `testResults` dicts for equality
  without checking `testStatus`, so two runs that both failed to run any
  tests (`testResults == {}` on both sides) were read as "behaviorally
  equivalent" and classified `SUCCESS`. Both MCIs in the first real batch
  were false positives because of this. Fixed by requiring
  `testStatus == PASSED` on both sides before comparing `testResults`, and
  added a regression test (`tests/test_run_pilot.py`) pinned to this exact
  scenario. `app/refactoring_agent.py`'s product-path equivalence check
  already required `test_status == PASSED` on both sides and was not
  affected.

## 2026-09-15

- **Bug: PIT never actually ran against the real target module across the entire
  109-MCI batch, and every "SUCCESS" silently skipped functional-integrity
  verification** (`validation/scoped_harness.py`, `validation/run_pilot.py`,
  `app/refactoring_agent.py`). Same root cause as the 2026-09-14 surefire bug, one
  layer further: `-pl <module> -am` pulls dependency modules into the PIT
  reactor too, PIT's own `mutationCoverage` goal defaults to
  `failWhenNoMutations=true`, and the `-DtargetClasses=` filter matches nothing
  in the first unrelated dependency module (e.g. `dubbo-common`) — PIT treats
  that as a hard `BUILD FAILURE` and aborts the whole reactor before ever
  reaching the actual target module. Discovered post-hoc: **all 95 SUCCESS
  results in the first full 109-MCI run had `pitStatus=FAILED` and
  `mutationTotal=0`** — not one of them had a genuine mutation-testing run
  behind it. `classify_transition()` (and the product-path `equivalent` check
  in `app/refactoring_agent.py`) never checked `pitStatus` directly, only the
  mutant-identity-map-derived `mutation_regressed()`, which compares two empty
  dicts and vacuously reports "no regression" — so this tier of the paper's
  RQ2.1 three-tier criteria (preservation of mutation scores) was never
  actually enforced anywhere. Fixed by adding `-DfailWhenNoMutations=false` to
  the scoped PIT command, and by requiring `pitStatus` to be `PASSED` (or
  `NOT_RUN`, when PIT wasn't requested at all) on both sides before trusting
  `mutation_regressed()`'s verdict, in both `run_pilot.py` and
  `refactoring_agent.py`. Two regression tests added
  (`tests/test_run_pilot.py`) pinned to this exact scenario. **The full
  109-MCI report generated before this fix
  (`pilot-eaffe40a-20260915-032515.json`) needs to be re-run** — its MCI-level
  classifications for compile/test/goal-check are still meaningful, but none
  of them carry real mutation-testing evidence.

- **Bug: PIT genuinely never ran against a JUnit 5 project at all — `-DfailWhenNoMutations=false`
  alone was not enough** (`app/harness.py`, `validation/run_pilot.py`,
  `app/refactoring_agent.py`). Two layers deep: (1) PIT invoked directly from the command
  line (not as a build step the project's own pom configures) has no way to know it needs
  `pitest-junit5-plugin` to recognize JUnit 5 tests — Dubbo's own pom never configures PIT
  at all, so nothing on PIT's plugin classpath supported JUnit 5, and the pre-scan found 0
  mutations everywhere ("please check you have correctly installed the pitest plugin for
  your project's test library"). (2) Adding `pitest-junit5-plugin` alone crashed the PIT
  minion subprocess instead (`OutputDirectoryProvider not available ... unaligned versions
  of the junit-platform-engine and junit-platform-launcher`, `UNKNOWN_ERROR`) because
  `pitest-junit5-plugin`'s own transitive `junit-platform-launcher` (~1.9.x/1.10.x) was
  older than Dubbo's actual `junit-platform-engine` (1.13.1, confirmed via `-N
  dependency:tree`). Fixed with a new `app.harness.ensure_pit_junit5_support(root,
  maven_repo_local)`: runs a fast (~1.5s) non-recursive `dependency:tree` to detect the
  project's real `junit-platform-engine` version, then patches the **isolated workspace
  copy's** root pom.xml (never the analyzed project's own source) via `ElementTree` to add
  a `pitest-maven` plugin declaration with both `pitest-junit5-plugin` and
  `junit-platform-launcher` pinned to that detected version. No-ops if the project already
  configures `pitest-maven` itself, or isn't on JUnit 5 at all. Verified end-to-end by hand
  against a real Dubbo module before wiring it in: went from "0 mutations found" all the
  way to `BUILD SUCCESS` with 713 real mutations generated, 10 killed. Called once per
  workspace copy (not per MCI) right after `_copy_project`, in both
  `RefactoringAgent.run()` and `run_pilot.py`'s `main()`. Four new tests in
  `tests/test_harness.py` cover: injection with a mocked detected version, no-op when
  `pitest-maven` is already configured, no-op when the project isn't on JUnit 5, and
  idempotency across repeated calls (the real `mvn -N dependency:tree` call is mocked out
  in all of them — not exercised by the unit tests, only by the manual end-to-end check
  above). **Combined with the earlier `pitStatus` check, this means the 109-MCI batch
  needs a third run before its PIT/mutation-testing evidence can be trusted** — the first
  run never ran PIT for real at all (silently, via the vacuous-equality bug), and the
  second (replay) run correctly *detected* that PIT wasn't running but couldn't fix why
  yet.

- **Bug (found immediately after the above): the `ensure_pit_junit5_support` pom.xml patch
  broke the baseline sanity check via Spotless.** `ElementTree.write()` re-serializes the
  *whole* pom.xml, not just the inserted fragment, so even though the result is
  semantically identical XML it no longer matches Dubbo's own committed formatting
  byte-for-byte — and Dubbo binds a Spotless format check to an early lifecycle phase
  (`process-sources`, goal chosen via its own `spotless.action` property), which now
  failed on the reformatted root pom before `test-compile` could even run, aborting the
  *unmodified* baseline. Rather than fighting `ElementTree` for byte-exact formatting
  preservation, reframed the fix around the paper's own definition: "Syntactic Validity"
  means successful **compilation**, not adherence to an opt-in style linter — code that
  fails Spotless is still fully legal, compilable Java. Added
  `ProjectHarness._style_check_skip_args()` (`-Dspotless.check.skip=true
  -Dspotless.apply.skip=true`, the plugin's own standard toggles) to every Maven command
  (compile/test/PIT) in both `app/harness.py` and `validation/scoped_harness.py` — this
  only affects the disposable isolated copy, not a statement that style doesn't matter in
  general. Verified by hand: `-pl dubbo-remoting/dubbo-remoting-netty4 -am` test-compile
  went from `BUILD FAILURE` (Spotless violation on the reformatted pom) to `BUILD SUCCESS`
  with these flags. Two new tests confirm all three commands in both harnesses carry both
  flags. Net effect on `FAILED_SYNTACTIC_VALIDITY` going forward: a candidate that used to
  fail purely on formatting (not a real compile error) will now correctly be judged on
  compilability alone — this is a definitional correction, not merely a workaround.

## Known caveat for RQ3 timing (not yet fixed, noted for analysis time)

Some of the wall-clock numbers already recorded during this session (e.g.
the ad-hoc detection-only scan timings) were taken while other CPU/network-
heavy work was running concurrently (Dubbo's own cold compile, other
background tasks) and are not clean measurements. When compiling RQ3
numbers from the full run: exclude any timing sample known to have overlapped
with a concurrent build, and keep PIT wall-clock time as its own bucket
rather than folding it into "refactoring time" — the harness does not yet
record per-phase (compile/test/PIT) timestamps, so this currently has to be
handled by hand when writing up results, not automatically. Worth adding
proper per-phase timing to `HarnessEvidence` later if it turns out to matter
for the paper, but not before we have one full data pass.
