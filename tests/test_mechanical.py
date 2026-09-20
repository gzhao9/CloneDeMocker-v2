import unittest

from studio.mechanical import inline_mock_to_field, rename_local_mock_to_field


class RenameLocalMockToFieldTest(unittest.TestCase):
    def test_deletes_declaration_and_renames_every_reference(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = Mockito.mock(Foo.class);\n"
            "    subject.accept(foo);\n"
            "    Mockito.verify(foo).run();\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(bail)
        self.assertNotIn("Foo foo =", rewritten)
        self.assertIn("subject.accept(sharedFoo);", rewritten)
        self.assertIn("Mockito.verify(sharedFoo).run();", rewritten)

    def test_handles_generic_wildcard_declaration(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Handler<?> handler = Mockito.mock(Handler.class, Mockito.withSettings().verboseLogging());\n"
            "    registry.put(key, handler);\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "handler", "sharedHandler")
        self.assertIsNone(bail)
        self.assertNotIn("Handler<?> handler", rewritten)
        self.assertIn("registry.put(key, sharedHandler);", rewritten)

    def test_renames_inside_a_lambda_that_does_not_redeclare_the_name(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = Mockito.mock(Foo.class);\n"
            "    await().untilAsserted(() -> Mockito.verify(foo).run());\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(bail)
        self.assertIn("Mockito.verify(sharedFoo).run()", rewritten)

    def test_leaves_member_access_and_literals_alone(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = Mockito.mock(Foo.class);\n"
            "    assertEquals(\"foo\", other.foo);\n"
            "    // foo stays in this comment\n"
            "    subject.accept(foo);\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(bail)
        self.assertIn('assertEquals("foo", other.foo);', rewritten)
        self.assertIn("// foo stays in this comment", rewritten)
        self.assertIn("subject.accept(sharedFoo);", rewritten)

    def test_bails_when_the_variable_is_reassigned(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = Mockito.mock(Foo.class);\n"
            "    subject.accept(foo);\n"
            "    foo = Mockito.mock(Foo.class);\n"
            "    subject.accept(foo);\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(rewritten)
        self.assertIn("assigned 2 times", bail)

    def test_bails_when_the_field_name_is_already_taken(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = Mockito.mock(Foo.class);\n"
            "    Foo sharedFoo = other();\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(rewritten)
        self.assertIn("already used as an identifier", bail)

    def test_bails_when_a_lambda_parameter_shadows_the_name(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = Mockito.mock(Foo.class);\n"
            "    list.forEach(foo -> foo.run());\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(rewritten)
        self.assertIn("redeclared in a nested scope", bail)

    def test_bails_when_the_assignment_is_not_a_mock_creation(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    Foo foo = new Foo();\n"
            "    subject.accept(foo);\n"
            "}\n"
        )
        rewritten, bail = rename_local_mock_to_field(source, "foo", "sharedFoo")
        self.assertIsNone(rewritten)
        self.assertIn("not a mock/spy creation", bail)


class InlineMockToFieldTest(unittest.TestCase):
    def test_replaces_a_single_inline_expression(self):
        source = (
            "@Test\n"
            "void test() {\n"
            "    String out = help.execute(Mockito.mock(CommandContext.class), null);\n"
            "}\n"
        )
        rewritten, bail = inline_mock_to_field(source, "Mockito.mock(CommandContext.class)", "sharedContext")
        self.assertIsNone(bail)
        self.assertIn("help.execute(sharedContext, null)", rewritten)

    def test_bails_when_the_expression_occurs_more_than_once(self):
        """两处内联 mock 是两个互相独立的实例，指向同一个字段会把它们合并。
        Two inline mocks are independent instances; one shared field would merge them."""
        source = (
            "@Test\n"
            "void test() {\n"
            "    Exchangers.bind(url, Mockito.mock(Replier.class));\n"
            "    Exchangers.bind(url, new Adapter(), Mockito.mock(Replier.class));\n"
            "}\n"
        )
        rewritten, bail = inline_mock_to_field(source, "Mockito.mock(Replier.class)", "sharedReplier")
        self.assertIsNone(rewritten)
        self.assertIn("independent mock instances", bail)

    def test_bails_when_the_expression_is_absent(self):
        source = "@Test\nvoid test() {\n    subject.run();\n}\n"
        rewritten, bail = inline_mock_to_field(source, "Mockito.mock(Foo.class)", "sharedFoo")
        self.assertIsNone(rewritten)
        self.assertIn("not found", bail)


if __name__ == "__main__":
    unittest.main()
