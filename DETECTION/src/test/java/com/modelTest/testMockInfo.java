package com.modelTest;

import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

import com.mockanalyzer.model.MockInfo;

/**
 * Unit test for simple App.
 */
public class testMockInfo {

    /**
     * Rigorous Test :-)
     */
    @Test
    public void shouldAnswerWithTrue() {
        MockInfo mockInfo1 = new MockInfo();
        MockInfo mockInfo2 = new MockInfo();

        mockInfo1.rawMockObjectId = 739;
        mockInfo1.variableName = "request";
        mockInfo1.variableType = "HttpRequest";
        mockInfo1.mockedClass = "org.apache.dubbo.remoting.http12.HttpRequest";
        mockInfo1.isReuseableMock = true;
        mockInfo1.mockPattern = "Creation:\n— Attribute Mock Creation\n\nStubbing:\n— @Before\n— Test Case\n\nVerification:\n— None";
        mockInfo1.mockRole = "mock";
        if (mockInfo1.classContext != null) {
            mockInfo1.classContext.packageName = "org.apache.dubbo.rpc.protocol.tri.rest.cors";
            mockInfo1.classContext.filePath = "C:\\Java_projects\\Apache\\dubbo\\dubbo-rpc\\dubbo-rpc-triple\\src\\test\\java\\org\\apache\\dubbo\\rpc\\protocol\\tri\\rest\\cors\\CorsHeaderFilterTest.java";
            mockInfo1.classContext.className = "CorsHeaderFilterTest";
        }

        mockInfo2.rawMockObjectId = 737;
        mockInfo2.mockRole = "mock";
        mockInfo2.variableName = "request";
        mockInfo2.variableType = "HttpRequest";
        mockInfo2.mockedClass = "org.apache.dubbo.remoting.http12.HttpRequest";
        mockInfo2.isReuseableMock = true;
        mockInfo2.mockPattern = mockInfo1.mockPattern; // or set as needed
        if (mockInfo2.classContext != null) {
            mockInfo2.classContext.packageName = "org.apache.dubbo.rpc.protocol.tri.rest.cors";
            mockInfo2.classContext.filePath = "C:\\Java_projects\\Apache\\dubbo\\dubbo-rpc\\dubbo-rpc-triple\\src\\test\\java\\org\\apache\\dubbo\\rpc\\protocol\\tri\\rest\\cors\\CorsHeaderFilterTest.java";
            mockInfo2.classContext.className = "CorsHeaderFilterTest";
        }
        
        assertTrue(mockInfo1.isEqual(mockInfo2), "MockInfo objects should be equal based on their properties");
    }
}
