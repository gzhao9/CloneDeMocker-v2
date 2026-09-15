package com.mockanalyzer.cloneDetector;

import com.mockanalyzer.model.MockCloneInstance;
import com.mockanalyzer.model.MockSequence;
import com.mockanalyzer.model.StatementInfo;

import java.util.*;
import java.util.stream.Collectors;

public class MockCloneDetector {

    /**
     * Entry point: run mock clone detection grouped by mockedClass + packageName,
     * but return results grouped by mockedClass.
     */
    public Map<String, List<MockCloneInstance>> detect(List<MockSequence> allSequences) {
        // Step 1: Group by (mockedClass + packageName)
        Map<String, List<MockSequence>> grouped = new HashMap<>();
        for (MockSequence seq : allSequences) {
            String key = seq.mockedClass + "#" + seq.packageName;
            grouped.computeIfAbsent(key, k -> new ArrayList<>()).add(seq);
            // if (seq.packageName != null && seq.packageName.length() < 100 && seq.packageName.length() > 4) {
            //     grouped.computeIfAbsent(key, k -> new ArrayList<>()).add(seq);
            // }
            
        }

        // Step 2: Run detection per group, collect by mockedClass
        Map<String, List<MockCloneInstance>> result = new HashMap<>();
        for (Map.Entry<String, List<MockSequence>> entry : grouped.entrySet()) {
            List<MockSequence> group = entry.getValue();
            if (group.size() < 2)
                continue;

            String[] parts = entry.getKey().split("#", 2);
            String mockedClass = parts[0];
            String packageName = parts[1];

            List<MockCloneInstance> clones = detectClonesInGroup(mockedClass, packageName, group);
            if (!clones.isEmpty()) {
                result.computeIfAbsent(mockedClass, k -> new ArrayList<>()).addAll(clones);
            }
        }
        return result;
    }

    /**
     * Core detection logic for one (mockedClass, packageName) group.
     */
    private List<MockCloneInstance> detectClonesInGroup(String mockedClass, String packageName,
            List<MockSequence> group) {
        // Step 1: Extract all abstractedSequences (List of List<String>)
        List<List<String>> abstractedSequences = new ArrayList<>();
        Map<MockSequence, List<String>> sequenceMap = new HashMap<>();

        for (MockSequence seq : group) {
            List<String> stmts = new ArrayList<>(seq.abstractedStatement.values());
            abstractedSequences.add(stmts);
            sequenceMap.put(seq, stmts);
        }

        // Step 2: Run frequent pattern mining and detection
        List<MockCloneInstance> detectedClones = new MockCloneMiner().runMutipStubbing(mockedClass, packageName, group,
                abstractedSequences);

        // === 新增 Step 3: 无 stub 处理 ===
        List<MockCloneInstance> noStubClones = detectNoStubClones(mockedClass, packageName, group);
        detectedClones.addAll(noStubClones);

        return detectedClones;
    }

    /**
     * 收集无 stub (abstractedStatement.isEmpty()) 的 MockSequence，
     * 按照同一个 filePath、mockedClass、packageName 合并为一个 clone group。
     *
     * @param group detectClonesInGroup 传进来的 sequence，已是同 mockedClass、packageName。
     */
    private List<MockCloneInstance> detectNoStubClones(
            String mockedClass,
            String packageName,
            List<MockSequence> group) {

        // 1. 筛选出无 stub 的 mock
        List<MockSequence> noStub = group.stream()
                .filter(seq -> seq.abstractedStatement.isEmpty())
                .collect(Collectors.toList());

        if (noStub.isEmpty()) {
            return Collections.emptyList();
        }

        // 2. 按 filePath 分组 (也可按 testMethodName, 或 filePath+testMethodName等)
        Map<String, List<MockSequence>> byFilePath = noStub.stream()
                .collect(Collectors.groupingBy(seq -> seq.filePath));

        List<MockCloneInstance> results = new ArrayList<>();

        for (Map.Entry<String, List<MockSequence>> e : byFilePath.entrySet()) {
            List<MockSequence> sameFile = e.getValue();

            // 如果同一个 filePath 下无 stub 的 mock < 2，不形成克隆
            // 至少两个 Mock Object 即可形成 creation-only MCI。
            // Two Mock Objects are sufficient to form a creation-only MCI.
            while (sameFile.size() >= 2) {

                // 这里假设只需一个 CloneInstance，把同文件、同 mockedClass 的都合并
                // 如果想要多次合并(eg. 先 2个,再 2个),可自行修改
                MockCloneInstance instance = new MockCloneInstance();
                instance.mockedClass = mockedClass;
                instance.packageName = packageName;

                // 这里假设 shareableStatements 只有 1 行 => "mock(...)"
                // 或者可以从 first sequence 的 shareableMockLines 取
                // 仅作示例:
                instance.sharedStatements = new ArrayList<>();
                instance.sequences = new ArrayList<>();

                // testCaseCount
                Set<String> testCases = new HashSet<>();
                Set<Integer> mockIDs = new HashSet<>();
                for (MockSequence seq : sameFile) {
                    if (!testCases.contains(seq.testMethodName) && !mockIDs.contains(seq.mockObjectId)
                            && !seq.isReuseableMock) {
                        testCases.add(seq.testMethodName);
                        mockIDs.add(seq.mockObjectId);
                        instance.sequences.add(seq);
                        for (int k : seq.rawStatementInfo.keySet()) {
                            StatementInfo stmt = seq.rawStatementInfo.get(k);
                            if (!stmt.type.equals("STUBBING") && !stmt.type.equals("VERIFICATION")
                                    && stmt.isMockRelated) {
                                seq.overlapLines.add(k);
                                break; // 只取第一个相关语句
                            }
                        }
                    }

                }
                instance.testCaseCount = testCases.size();

                // 我们约定 locReduced = 1*(seqCount - 1), 具体可再改
                instance.sharedStatementLineCount = 0;
                instance.mockObjectCount = mockIDs.size();
                instance.sequenceCount = instance.mockObjectCount;
                instance.locReduced = (instance.sequenceCount - 1);
                if (instance.mockObjectCount > 1) {
                    results.add(instance);
                }
                int oldSize = sameFile.size();
                sameFile = sameFile.stream()
                        .filter(seq -> !instance.sequences.contains(seq)) // 只保留非可重用的 mock
                        .collect(Collectors.toList());
                if (sameFile.size() == oldSize) {
                    break;
                }
            }
        }

        return results;
    }

}
