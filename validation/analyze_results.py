"""跑完 run_pilot.py 之后，把结果和检测原始数据按论文方法论转成 RQ1-RQ4 对应的表格
数据，和 paper_reference_data.json 里的旧数字放一起对比，同时按 harness 判定原因做
失败分类统计（不用再人工过一遍）。

Turns a run_pilot.py report plus its underlying raw detection data into the paper's
RQ1-RQ4 table shapes, side by side with the old numbers in paper_reference_data.json,
and tallies failure reasons from the harness's own classification instead of a manual
pass.

用法 / Usage:
    uv run python validation/analyze_results.py --report validation/results/pilot-XXXX.json
    (--out 不传时，默认写到同名的 validation/results/analysis-XXXX.md 和 .json)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from validation.diff_utils import split_diff_by_file, apply_unified_diff  # noqa: E402

REFERENCE_PATH = REPO_ROOT / "validation" / "paper_reference_data.json"

# MODEL_DECLINED 原因文本的关键词分类规则：按 2026-09-15 对 eaffe40a 全量跑批 11 条
# MODEL_DECLINED 原文逐条读出来的、模型自己反复用的措辞总结出来的，多标签（一条原因
# 可能同时命中多个类别），按 dict 顺序做展示优先级。这是"用关键词代替人工读"的程序化
# 分类，不是猜的——每条规则后面的注释都标注了它对应哪句原文用语。
# Keyword-based classifier for MODEL_DECLINED reason text: derived by actually reading
# all 11 MODEL_DECLINED reasons from the 2026-09-15 eaffe40a full batch and generalizing
# the model's own recurring phrasing. Multi-label (one reason can match several
# categories); dict order sets display priority. This is "keywords instead of manual
# reading", not a guess — each rule's comment cites the exact phrasing it targets.
DECLINE_CATEGORY_RULES: dict[str, list[str]] = {
    # "adding a new shared test utility source file", "not supplied for modification",
    # "moving...into a new source file", "shared fixture/helper would require adding a file"
    "needs_new_shared_file_across_test_classes": [
        r"new (shared )?(test )?(utility|source) file",
        r"not supplied for modification",
        r"new source file",
        r"require(s|ing)? adding a file",
    ],
    # "not injected into, referenced by, or otherwise reachable from",
    # "never passed to ... any other collaborator", "behaviorally disconnected",
    # "dead-code cleanup", "apparently disconnected"
    "mock_disconnected_from_tested_behavior": [
        r"never passed to",
        r"not (injected into|reachable from)",
        r"behaviorally disconnected",
        r"dead[- ]code",
        r"apparently disconnected",
        r"disconnected (mockito )?side effect",
    ],
    # "each mock is method-local and ... relies on its own independently configured",
    # "independently scoped to distinct ... lifecycles", "intentionally changes the mock
    # response", "intentionally creates separate mocks"
    "semantically_divergent_across_instances": [
        r"independently (configured|scoped)",
        r"intentionally (changes|creates)",
        r"distinct .*lifecycles",
    ],
    # "requires executing or inspecting the target implementation and test suite, which
    # is not available in the supplied files"
    "needs_runtime_verification_beyond_static_source": [
        r"requires executing or inspecting",
        r"execut(e|ing) the target implementation",
    ],
    # "supplied file is too large to reproduce reliably without risking truncation",
    # "large inline excerpt without an independently addressable source artifact"
    "full_file_response_too_large_or_unverifiable": [
        r"too large to reproduce",
        r"incomplete for reliable reconstruction",
        r"independently addressable source artifact",
    ],
}


def categorize_declined_reasons(report: dict[str, Any]) -> dict[str, Any]:
    """对 report 里所有 MODEL_DECLINED 的 reason 文本跑一遍关键词规则，多标签打分，
    不逐条人工看。命中 0 条规则的归到 uncategorized，附原文方便回头补规则。
    Runs the keyword rules over every MODEL_DECLINED reason text in the report — no
    per-case manual reading. Anything matching zero rules goes to "uncategorized" with
    its raw text attached, so the rule set can be extended later."""
    compiled = {name: [re.compile(p, re.IGNORECASE) for p in patterns]
                for name, patterns in DECLINE_CATEGORY_RULES.items()}
    by_category: dict[str, list[dict[str, str]]] = {name: [] for name in DECLINE_CATEGORY_RULES}
    uncategorized: list[dict[str, str]] = []

    for result in report.get("results", []):
        if result.get("classification") != "MODEL_DECLINED":
            continue
        reason = result.get("reason", "")
        entry = {"mciId": result.get("mciId", "?"), "reason": reason}
        matched_any = False
        for name, patterns in compiled.items():
            if any(p.search(reason) for p in patterns):
                by_category[name].append(entry)
                matched_any = True
        if not matched_any:
            uncategorized.append(entry)

    return {
        "counts": {name: len(entries) for name, entries in by_category.items() if entries},
        "byCategory": {name: entries for name, entries in by_category.items() if entries},
        "uncategorizedCount": len(uncategorized),
        "uncategorized": uncategorized,
    }


def analyze_goal_shortfalls(report: dict[str, Any], mci_lookup: dict[str, Any],
                             project_root: Path) -> list[dict[str, Any]]:
    """对每个 FAILED_REFACTORING_GOAL 的 MCI，重放它的 diff、重新跑一遍
    `_goal_check` 同款的"改动前后重复次数"比较，把具体还剩哪几行仍然重复列出来——
    而不是只知道 goalAchieved=false 这一个布尔值。用真实项目源码重建 before/after，
    因为 report 本身不存这份对比明细。
    For every FAILED_REFACTORING_GOAL MCI, replays its diff and re-runs the same
    before/after duplicate-line comparison `_goal_check` uses, listing exactly which
    lines are still duplicated — not just the boolean goalAchieved=false. Rebuilds
    before/after from the real project source since the report itself doesn't persist
    this comparison detail."""
    def normalize(line: str) -> str:
        return " ".join(line.split())

    shortfalls = []
    for result in report.get("results", []):
        if result.get("classification") != "FAILED_REFACTORING_GOAL":
            continue
        mci_id = result.get("mciId")
        instance = mci_lookup.get(mci_id)
        proposal_id = result.get("proposalId")
        if instance is None or proposal_id is None:
            continue
        diff_path = REPO_ROOT / ".clonedemocker" / "runs" / report["runId"] / "refactoring" / proposal_id / "changes.diff"
        if not diff_path.is_file():
            continue
        by_file = split_diff_by_file(diff_path.read_text(encoding="utf-8"))

        shared_lines: set[str] = set()
        for sequence in instance.get("sequences", []):
            for value in (sequence.get("shareableMockLines") or {}).values():
                normalized = normalize(str(value))
                if normalized:
                    shared_lines.add(normalized)

        still_duplicated = []
        for relative, hunks in by_file.items():
            source_path = project_root / relative
            if not source_path.is_file():
                continue
            original = source_path.read_text(encoding="utf-8")
            patched = apply_unified_diff(original, hunks)
            original_lines = [normalize(l) for l in original.splitlines()]
            new_lines = [normalize(l) for l in patched.splitlines()]
            for shared in shared_lines:
                before = original_lines.count(shared)
                after = new_lines.count(shared)
                if before > 1 and after >= before:
                    still_duplicated.append({"line": shared, "before": before, "after": after})

        shortfalls.append({"mciId": mci_id, "stillDuplicatedLines": still_duplicated})
    return shortfalls


def flat_clones(raw: dict[str, Any]) -> list[dict[str, Any]]:
    clones = raw.get("detectedMockClones", {})
    if isinstance(clones, list):
        return clones
    result = []
    for mocked_class, instances in clones.items():
        for instance in instances:
            instance = dict(instance)
            instance.setdefault("mockedClass", mocked_class)
            result.append(instance)
    return result


def indexed_mci_ids(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按 detection_service.py::detect() 同样的规则给每个 MCI 分配 id，
    这样才能把 pilot 报告里的 mciId 和原始检测 JSON 里的具体实例对上。
    Assigns each MCI the same id detection_service.py::detect() does, so the
    pilot report's mciId can be matched back to the specific raw instance."""
    clones = raw.get("detectedMockClones", {})
    by_id: dict[str, dict[str, Any]] = {}
    for mocked_class, instances in clones.items():
        for index, instance in enumerate(instances):
            by_id[f"{mocked_class}::{index + 1}"] = instance
    return by_id


def class_key(mock_object: dict[str, Any]) -> tuple[str, str]:
    ctx = mock_object.get("classContext", {})
    return (ctx.get("packageName", ""), ctx.get("className", ""))


def rq1_detection_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """检测规模与影响范围，对应 Table 5/Table 6 的方法论（2026-09-14 用论文自带的
    dubbo.json 逐项核对过：case-level 分子、MO/LOC 削减百分比都能精确/近似复现）。
    Detection scale and impact scope, matching Table 5/Table 6's methodology (verified
    2026-09-14 against the paper's own dubbo.json: the case-level numerator and the
    MO/LOC reduction percentages reproduce exactly/closely)."""
    clones = flat_clones(raw)
    mock_objects = list({mo["rawMockObjectId"]: mo for mo in raw.get("detectedMockObjects", [])}.values())
    mo_by_id = {mo["rawMockObjectId"]: mo for mo in mock_objects}

    all_classes = {class_key(mo) for mo in mock_objects}
    clone_mo_ids = {
        sequence.get("mockObjectId")
        for instance in clones
        for sequence in instance.get("sequences", [])
    }
    clone_classes = {class_key(mo_by_id[mid]) for mid in clone_mo_ids if mid in mo_by_id}

    clone_test_cases = {
        (sequence.get("filePath", ""), sequence.get("testMethodName", ""))
        for instance in clones
        for sequence in instance.get("sequences", [])
    }

    mo_reduction = sum(instance.get("mockObjectCount", 0) - 1 for instance in clones)
    loc_reduction = sum(instance.get("locReduced", 0) for instance in clones)
    clone_involved_mo = sum(instance.get("mockObjectCount", 0) for instance in clones)
    total_loc = sum(1 for mo in mock_objects for s in mo.get("statements", []) if s.get("isMockRelated"))
    clone_involved_loc = sum(
        1 for mid in clone_mo_ids if mid in mo_by_id
        for s in mo_by_id[mid].get("statements", []) if s.get("isMockRelated")
    )

    def pct(numerator: float, denominator: float) -> float | None:
        return round(100 * numerator / denominator, 1) if denominator else None

    return {
        "mockObjects": len(mock_objects),
        "mockCloneInstances": len(clones),
        "totalClassesUsingMocks": len(all_classes),
        "classesWithCloneInvolvedMocks": len(clone_classes),
        "classLevelPct": pct(len(clone_classes), len(all_classes)),
        "classLevelFrac": f"{len(clone_classes)}/{len(all_classes)}",
        "caseLevelInvolvedCount": len(clone_test_cases),
        "moReduction": mo_reduction,
        "locReduction": loc_reduction,
        "wholeProjectMoPct": pct(mo_reduction, len(mock_objects)),
        "wholeProjectLocPct": pct(loc_reduction, total_loc),
        "cloneInvolvedMoPct": pct(mo_reduction, clone_involved_mo),
        "cloneInvolvedLocPct": pct(loc_reduction, clone_involved_loc),
    }


def rq2_success_rates(report: dict[str, Any], mci_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """MCI 级和测试级成功率，以及一次生成 vs 修复后的对比，对应 Table 7 加上论文
    RQ2.1 的 first-pass 概念。测试级用每个 MCI 的 testCaseCount 加权（我们的判定是
    整个 MCI 一次性通过/失败，不拆到单个测试用例，这是这个近似下能做到的最细粒度）。
    MCI-level and test-level success, plus first-pass vs after-repair, matching Table 7
    and the paper's first-pass concept from RQ2.1. Test-level is weighted by each MCI's
    testCaseCount (our verdict is pass/fail for the whole MCI at once, not decomposed
    per individual test case, so this is the finest grain this approximation supports)."""
    skip_classes = {"SKIPPED_NO_TEST_CLASS", "SKIPPED_BASELINE_FAILED"}
    attempted = [r for r in report.get("results", []) if r.get("classification") not in skip_classes]

    def test_case_count(mci_id: str) -> int:
        instance = mci_lookup.get(mci_id)
        return instance.get("testCaseCount", 0) if instance else 0

    mci_success = sum(1 for r in attempted if r.get("classification") == "SUCCESS")
    mci_success_first_pass = sum(1 for r in attempted if r.get("firstPassClassification") == "SUCCESS")

    total_tests = sum(test_case_count(r["mciId"]) for r in attempted)
    success_tests = sum(test_case_count(r["mciId"]) for r in attempted if r.get("classification") == "SUCCESS")
    success_tests_first_pass = sum(
        test_case_count(r["mciId"]) for r in attempted if r.get("firstPassClassification") == "SUCCESS"
    )

    def pct(numerator: float, denominator: float) -> float | None:
        return round(100 * numerator / denominator, 1) if denominator else None

    return {
        "attemptedMcis": len(attempted),
        "mciSuccess": mci_success,
        "mciSuccessPct": pct(mci_success, len(attempted)),
        "mciSuccessFirstPass": mci_success_first_pass,
        "mciSuccessFirstPassPct": pct(mci_success_first_pass, len(attempted)),
        "totalImpactedTestCases": total_tests,
        "testSuccess": success_tests,
        "testSuccessPct": pct(success_tests, total_tests),
        "testSuccessFirstPass": success_tests_first_pass,
        "testSuccessFirstPassPct": pct(success_tests_first_pass, total_tests),
    }


def failure_breakdown(report: dict[str, Any]) -> dict[str, Any]:
    """按 harness/流程自己给出的判定原因统计失败类型——查日志得出，不是人工过一遍。
    Tallies failure types from the harness/pipeline's own verdicts — derived from the
    logs, not a manual pass."""
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for result in report.get("results", []):
        classification = result.get("classification", "UNKNOWN")
        counts[classification] = counts.get(classification, 0) + 1
        if classification != "SUCCESS":
            examples.setdefault(classification, [])
            if len(examples[classification]) < 5:
                examples[classification].append(result.get("mciId", "?"))
    return {"counts": counts, "exampleMciIds": examples}


def rq3_cost(report: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
    """token/成本统计，first-pass（只算第一次调用）和含修复轮次的总成本分开算，
    PIT 的耗时不计入这里（PIT 没有 token 成本，只是 wall-clock 时间，且当前 harness
    还没记录逐阶段耗时，参考 OPTIMIZATION_LOG.md 里的说明）。
    Token/cost accounting, with first-pass-only and total-including-repairs kept
    separate; PIT's wall-clock time is not part of this (it has no token cost, and the
    harness doesn't record per-phase timing yet — see OPTIMIZATION_LOG.md)."""
    input_per_m = pricing["inputPerMillionUsd"]
    output_per_m = pricing["outputPerMillionUsd"]

    total_input = total_output = total_calls = 0
    first_pass_input = first_pass_output = 0
    for result in report.get("results", []):
        attempts = result.get("attempts", [])
        total_calls += len(attempts)
        for attempt in attempts:
            usage = attempt.get("usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            if attempt.get("attempt") == 1:
                first_pass_input += usage.get("input_tokens", 0)
                first_pass_output += usage.get("output_tokens", 0)

    def cost(input_tokens: int, output_tokens: int) -> float:
        return round(input_tokens / 1_000_000 * input_per_m + output_tokens / 1_000_000 * output_per_m, 4)

    return {
        "model": pricing["model"],
        "pricingAssumption": pricing["source"],
        "totalApiCalls": total_calls,
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCostUsd": cost(total_input, total_output),
        "firstPassInputTokens": first_pass_input,
        "firstPassOutputTokens": first_pass_output,
        "firstPassCostUsd": cost(first_pass_input, first_pass_output),
    }


def render_markdown(report: dict[str, Any], rq1: dict[str, Any], rq2: dict[str, Any],
                     failures: dict[str, Any], rq3: dict[str, Any], reference: dict[str, Any],
                     declined_categories: dict[str, Any] | None = None,
                     goal_shortfalls: list[dict[str, Any]] | None = None) -> str:
    dubbo_old = reference["table5_study_subjects"]["dubbo_corrected"]
    table6_old = reference["table6_scope_vs_reduction"]["dubbo"]
    table7_old = reference["table7_refactoring_success_rates"]["dubbo"]

    lines = [
        f"# {report.get('projectRoot', '?')} — 新旧数据对比 / new vs. old",
        f"generated from `{report.get('runId', '?')}`, model `{report.get('model', '?')}`, "
        f"repair={report.get('repairEnabled')}, directLlmBaseline={report.get('directLlmBaseline')}",
        "",
        "## RQ1 检测规模 / detection scale (Table 5 & 6)",
        "",
        "| | 旧 Dubbo 3.2 (78/592 修正版) | 新 Dubbo 3.3.6 |",
        "|---|---|---|",
        f"| Mock Objects | {dubbo_old['mockObjects']} | {rq1['mockObjects']} |",
        f"| Mock Clone Instances | {dubbo_old['mockCloneInstances']} | {rq1['mockCloneInstances']} |",
        f"| Class-Level 影响占比 | {table6_old['classLevelPct']}% ({table6_old['classLevelFrac']}) | "
        f"{rq1['classLevelPct']}% ({rq1['classLevelFrac']}) |",
        f"| Case-Level 涉及测试用例数 | {table6_old['caseLevelFrac'].split('/')[0]} | {rq1['caseLevelInvolvedCount']} |",
        f"| Clone-Involved MO 削减 | {table6_old['cloneInvolvedMoPct']}% | {rq1['cloneInvolvedMoPct']}% |",
        f"| Clone-Involved LOC 削减 | {table6_old['cloneInvolvedLocPct']}% | {rq1['cloneInvolvedLocPct']}% |",
        f"| Whole-Project MO 削减 | {table6_old['wholeProjectMoPct']}% | {rq1['wholeProjectMoPct']}% |",
        f"| Whole-Project LOC 削减 | {table6_old['wholeProjectLocPct']}% | {rq1['wholeProjectLocPct']}% |",
        "",
        "## RQ2 重构成功率 / refactoring success rate (Table 7)",
        "",
        "| | 旧 Dubbo 3.2 | 新 Dubbo 3.3.6（一次生成 first-pass） | 新 Dubbo 3.3.6（含修复 final） |",
        "|---|---|---|---|",
        f"| MCI 级 | {table7_old['mciLevelPct']}% | {rq2['mciSuccessFirstPassPct']}% "
        f"({rq2['mciSuccessFirstPass']}/{rq2['attemptedMcis']}) | {rq2['mciSuccessPct']}% "
        f"({rq2['mciSuccess']}/{rq2['attemptedMcis']}) |",
        f"| 测试级 | {table7_old['testLevelPct']}% | {rq2['testSuccessFirstPassPct']}% "
        f"({rq2['testSuccessFirstPass']}/{rq2['totalImpactedTestCases']}) | {rq2['testSuccessPct']}% "
        f"({rq2['testSuccess']}/{rq2['totalImpactedTestCases']}) |",
        "",
        "## 失败原因统计（查日志自动得出，不是人工过一遍）/ failure breakdown (from logs, not manual)",
        "",
        "| 分类 | 数量 | 示例 MCI |",
        "|---|---|---|",
    ]
    for classification, count in sorted(failures["counts"].items(), key=lambda kv: (-kv[1], kv[0])):
        examples = ", ".join(failures["exampleMciIds"].get(classification, []))
        lines.append(f"| {classification} | {count} | {examples} |")

    if declined_categories and declined_categories.get("counts"):
        lines += [
            "",
            "### MODEL_DECLINED 细分（关键词规则自动打标，可多标签）/ breakdown "
            "(auto-tagged by keyword rules, multi-label)",
            "",
            "| 类别 | 数量 | MCI |",
            "|---|---|---|",
        ]
        for name, count in sorted(declined_categories["counts"].items(), key=lambda kv: -kv[1]):
            ids = ", ".join(e["mciId"] for e in declined_categories["byCategory"][name])
            lines.append(f"| {name} | {count} | {ids} |")
        if declined_categories.get("uncategorizedCount"):
            ids = ", ".join(e["mciId"] for e in declined_categories["uncategorized"])
            lines.append(f"| uncategorized | {declined_categories['uncategorizedCount']} | {ids} |")

    if goal_shortfalls:
        lines += [
            "",
            "### FAILED_REFACTORING_GOAL 明细：改动后仍然重复的行 / lines still "
            "duplicated after the change",
            "",
        ]
        for entry in goal_shortfalls:
            lines.append(f"- **{entry['mciId']}**:")
            for dup in entry["stillDuplicatedLines"]:
                lines.append(f"  - `{dup['line']}` (before={dup['before']}, after={dup['after']})")

    lines += [
        "",
        "## RQ3 成本 / cost",
        "",
        f"model: `{rq3['model']}` — {rq3['pricingAssumption']}",
        "",
        "| | 一次生成 first-pass | 含修复 total |",
        "|---|---|---|",
        f"| API 调用次数 | — | {rq3['totalApiCalls']} |",
        f"| Input tokens | {rq3['firstPassInputTokens']} | {rq3['totalInputTokens']} |",
        f"| Output tokens | {rq3['firstPassOutputTokens']} | {rq3['totalOutputTokens']} |",
        f"| 估算成本 (USD) | ${rq3['firstPassCostUsd']} | ${rq3['totalCostUsd']} |",
        "",
        "旧论文单独的 Dubbo RQ3 数字没有留存（只有六项目总计 153 分钟 / $14.41 / "
        "730万 token，以及单项目 $0.29-$5.96 的区间），所以这里没法逐项对比，只能列新数据。",
        "",
        "PIT 的墙钟耗时没有计入上面的时间/成本（PIT 不消耗 token，且 harness 目前还没有"
        "记录逐阶段耗时，细节见 `OPTIMIZATION_LOG.md`）。",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="run_pilot.py 生成的 pilot-*.json")
    parser.add_argument("--out", default=None, help="输出文件前缀（不含扩展名），默认和 report 同名")
    args = parser.parse_args()

    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))

    detection_path = REPO_ROOT / ".clonedemocker" / "runs" / report["runId"] / "mock-clone-instances.json"
    raw = json.loads(detection_path.read_text(encoding="utf-8"))
    mci_lookup = indexed_mci_ids(raw)

    rq1 = rq1_detection_stats(raw)
    rq2 = rq2_success_rates(report, mci_lookup)
    failures = failure_breakdown(report)
    rq3 = rq3_cost(report, reference["current_run_model_pricing"])
    declined_categories = categorize_declined_reasons(report)
    goal_shortfalls = analyze_goal_shortfalls(report, mci_lookup, Path(report["projectRoot"]))

    analysis = {
        "rq1": rq1, "rq2": rq2, "failures": failures, "rq3": rq3,
        "declinedCategories": declined_categories, "goalShortfalls": goal_shortfalls,
    }
    out_prefix = Path(args.out) if args.out else report_path.with_name(f"analysis-{report_path.stem}")
    out_prefix.with_suffix(".json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        render_markdown(report, rq1, rq2, failures, rq3, reference, declined_categories, goal_shortfalls),
        encoding="utf-8",
    )
    print(f"wrote {out_prefix.with_suffix('.json')}")
    print(f"wrote {out_prefix.with_suffix('.md')}")


if __name__ == "__main__":
    main()
