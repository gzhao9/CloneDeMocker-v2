package com.mockanalyzer.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 表示同一个 Mock 对象在同一个类中，如何在多个测试方法中使用的序列。
 * 主要用于输出可读性更好的结构，以及保存抽象化信息。
 * Represents the sequence of how the same mock object is used in multiple test methods in the same class.
 * Mainly used for outputting a more readable structure and saving abstracted information.
 */
public class MockSequence {
    public int mockObjectId;
    public String mockRole;

    /**
     * Mock 的变量名，例如 "invoker"
     */
    public String variableName;

    /**
     * 变量类型，例如 "Invoker<?>"
     */
    public String variableType;

    /**
     * 被Mock的真实类，例如 "org.apache.dubbo.rpc.Invoker<?>"
     */
    public String mockedClass;
    public String packageName;
    public String filePath;
    public String className;
    public String testMethodName;    
    public boolean isReuseableMock = false;

    public List<Integer> overlapLines = new ArrayList<>();
    public String testMethodRawCode = "";

    /**
     * 在该类中，与此 Mock 相关的“共享语句”列表
     * （locate = "Attribute", "@Before", "@After", "Helper Method"等）
     * The list of shareable statements in this class
     * (locate = "Attribute", "@Before", "@After", "Helper Method", etc.)
     */
    public Map<Integer, String> shareableMockLines = new LinkedHashMap<>();

    /**
     * 试用例对这个 Mock 的使用序列
     * The sequence of using this mock in test cases
     */
    public Map<Integer, String> testMockLines = new LinkedHashMap<>();

    /**
     * 该 Mock 的抽象化语句
     * The abstracted statement of this mock
     */
    public Map<Integer, String> abstractedStatement = new LinkedHashMap<>();

    /**
     * 该 Mock 的原始语句信息
     * The raw statement info
     */
    public Map<Integer, StatementInfo> rawStatementInfo = new LinkedHashMap<>();
    /*
     * 以下是一些方法，用于格式化输出
     * Print the mock sequence in a readable format.
     * @return the formatted mock sequence
     */
}
