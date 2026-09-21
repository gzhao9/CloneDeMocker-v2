import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import gradle_support
from studio.harness import BuildScope, HarnessStatus, ProjectHarness, is_module_directory
from studio.long_paths import long_path
from studio.preflight import gradle_root, inspect_project
from validation.scoped_harness import ScopedProjectHarness

GRADLE_REPORT = """<testsuite name="org.demo.FooTests" tests="1" failures="0" errors="0" skipped="0">
  <testcase name="works" classname="org.demo.FooTests"/>
</testsuite>"""


def _spring_like(root: Path) -> gradle_support.GradleProjects:
    """Spring Security 的形状：子项目构建文件不叫 build.gradle，web 的测试输出被 config 依赖。
    Spring Security's shape: subproject build files are not named build.gradle, and config
    consumes web's test output, which in turn consumes core's."""
    return gradle_support.GradleProjects(
        root=root,
        directories={"": ":", "core": ":spring-security-core", "web": ":spring-security-web",
                     "config": ":spring-security-config", "oauth2/oauth2-client": ":spring-security-oauth2-client"},
        test_output_consumers={":spring-security-core": {":spring-security-web"},
                               ":spring-security-web": {":spring-security-config"}},
        toolchains={"25": True},
    )


class GradleHarnessTest(unittest.TestCase):
    def _root(self, temporary: str) -> Path:
        root = Path(temporary)
        (root / "settings.gradle").write_text("", encoding="utf-8")
        (root / "gradlew.bat").write_text("", encoding="utf-8")
        (root / "gradlew").write_text("", encoding="utf-8")
        return root

    def _commands(self, root: Path, scope):
        with patch.object(gradle_support, "discover_projects", return_value=_spring_like(root)):
            return ProjectHarness()._build_commands(root, scope)

    def test_scope_maps_directories_to_gradle_project_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            compile_command, test_command, pit_command = self._commands(
                root, BuildScope(("oauth2/oauth2-client",), ("org.demo.FooTests",)))
            self.assertIn(":spring-security-oauth2-client:testClasses", compile_command)
            self.assertIn(":spring-security-oauth2-client:test", test_command)
            self.assertIn(":spring-security-oauth2-client:pitest", pit_command)
            self.assertNotIn("testClasses", compile_command)

    def test_test_output_consumers_are_compiled_transitively_like_amd(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            compile_command, test_command, _ = self._commands(root, BuildScope(("core",), ("org.demo.FooTests",)))
            self.assertIn(":spring-security-core:testClasses", compile_command)
            self.assertIn(":spring-security-web:testClasses", compile_command)
            self.assertIn(":spring-security-config:testClasses", compile_command)
            # 下游只编译，不跑测试，和 Maven 的 -amd 加 -Dtest 过滤一致。
            # Downstream projects are compiled, not tested, as with Maven's -amd plus -Dtest.
            self.assertNotIn(":spring-security-web:test", test_command)

    def test_every_test_task_carries_the_filter_and_forces_a_fresh_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            _, test_command, pit_command = self._commands(
                root, BuildScope(("core", "web"), ("org.demo.ATests", "org.demo.BTests")))
            for task in (":spring-security-core:test", ":spring-security-web:test"):
                index = test_command.index(task)
                self.assertEqual(["--tests", "org.demo.ATests", "--tests", "org.demo.BTests", "--rerun"],
                                 test_command[index + 1:index + 6])
            self.assertIn("-PcloneDeMockerPitProjects=:spring-security-core,:spring-security-web", pit_command)
            self.assertIn("-PcloneDeMockerPitTests=org.demo.ATests,org.demo.BTests", pit_command)

    def test_all_commands_use_the_verify_init_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            for command in self._commands(root, BuildScope(("core",), ("org.demo.FooTests",))):
                self.assertEqual(str(gradle_support.verify_init_script()), command[command.index("-I") + 1])

    def test_without_a_scope_the_whole_build_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            compile_command, test_command, pit_command = self._commands(root, None)
            self.assertIn("testClasses", compile_command)
            self.assertIn("test", test_command)
            self.assertIn("-PcloneDeMockerPitProjects=*", pit_command)

    def test_missing_toolchain_is_reported_instead_of_blamed_on_the_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            projects = _spring_like(root)
            projects.toolchains = {"25": False}
            with patch.object(gradle_support, "discover_projects", return_value=projects), \
                    patch.object(ProjectHarness, "_execute") as execute:
                evidence = ProjectHarness().validate(root, scope=BuildScope(("core",), ("org.demo.FooTests",)))
            execute.assert_not_called()
            self.assertEqual(HarnessStatus.UNAVAILABLE, evidence.compile_status)
            self.assertIn("25", " ".join(evidence.diagnostics))

    def test_validate_targets_reads_gradle_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)

            def execute(command, cwd, evidence):
                evidence.commands.append(command)
                report = cwd / "core" / "build" / "test-results" / "test" / "TEST-org.demo.FooTests.xml"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(GRADLE_REPORT, encoding="utf-8")
                return HarnessStatus.PASSED

            with patch.object(gradle_support, "discover_projects", return_value=_spring_like(root)), \
                    patch.object(ProjectHarness, "_execute", side_effect=execute):
                evidence = ProjectHarness().validate_targets(root, ["org.demo.FooTests"], ["core"])
            self.assertEqual(HarnessStatus.PASSED, evidence.test_status)
            self.assertIn(":spring-security-core:test", evidence.commands[0])
            self.assertIn("org.demo.FooTests#works", evidence.test_results)

    def test_scoped_harness_takes_the_same_gradle_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            harness = ScopedProjectHarness(["org.demo.FooTests"], ["web"])
            with patch.object(gradle_support, "discover_projects", return_value=_spring_like(root)):
                _, test_command, _ = harness._build_commands(root)
            self.assertIn(":spring-security-web:test", test_command)

    def test_class_in_a_custom_source_set_runs_through_its_own_test_task(self):
        # saml2 的 OpenSAML 5 测试在 src/opensaml5Test，只有 opensaml5Test 任务会执行它们。
        # saml2's OpenSAML 5 tests live in src/opensaml5Test and only its own task runs them.
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            source = root / "saml2" / "src" / "opensaml5Test" / "java"
            (source / "org" / "demo").mkdir(parents=True)
            (source / "org" / "demo" / "SamlTests.java").write_text("", encoding="utf-8")
            projects = _spring_like(root)
            projects.directories["saml2"] = ":saml2"
            projects.test_tasks[":saml2"] = [
                gradle_support.TestTask("test", "test", "testClasses", str(root / "saml2" / "src" / "test" / "java")),
                gradle_support.TestTask("opensaml5Test", "opensaml5Test", "opensaml5TestClasses", str(source)),
            ]
            with patch.object(gradle_support, "discover_projects", return_value=projects):
                compile_command, test_command, pit_command = ProjectHarness()._build_commands(
                    root, BuildScope(("saml2",), ("org.demo.SamlTests",)))
            self.assertIn(":saml2:opensaml5TestClasses", compile_command)
            self.assertIn(":saml2:opensaml5Test", test_command)
            self.assertNotIn(":saml2:test", test_command)
            self.assertIn("-PcloneDeMockerPitTestSourceSets=opensaml5Test", pit_command)


class GradleProjectsTest(unittest.TestCase):
    def test_test_task_lookup_sees_source_files_past_max_path(self):
        # 批次副本里 saml2 的 opensaml5Test 源文件正好 260 字符；查不到就退回 test 任务，测试一个都不跑。
        # In a batch copy saml2's opensaml5Test source file is exactly 260 characters; missing it
        # falls back to the test task, which runs none of its tests.
        # TemporaryDirectory 删不掉超过 MAX_PATH 的树 / TemporaryDirectory cannot remove a tree past MAX_PATH
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, long_path(Path(temporary)), True)
        source = Path(temporary) / ("d" * 60) / "src" / "opensaml5Test" / "java"
        package = long_path(source / "org" / ("p" * 60) / ("q" * 60))
        package.mkdir(parents=True)
        (package / "LongNamedTests.java").write_text("", encoding="utf-8")
        projects = _spring_like(Path(temporary))
        projects.test_tasks[":saml2"] = [
            gradle_support.TestTask("opensaml5Test", "opensaml5Test", "opensaml5TestClasses", str(source))]
        test_class = f"org.{'p' * 60}.{'q' * 60}.LongNamedTests"
        self.assertGreater(len(str(source)) + len(test_class) + len(".java"), 260)
        self.assertEqual(["opensaml5Test"],
                         [task.name for task in projects.test_tasks_for(":saml2", test_class, fallback=False)])

    def test_nested_directory_falls_back_to_nearest_project(self):
        projects = _spring_like(Path("."))
        self.assertEqual(":spring-security-oauth2-client", projects.project_for("oauth2/oauth2-client/src/test"))
        self.assertEqual(":", projects.project_for("docs"))

    def test_non_default_build_file_names_mark_a_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "core"
            module.mkdir()
            self.assertFalse(is_module_directory(module))
            (module / "spring-security-core.gradle").write_text("", encoding="utf-8")
            self.assertTrue(is_module_directory(module))
            self.assertFalse(is_module_directory(Path(temporary)))

    def test_english_environment_keeps_existing_options(self):
        with patch.dict("os.environ", {"JAVA_TOOL_OPTIONS": "-Xss4m"}):
            self.assertEqual("-Xss4m " + gradle_support.ENGLISH_JAVA_TOOL_OPTIONS,
                             gradle_support.english_environment()["JAVA_TOOL_OPTIONS"])

    def test_only_gradle_commands_get_the_english_environment(self):
        self.assertTrue(gradle_support.is_gradle_command([r"D:\p\gradlew.bat", "test"]))
        self.assertFalse(gradle_support.is_gradle_command(["mvn.cmd", "test"]))


class GradlePreflightTest(unittest.TestCase):
    def test_missing_toolchain_blocks_before_any_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "settings.gradle").write_text("", encoding="utf-8")
            projects = _spring_like(root)
            projects.toolchains = {"25": False}
            with patch.object(gradle_support, "discover_projects", return_value=projects):
                result = inspect_project(root)
            self.assertEqual("gradle", result["buildSystem"])
            self.assertIn("GRADLE_TOOLCHAIN_MISSING", [item["code"] for item in result["findings"]])
            self.assertTrue(result["sagRecommended"])

    def test_healthy_build_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "settings.gradle").write_text("", encoding="utf-8")
            with patch.object(gradle_support, "discover_projects", return_value=_spring_like(root)):
                result = inspect_project(root)
            self.assertEqual("OK", result["severity"])
            self.assertEqual("OK", result["buildProbe"]["status"])

    def test_subproject_points_at_the_settings_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "settings.gradle").write_text("", encoding="utf-8")
            module = root / "core"
            module.mkdir()
            (module / "spring-security-core.gradle").write_text("", encoding="utf-8")
            self.assertEqual(root, gradle_root(module))
            self.assertIsNone(gradle_root(root))


if __name__ == "__main__":
    unittest.main()
