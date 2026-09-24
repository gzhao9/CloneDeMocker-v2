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
    void excludesMocksPassedDirectlyAsMethodArguments() throws Exception {
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
                        service.setDependency((Dependency) mock(Dependency.class));
                        service.setDependency((mock(Dependency.class)));
                    }
                }
                """);

        List<MockInfo> mocks = MockInfoExporter.analyzeProject(directory, false);

        // 仅检测有可替换名称的 mock：if 块内的 dependency 和局部变量 service。
        // 直接传给 setDependency 的 mock(Dependency.class) 不应成为重构候选。
        // Only mocks with a replaceable name are collected: nested `dependency` and
        // local `service`. mock(Dependency.class) passed to setDependency is excluded.
        assertEquals(2, mocks.size());
        assertTrue(mocks.stream().anyMatch(mock -> "dependency".equals(mock.variableName)
                && mock.statements.stream().anyMatch(stmt -> "STUBBING".equals(stmt.type))));
        assertTrue(mocks.stream().anyMatch(mock -> "service".equals(mock.variableName)));
        assertFalse(mocks.stream().anyMatch(mock -> mock.variableName != null && mock.variableName.startsWith("$inline")));
    }
}
