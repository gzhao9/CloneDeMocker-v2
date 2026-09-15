package com.mockanalyzer.model;

import java.util.*;

/**
 * 存储 Mock 相关信息
 * Stores information about a mock object
 */

public class MockInfo {
    public int rawMockObjectId;
    public String variableName;
    public String variableType;
    public String mockedClass;
    public boolean isReuseableMock = false;
    public String mockPattern = "";
    // Indicates whether this is a mock or a spy. Possible values: "mock", "spy"
    public String mockRole = "";

    // 类上下文
    // ClassContext
    public ClassContext classContext = new ClassContext();

    // locationContext
    public List<StatementInfo> statements = new ArrayList<>();

    public static class ClassContext {
        public String packageName;
        public String filePath;
        public String className;
        // Getter & Setter
    }

    public void checkmockPattern() {
        Map<String, Map<String, Set<String>>> categoryMap = new LinkedHashMap<>() {
            {
                put("creation", new LinkedHashMap<>());
                put("stubbing", new LinkedHashMap<>());
                put("verification", new LinkedHashMap<>());
            }
        };

        this.isReuseableMock = false;

        for (StatementInfo stmt : statements) {

            try {
                stmt.checkLocate();
            } catch (Exception e) {
                stmt.locate = "Other Methods";
            }
            final String type = stmt.type.toUpperCase();
            final String locate = stmt.locate;
            final String methodName = stmt.locationContext.methodName;

            // ===== 属性识别逻辑 =====
            final boolean isFieldOperation = type.equals("FIELD_MOCK_CREATION")
                    || type.equals("FIELD_SPY_CREATION")
                    || type.equals("FIELD_MOCK_CREATION");
            if (isFieldOperation)
                this.isReuseableMock = true;

            // ===== 分类处理逻辑 =====
            switch (type) {
                case "FIELD_DECLARATION":
                case "FIELD_MOCK_CREATION":
                case "FIELD_SPY_CREATION":
                case "DECLARATION":
                case "ASSIGNMENT":
                case "ASSIGNMENT_MOCK":
                case "ASSIGNMENT_SPY":
                case "METHOD_MOCK_CREATION":
                case "METHOD_SPY_CREATION":
                    handleCreation(categoryMap.get("creation"), stmt, this.isReuseableMock);
                    break;

                case "STUBBING":
                    updateLocationCount(categoryMap.get("stubbing"), locate, methodName);
                    break;

                case "VERIFICATION":
                    updateLocationCount(categoryMap.get("verification"), locate, methodName);
                    break;
            }
        }

        this.mockPattern = formatPatternOutput(categoryMap);
        this.mockRole = isSpy() ? "spy" : "mock";
    }
    public boolean isSpy() {
        for (StatementInfo stmt : statements) {
            if (stmt.type != null && stmt.type.toLowerCase().contains("mock")) {
            return false;
            }
        }
        return true;
    }

    private void handleCreation(
            Map<String, Set<String>> creationMap,
            StatementInfo stmt,
            boolean isGlobalField) {
        String key = null;

        switch (stmt.type.toUpperCase()) {
            case "FIELD_DECLARATION":
                key = "Declaration Attribute";
                break;

            case "FIELD_MOCK_CREATION":
            case "FIELD_SPY_CREATION":
                key = "Attribute Mock Creation";
                break;

            case "METHOD_MOCK_CREATION":
            case "METHOD_SPY_CREATION":
            case "ASSIGNMENT_MOCK":
            case "ASSIGNMENT_SPY":
                if (stmt.locate.equals("@Before")) {
                    key = "Global Init in @Before";
                    this.isReuseableMock = true;
                } else {
                    key = isGlobalField ? String.format("Lazy-init via %s", stmt.locate)
                            : String.format("Local Assignment in %s", stmt.locate);
                }
                break;

            case "DECLARATION":
                // 明确区分本地声明场景
                key = String.format("Local Mock Creation in %s",
                        stmt.locate.equals("Helper Method") ? "Helper" : stmt.locate);
                break;
        }

        if (key != null) {
            updateLocationCount(creationMap, key, stmt.locationContext.methodName);
        }
    }

    private void updateLocationCount(
            Map<String, Set<String>> map,
            String locationType,
            String methodName) {
        Set<String> methods = map.computeIfAbsent(locationType, k -> new HashSet<>());
        methods.add(methodName);
    }

    private String formatPatternOutput(Map<String, Map<String, Set<String>>> categoryMap) {
        StringBuilder sb = new StringBuilder();

        categoryMap.forEach((category, details) -> {
            if (category.equals("creation") &&
                    details.keySet().containsAll(Arrays.asList("Declaration Attribute", "Global Init in @Before")) &&
                    details.size() == 2) {
                // 合并为 Attribute Mock Creation
                Map<String, Set<String>> mergedDetails = new LinkedHashMap<>();
                mergedDetails.put("Attribute Mock Creation", new HashSet<>());
                details = mergedDetails; // 替换原始数据
            }
            sb.append(category.substring(0, 1).toUpperCase())
                    .append(category.substring(1))
                    .append(":\n");

            if (details.isEmpty()) {
                sb.append("— None\n");
            } else {
                details.forEach((desc, methods) -> {
                    // String times = methods.size() > 1 ? "Multiple Times" : "Single Time";
                    sb.append("— ").append(desc).append("\n");
                    // .append(" (").append(times).append(")\n");
                });
            }
            sb.append("\n");
        });

        return sb.toString().trim();
    }

    // 以下是一些方法，用于格式化输出
    // Print the mock sequence in a readable format.
    public List<MockSequence> toMockSequences() {

        // 1) 标记所有要保留的语句（包括 stubbing/verification + 邻居）
        // 用行号或直接用引用保存都行，这里用行号
        Set<Integer> linesToKeep = new HashSet<>();

        // 遍历 statements
        for (int i = 0; i < statements.size(); i++) {
            StatementInfo stmt = statements.get(i);
            stmt.checkLocate();
            String upperType = stmt.type.toUpperCase();

            // 如果是 STUBBING 或 VERIFICATION，则加上它自己及它的邻居
            if (!"REFERENCE".equals(upperType)) {
                linesToKeep.add(stmt.line);
                linesToKeep.add(stmt.line - 1); // 前一行
                linesToKeep.add(stmt.line + 1); // 后一行
            }
        }

        // 注意：并不是所有语句都需要邻居，比如 CREATION 本身也可能想要保留，但它并不需要把邻居拿进来
        // 因为你只要求 stubbing / verification 才要邻居

        // 2) 分组：shareable vs. testCase
        // key = test method name, value=此方法的语句
        List<StatementInfo> shareableList = new ArrayList<>();
        Map<String, List<StatementInfo>> testCaseMap = new LinkedHashMap<>();

        for (StatementInfo stmt : statements) {
            if (isShareableLocate(stmt.locate)) {
                shareableList.add(stmt);
            } else if ("Test Case".equals(stmt.locate) && linesToKeep.contains(stmt.line)) {
                String methodName = stmt.locationContext.methodName;
                testCaseMap.computeIfAbsent(methodName, k -> new ArrayList<>()).add(stmt);
            }
        }

        // 3) 针对每个测试方法，构造一个 MockSequences
        List<MockSequence> result = new ArrayList<>();
        for (Map.Entry<String, List<StatementInfo>> entry : testCaseMap.entrySet()) {
            String testMethodName = entry.getKey();
            List<StatementInfo> testStmts = entry.getValue();
            String testMethodRawCode = "";
            if (!testStmts.isEmpty() && testStmts.get(0).locationContext != null) {
                testMethodRawCode = testStmts.get(0).locationContext.methodRawCode;
            }

            // 创建一个新的 MockSequences
            MockSequence seq = new MockSequence();
            seq.mockRole = this.mockRole;
            seq.mockObjectId = this.rawMockObjectId;
            seq.variableName = this.variableName;
            seq.variableType = this.variableType;
            seq.mockedClass =  this.mockedClass;            
            seq.packageName = this.classContext.packageName;
            seq.filePath = this.classContext.filePath;
            seq.className = this.classContext.className;
            seq.isReuseableMock = this.isReuseableMock;
            seq.testMethodName = testMethodName;
            seq.testMethodRawCode = testMethodRawCode;

            // 3.1) 先处理 shareableList
            for (StatementInfo s : shareableList) {
                addToMockSequences(seq, s);
            }

            // 3.2) 再处理测试方法语句
            for (StatementInfo s : testStmts) {
                addToMockSequences(seq, s);
            }

            result.add(seq);
        }

        return result;
    }

    /**
     * 判断 locate 是否是共享类型
     */
    private boolean isShareableLocate(String locate) {
        // 你可按需扩展
        return Arrays.asList("Attribute", "@Before", "@After", "Helper Method").contains(locate);
    }

    /**
     * 将某条语句放入MockSequences的 map。
     */
    private void addToMockSequences(MockSequence seq, StatementInfo stmt) {
        boolean isShareable = isShareableLocate(stmt.locate);
        stmt.isShareable = isShareable;
        Map<Integer, String> linesMap = isShareable ? seq.shareableMockLines : seq.testMockLines;

        linesMap.put(stmt.line, stmt.code);
        seq.rawStatementInfo.put(stmt.line, stmt);

        // 如果是 stubbing，放 abstracted
        if ("STUBBING".equalsIgnoreCase(stmt.type)) {
            seq.abstractedStatement.put(stmt.line, stmt.abstractedStatement);
        }
    }
    public boolean isEqual(MockInfo other) {
        if (other == null) return false;
        if (this == other) return true;

        boolean basicEqual = Objects.equals(this.variableName, other.variableName)
                && Objects.equals(this.variableType, other.variableType)
                && Objects.equals(this.mockedClass, other.mockedClass)
                && this.isReuseableMock == other.isReuseableMock
                && Objects.equals(this.mockRole, other.mockRole)
                && Objects.equals(this.classContext.packageName, other.classContext.packageName)
                && Objects.equals(this.classContext.filePath, other.classContext.filePath)
                && Objects.equals(this.classContext.className, other.classContext.className);

        if (!basicEqual) return false;

        if (this.statements.size() != other.statements.size()) return false;
        for (int i = 0; i < this.statements.size(); i++) {
            StatementInfo s1 = this.statements.get(i);
            StatementInfo s2 = other.statements.get(i);
            if (s1 == null && s2 == null) continue;
            if (s1 == null || s2 == null) return false;
            if (!s1.isEqual(s2)) return false;
        }
        return true;
    }
    public boolean isGlobalFinal() {
        for (StatementInfo stmt : statements) {
            if (stmt.code != null 
                && stmt.code.contains("final") 
                && stmt.locationContext != null 
                && "FieldDeclaration".equals(stmt.locationContext.methodName)) {
                return true;
            }
        }
        return false; // 这里可以根据实际情况判断是否为全局变量
    }

}
