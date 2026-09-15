package com.mockanalyzer.model;

import java.util.*;

/**
 * 存储单条语句信息
 */
// public class StatementInfo {
// String type; // "ASTNodeType" => 你可改成更细的 "DECLARATION" / "STUBBING" /
// "ASSIGNMENT" 等
// String code;
// int line;

// // 方法上下文
// String methodName;
// List<String> methodAnnotations = new ArrayList<>();
// }
public class StatementInfo {
    public String code;
    public int line;
    public String type;
    public String locate = "";
    public String abstractedStatement = "";
    public boolean isMockRelated = false;
    public boolean isShareable = false; // 是否可共享的语句

    // 其他字段如 locationContext
    public LocationContext locationContext = new LocationContext();

    public static class LocationContext {
        public String methodName;
        public List<String> methodAnnotations;
        public String methodRawCode;
        // Getter & Setter
    }
    public boolean isEqual(StatementInfo other) {
        if (other == null) {
            return false;
        }
        return Objects.equals(this.code, other.code)
            && this.line == other.line
            && Objects.equals(this.type, other.type)
            && Objects.equals(this.locate, other.locate)
            && Objects.equals(this.abstractedStatement, other.abstractedStatement)
            && this.isMockRelated == other.isMockRelated
            && this.isShareable == other.isShareable
            && Objects.equals(this.locationContext.methodName, other.locationContext.methodName)
            && Objects.equals(this.locationContext.methodAnnotations, other.locationContext.methodAnnotations)
            && Objects.equals(this.locationContext.methodRawCode, other.locationContext.methodRawCode);
    }
    public StatementInfo copy() {
        StatementInfo copy = new StatementInfo();
        copy.code = this.code;
        copy.line = this.line;
        copy.type = this.type;
        copy.isMockRelated = this.isMockRelated;
        copy.abstractedStatement = this.abstractedStatement;

        copy.locationContext.methodName = this.locationContext.methodName;
        copy.locationContext.methodAnnotations = new ArrayList<>(this.locationContext.methodAnnotations);
        copy.locationContext.methodRawCode = this.locationContext.methodRawCode;

        return copy;
    }

    public void checkLocate() {
        String methodName = locationContext.methodName != null ? locationContext.methodName : "";
        List<String> annotations = locationContext.methodAnnotations != null ? locationContext.methodAnnotations
                : Collections.emptyList();

        // 条件 1：方法名包含"test" (不区分大小写)
        if (methodName.toLowerCase().contains("test")) {
            this.locate = "Test Case";
            return;
        }

        // 条件 2：注解包含 @test (不区分大小写)
        Set<String> forbiddenAnnotations = new HashSet<>(Arrays.asList(
                "@test", "test"// 兼容有无 @ 的情况
        ));
        for (String ann : annotations) {
            String lowerAnn = ann.toLowerCase().replaceAll("@", "");
            for (String eachAnn : forbiddenAnnotations) {
                if (lowerAnn.toLowerCase().contains(eachAnn)) {
                    this.locate = "Test Case";
                    return;
                }
            }
        }

        // 条件 3：包含 Before 关键词注解 (例如 @BeforeEach)
        for (String ann : annotations) {
            if (ann.toLowerCase().contains("before")) {
                this.locate = "@Before";
                return;
            }
        }

        // 条件 4：包含 After 关键词注解 (例如 @After)
        for (String ann : annotations) {
            if (ann.toLowerCase().contains("after")) {
                this.locate = "@After";
                return;
            }
        }

        // 条件 5：方法名包含 helper 关键词
        String lowerMethodName = methodName.toLowerCase();
        String[] helperKeywords = { "mock", "init", "create", "setup", "prepare", "build", "initialize", "reset",
                "configure" };
        for (String kw : helperKeywords) {
            if (lowerMethodName.contains(kw)) {
                this.locate = "Helper Method";
                return;
            }
        }

        // 条件 6：字段声明
        if ("FieldDeclaration".equals(methodName)) {
            this.locate = "Attribute";
            return;
        }

        // 条件 7：包含 Override 关键词注解 (例如 @Override)
        for (String ann : annotations) {
            if (ann.toLowerCase().contains("override")) {
                this.locate = "@Override";
                return;
            }
        }

        // 默认分类
        this.locate = "Other Methods";
    }

}
