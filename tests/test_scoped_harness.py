import tempfile
import unittest
from pathlib import Path

from validation.scoped_harness import ScopedProjectHarness


class ScopedHarnessTest(unittest.TestCase):
    def test_maven_repo_local_covers_compile_test_and_pit_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            repo = Path(temporary) / ".m2-repo"

            harness = ScopedProjectHarness(["demo.FooTest"], ["module-a"], maven_repo_local=repo)
            compile_command, test_command, pit_command = harness._build_commands(root)

            expected = f"-Dmaven.repo.local={repo.resolve()}"
            for command in (compile_command, test_command, pit_command):
                self.assertIn(expected, command)
                self.assertEqual(expected, command[1])

    def test_without_maven_repo_local_omits_the_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")

            harness = ScopedProjectHarness(["demo.FooTest"], ["module-a"])
            compile_command, test_command, pit_command = harness._build_commands(root)

            for command in (compile_command, test_command, pit_command):
                self.assertFalse(any(arg.startswith("-Dmaven.repo.local=") for arg in command))

    def test_all_three_commands_skip_style_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")

            harness = ScopedProjectHarness(["demo.FooTest"], ["module-a"])
            compile_command, test_command, pit_command = harness._build_commands(root)

            for command in (compile_command, test_command, pit_command):
                self.assertIn("-Dspotless.check.skip=true", command)
                self.assertIn("-Dspotless.apply.skip=true", command)


if __name__ == "__main__":
    unittest.main()
