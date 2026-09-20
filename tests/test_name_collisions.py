"""
命名冲突：模型看不见的那一类失败。

这一组测试来自一次真实失败。模型为 `ServiceDiscovery::1` 生成的 helper 类本身完全正确，
但它叫 `MockServiceDiscovery`，而目标测试文件第 33 行本来就写着

    import org.apache.dubbo.registry.client.support.MockServiceDiscovery;

Java 里显式单类型 import 的优先级高于同包解析，于是文件里每一处 `MockServiceDiscovery`
仍然指向被 import 的旧类，新方法一律"找不到符号"。编译失败，22249 tokens 作废，而
`goalAchieved` 是 True——重复确实消除了，只是编译不过。

这不是模型能力问题：payload 里没有任何东西能告诉它这个名字已经被占用。

Name collisions: the class of failure the model cannot see.

These tests come from a real one. The helper class generated for `ServiceDiscovery::1` was
correct in itself, but it was named `MockServiceDiscovery` while line 33 of the target test file
already read `import org.apache.dubbo.registry.client.support.MockServiceDiscovery;`. An
explicit single-type import outranks same-package resolution in Java, so every mention of that
name still meant the imported class and the new methods were all "cannot find symbol".
Compilation failed and 22249 tokens were lost, with `goalAchieved` true — the duplication really
had gone, the code just would not build.

Not a capability problem: nothing in the payload told the model the name was taken.
"""

import tempfile
import unittest
from pathlib import Path

from studio.payloads import _taken_class_names, _taken_identifiers
from studio.refactoring_agent import RefactoringAgent

IMPORTING_TEST = """package org.apache.dubbo.registry.client.event.listener;

import org.apache.dubbo.registry.client.support.MockServiceDiscovery;
import org.mockito.Mockito;

public class ListenerTest {
    private ServiceDiscovery serviceDiscovery;
    private MetadataInfo metadataInfo_222;

    @Test
    void testNotify() {
        int retries = 3;
    }
}
"""


class TakenClassNamesTest(unittest.TestCase):
    def test_an_imported_type_counts_as_taken(self):
        target = Path("src/test/java/demo/ListenerTest.java")
        with tempfile.TemporaryDirectory() as temporary:
            taken = _taken_class_names(target, {target: IMPORTING_TEST}, Path(temporary))
        self.assertIn("MockServiceDiscovery", taken)
        self.assertIn("Mockito", taken)

    def test_a_sibling_in_the_same_package_counts_as_taken(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "src" / "test" / "java" / "demo"
            package.mkdir(parents=True)
            (package / "ExistingHelper.java").write_text("class ExistingHelper {}", encoding="utf-8")
            target = Path("src/test/java/demo/ListenerTest.java")

            taken = _taken_class_names(target, {target: IMPORTING_TEST}, root)

        self.assertIn("ExistingHelper", taken)

    def test_lowercase_static_imports_are_not_class_names(self):
        """`import static org.mockito.Mockito.when;` 里的 when 是方法名，不该被当成类名占位。
        The `when` in a static import is a method, not a class name to reserve."""
        source = "package demo;\nimport static org.mockito.Mockito.when;\n"
        target = Path("A.java")
        with tempfile.TemporaryDirectory() as temporary:
            taken = _taken_class_names(target, {target: source}, Path(temporary))
        self.assertNotIn("when", taken)


class TakenIdentifiersTest(unittest.TestCase):
    def test_finds_fields_and_locals(self):
        names = _taken_identifiers(IMPORTING_TEST)
        self.assertIn("serviceDiscovery", names)
        self.assertIn("metadataInfo_222", names)
        self.assertIn("retries", names)


class NewFileCollisionGuardTest(unittest.TestCase):
    """prompt 拦不住时的兜底：在阶段内就报错，交给同一步重试改名，
    而不是一路烧到编译才换来一句 "cannot find symbol"。
    The backstop for when the prompt does not hold: fail inside the stage so its retry renames,
    rather than burning through to a compile for one "cannot find symbol"."""

    def test_rejects_a_new_class_whose_name_is_already_imported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = Path("src/test/java/demo/ListenerTest.java")
            new_file = {"path": "src/test/java/demo/MockServiceDiscovery.java",
                        "content": "package demo;\npublic class MockServiceDiscovery {}\n"}

            replacements, errors = RefactoringAgent._apply_edits(
                root, {existing: IMPORTING_TEST}, [], [new_file])

            self.assertEqual({}, replacements)
            self.assertTrue(any("collides with a type already imported" in e for e in errors), errors)
            self.assertTrue(any("MockServiceDiscovery" in e for e in errors), errors)

    def test_allows_a_class_name_nothing_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = Path("src/test/java/demo/ListenerTest.java")
            new_file = {"path": "src/test/java/demo/MockServiceDiscoveryWithRetryMetadata.java",
                        "content": "package demo;\npublic class MockServiceDiscoveryWithRetryMetadata {}\n"}

            replacements, errors = RefactoringAgent._apply_edits(
                root, {existing: IMPORTING_TEST}, [], [new_file])

            self.assertEqual([], errors)
            self.assertIn(Path(new_file["path"]), replacements)


if __name__ == "__main__":
    unittest.main()
