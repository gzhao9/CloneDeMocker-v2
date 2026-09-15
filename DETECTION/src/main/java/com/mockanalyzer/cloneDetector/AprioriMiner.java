package com.mockanalyzer.cloneDetector;

import java.util.*;

/**
 * A standard Apriori implementation for finding frequent itemsets (unordered subsets)
 * in a given list of transactions.
 */
public class AprioriMiner {

    /**
     * Runs the Apriori algorithm to find frequent itemsets.
     * 
     * @param transactions list of mock statement transactions
     * @param minSupport   minimum number of transactions (≥2) an itemset must appear in
     * @return map of frequent itemsets to set of transaction indices (each itemset → which transactions it appears in)
     */
    public Map<Set<String>, Set<Integer>> mine(List<List<String>> transactions, int minSupport) {
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
    private Set<String> tryMerge(Set<String> a, Set<String> b, int prefixSize) {
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
