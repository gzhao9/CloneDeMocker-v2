package com.mockanalyzer.cloneDetector;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.*;

class AprioriMinerTest {

    private static List<List<String>> randomTransactions(Random random) {
        int count = 2 + random.nextInt(8);
        List<List<String>> transactions = new ArrayList<>();
        for (int t = 0; t < count; t++) {
            List<String> transaction = new ArrayList<>();
            int length = random.nextInt(7);
            for (int i = 0; i < length; i++) {
                transaction.add("s" + random.nextInt(8));
            }
            transactions.add(transaction);
        }
        return transactions;
    }

    @Test
    void closedItemsetsClusterExactlyLikeAllFrequentItemsets() {
        // 闭项集路径的正确性依据：下游聚类结果与完整 Apriori 输出逐一相同。
        // The closed path's justification: downstream clustering identical to the full Apriori output.
        Random random = new Random(20260921);
        for (int round = 0; round < 3000; round++) {
            List<List<String>> transactions = randomTransactions(random);
            Map<Set<String>, Set<Integer>> all = AprioriMiner.mineAll(transactions, 2);
            Map<Set<String>, Set<Integer>> closed = AprioriMiner.mineClosed(transactions, 2);
            assertEquals(MockCloneMiner.cluster(transactions.size(), all),
                    MockCloneMiner.cluster(transactions.size(), closed), "transactions " + transactions);
        }
    }

    @Test
    void closedItemsetsAreTheFrequentItemsetsNoSupersetMatches() {
        Random random = new Random(7);
        for (int round = 0; round < 2000; round++) {
            List<List<String>> transactions = randomTransactions(random);
            Map<Set<String>, Set<Integer>> all = AprioriMiner.mineAll(transactions, 2);
            Set<Set<String>> expected = new HashSet<>();
            for (Map.Entry<Set<String>, Set<Integer>> entry : all.entrySet()) {
                boolean closed = all.entrySet().stream().noneMatch(other -> other.getKey().size() > entry.getKey().size()
                        && other.getKey().containsAll(entry.getKey()) && other.getValue().equals(entry.getValue()));
                if (closed) {
                    expected.add(new TreeSet<>(entry.getKey()));
                }
            }
            Map<Set<String>, Set<Integer>> closed = AprioriMiner.mineClosed(transactions, 2);
            assertEquals(expected, closed.keySet(), "transactions " + transactions);
            closed.forEach((itemset, tids) -> assertEquals(all.get(itemset), tids));
        }
    }

    @Test
    void smallInputsStillTakeTheOriginalApriori() {
        Random random = new Random(11);
        for (int round = 0; round < 2000; round++) {
            List<List<String>> transactions = randomTransactions(random);
            assertEquals(AprioriMiner.mineAll(transactions, 2), new AprioriMiner().mine(transactions, 2));
        }
    }

    @Test
    @Timeout(5)
    void manySharedStubsNoLongerExplode() {
        // 三个序列共享 40 条 stub：完整频繁项集有 2^40 个，原 Apriori 跑不完。
        // Three sequences sharing 40 stubs: 2^40 frequent itemsets, which the original Apriori never finishes.
        List<String> shared = new ArrayList<>();
        for (int i = 0; i < 40; i++) {
            shared.add("when(Dao.find" + i + "()).thenReturn(Object)");
        }
        List<List<String>> transactions = new ArrayList<>();
        for (int t = 0; t < 3; t++) {
            List<String> transaction = new ArrayList<>(shared);
            transaction.add("when(Dao.only" + t + "()).thenReturn(Object)");
            transactions.add(transaction);
        }
        Map<Set<String>, Set<Integer>> mined = new AprioriMiner().mine(transactions, 2);
        assertEquals(Set.of(0, 1, 2), mined.get(new TreeSet<>(shared)));
        assertEquals(Map.of(new TreeSet<>(shared), List.of(0, 1, 2)), MockCloneMiner.cluster(3, mined));
    }
}
