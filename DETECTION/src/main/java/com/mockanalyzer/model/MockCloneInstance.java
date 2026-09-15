package com.mockanalyzer.model;

import java.util.List;

public class MockCloneInstance {
    public String mockedClass;
    public String packageName;

    // ------ Stats ------
    public int testCaseCount;
    public int sequenceCount;
    public int sharedStatementLineCount;
    public int locReduced;
    public int mockObjectCount; // abstractedStatements count

    public List<String> sharedStatements; // abstractedStatements that are shared

    public List<MockSequence> sequences;
}
