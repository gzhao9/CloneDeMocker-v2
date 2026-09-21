import unittest
from pathlib import Path

from studio.payloads import integration_payload, route_integration, verbatim_failures

SOURCE = """package demo;

class FooTest {

    private Dependency value;

    @BeforeEach
    void setUp() {
        value = Mockito.mock(Dependency.class);
        Mockito.when(value.name()).thenReturn("shared");
    }

    @Test
    void testFirst() {
        Mockito.when(value.extra()).thenReturn(1);
        subject.accept(value);
    }
}
"""


def line_of(needle: str) -> int:
    for number, text in enumerate(SOURCE.splitlines(), start=1):
        if needle in text:
            return number
    raise AssertionError(f"not in fixture: {needle}")


def fixture() -> tuple[Path, dict, dict, dict[Path, str]]:
    relative = Path("src/test/java/demo/FooTest.java")
    sequence = {
        "filePath": relative.as_posix(),
        "className": "FooTest",
        "testMethodName": "testFirst",
        "variableName": "value",
        "testMockLines": {
            str(line_of("value.extra()")): 'Mockito.when(value.extra()).thenReturn(1);',
        },
        "shareableMockLines": {
            str(line_of("Mockito.mock(Dependency.class)")): "value = Mockito.mock(Dependency.class);",
            str(line_of("value.name()")): 'Mockito.when(value.name()).thenReturn("shared");',
        },
    }
    instance = {"mockedClass": "demo.Dependency", "sharedStatementLineCount": 2, "sequences": [sequence]}
    return relative, instance, sequence, {relative: SOURCE}


class BeforeVariantPayloadTest(unittest.TestCase):
    """before 变体要改的是 @Before 里的代码，payload 必须带上那段原文。

    提示词一直声明会给出 setup 方法，而 payload 只带了测试方法，模型于是以「setup 源码
    不在 verbatim 中」为由拒绝重构——Dubbo 3.3.6 那一轮 before 变体的 4 次拒绝全部如此。
    The before variant edits code inside @Before, so the payload has to carry that text.
    The prompt always declared the setup method as input while the payload shipped only the
    test method, so the model refused with "the setup method source is not present in the
    supplied verbatim" — all four before-variant refusals on Dubbo 3.3.6.
    """

    def test_routes_to_the_before_variant(self):
        _, instance, sequence, _ = fixture()
        self.assertEqual("before", route_integration(instance, sequence))

    def test_carries_the_setup_method_verbatim(self):
        root, instance, sequence, files = fixture()
        payload = integration_payload(Path("."), instance, sequence, files, "helper() {}")

        texts = [entry["text"] for entry in payload["verbatim"]["setupMethods"]]
        self.assertEqual(1, len(texts))
        self.assertIn("@BeforeEach", texts[0])
        self.assertIn("value = Mockito.mock(Dependency.class);", texts[0])

    def test_carries_each_setup_statement_verbatim(self):
        _, instance, sequence, files = fixture()
        payload = integration_payload(Path("."), instance, sequence, files, "helper() {}")

        statements = [entry["text"] for entry in payload["verbatim"]["setupStatements"]]
        self.assertIn("value = Mockito.mock(Dependency.class);", statements)
        self.assertIn('Mockito.when(value.name()).thenReturn("shared");', statements)

    def test_every_added_region_survives_the_pre_send_assertion(self):
        """补进去的文本必须是文件里的原文，否则等于又制造了一个抄不得的 oldString。
        The added text must be the file's own, or it merely creates another `oldString`
        that can never match."""
        _, instance, sequence, files = fixture()
        payload = integration_payload(Path("."), instance, sequence, files, "helper() {}")

        self.assertEqual([], verbatim_failures(payload, files))

    def test_local_variant_is_left_alone(self):
        """local 变体的编辑点就在测试方法里，不该被塞进 setup 文本。
        The local variant edits the test method itself and must not be handed setup text."""
        _, instance, sequence, files = fixture()
        instance = {**instance, "sharedStatementLineCount": 1}
        sequence = {**sequence, "shareableMockLines": {}}
        self.assertEqual("local", route_integration(instance, sequence))

        payload = integration_payload(Path("."), instance, sequence, files, "helper() {}")
        self.assertEqual([], payload["verbatim"]["setupMethods"])
        self.assertEqual([], payload["verbatim"]["setupStatements"])


if __name__ == "__main__":
    unittest.main()
