package com.mockanalyzer.sequencesParser;

import java.util.concurrent.atomic.AtomicLong;

/**
 * 统计符号解析因 StackOverflowError 而降级的次数。
 * Counts how often symbol resolution degraded because of a StackOverflowError.
 *
 * JavaParser 在比较自引用泛型（F-bounded，如 {@code T extends Comparable<T>}）时，
 * ResolvedReferenceType.compareConsideringTypeParameters 与
 * compareConsideringVariableTypeParameters 会互相递归且没有环检测，抛出 StackOverflowError。
 * 该错误继承自 Error 而非 Exception，因此各解析点原有的 catch 接不住它，整个扫描进程会被终止。
 * 各解析点本就备有语法级兜底路径，放宽 catch 后即可按原设计降级；此处记录降级次数，
 * 使降级可被如实报告，而不是静默发生。
 */
public final class ResolutionDiagnostics {
    private static final AtomicLong STACK_OVERFLOWS = new AtomicLong();

    private ResolutionDiagnostics() {
    }

    /** 在解析点的 catch 中调用；只有 StackOverflowError 会被计入。 */
    public static void note(Throwable failure) {
        if (failure instanceof StackOverflowError) {
            STACK_OVERFLOWS.incrementAndGet();
        }
    }

    public static long stackOverflowCount() {
        return STACK_OVERFLOWS.get();
    }
}
