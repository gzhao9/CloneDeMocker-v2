package com.mockanalyzer.sequencesParser;

import java.util.*;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.CastExpr;
import com.github.javaparser.ast.expr.EnclosedExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.resolution.UnsolvedSymbolException;
import com.github.javaparser.resolution.types.ResolvedType;
import com.mockanalyzer.model.MockInfo;
import com.mockanalyzer.model.StatementInfo;

/**
 * Analyzes class and package-level context, and provides utility methods
 * for mock detection and type resolution.
 */
public class MockAnalyzer {
    private final String filePath;
    private String packageName;
    private String className;

    private List<MockInfo> globalVariables = new ArrayList<>();
    private List<MockInfo> finalMocks = new ArrayList<>();

    public MockAnalyzer(String filePath) {
        this.filePath = filePath;
    }

    public void extractPackageName(CompilationUnit cu) {
        this.packageName = cu.getPackageDeclaration()
                .map(pd -> pd.getNameAsString())
                .orElse("");
    }

    public void setCurrentClass(ClassOrInterfaceDeclaration clazz) {
        this.className = clazz.getNameAsString();

        // Step 1: 扫描“全局变量”(字段)
        scanGlobalVariables(clazz);
        // Step 2: 扫描方法
        scanMethods(clazz);
        // 此时所有方法扫描完毕
        // Step 3: 检查 globalVariables 的 statements 是否含 mock 相关内容
        // 将 methodVariables 中的 MockInfo 合并到 globalVariables
        for (MockInfo mockInfo : globalVariables) {
            boolean hasMockUsage = mockInfo.statements.stream().anyMatch(stmt -> stmt.isMockRelated);
            if (hasMockUsage) {
                boolean exists = finalMocks.stream().anyMatch(existing -> existing.isEqual(mockInfo));
                if (!exists) {
                    mockInfo.checkmockPattern();
                    finalMocks.add(mockInfo);
                }
            }
        }
    }

    private void scanGlobalVariables(ClassOrInterfaceDeclaration clazz) {
        clazz.getFields().forEach(field -> {
            field.getVariables().forEach(variable -> {
                MockInfo mockInfo = new MockInfo();
                mockInfo.variableName = variable.getNameAsString();
                mockInfo.variableType = variable.getType().asString();
                try {
                    ResolvedType resolvedType = variable.getType().resolve();
                    mockInfo.mockedClass = resolvedType.describe();
                } catch (UnsolvedSymbolException | StackOverflowError e) {
                    ResolutionDiagnostics.note(e);
                    mockInfo.mockedClass = variable.getType().asString();
                }

                mockInfo.classContext.packageName = this.packageName;
                mockInfo.classContext.filePath = this.filePath;
                mockInfo.classContext.className = this.className;

                StatementInfo statementInfo = new StatementInfo();
                statementInfo.code = field.toString();
                statementInfo.line = field.getBegin().map(pos -> pos.line).orElse(-1);
                statementInfo.locationContext.methodName = field.getMetaModel().getTypeName();
                statementInfo.locationContext.methodAnnotations = new ArrayList<>();
                statementInfo.type = "FIELD_DECLARATION"; // 默认类型

                // 1. Check annotations
                field.getAnnotations().forEach(annotation -> {
                    String annotationName = annotation.getNameAsString().toLowerCase();
                    if (annotationName.equals("mock")) {
                        statementInfo.type = "FIELD_MOCK_CREATION";
                        statementInfo.isMockRelated = true;
                    } else if (annotationName.equals("spy")) {
                        statementInfo.type = "FIELD_SPY_CREATION";
                        statementInfo.isMockRelated = true;
                    }
                    statementInfo.locationContext.methodAnnotations.add(annotation.getNameAsString());
                });

                // 2. 检查变量初始化（mock、spy 或普通初始化）
                if (variable.getInitializer().isPresent()) {
                    Expression initializer = variable.getInitializer().get();
                    if (CreationAnalyzer.isMockCreation(initializer)) {
                        statementInfo.type = "FIELD_MOCK_CREATION";
                        statementInfo.isMockRelated = true;
                    } else if (CreationAnalyzer.isSpyCreation(initializer)) {
                        statementInfo.type = "FIELD_SPY_CREATION";
                        statementInfo.isMockRelated = true;
                    } else if (!statementInfo.isMockRelated) {
                        statementInfo.type = "FIELD_INITIALIZATION"; // 普通赋值（无 @Mock/@Spy 注解）
                    }
                }

                mockInfo.statements.add(statementInfo);
                globalVariables.add(mockInfo);
            });
        });
    }

    private void scanMethods(ClassOrInterfaceDeclaration clazz) {
        clazz.getMethods().forEach(method -> {
            String methodName = method.getNameAsString();
            List<String> methodAnnotations = new ArrayList<>();
            String methodRawCode = method.toString();
            method.getAnnotations().forEach(annotation -> methodAnnotations.add(annotation.getNameAsString()));

            List<MockInfo> methodVariables = new ArrayList<>();

            method.getBody().ifPresent(body -> {
                // 递归遍历方法体内所有表达式语句，而不仅是顶层语句：
                // if/for/while/try/lambda 等嵌套块内的 mock 创建/打桩之前会被漏掉。
                // Walk every expression statement anywhere in the method body, not just
                // the top-level ones: mock creation/stubbing inside if/for/while/try/lambda
                // blocks was previously invisible to this scan.
                body.findAll(ExpressionStmt.class).forEach(statement -> {
                    StatementInfo statementInfo = new StatementInfo();
                    statementInfo.code = statement.toString();
                    statementInfo.line = statement.getBegin().map(pos -> pos.line).orElse(-1);
                    statementInfo.locationContext.methodName = methodName;
                    statementInfo.locationContext.methodAnnotations = methodAnnotations;
                    statementInfo.locationContext.methodRawCode = methodRawCode;

                    // Analyze the statement
                    {
                        var expression = statement.getExpression();

                        // Handle variable declarations
                        if (expression.isVariableDeclarationExpr()) {
                            expression.asVariableDeclarationExpr().getVariables().forEach(variable -> {
                                MockInfo mockInfo = new MockInfo();
                                mockInfo.variableName = variable.getNameAsString();
                                mockInfo.variableType = variable.getType().asString();
                                mockInfo.classContext.packageName = this.packageName;
                                mockInfo.classContext.filePath = this.filePath;
                                mockInfo.classContext.className = this.className;

                                // === 新增：为方法内局部 mock 解析 mockedClass ===
                                try {
                                    ResolvedType resolvedType = variable.getType().resolve();
                                    mockInfo.mockedClass = resolvedType.describe();
                                } catch (UnsolvedSymbolException | StackOverflowError e) {
                                    ResolutionDiagnostics.note(e);
                                    // 退化到变量声明的类型，保证不是 null
                                    mockInfo.mockedClass = mockInfo.variableType;
                                }

                                if (variable.getInitializer().isPresent()) {
                                    var initializer = variable.getInitializer().get();
                                    if (CreationAnalyzer.isMockCreation(initializer)) {
                                        statementInfo.type = "METHOD_MOCK_CREATION";
                                        statementInfo.isMockRelated = true;
                                    } else if (CreationAnalyzer.isSpyCreation(initializer)) {
                                        statementInfo.type = "METHOD_SPY_CREATION";
                                        statementInfo.isMockRelated = true;
                                    } else {
                                        statementInfo.type = "METHOD_VARIABLE_INITIALIZATION";
                                    }
                                } else {
                                    statementInfo.type = "METHOD_VARIABLE_DECLARATION";
                                }

                                mockInfo.statements.add(statementInfo);
                                methodVariables.add(mockInfo);
                            });
                        }

                        // Handle assignments
                        else if (expression.isAssignExpr()) {
                            var assignExpr = expression.asAssignExpr();
                            String targetName = assignExpr.getTarget().toString();

                            if (CreationAnalyzer.isMockCreation(assignExpr.getValue())) {
                                statementInfo.type = "ASSIGNMENT_MOCK";
                                statementInfo.isMockRelated = true;
                            } else if (CreationAnalyzer.isSpyCreation(assignExpr.getValue())) {
                                statementInfo.type = "ASSIGNMENT_SPY";
                                statementInfo.isMockRelated = true;
                            } else {
                                statementInfo.type = "ASSIGNMENT";
                            }

                            // Check if the target is in methodVariables or globalVariables
                            addStatementToMockInfo(targetName, statementInfo, methodVariables, globalVariables,
                                    expression);
                        }

                        // Handle verification
                        // Optional<String> verifyTargetOpt = getVerificationTargetVariable(expression);
                        else if (getVerificationTargetVariable(expression).isPresent()) {
                            String mockTarget = getVerificationTargetVariable(expression).get();
                            statementInfo.type = "VERIFICATION";
                            statementInfo.isMockRelated = true;

                            addStatementToMockInfo(mockTarget, statementInfo, methodVariables, globalVariables,
                                    expression);
                        } else {

                            // 尝试提取 stubbing 目标 mock 变量
                            Optional<String> mockTargetOpt = StubbingAnalyzer.getStubbingTargetVariable(expression);

                            if (mockTargetOpt.isPresent()) {
                                String mockTarget = mockTargetOpt.get();
                                statementInfo.type = "STUBBING";
                                statementInfo.isMockRelated = true;

                                // 添加 statementInfo 到对应的 MockInfo（先查 methodVariables，再查 globalVariables）
                                addStatementToMockInfo(mockTarget, statementInfo, methodVariables, globalVariables,
                                        expression);
                            }

                            // 非 stubbing、非 verify 的普通表达式
                            Set<String> allVars = extractAllVariableNames(expression);
                            if (!allVars.isEmpty()) {
                                statementInfo.type = "REFERENCE";

                                statementInfo.isMockRelated = false;
                                addStatementToMatchingMockInfos(allVars, statementInfo, methodVariables,
                                        globalVariables);
                            }
                        }

                    }
                });

                // 捕获未绑定到变量/字段的 mock、spy 创建。但直接作为另一个方法参数传入的
                // mock 不进入检测池：它没有可替换的名字/生命周期，提取它通常只会把
                // mock(...) 包成一个 helper，反而降低测试可读性。
                // Capture unbound mock/spy creations, except calls passed directly as an
                // argument to another method. The latter have no replaceable name or
                // lifecycle; extracting them normally just wraps mock(...) in a helper and
                // makes the test less readable.
                int[] inlineCounter = {0};
                body.findAll(MethodCallExpr.class).forEach(call -> {
                    boolean isMock = CreationAnalyzer.isMockCreation(call);
                    boolean isSpy = !isMock && CreationAnalyzer.isSpyCreation(call);
                    if (!isMock && !isSpy) {
                        return;
                    }
                    Node parent = call.getParentNode().orElse(null);
                    if (parent instanceof VariableDeclarator || parent instanceof AssignExpr) {
                        return; // 已经被声明/赋值分支处理过，避免重复计数
                    }
                    if (isDirectMethodArgument(call)) {
                        return;
                    }

                    inlineCounter[0]++;
                    String argType = call.getArguments().isEmpty() ? "Object"
                            : call.getArgument(0).toString().replace(".class", "");

                    MockInfo mockInfo = new MockInfo();
                    mockInfo.variableName = "$inline" + inlineCounter[0] + "(" + argType + ")";
                    mockInfo.variableType = argType;
                    mockInfo.mockedClass = argType;
                    mockInfo.classContext.packageName = this.packageName;
                    mockInfo.classContext.filePath = this.filePath;
                    mockInfo.classContext.className = this.className;

                    StatementInfo inlineStatement = new StatementInfo();
                    inlineStatement.code = call.toString();
                    inlineStatement.line = call.getBegin().map(pos -> pos.line).orElse(-1);
                    inlineStatement.locationContext.methodName = methodName;
                    inlineStatement.locationContext.methodAnnotations = methodAnnotations;
                    inlineStatement.locationContext.methodRawCode = methodRawCode;
                    inlineStatement.type = isMock ? "METHOD_MOCK_CREATION" : "METHOD_SPY_CREATION";
                    inlineStatement.isMockRelated = true;

                    mockInfo.statements.add(inlineStatement);
                    methodVariables.add(mockInfo);
                });
            });

            // 将 methodVariables 中的 MockInfo 合并到 globalVariables
            for (MockInfo mockInfo : methodVariables) {
                boolean hasMockUsage = mockInfo.statements.stream().anyMatch(stmt -> stmt.isMockRelated);
                if (hasMockUsage) {
                    boolean exists = finalMocks.stream().anyMatch(existing -> existing.isEqual(mockInfo));
                    if (!exists) {
                        mockInfo.checkmockPattern();
                        finalMocks.add(mockInfo);
                    }
                }
            }

        });
    }

    private boolean isDirectMethodArgument(MethodCallExpr call) {
        Node current = call;
        while (current.getParentNode().isPresent()) {
            Node parent = current.getParentNode().get();
            if (parent instanceof MethodCallExpr) {
                return ((MethodCallExpr) parent).getArguments().contains(current);
            }
            // Parentheses and casts do not give the inline mock an independent lifecycle.
            if (parent instanceof EnclosedExpr || parent instanceof CastExpr) {
                current = parent;
                continue;
            }
            return false;
        }
        return false;
    }

    /**
     * 将 statementInfo 添加到对应的 MockInfo 中
     */
    private void addStatementToMockInfo(String mockTarget,
            StatementInfo rawstatementInfo,
            List<MockInfo> methodVariables,
            List<MockInfo> globalVariables,
            Expression expressionForAbstract) {

        StatementInfo statementInfo = rawstatementInfo.copy();

        // 先查局部变量
        Optional<MockInfo> localMockOpt = methodVariables.stream()
                .filter(var -> var.variableName.equals(mockTarget))
                .findFirst();

        if (localMockOpt.isPresent()) {
            MockInfo mock = localMockOpt.get();

            // 如果是 STUBBING 类型，进行抽象化
            if ("STUBBING".equals(statementInfo.type)) {
                statementInfo.abstractedStatement = StubbingAnalyzer.abstractStubbingStatement(expressionForAbstract,
                        mock);
            }

            if (!isDuplicateStatement(mock, statementInfo)) {
                mock.statements.add(statementInfo);
            }
            return;
        }

        // 再查全局变量
        Optional<MockInfo> globalMockOpt = globalVariables.stream()
                .filter(var -> var.variableName.equals(mockTarget))
                .findFirst();

        if (globalMockOpt.isPresent()) {
            MockInfo mock = globalMockOpt.get();

            // 如果是 STUBBING 类型，进行抽象化
            if ("STUBBING".equals(statementInfo.type)) {
                statementInfo.abstractedStatement = StubbingAnalyzer.abstractStubbingStatement(expressionForAbstract,
                        mock);
            }

            if (!isDuplicateStatement(mock, statementInfo)) {
                mock.statements.add(statementInfo);
            }
        }
    }

    /**
     * 判断是否是 verification 调用，并提取 verify 的 mock 变量名
     * 示例：verify(service).foo() ➜ 提取 service
     */
    private Optional<String> getVerificationTargetVariable(Expression expr) {
        if (!expr.isMethodCallExpr())
            return Optional.empty();

        try {
            MethodCallExpr current = expr.asMethodCallExpr();

            while (true) {
                if (current.getNameAsString().equals("verify")) {
                    if (!current.getArguments().isEmpty()) {
                        Expression arg = current.getArgument(0);
                        String varName = StubbingAnalyzer.extractVariableName(arg);
                        if (varName != null) {
                            return Optional.of(varName);
                        }
                    }
                    break;
                }

                // 继续向上追踪 scope
                if (current.getScope().isPresent() && current.getScope().get().isMethodCallExpr()) {
                    current = current.getScope().get().asMethodCallExpr();
                } else {
                    break;
                }
            }

        } catch (Exception e) {
            // 解析失败容错
        }

        return Optional.empty();
    }

    /**
     * 检查是否已有相同 statementInfo（三项内容相同）
     */
    private boolean isDuplicateStatement(MockInfo mockInfo, StatementInfo newInfo) {
        return mockInfo.statements.stream().anyMatch(existing -> existing.code.equals(newInfo.code) &&
                existing.line == newInfo.line &&
                existing.code.equals(newInfo.code));
    }

    private void addStatementToMatchingMockInfos(Set<String> varNames,
            StatementInfo statementInfo,
            List<MockInfo> methodVariables,
            List<MockInfo> globalVariables) {
        for (String varName : varNames) {
            methodVariables.stream()
                    .filter(mock -> mock.variableName.equals(varName))
                    .filter(mock -> !isDuplicateStatement(mock, statementInfo))
                    .forEach(mock -> mock.statements.add(statementInfo));

            globalVariables.stream()
                    .filter(mock -> mock.variableName.equals(varName))
                    .filter(mock -> !isDuplicateStatement(mock, statementInfo))
                    .forEach(mock -> mock.statements.add(statementInfo));
        }
    }

    /**
     * 递归提取表达式中所有变量名（NameExpr 和 FieldAccessExpr）
     * 示例：foo(bar, obj.baz) ➜ bar, baz
     */
    private Set<String> extractAllVariableNames(Expression expr) {
        Set<String> variableNames = new HashSet<>();

        expr.walk(node -> {
            if (node instanceof NameExpr) {
                variableNames.add(((NameExpr) node).getNameAsString());
            } else if (node instanceof FieldAccessExpr) {
                variableNames.add(((FieldAccessExpr) node).getNameAsString());
            }
        });

        return variableNames;
    }

    public List<MockInfo> getFinalMocks() {
        return finalMocks;
    }
}
