package com.mockanalyzer.cloneDetector;

import com.mockanalyzer.model.MockCloneInstance;
import com.mockanalyzer.model.MockSequence;
import com.mockanalyzer.model.StatementInfo;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Collectors;

/**
 * 论文步骤二和三：Frequent Stub Set Mining 与 Mock Clone Instance Formation。
 * Paper Steps 2 and 3: Frequent Stub Set Mining and Mock Clone Instance Formation.
 */
public class MockCloneMiner {
    private static final int MIN_SUPPORT = 2;

    /**
     * 保留旧入口以兼容已有调用；实现已改为论文中的并列分支搜索。
     * Keeps the legacy entry point while using the paper's tie-branching search.
     */
    public List<MockCloneInstance> runMutipStubbing(String mockedClass, String packageName,
            List<MockSequence> group, List<List<String>> abstractedSequences) {
        return formMockCloneInstances(mockedClass, packageName, group, abstractedSequences);
    }

    public List<MockCloneInstance> formMockCloneInstances(String mockedClass, String packageName,
            List<MockSequence> group, List<List<String>> abstractedSequences) {
        Map<Set<String>, Set<Integer>> frequentStubSets =
                new AprioriMiner().mine(abstractedSequences, MIN_SUPPORT);
        if (frequentStubSets.isEmpty()) {
            return new ArrayList<>();
        }

        List<FrequentStubSet> centers = frequentStubSets.entrySet().stream()
                .map(entry -> new FrequentStubSet(entry.getKey(), entry.getValue()))
                .sorted(Comparator.comparingInt(FrequentStubSet::estimatedGain).reversed()
                        .thenComparing(FrequentStubSet::key))
                .collect(Collectors.toList());

        // 对最高 Estimated Gain 相同的中心进行分支，最终比较 Actual Gain。
        // Branch on centers tied for highest Estimated Gain, then compare Actual Gain.
        PaperClusteringSearch search = new PaperClusteringSearch(group.size(), centers);
        Map<Set<String>, List<Integer>> assignments = search.findBestClustering();
        return buildInstances(mockedClass, packageName, group, assignments);
    }

    private List<MockCloneInstance> buildInstances(String mockedClass, String packageName,
            List<MockSequence> group, Map<Set<String>, List<Integer>> assignments) {
        List<MockCloneInstance> results = new ArrayList<>();
        for (Map.Entry<Set<String>, List<Integer>> entry : assignments.entrySet()) {
            Set<String> sharedStatements = entry.getKey();
            List<Integer> sequenceIndices = entry.getValue();
            if (sequenceIndices.size() < MIN_SUPPORT) {
                continue;
            }

            MockCloneInstance instance = new MockCloneInstance();
            instance.sequences = sequenceIndices.stream().map(group::get).collect(Collectors.toList());
            instance.mockedClass = mockedClass;
            instance.packageName = packageName;
            instance.sharedStatements = new ArrayList<>(sharedStatements);
            instance.sequenceCount = sequenceIndices.size();
            instance.testCaseCount = (int) sequenceIndices.stream()
                    .map(index -> group.get(index).testMethodName + "::" + group.get(index).className)
                    .distinct().count();
            instance.sharedStatementLineCount = sharedStatements.size();
            instance.locReduced = 0;

            Set<Integer> mockObjectIds = new HashSet<>();
            for (MockSequence sequence : instance.sequences) {
                mockObjectIds.add(sequence.mockObjectId);
                if (sequence.isReuseableMock) {
                    instance.locReduced--;
                }
                for (Map.Entry<Integer, StatementInfo> statement : sequence.rawStatementInfo.entrySet()) {
                    StatementInfo info = statement.getValue();
                    if (sharedStatements.contains(info.abstractedStatement) && !info.isShareable) {
                        instance.locReduced++;
                        sequence.overlapLines.add(statement.getKey());
                    }
                }
            }
            instance.mockObjectCount = mockObjectIds.size();
            if (instance.mockObjectCount > 1) {
                results.add(instance);
            }
        }
        return results;
    }

    private record FrequentStubSet(Set<String> statements, Set<Integer> sequenceIndices) {
        private FrequentStubSet(Set<String> statements, Set<Integer> sequenceIndices) {
            this.statements = new TreeSet<>(statements);
            this.sequenceIndices = new TreeSet<>(sequenceIndices);
        }

        int estimatedGain() {
            return statements.size() * (sequenceIndices.size() - 1);
        }

        String key() {
            return String.join("\u001f", statements);
        }
    }

    /**
     * 论文中的 ExploreClustering。只保留 Actual Gain 最大的完整聚类。
     * The paper's ExploreClustering procedure. Retains the complete clustering with maximum Actual Gain.
     */
    private static final class PaperClusteringSearch {
        private final int sequenceCount;
        private final List<FrequentStubSet> centers;
        private Map<Set<String>, List<Integer>> best = new LinkedHashMap<>();
        private int bestGain = -1;
        private String bestSignature = "";

        private PaperClusteringSearch(int sequenceCount, List<FrequentStubSet> centers) {
            this.sequenceCount = sequenceCount;
            this.centers = centers;
        }

        Map<Set<String>, List<Integer>> findBestClustering() {
            explore(0, new LinkedHashMap<>());
            return best;
        }

        private void explore(int sequenceIndex, Map<Set<String>, List<Integer>> clustering) {
            if (sequenceIndex == sequenceCount) {
                consider(clustering);
                return;
            }

            List<FrequentStubSet> matched = centers.stream()
                    .filter(center -> center.sequenceIndices.contains(sequenceIndex))
                    .collect(Collectors.toList());
            if (matched.isEmpty()) {
                explore(sequenceIndex + 1, clustering);
                return;
            }

            int maximumEstimatedGain = matched.stream()
                    .mapToInt(FrequentStubSet::estimatedGain)
                    .max().orElse(0);
            for (FrequentStubSet center : matched) {
                if (center.estimatedGain() != maximumEstimatedGain) {
                    continue;
                }
                List<Integer> members = clustering.computeIfAbsent(center.statements, ignored -> new ArrayList<>());
                members.add(sequenceIndex);
                explore(sequenceIndex + 1, clustering);
                members.remove(members.size() - 1);
                if (members.isEmpty()) {
                    clustering.remove(center.statements);
                }
            }
        }

        private void consider(Map<Set<String>, List<Integer>> clustering) {
            int actualGain = clustering.entrySet().stream()
                    .filter(entry -> entry.getValue().size() >= MIN_SUPPORT)
                    .mapToInt(entry -> entry.getKey().size() * (entry.getValue().size() - 1))
                    .sum();
            String signature = clustering.entrySet().stream()
                    .filter(entry -> entry.getValue().size() >= MIN_SUPPORT)
                    .map(entry -> String.join("\u001f", new TreeSet<>(entry.getKey())) + "=" + entry.getValue())
                    .sorted().collect(Collectors.joining("|"));
            if (actualGain > bestGain || (actualGain == bestGain && signature.compareTo(bestSignature) < 0)) {
                bestGain = actualGain;
                bestSignature = signature;
                best = new LinkedHashMap<>();
                clustering.entrySet().stream()
                        .filter(entry -> entry.getValue().size() >= MIN_SUPPORT)
                        .sorted(Map.Entry.comparingByKey(Comparator.comparing(set -> String.join("\u001f", set))))
                        .forEach(entry -> best.put(new TreeSet<>(entry.getKey()), new ArrayList<>(entry.getValue())));
            }
        }
    }
}
