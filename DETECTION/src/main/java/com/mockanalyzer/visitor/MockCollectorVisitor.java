package com.mockanalyzer.visitor;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import com.mockanalyzer.model.MockInfo;
import com.mockanalyzer.sequencesParser.MockAnalyzer;

import java.util.List;

/**
 * Entry visitor for traversing source files and collecting mock usage information.
 * Delegates logic to MockAnalyzer and MockStatementRecorder.
 */
public class MockCollectorVisitor extends VoidVisitorAdapter<Void> {
    private final MockAnalyzer analyzer;

    public MockCollectorVisitor(String filePath) {
        this.analyzer = new MockAnalyzer(filePath);
    }

    @Override
    public void visit(CompilationUnit cu, Void arg) {
        analyzer.extractPackageName(cu);
        super.visit(cu, arg);
    }

    @Override
    public void visit(ClassOrInterfaceDeclaration clazz, Void arg) {
        analyzer.setCurrentClass(clazz);
        super.visit(clazz, arg);
    }

    public List<MockInfo> getFinalMockList() {
        return analyzer.getFinalMocks();
    }
}
