import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.harness import (BuildScope, HarnessEvidence, HarnessStatus, ProjectHarness,
                             ensure_pit_junit5_support, mutation_regressed,
                             verification_failure_reason)

MINIMAL_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>demo</groupId>
  <artifactId>demo-parent</artifactId>
  <version>1.0</version>
  <packaging>pom</packaging>
</project>
"""

POM_WITH_PITEST_ALREADY_CONFIGURED = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>demo</groupId>
  <artifactId>demo-parent</artifactId>
  <version>1.0</version>
  <packaging>pom</packaging>
  <build>
    <plugins>
      <plugin>
        <groupId>org.pitest</groupId>
        <artifactId>pitest-maven</artifactId>
        <version>1.15.0</version>
      </plugin>
    </plugins>
  </build>
</project>
"""


SUREFIRE_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="demo.FooTest" tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="demo.FooTest" name="testA" time="0.01"/>
  <testcase classname="demo.FooTest" name="testB" time="0.01">
    <failure message="boom">stack</failure>
  </testcase>
  <testcase classname="demo.FooTest" name="testC" time="0.0">
    <skipped/>
  </testcase>
</testsuite>
"""


def mutations_xml(entries: list[tuple[str, str, str, str, str]]) -> str:
    body = "".join(
        f"<mutation detected='true' status='{status}'>"
        f"<sourceFile>Foo.java</sourceFile>"
        f"<mutatedClass>{mutated_class}</mutatedClass>"
        f"<mutatedMethod>{method}</mutatedMethod>"
        f"<lineNumber>{line}</lineNumber>"
        f"<mutator>{mutator}</mutator>"
        f"</mutation>"
        for status, mutated_class, method, line, mutator in entries
    )
    return f"<mutations>{body}</mutations>"


class HarnessTest(unittest.TestCase):
    def test_verification_requires_an_executed_test_result(self):
        evidence = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.PASSED)

        reason = verification_failure_reason(evidence, run_pit=False)

        self.assertIn("No non-skipped test result", reason)

    def test_verification_requires_every_scoped_test_class(self):
        evidence = HarnessEvidence(
            HarnessStatus.PASSED, HarnessStatus.PASSED,
            test_results={"demo.FirstTest#testA": "PASSED"},
        )

        reason = verification_failure_reason(evidence, False, ["demo.FirstTest", "demo.SecondTest"])

        self.assertIn("demo.SecondTest", reason)

    def test_verification_requires_a_real_pit_report_when_requested(self):
        evidence = HarnessEvidence(
            HarnessStatus.PASSED, HarnessStatus.PASSED, HarnessStatus.PASSED,
            test_results={"demo.FooTest#testA": "PASSED"},
        )

        reason = verification_failure_reason(evidence, run_pit=True)

        self.assertIn("no usable mutation evidence", reason)

    def test_collect_test_identities_reads_status_per_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "target" / "surefire-reports"
            reports.mkdir(parents=True)
            (reports / "TEST-demo.FooTest.xml").write_text(SUREFIRE_REPORT, encoding="utf-8")

            identities = ProjectHarness._collect_test_identities(root)

            self.assertEqual({
                "demo.FooTest#testA": "PASSED",
                "demo.FooTest#testB": "FAILED",
                "demo.FooTest#testC": "SKIPPED",
            }, identities)

    def test_clear_test_reports_prevents_reusing_an_older_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "target" / "surefire-reports" / "TEST-demo.FooTest.xml"
            report.parent.mkdir(parents=True)
            report.write_text(SUREFIRE_REPORT, encoding="utf-8")

            ProjectHarness._clear_test_reports(root)

            self.assertFalse(report.exists())

    def test_collect_mutation_summary_ignores_reports_older_than_since(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_dir = root / "target" / "pit-reports" / "202601010000"
            report_dir.mkdir(parents=True)
            report_path = report_dir / "mutations.xml"
            report_path.write_text(mutations_xml([
                ("KILLED", "demo.Foo", "bar", "10", "VoidMethodCallMutator"),
                ("SURVIVED", "demo.Foo", "bar", "12", "VoidMethodCallMutator"),
            ]), encoding="utf-8")

            future = time.time() + 3600
            self.assertIsNone(ProjectHarness._collect_mutation_summary(root, future))

            past = time.time() - 3600
            summary = ProjectHarness._collect_mutation_summary(root, past)
            self.assertEqual(2, summary["total"])
            self.assertEqual(0.5, summary["mutationScore"])
            self.assertEqual({"KILLED": 1, "SURVIVED": 1}, summary["counts"])

    def test_collect_mutation_summary_finds_reports_with_no_intermediate_subdirectory(self):
        """PIT actually writes to <module>/target/pit-reports/mutations.xml directly --
        no timestamped subdirectory between pit-reports and mutations.xml, unlike the
        fixture above. A prior version of this method globbed for exactly one such
        subdirectory and silently found nothing on every real run as a result."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_dir = root / "some-module" / "target" / "pit-reports"
            report_dir.mkdir(parents=True)
            (report_dir / "mutations.xml").write_text(mutations_xml([
                ("KILLED", "demo.Foo", "bar", "10", "VoidMethodCallMutator"),
            ]), encoding="utf-8")

            summary = ProjectHarness._collect_mutation_summary(root, time.time() - 3600)
            self.assertIsNotNone(summary)
            self.assertEqual(1, summary["total"])

    def test_mutation_regressed_true_when_a_previously_killed_mutant_survives(self):
        baseline = {"mutants": {"demo.Foo|bar|10|M": "KILLED", "demo.Foo|bar|12|M": "SURVIVED"}}
        candidate_ok = {"mutants": {"demo.Foo|bar|10|M": "KILLED", "demo.Foo|bar|12|M": "KILLED"}}
        candidate_regressed = {"mutants": {"demo.Foo|bar|10|M": "SURVIVED", "demo.Foo|bar|12|M": "KILLED"}}

        self.assertFalse(mutation_regressed(baseline, candidate_ok))
        self.assertTrue(mutation_regressed(baseline, candidate_regressed))

    def test_mutation_regressed_false_when_baseline_has_no_mutants(self):
        self.assertFalse(mutation_regressed({}, {"mutants": {"x": "SURVIVED"}}))

    def test_build_commands_without_maven_repo_local_keeps_default_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")

            compile_command, test_command, pit_command = ProjectHarness()._build_commands(root)

            for command in (compile_command, test_command, pit_command):
                self.assertFalse(any(arg.startswith("-Dmaven.repo.local=") for arg in command))

    def test_build_commands_with_maven_repo_local_covers_compile_test_and_pit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            repo = Path(temporary) / ".m2-repo"

            harness = ProjectHarness(maven_repo_local=repo)
            compile_command, test_command, pit_command = harness._build_commands(root)

            expected = f"-Dmaven.repo.local={repo.resolve()}"
            for command in (compile_command, test_command, pit_command):
                self.assertIn(expected, command)
                # Right after the mvn/mvnw executable, before any goal.
                self.assertEqual(expected, command[1])

    def test_build_commands_always_skip_style_checks(self):
        """回归测试：Spotless 这类格式检查即使不通过，代码本身还是完全合法、能编译
        的 Java——论文"Syntactic Validity"指的是编译器意义上能不能编译，不是某个
        项目自选的排版约定，而且我们往 pom.xml 注入 PIT 配置会重新序列化整份文件，
        格式检查会把这个语义无关的差异当成违规、直接中止构建。
        Regression test: failing a Spotless-style check doesn't mean the code isn't
        valid, compilable Java — the paper's "Syntactic Validity" means compiler-level
        compilability, not an opt-in style linter's opinion, and injecting PIT config
        into pom.xml re-serializes the whole file, which such a check would flag as a
        (semantically irrelevant) violation and abort the build over."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")

            compile_command, test_command, pit_command = ProjectHarness()._build_commands(root)

            for command in (compile_command, test_command, pit_command):
                self.assertIn("-Dspotless.check.skip=true", command)
                self.assertIn("-Dspotless.apply.skip=true", command)

    def test_validate_targets_scopes_maven_and_requires_fresh_target_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")

            def execute(command, cwd, evidence):
                evidence.commands.append(command)
                report = cwd / "module-a" / "target" / "surefire-reports" / "TEST-demo.FooTest.xml"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(SUREFIRE_REPORT, encoding="utf-8")
                return HarnessStatus.PASSED

            with patch.object(ProjectHarness, "_execute", side_effect=execute):
                evidence = ProjectHarness().validate_targets(root, ["demo.FooTest"], ["module-a"])

            self.assertEqual(HarnessStatus.PASSED, evidence.test_status)
            self.assertIn("-pl", evidence.commands[0])
            self.assertIn("module-a", evidence.commands[0])
            self.assertIn("-Dtest=demo.FooTest", evidence.commands[0])
            self.assertIn("demo.FooTest#testA", evidence.test_results)

    def test_harness_evidence_round_trip_preserves_statuses(self):
        original = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.FAILED,
                                   test_results={"demo.Foo#x": "FAILED"}, mutation_total=3)
        restored = HarnessEvidence.from_dict(original.as_dict())
        self.assertEqual(original.as_dict(), restored.as_dict())


class EnsurePitJunit5SupportTest(unittest.TestCase):
    """回归测试：全量 109 个 MCI 的批次里，直接命令行调用 PIT 时它完全不知道要用
    pitest-junit5-plugin 识别 JUnit 5 测试，而且 pitest-junit5-plugin 自带的
    junit-platform-launcher 版本比项目实际用的 junit-platform-engine 旧就会让
    minion 子进程直接崩溃——两者都要显式声明，且 launcher 版本必须和项目探测出来的
    engine 版本一致，不能写死。
    Regression tests: in the full 109-MCI batch, PIT had no way of knowing to use
    pitest-junit5-plugin for JUnit 5 tests when invoked directly from the command line,
    and pitest-junit5-plugin's own transitive junit-platform-launcher being older than
    the project's actual junit-platform-engine crashed the minion subprocess outright —
    both must be declared explicitly, and the launcher version must match whatever the
    project actually resolves to, not a hard-coded constant."""

    def test_injects_pitest_junit5_plugin_with_a_matching_launcher_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text(MINIMAL_POM, encoding="utf-8")

            with patch("studio.harness._detect_junit_platform_engine_version", return_value="1.13.1"):
                changed = ensure_pit_junit5_support(root)

            self.assertTrue(changed)
            patched = (root / "pom.xml").read_text(encoding="utf-8")
            self.assertIn("pitest-junit5-plugin", patched)
            self.assertIn("<groupId>org.junit.platform</groupId>", patched)
            self.assertIn("<artifactId>junit-platform-launcher</artifactId>", patched)
            self.assertIn("<version>1.13.1</version>", patched)

    def test_is_a_noop_when_pitest_maven_is_already_configured(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text(POM_WITH_PITEST_ALREADY_CONFIGURED, encoding="utf-8")

            with patch("studio.harness._detect_junit_platform_engine_version") as detect:
                changed = ensure_pit_junit5_support(root)

            detect.assert_not_called()
            self.assertFalse(changed)
            self.assertEqual(POM_WITH_PITEST_ALREADY_CONFIGURED, (root / "pom.xml").read_text(encoding="utf-8"))

    def test_is_a_noop_when_the_project_is_not_on_junit5(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text(MINIMAL_POM, encoding="utf-8")

            with patch("studio.harness._detect_junit_platform_engine_version", return_value=None):
                changed = ensure_pit_junit5_support(root)

            self.assertFalse(changed)
            self.assertEqual(MINIMAL_POM, (root / "pom.xml").read_text(encoding="utf-8"))

    def test_calling_it_twice_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text(MINIMAL_POM, encoding="utf-8")

            with patch("studio.harness._detect_junit_platform_engine_version", return_value="1.13.1"):
                self.assertTrue(ensure_pit_junit5_support(root))
                self.assertFalse(ensure_pit_junit5_support(root))


if __name__ == "__main__":
    unittest.main()


class BuildScopeTest(unittest.TestCase):
    """裁剪的动机是速度：不加 -pl，Maven 会为一次只碰一两个测试文件的改动，把整个
    reactor（Dubbo 124 个模块）的生命周期走一遍。
    Scoping exists for speed: without -pl, Maven walks the whole reactor's lifecycle
    (124 Dubbo modules) for a change that touches one or two test files."""

    def _commands(self, root: Path, scope):
        return ProjectHarness()._build_commands(root, scope)

    def _project(self, temporary: str) -> Path:
        root = Path(temporary)
        (root / "pom.xml").write_text(MINIMAL_POM, encoding="utf-8")
        return root

    def test_without_a_scope_the_whole_reactor_is_built(self):
        with tempfile.TemporaryDirectory() as temporary:
            compile_command, test_command, _ = self._commands(self._project(temporary), None)
            self.assertNotIn("-pl", compile_command)
            self.assertNotIn("-pl", test_command)
            self.assertNotIn("-Dtest", " ".join(test_command))

    def test_a_scope_narrows_compile_test_and_pit_alike(self):
        with tempfile.TemporaryDirectory() as temporary:
            scope = BuildScope(modules=("mod-a",), test_classes=("demo.ATest",))
            compile_command, test_command, pit_command = self._commands(self._project(temporary), scope)
            for command in (compile_command, test_command, pit_command):
                self.assertIn("-pl", command)
                self.assertIn("mod-a", command)
                self.assertIn("-am", command)
            self.assertIn("-Dtest=demo.ATest", test_command)
            self.assertIn("-DtargetTests=demo.ATest", pit_command)

    def test_scoped_tests_carry_both_no_test_tolerances(self):
        """-am 把依赖模块拉进 reactor，Maven 对每个模块套用同一个 -Dtest 过滤。
        -DfailIfNoTests 只管"没有测试"，管不住"有测试但没有匹配的类"。
        -am pulls dependency modules in and the same -Dtest filter applies to each;
        -DfailIfNoTests covers "no tests", not "tests but none matching"."""
        with tempfile.TemporaryDirectory() as temporary:
            _, test_command, _ = self._commands(
                self._project(temporary), BuildScope(modules=("mod-a",), test_classes=("demo.ATest",)))
            self.assertIn("-DfailIfNoTests=false", test_command)
            self.assertIn("-Dsurefire.failIfNoSpecifiedTests=false", test_command)

    def test_a_consumed_test_jar_pulls_downstream_modules_back_in(self):
        """-pl X -am 不构建依赖 X 的模块。X 发布 test-jar 时这是个盲区，要用 -amd 补回来。
        -pl X -am omits modules that depend on X; when X publishes a test-jar that is a
        blind spot, and -amd closes it."""
        with tempfile.TemporaryDirectory() as temporary:
            root = self._project(temporary)
            consumer = root / "mod-b"
            consumer.mkdir()
            (consumer / "pom.xml").write_text(
                "<project><dependencies><dependency>"
                "<artifactId>mod-a</artifactId><type>test-jar</type>"
                "</dependency></dependencies></project>", encoding="utf-8")
            with_consumer, _, _ = self._commands(root, BuildScope(modules=("mod-a",)))
            self.assertIn("-amd", with_consumer)

    def test_an_unconsumed_module_does_not_pull_downstream_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            command, _, _ = self._commands(self._project(temporary), BuildScope(modules=("mod-z",)))
            self.assertNotIn("-amd", command)

    def test_evidence_records_what_was_actually_covered(self):
        """裁剪之后"测试通过"不再等于"整个项目通过"，证据里必须能看出区别。
        Once narrowed, "tests passed" no longer means the whole project passed; the
        evidence has to show the difference."""
        self.assertEqual("the whole project", BuildScope().describe())
        self.assertIn("mod-a", BuildScope(modules=("mod-a",)).describe())
        self.assertEqual("the whole project", HarnessEvidence().as_dict()["scope"])
