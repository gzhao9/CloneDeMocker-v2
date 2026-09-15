package com.mockanalyzer.sequencesParser;

import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MethodCallExpr;

public class CreationAnalyzer {
    public static boolean isMockCreation(Expression expression) {
        if (!expression.isMethodCallExpr())
            return false;

        try {
            // 精确匹配方法名，而不是前缀匹配：mockConstruction/mockConstructionWithAnswer/
            // mockStatic/mockingDetails 等方法的限定名同样以 "org.mockito.Mockito.mock" 开头，
            // 但它们返回的是 MockedStatic/MockedConstruction 拦截句柄，不是普通 mock 对象，
            // 用 startsWith 会把它们错误地当成 mock(...) 创建。
            // Exact match, not a prefix match: mockConstruction/mockConstructionWithAnswer/
            // mockStatic/mockingDetails also have qualified names starting with
            // "org.mockito.Mockito.mock", but they return MockedStatic/MockedConstruction
            // interceptor handles, not a plain mock object; startsWith would misclassify them.
            String qualifiedName = expression.asMethodCallExpr().resolve().getQualifiedName();
            return qualifiedName.equals("org.mockito.Mockito.mock");
        } catch (Exception e) {
            // 无依赖源码仍需可检测：仅接受静态导入形式 mock(...) 或 Mockito.mock(...)。
            // Keep source-only detection: accept static-import mock(...) or Mockito.mock(...).
            return hasMockitoShape(expression.asMethodCallExpr(), "mock");
        }
    }

    public static boolean isSpyCreation(Expression expression) {
        if (!expression.isMethodCallExpr())
            return false;

        try {
            String qualifiedName = expression.asMethodCallExpr().resolve().getQualifiedName();
            return qualifiedName.equals("org.mockito.Mockito.spy");
        } catch (Exception e) {
            return hasMockitoShape(expression.asMethodCallExpr(), "spy");
        }
    }

    private static boolean hasMockitoShape(MethodCallExpr call, String methodName) {
        if (!call.getNameAsString().equals(methodName)) return false;
        if (call.getScope().isEmpty()) {
            return call.findCompilationUnit().map(unit -> unit.getImports().stream().anyMatch(importDeclaration ->
                    importDeclaration.isStatic()
                            && importDeclaration.getNameAsString().startsWith("org.mockito.Mockito")
                            && (importDeclaration.isAsterisk()
                                || importDeclaration.getNameAsString().endsWith("." + methodName))))
                    .orElse(false);
        }
        String scope = call.getScope().get().toString();
        return scope.equals("Mockito") || scope.equals("org.mockito.Mockito");
    }

}
