import unittest

from studio.source_map import enclosing_method, locate

FILE = (
    "package demo;\n"
    "\n"
    "class SampleTest {\n"
    "\n"
    "    /**\n"
    "     * Javadoc that belongs to the method.\n"
    "     */\n"
    "    @Test\n"
    "    void testThing() throws Exception {\n"
    "        Foo foo = Mockito.mock(Foo.class);\n"
    "        Mockito.verify(foo, Mockito.times(2))\n"
    "                .received(captor.capture(), any());\n"
    "        await().untilAsserted(() -> {\n"
    "            Mockito.verify(foo).run();\n"
    "        });\n"
    "    }\n"
    "\n"
    "    @Test\n"
    "    void other() {\n"
    "        Foo foo = Mockito.mock(Foo.class);\n"
    "    }\n"
    "}\n"
)


class LocateTest(unittest.TestCase):
    def test_finds_text_the_detector_stripped_of_indentation(self):
        """检测器去掉了类级别缩进，定位必须仍然命中。
        The detector strips class-level indentation; location must still hit."""
        span = locate(FILE, "Mockito.verify(foo).run();")
        self.assertIsNotNone(span)
        self.assertEqual(FILE[span[0]:span[1]], "Mockito.verify(foo).run();")

    def test_finds_a_statement_the_detector_joined_onto_one_line(self):
        """文件里折行的语句，检测器拼接时删掉了空白，两边都去空白才能对上。
        The detector joins a wrapped statement by deleting whitespace; only removing
        it on both sides matches."""
        joined = "Mockito.verify(foo, Mockito.times(2)).received(captor.capture(), any());"
        span = locate(FILE, joined)
        self.assertIsNotNone(span)
        self.assertIn(".received(captor.capture(), any());", FILE[span[0]:span[1]])
        self.assertIn("\n", FILE[span[0]:span[1]])

    def test_tolerates_crlf_from_the_detector_against_an_lf_file(self):
        """检测器输出 CRLF，源文件是 LF。
        The detector emits CRLF; the source file is LF."""
        span = locate(FILE, "await().untilAsserted(() -> {\r\nMockito.verify(foo).run();\r\n});")
        self.assertIsNotNone(span)
        self.assertNotIn("\r", FILE[span[0]:span[1]])

    def test_returns_none_when_ambiguous_and_no_anchor_is_given(self):
        """`Foo foo = Mockito.mock(Foo.class);` 在两个测试方法里各出现一次。
        The same statement appears in both test methods."""
        self.assertIsNone(locate(FILE, "Foo foo = Mockito.mock(Foo.class);"))

    def test_uses_the_line_anchor_to_disambiguate(self):
        first = locate(FILE, "Foo foo = Mockito.mock(Foo.class);", near_line=10)
        second = locate(FILE, "Foo foo = Mockito.mock(Foo.class);", near_line=20)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertLess(first[0], second[0])

    def test_returns_none_when_absent(self):
        self.assertIsNone(locate(FILE, "Mockito.mock(Absent.class);"))


class EnclosingMethodTest(unittest.TestCase):
    def _method_at(self, needle: str) -> str:
        span = enclosing_method(FILE, FILE.index(needle))
        self.assertIsNotNone(span)
        return FILE[span[0]:span[1]]

    def test_expands_to_the_whole_method_including_annotation_and_javadoc(self):
        method = self._method_at("Foo foo = Mockito.mock(Foo.class);")
        self.assertTrue(method.lstrip().startswith("/**"))
        self.assertIn("@Test", method)
        self.assertIn("void testThing() throws Exception {", method)
        self.assertTrue(method.rstrip().endswith("}"))

    def test_does_not_stop_at_a_lambda_body(self):
        """`await().untilAsserted(() -> {` 看起来像方法签名，必须被排除，
        否则抓到的是 lambda 体而不是测试方法。
        `await().untilAsserted(() -> {` resembles a signature and must be rejected,
        or the lambda body is returned instead of the test method."""
        method = self._method_at("Mockito.verify(foo).run();")
        self.assertIn("void testThing()", method)

    def test_result_is_verbatim_and_unique_in_the_file(self):
        method = self._method_at("Foo foo = Mockito.mock(Foo.class);")
        self.assertEqual(FILE.count(method), 1)

    def test_is_not_fooled_by_an_arrow_inside_a_comment(self):
        """注释里的 `->` 曾让 lambda 排除规则误伤真方法（Dubbo 实例）。
        A `->` in a comment used to make the lambda exclusion reject a real method."""
        source = (
            "class T {\n"
            "    // instance listener -> service listener flow\n"
            "    @Test\n"
            "    void testFlow() {\n"
            "        Foo foo = Mockito.mock(Foo.class);\n"
            "    }\n"
            "}\n"
        )
        span = enclosing_method(source, source.index("Mockito.mock"))
        self.assertIsNotNone(span)
        self.assertIn("void testFlow()", source[span[0]:span[1]])

    def test_expands_past_a_repeated_anonymous_class_method(self):
        """匿名内部类的覆写方法签名合法但文本重复，必须继续往外扩到唯一的测试方法。
        An anonymous class's override is a valid signature but its text repeats, so
        expansion must continue to the unique enclosing test method."""
        body = (
            "        Wrapper w = new Wrapper() {\n"
            "            @Override\n"
            "            protected Service create() {\n"
            "                Service s = Mockito.mock(Service.class);\n"
            "                return s;\n"
            "            }\n"
            "        };\n"
        )
        source = ("class T {\n"
                  "    @Test\n    void first() {\n" + body + "    }\n"
                  "    @Test\n    void second() {\n" + body + "    }\n"
                  "}\n")
        span = enclosing_method(source, source.index("Mockito.mock"))
        self.assertIsNotNone(span)
        text = source[span[0]:span[1]]
        self.assertIn("void first()", text)
        self.assertEqual(source.count(text), 1)

    def test_handles_braces_inside_strings_and_comments(self):
        source = (
            "class T {\n"
            "    @Test\n"
            "    void t() {\n"
            '        String brace = "} not a close";\n'
            "        // } also not a close\n"
            "        Foo foo = Mockito.mock(Foo.class);\n"
            "    }\n"
            "}\n"
        )
        span = enclosing_method(source, source.index("Mockito.mock"))
        self.assertIsNotNone(span)
        method = source[span[0]:span[1]]
        self.assertIn("void t() {", method)
        self.assertIn("Foo foo = Mockito.mock(Foo.class);", method)


if __name__ == "__main__":
    unittest.main()
