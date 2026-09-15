package com.mockanalyzer.sequencesParser;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.mockanalyzer.model.MockInfo;

public class StubbingAnalyzer {
    // 从表达式中提取变量名，例如 mock 对象名
    public static String extractVariableName(Expression expr) {
        if (expr == null)
            return null;

        if (expr.isNameExpr()) {
            return expr.asNameExpr().getNameAsString();
        } else if (expr.isFieldAccessExpr()) {
            return expr.asFieldAccessExpr().getNameAsString();
        } else if (expr.isMethodCallExpr()) {
            return extractVariableName(expr.asMethodCallExpr().getScope().orElse(null));
        }

        return null;
    }

    // 遍历链式调用，找到 when() 或 given()：
    private static Optional<MethodCallExpr> findWhenOrGivenCall(MethodCallExpr call) {
        MethodCallExpr current = call;
        while (true) {
            String methodName = current.getNameAsString();
            if (methodName.equals("when") || methodName.equals("given")) {
                return Optional.of(current);
            }
            if (current.getScope().isPresent() && current.getScope().get().isMethodCallExpr()) {
                current = current.getScope().get().asMethodCallExpr();
            } else {
                break;
            }
        }
        return Optional.empty();
    }

    // 提取 mock 对象名（when/given 的参数）
    private static Optional<String> extractMockFromWhenOrGiven(MethodCallExpr whenOrGivenCall) {
        if (!whenOrGivenCall.getArguments().isEmpty()) {
            Expression arg = whenOrGivenCall.getArgument(0);

            // case 1: when(mock.method())
            if (arg.isMethodCallExpr()) {
                MethodCallExpr innerCall = arg.asMethodCallExpr();
                if (innerCall.getScope().isPresent()) {
                    return Optional.ofNullable(extractVariableName(innerCall.getScope().get()));
                }
            }

            // case 2: when(mock)
            else {
                return Optional.ofNullable(extractVariableName(arg));
            }
        }
        return Optional.empty();
    }

    // 整合判断与提取
    public static Optional<String> getStubbingTargetVariable(Expression expr) {
        if (!expr.isMethodCallExpr())
            return Optional.empty();

        try {
            MethodCallExpr call = expr.asMethodCallExpr();
            Optional<MethodCallExpr> whenOrGivenOpt = findWhenOrGivenCall(call);
            if (whenOrGivenOpt.isPresent()) {
                return extractMockFromWhenOrGiven(whenOrGivenOpt.get());
            }
        } catch (Exception e) {
            // Resolve 失败
        }

        return Optional.empty();
    }

    /**
     * 将 BDDMockito 风格方法转换为 Mockito 标准风格方法
     * e.g., given ➜ when, willReturn ➜ doReturn, willReturn ➜ thenReturn
     */
    private static String normalizeMockitoMethodName(String methodName) {
        Map<String, String> methodMap = new HashMap<>();

        // given ➜ when
        methodMap.put("given", "when");

        // BDD ➜ doXXX
        methodMap.put("willAnswer", "doAnswer");
        methodMap.put("willCallRealMethod", "doCallRealMethod");
        methodMap.put("willNothing", "doNothing");
        methodMap.put("willReturn", "doReturn");
        methodMap.put("willThrow", "doThrow");

        // BDD ➜ thenXXX
        methodMap.put("will", "then");
        methodMap.put("willAnswer", "thenAnswer");
        methodMap.put("willCallRealMethod", "thenCallRealMethod");
        methodMap.put("willReturn", "thenReturn");
        methodMap.put("willThrow", "thenThrow");

        return methodMap.getOrDefault(methodName, methodName); // 默认返回原名
    }

    public static String abstractStubbingStatement(Expression expression, MockInfo mock) {

        if (!expression.isMethodCallExpr())
            return expression.toString();

        try {
            MethodCallExpr fullCall = expression.asMethodCallExpr();
            Deque<String> chain = new ArrayDeque<>();

            // Step 1: 找到 when/given 调用
            Optional<MethodCallExpr> whenCallOpt = findWhenOrGivenCall(fullCall);
            MethodCallExpr current = fullCall;

            // Step 2: 遍历链式结构
            while (true) {
                String rawMethod = current.getNameAsString();
                String methodName = normalizeMockitoMethodName(rawMethod);
                StringBuilder builder = new StringBuilder();
                builder.append(methodName).append("(");

                // 特别处理 when/given：抽象化为完整调用
                if (methodName.equals("when") && current == whenCallOpt.orElse(null)) {
                    Expression whenArg = current.getArgument(0);

                    if (whenArg.isMethodCallExpr()) {
                        MethodCallExpr stubMethod = whenArg.asMethodCallExpr();

                        // 提取 mock 类型
                        String mockType = mock.mockedClass;
                        if (mockType == null || "null".equals(mockType)) {
                            mockType = mock.variableType != null ? mock.variableType : "java.lang.Object";
                        }
                        // if (stubMethod.getScope().isPresent()) {
                        // try {
                        // mockType = stubMethod.getScope().get().calculateResolvedType().describe();
                        // } catch (Exception ignored) {
                        // }
                        // }
                        // ---------- 修正：向上追溯到链式调用的根部 ----------
                        Expression rootScope = stubMethod.getScope().orElse(null);
                        while (rootScope != null && rootScope.isMethodCallExpr()) {
                            rootScope = rootScope.asMethodCallExpr().getScope().orElse(null);
                        }
                        if (rootScope != null) {
                            try {
                                mockType = rootScope.calculateResolvedType().describe(); // 真正的 mock 对象类型
                            } catch (Exception ignored) {
                                // 保持原值
                            }
                        }
                        // ---------- 结束修正 ----------

                        // 提取参数类型
                        List<String> paramTypes = new ArrayList<>();
                        for (Expression param : stubMethod.getArguments()) {
                            boolean isMockitoMatcher = false;
                            if (param.isMethodCallExpr()) {
                                MethodCallExpr paramAsMethodCall = param.asMethodCallExpr();
                                try {
                                    // Attempt to resolve the method call
                                    String qualifiedSignature = paramAsMethodCall.resolve().getQualifiedSignature();
                                    // Check if the method belongs to Mockito's argument matchers packages
                                    if (qualifiedSignature.startsWith("org.mockito.ArgumentMatchers.") ||
                                            qualifiedSignature.startsWith("org.mockito.Matchers.")) { // Matchers is
                                                                                                      // deprecated but
                                                                                                      // might still be
                                                                                                      // used
                                        isMockitoMatcher = true;
                                    }
                                } catch (Exception e) {
                                    // Resolution failed, fallback to name-based check or treat as non-matcher
                                    // For robustness, we can still include a simplified name check as a fallback
                                    String paramName = paramAsMethodCall.getNameAsString();
                                    if (paramName.startsWith("any") || paramName.equals("eq")
                                            || paramName.equals("isA")) {
                                        // A minimal set of common matchers if resolution fails
                                        isMockitoMatcher = true;
                                    }
                                }
                            }

                            if (isMockitoMatcher) {
                                paramTypes.add(param.toString()); // Use toString() for matchers
                            } else {
                                try {
                                    paramTypes.add(param.calculateResolvedType().describe());
                                } catch (Exception e) {
                                    // Fallback if type resolution fails for non-matchers
                                    paramTypes.add(param.toString());
                                }
                            }
                        }
                        // 2) 从 rootScope 到原 stubMethod 收集完整方法链
                        StringBuilder methodCallChain = new StringBuilder();
                        MethodCallExpr cur = stubMethod;
                        methodCallChain.insert(0, cur.getNameAsString()); // 先放最末方法
                        while (cur.getScope().isPresent() && cur.getScope().get().isMethodCallExpr()) {
                            cur = cur.getScope().get().asMethodCallExpr();
                            methodCallChain.insert(0, cur.getNameAsString() + ".");
                        }

                        builder.append(mockType)
                                .append(".")
                                .append(methodCallChain)
                                .append("(")
                                .append(String.join(", ", paramTypes))
                                .append(")");
                    } else {
                        // fallback
                        builder.append(mock.mockedClass);
                    }

                } else {
                    // 其他调用（如 thenReturn/doThrow），只解析参数类型
                    List<String> argTypes = new ArrayList<>();
                    for (Expression arg : current.getArguments()) {
                        try {
                            argTypes.add(arg.calculateResolvedType().describe());
                        } catch (Exception e) {
                            String simpleType; // 兜底结果

                            /* ---------- ① 尝试解析返回类型（MethodCallExpr 专用） ---------- */
                            if (arg.isMethodCallExpr()) {
                                try {
                                    ResolvedMethodDeclaration r = arg.asMethodCallExpr().resolve(); // 解析失败直接跳过
                                    simpleType = r.getReturnType().describe(); // 例如 io.netty.channel.ChannelFuture

                                } catch (Exception ignored) {
                                    simpleType = null; // 留给下一步
                                }
                            } else {
                                /* ---------- ② 若不是方法调用，再退回 calculateResolvedType() ---------- */
                                try {
                                    simpleType = arg.calculateResolvedType().describe();
                                } catch (Exception ignored) {
                                    simpleType = null;
                                }
                            }

                            /* ---------- ③ 最终仍失败，保留源码文本 ---------- */
                            if (simpleType == null) {
                                simpleType = arg.toString(); // 如 channel.newSucceededFuture()
                            }

                            argTypes.add(simpleType);
                        }
                    }
                    builder.append(String.join(", ", argTypes));
                }

                builder.append(")");
                chain.addFirst(builder.toString());

                if (current.getScope().isPresent() && current.getScope().get().isMethodCallExpr()) {
                    current = current.getScope().get().asMethodCallExpr();
                } else {
                    break;
                }
            }

            return String.join(".", chain);

        } catch (Exception e) {
            return expression.toString();
        }
    }
}