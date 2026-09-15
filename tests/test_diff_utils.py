import difflib
import unittest
from pathlib import Path

from validation.diff_utils import apply_unified_diff, replay_replacements, split_diff_by_file


def make_diff(original: str, new: str, path: str = "src/Test.java") -> str:
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


class ApplyUnifiedDiffTest(unittest.TestCase):
    def test_round_trips_a_single_hunk_change(self):
        original = "class Test {\n  int a = 1;\n  int b = 2;\n}\n"
        new = "class Test {\n  int a = 99;\n  int b = 2;\n}\n"
        diff_text = make_diff(original, new)
        hunks = split_diff_by_file(diff_text)[Path("src/Test.java")]

        self.assertEqual(new, apply_unified_diff(original, hunks))

    def test_round_trips_multiple_separated_hunks(self):
        original = "\n".join(f"line{i}" for i in range(1, 40)) + "\n"
        lines = original.splitlines()
        lines[2] = "CHANGED-line3"
        lines[30] = "CHANGED-line31"
        new = "\n".join(lines) + "\n"
        diff_text = make_diff(original, new)
        hunks = split_diff_by_file(diff_text)[Path("src/Test.java")]

        self.assertEqual(new, apply_unified_diff(original, hunks))

    def test_round_trips_insertion_and_deletion(self):
        original = "class Test {\n  void a() {}\n  void b() {}\n}\n"
        new = "class Test {\n  void a() {}\n  void newMethod() {}\n  void b() {}\n}\n"
        diff_text = make_diff(original, new)
        hunks = split_diff_by_file(diff_text)[Path("src/Test.java")]

        self.assertEqual(new, apply_unified_diff(original, hunks))

    def test_round_trips_change_at_very_start_and_end(self):
        original = "first\nmiddle\nlast\n"
        new = "FIRST\nmiddle\nLAST\n"
        diff_text = make_diff(original, new)
        hunks = split_diff_by_file(diff_text)[Path("src/Test.java")]

        self.assertEqual(new, apply_unified_diff(original, hunks))

    def test_no_change_produces_identical_content(self):
        original = "class Test {}\n"
        # unified_diff on identical sequences yields an empty diff — nothing to apply.
        hunks = ""
        self.assertEqual(original, apply_unified_diff(original, hunks))


class SplitAndReplayTest(unittest.TestCase):
    def test_splits_a_multi_file_diff(self):
        original_a = "class A {\n  int x = 1;\n}\n"
        new_a = "class A {\n  int x = 2;\n}\n"
        original_b = "class B {\n  int y = 1;\n}\n"
        new_b = "class B {\n  int y = 2;\n}\n"
        combined = make_diff(original_a, new_a, "src/A.java") + make_diff(original_b, new_b, "src/B.java")

        replacements = replay_replacements(combined, {
            Path("src/A.java"): original_a,
            Path("src/B.java"): original_b,
        })

        self.assertEqual(new_a, replacements[Path("src/A.java")])
        self.assertEqual(new_b, replacements[Path("src/B.java")])

    def test_ignores_files_not_present_in_the_provided_originals(self):
        original_a = "class A {\n  int x = 1;\n}\n"
        new_a = "class A {\n  int x = 2;\n}\n"
        diff_text = make_diff(original_a, new_a, "src/A.java")

        replacements = replay_replacements(diff_text, {})

        self.assertEqual({}, replacements)


if __name__ == "__main__":
    unittest.main()
