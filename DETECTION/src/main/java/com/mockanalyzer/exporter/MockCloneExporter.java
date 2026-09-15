package com.mockanalyzer.exporter;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.mockanalyzer.cloneDetector.MockCloneDetector;
import com.mockanalyzer.model.MockCloneInstance;
import com.mockanalyzer.model.MockInfo;
import com.mockanalyzer.model.MockSequence;
import com.mockanalyzer.model.DetectionScope;

import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.*;

public class MockCloneExporter {

    /**
     * Analyze a project, extract mock sequences, detect mock clones, and write to JSON.
     *
     * @param projectRoot the root of the Java project
     * @param outputPath path to the output JSON file
     * @param runCommand whether to re-run maven/gradle build
     */
    public static void exportClones(Path projectRoot, String outputPath, boolean runCommand) throws Exception {
        exportClones(projectRoot, outputPath, runCommand, DetectionScope.all(), null);
    }

    public static void exportClones(Path projectRoot, String outputPath, boolean runCommand,
            DetectionScope scope, Set<Integer> selectedMockIds) throws Exception {
        // Step 1: Analyze
        List<MockInfo> combinedResults = MockInfoExporter.analyzeProject(projectRoot, runCommand, scope);
        exportClones(combinedResults, outputPath, selectedMockIds);
    }

    /**
     * 论文步骤二和三：从已选 Mock Objects 生成 Frequent Stub Sets 和 MCIs。
     * Paper Steps 2 and 3: forms Frequent Stub Sets and MCIs from selected Mock Objects.
     */
    public static void exportClones(List<MockInfo> combinedResults, String outputPath,
            Set<Integer> selectedMockIds) throws Exception {
        List<MockInfo> fixedMockInfos = new ArrayList<>();

        // Step 2: Flatten all sequences
        List<MockSequence> allSequences = new ArrayList<>();
        for (MockInfo mockInfo : combinedResults) {
            // null 表示兼容旧命令的“全部”；空集合表示用户明确取消了全部对象。
            // null preserves the legacy "all" behavior; an empty set means the user selected none.
            boolean selected = selectedMockIds == null || selectedMockIds.contains(mockInfo.rawMockObjectId);
            if (selected && !mockInfo.isSpy() && !mockInfo.isGlobalFinal()) {
                mockInfo.mockRole = "mock";
                fixedMockInfos.add(mockInfo);
                allSequences.addAll(mockInfo.toMockSequences());
                continue; // Skip empty mock info
            }
        }

        // Step 3: Detect Clones
        // 零选择必须立即返回；旧 Apriori 路径在空输入时不会收敛。
        // Empty selection must return immediately; the legacy Apriori path does not converge on empty input.
        Map<String, List<MockCloneInstance>> cloneMap = allSequences.isEmpty()
                ? new LinkedHashMap<>()
                : new MockCloneDetector().detect(allSequences);
        MockCloneResult cloneResult = new MockCloneResult(fixedMockInfos, cloneMap);
        // Step 4: Write JSON
        Gson gson = new GsonBuilder()
                .setPrettyPrinting()
                .disableHtmlEscaping()
                .create();

        try (OutputStreamWriter writer = new OutputStreamWriter(
                new FileOutputStream(outputPath), StandardCharsets.UTF_8)) {
            gson.toJson(cloneResult, writer);
        }

        System.out.println("Mock clone detection completed. Result -> " + outputPath);
    }
    private static class MockCloneResult{
        private Map<String, List<MockCloneInstance>> detectedMockClones;
        private List<MockInfo> detectedMockObjects;
        public MockCloneResult() {
            this.detectedMockObjects = new ArrayList<>();
            this.detectedMockClones = new HashMap<>();
        }
        public MockCloneResult(List<MockInfo> detectedMockObjects, Map<String, List<MockCloneInstance>> detectedMockClones) {
            this.detectedMockObjects = detectedMockObjects;
            this.detectedMockClones = detectedMockClones;
        }
        public void setDetectedMockObjects(List<MockInfo> detectedMockObjects) {
            this.detectedMockObjects = detectedMockObjects;
        }
        public void setDetectedMockClones(Map<String, List<MockCloneInstance>> detectedMockClones) {
            this.detectedMockClones = detectedMockClones;
        }
    }
}
