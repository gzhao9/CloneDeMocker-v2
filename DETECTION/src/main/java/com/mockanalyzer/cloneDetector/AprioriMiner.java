package com.mockanalyzer.cloneDetector;

import java.util.*;

/**
 * A standard Apriori implementation for finding frequent itemsets (unordered subsets)
 * in a given list of transactions.
 *
 * 小规模输入仍走原来的 Apriori；共享 stub 很多时（例如 cloudstack），全部频繁项集的数量是
 * 2^共享数，Apriori 会指数爆炸，这时改为只返回闭频繁项集。
 * Small inputs still take the original Apriori. When many stubs are shared (e.g. cloudstack) the
 * frequent itemsets number 2^shared and Apriori explodes; only closed frequent itemsets are
 * returned then.
 *
 * 两条路径下游结果相同：{@link MockCloneMiner} 每个序列只取 estimatedGain = |S|·(support−1)
 * 最大的中心。非闭项集 S 的闭包与它出现在同一批序列里、却多至少一条语句，gain 严格更大，
 * 所以非闭项集从来不会被选中。
 * Both paths give the same downstream result: {@link MockCloneMiner} only ever takes, per sequence,
 * a center of maximal estimatedGain = |S|·(support−1). A non-closed S has a closure present in the
 * very same sequences with at least one more statement, so a strictly higher gain; a non-closed
 * itemset is never chosen.
 */
public class AprioriMiner {

    /**
     * 原 Apriori 最多会产出多少个频繁项集时仍然走它。按闭项集推上界：每个频繁项集都是某个
     * 闭项集 c 的非空子集，所以总数 ≤ Σ(2^|c| − 1)。
     * The most frequent itemsets the original Apriori may produce and still be used. Bounded via
     * the closed itemsets: every frequent itemset is a non-empty subset of some closed c, so the
     * total is ≤ Σ(2^|c| − 1).
     */
    static final long LEGACY_ITEMSET_LIMIT = 4096;

    /**
     * @param transactions list of mock statement transactions
     * @param minSupport   minimum number of transactions (≥2) an itemset must appear in
     * @return map of frequent itemsets to set of transaction indices (each itemset → which transactions
     *         it appears in); only the closed ones when the full set would exceed {@link #LEGACY_ITEMSET_LIMIT}
     */
    public Map<Set<String>, Set<Integer>> mine(List<List<String>> transactions, int minSupport) {
        Map<Set<String>, Set<Integer>> closed = mineClosed(transactions, minSupport);
        if (frequentItemsetUpperBound(closed.keySet()) <= LEGACY_ITEMSET_LIMIT) {
            return mineAll(transactions, minSupport);
        }
        return closed;
    }

    /**
     * 闭频繁项集：恰好是至少 minSupport 个 transaction 的交集。逐个 transaction 维护"已见
     * transaction 的所有交集"，规模随不同交集的个数增长，而不是随 2^共享数。
     * Closed frequent itemsets: exactly the intersections of at least minSupport transactions. Keeps
     * "every intersection of the transactions seen so far" one transaction at a time, which grows
     * with the number of distinct intersections rather than with 2^shared.
     */
    static Map<Set<String>, Set<Integer>> mineClosed(List<List<String>> transactions, int minSupport) {
        List<Set<String>> sets = new ArrayList<>();
        Set<Set<String>> intersections = new LinkedHashSet<>();
        for (List<String> transaction : transactions) {
            Set<String> current = new TreeSet<>(transaction);
            sets.add(current);
            List<Set<String>> added = new ArrayList<>();
            for (Set<String> seen : intersections) {
                Set<String> common = new TreeSet<>(seen);
                common.retainAll(current);
                if (!common.isEmpty()) {
                    added.add(common);
                }
            }
            if (!current.isEmpty()) {
                added.add(current);
            }
            intersections.addAll(added);
        }

        Map<Set<String>, Set<Integer>> result = new LinkedHashMap<>();
        for (Set<String> itemset : intersections) {
            Set<Integer> tidSet = new HashSet<>();
            for (int tIdx = 0; tIdx < sets.size(); tIdx++) {
                if (sets.get(tIdx).containsAll(itemset)) {
                    tidSet.add(tIdx);
                }
            }
            if (tidSet.size() >= minSupport) {
                result.put(itemset, tidSet);
            }
        }
        return result;
    }

    static long frequentItemsetUpperBound(Collection<Set<String>> closedItemsets) {
        long total = 0;
        for (Set<String> itemset : closedItemsets) {
            if (itemset.size() >= 62) {
                return Long.MAX_VALUE;
            }
            total += (1L << itemset.size()) - 1;
            if (total > LEGACY_ITEMSET_LIMIT) {
                return total;
            }
        }
        return total;
    }

    /**
     * Runs the Apriori algorithm to find all frequent itemsets.
     */
    static Map<Set<String>, Set<Integer>> mineAll(List<List<String>> transactions, int minSupport) {
        // final result: itemset -> set of transaction indices
        Map<Set<String>, Set<Integer>> result = new LinkedHashMap<>();

        // Step 1: find all 1-itemsets and their TID sets
        Map<Set<String>, Set<Integer>> current = new LinkedHashMap<>();
        for (int tIdx = 0; tIdx < transactions.size(); tIdx++) {
            List<String> transaction = transactions.get(tIdx);
            for (String item : transaction) {
                Set<String> singleton = Collections.singleton(item);
                current.computeIfAbsent(singleton, k -> new HashSet<>()).add(tIdx);
            }
        }

        // filter out < minSupport
        current.entrySet().removeIf(e -> e.getValue().size() < minSupport);
        // add these to final result
        result.putAll(current);

        // we now move from k=2 upwards until no more frequent itemsets
        int k = 2;
        while (!current.isEmpty()) {
            Map<Set<String>, Set<Integer>> candidates = new LinkedHashMap<>();
            List<Set<String>> prevItemsets = new ArrayList<>(current.keySet());

            // try merging each pair of (k-1)-itemsets
            for (int i = 0; i < prevItemsets.size(); i++) {
                for (int j = i + 1; j < prevItemsets.size(); j++) {
                    Set<String> a = prevItemsets.get(i);
                    Set<String> b = prevItemsets.get(j);

                    // fix: use (k - 2) so that for k=2, prefixSize=0 => no front check
                    Set<String> merged = tryMerge(a, b, k - 2);
                    if (merged != null && !candidates.containsKey(merged)) {
                        // compute support by scanning transactions
                        Set<Integer> tidSet = new HashSet<>();
                        for (int tIdx = 0; tIdx < transactions.size(); tIdx++) {
                            if (transactions.get(tIdx).containsAll(merged)) {
                                tidSet.add(tIdx);
                            }
                        }
                        if (tidSet.size() >= minSupport) {
                            candidates.put(merged, tidSet);
                        }
                    }
                }
            }

            // add these k-itemsets to final result
            result.putAll(candidates);

            // these candidates become the new 'current' for next iteration (k+1)
            current = candidates;
            k++;
        }

        return result;
    }

    /**
     * Attempt to merge two (k-1)-itemsets a, b into one k-itemset,
     * requiring that their first (k-2) items are identical (sorted order),
     * and that they differ in the last item.
     */
    private static Set<String> tryMerge(Set<String> a, Set<String> b, int prefixSize) {
        // If size differs or not exactly k-1, can't merge
        if (a.size() != b.size()) return null;
        if (a.size() != prefixSize + 1) return null;

        // Sort them to check prefix
        List<String> listA = new ArrayList<>(a);
        List<String> listB = new ArrayList<>(b);
        Collections.sort(listA);
        Collections.sort(listB);

        // check first prefixSize items
        for (int i = 0; i < prefixSize; i++) {
            if (!listA.get(i).equals(listB.get(i))) {
                return null;
            }
        }

        // the (k-2)th item must be different for merging
        // but for k=2 => prefixSize=0, we skip this check
        // If prefixSize>0, we confirm the last item of each is not the same
        if (prefixSize > 0 && listA.get(prefixSize).equals(listB.get(prefixSize))) {
            return null;
        }

        // union
        Set<String> merged = new TreeSet<>(a);
        merged.addAll(b);

        // confirm final size == prefixSize+2 => k
        if (merged.size() == prefixSize + 2) {
            return merged;
        }
        return null;
    }

    // Temporary main for testing
    public static void main(String[] args) {
        AprioriMiner miner = new AprioriMiner();

        // Test data
        List<List<String>> transactions = List.of(
            List.of("s1", "s2", "s3"),
            List.of("s2", "s3", "s4"),
            List.of("s2", "s4", "s5"),
            List.of("s1", "s4"),
            List.of("s1", "s6")
        );

        // Should see itemsets like {s2, s3}, {s2, s4}, {s1, s4}, etc.
        Map<Set<String>, Set<Integer>> result = miner.mine(transactions, 2);

        System.out.println("Frequent Itemsets:");
        for (Map.Entry<Set<String>, Set<Integer>> entry : result.entrySet()) {
            System.out.println(entry.getKey() + " -> " + entry.getValue());
        }
    }
}
