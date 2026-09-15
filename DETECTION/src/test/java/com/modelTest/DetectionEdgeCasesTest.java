package com.modelTest;

import com.github.javaparser.StaticJavaParser;
import com.google.gson.JsonParser;
import com.mockanalyzer.exporter.MockCloneExporter;
import com.mockanalyzer.exporter.MockInfoExporter;
import com.mockanalyzer.model.MockInfo;
import com.mockanalyzer.sequencesParser.CreationAnalyzer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class DetectionEdgeCasesTest {
    @TempDir Path directory;

    @Test
    void detectsMockitoCreationWithoutResolvedDependencies() {
        var unit = StaticJavaParser.parse("import static org.mockito.Mockito.mock; class T { void t() { Object x = mock(Object.class); } }");
        var importedCall = unit.findFirst(com.github.javaparser.ast.expr.MethodCallExpr.class).orElseThrow();
        assertTrue(CreationAnalyzer.isMockCreation(importedCall));
        assertTrue(CreationAnalyzer.isMockCreation(StaticJavaParser.parseExpression("Mockito.mock(Service.class)")));
        assertFalse(CreationAnalyzer.isMockCreation(StaticJavaParser.parseExpression("mock(Service.class)")));
        assertFalse(CreationAnalyzer.isMockCreation(StaticJavaParser.parseExpression("factory.mock(Service.class)")));
    }

    @Test
    @Timeout(2)
    void emptySelectionReturnsAnEmptyResult() throws Exception {
        Path output = directory.resolve("empty.json");
        MockCloneExporter.exportClones(Collections.emptyList(), output.toString(), Collections.emptySet());
        var json = JsonParser.parseString(Files.readString(output)).getAsJsonObject();
        assertEquals(0, json.getAsJsonArray("detectedMockObjects").size());
        assertEquals(0, json.getAsJsonObject("detectedMockClones").size());
    }

    @Test
    void detectsMocksInsideNestedBlocksAndInlineArguments() throws Exception {
        Path src = Files.createDirectories(directory.resolve("src/test/java/demo"));
        Files.writeString(src.resolve("NestedAndInlineTest.java"), """
                package demo;
                import static org.mockito.Mockito.mock;
                import static org.mockito.Mockito.when;

                class NestedAndInlineTest {
                    interface Dependency { String load(); }
                    interface Service { void setDependency(Dependency dependency); }

                    void testNestedMock() {
                        if (true) {
                            Dependency dependency = mock(Dependency.class);
                            when(dependency.load()).thenReturn("nested");
                        }
                    }

                    void testInlineMock() {
                        Service service = mock(Service.class);
                        service.setDependency(mock(Dependency.class));
                    }
                }
                """);

        List<MockInfo> mocks = MockInfoExporter.analyzeProject(directory, false);

        // 3 个 Mock Object：if 块内的 dependency、testInlineMock 里声明的 service、
        // 以及从未绑定变量、直接作为参数传入的内联 mock(Dependency.class)。
        // 3 mock objects: the nested `dependency` inside the if-block, the declared
        // `service`, and the inline mock(Dependency.class) passed straight as an argument.
        assertEquals(3, mocks.size());
        assertTrue(mocks.stream().anyMatch(mock -> "dependency".equals(mock.variableName)
                && mock.statements.stream().anyMatch(stmt -> "STUBBING".equals(stmt.type))));
        assertTrue(mocks.stream().anyMatch(mock -> "service".equals(mock.variableName)));
        assertTrue(mocks.stream().anyMatch(mock -> mock.variableName != null && mock.variableName.startsWith("$inline")
                && "Dependency".equals(mock.mockedClass)));
    }
}
