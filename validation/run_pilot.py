"""按论文 RQ2.1 的三层标准，在真实项目上验证重构通过率。
Validates the refactoring pass rate on a real project against the paper's RQ2.1 3-tier criteria.

一批 MCI 共享同一个隔离副本，按顺序依次叠加应用，不用每个 MCI 都单独复制一次项目、
各自冷编译一次——项目只复制、只冷编译一次，后续都是在同一份副本上增量编译。
每个 MCI 的 before/after 都运行完整项目回归；被选中的测试类还必须出现在完整测试
报告中，防止全量命令成功却没有实际覆盖改动所在测试。为了保证每个 MCI 的实验结果
互相独立（不受前一个 MCI 是否成功影响），无论这个 MCI 最终成功还是失败，测完都会
把它自己动过的文件恢复原状，下一个 MCI 总是从同一份未改动的基线开始。
A batch of MCIs shares one isolated copy applied cumulatively in order, instead of every MCI
getting its own fresh copy and its own cold compile — the project is copied and cold-compiled
exactly once; everything after that is an incremental compile on the same copy. Each MCI's own
Every MCI runs a complete-project regression before and after the candidate. Its selected test
classes must also appear in the full test report, preventing a green reactor from accepting a
candidate whose affected tests never executed. To keep every MCI independent, its touched files
are restored after either success or failure, so the next MCI starts from the same baseline.

用法 / Usage:
    uv run python validation/run_pilot.py --project-root "D:\\Java_projects\\Apache\\dubbo-3.3.6" --limit 2 --use-mock
    uv run python validation/run_pilot.py --project-root "D:\\Java_projects\\Apache\\dubbo-3.3.6" --limit 2

三个实验对照组（RQ4）用同一批 MCI 跑三次，只切换这两个参数：
Three RQ4 comparison arms, run against the same MCI set by only toggling these two flags:
    直接 LLM baseline / direct LLM baseline  : --direct-llm-baseline --no-repair
    结构化 MCI 一次生成 / structured, one-shot : --no-repair
    结构化 MCI + Harness 修复 / structured + repair (默认 / default): (无参数 / no flags)
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from studio.detection_service import DetectionService  # noqa: E402
from studio.harness import (ProjectHarness, ensure_pit_junit5_support, mutation_regressed,
                             verification_failure_reason)  # noqa: E402
from studio.model_provider import MockModelProvider  # noqa: E402
from studio.refactoring_agent import RefactoringAgent, _workspace_root  # noqa: E402
from validation.diff_utils import replay_replacements  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")


def load_env_file() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def affected_test_classes(instance: dict) -> list[str]:
    classes = set()
    for sequence in instance.get("sequences", []):
        package = sequence.get("packageName") or ""
        class_name = sequence.get("className") or ""
        if class_name:
            classes.add(f"{package}.{class_name}" if package else class_name)
    return sorted(classes)


def _module_for_file(resolved_file: Path, project_root: Path) -> str | None:
    """从测试文件往上找最近的 pom.xml，就是它所属的 Maven 模块 / Walks up from a test
    file to the nearest pom.xml, which is the Maven module it belongs to."""
    current = resolved_file.parent
    while True:
        if current == project_root:
            return None
        if (current / "pom.xml").is_file():
            return current.relative_to(project_root).as_posix()
        if current.parent == current:
            return None
        current = current.parent


def affected_modules(instance: dict, project_root: Path) -> list[str]:
    """这个 MCI 涉及的测试文件分别属于哪些模块，用来给 Maven 传 -pl -am，
    不用每次都把整个 reactor 走一遍。
    Which modules this MCI's test files belong to, used to pass -pl -am to Maven so it
    doesn't have to walk the whole reactor every time."""
    modules: set[str] = set()
    for sequence in instance.get("sequences", []):
        raw_path = Path(sequence.get("filePath", ""))
        path = raw_path if raw_path.is_absolute() else project_root / raw_path
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        module = _module_for_file(resolved, project_root)
        if module:
            modules.add(module)
    return sorted(modules)


def classify_transition(previous: dict, current: dict, goal_achieved: bool, pit_regressed: bool,
                        run_pit: bool = False, expected_test_classes: list[str] | None = None) -> str:
    """按论文 RQ2.1 三层标准，比较相邻两次 harness 结果，再叠加变异体回归与重构目标
    达成检查 / Compares two consecutive harness results per the paper's RQ2.1 three-tier
    criteria, then layers on the mutant-regression and refactoring-goal checks."""
    if current.get("compileStatus") != "PASSED":
        return "FAILED_SYNTACTIC_VALIDITY"
    # testStatus != PASSED 通常意味着 Maven 在跑到目标模块之前就中止了（比如 -am 拉进来的
    # 依赖模块没有匹配 -Dtest 的测试类），这种情况下 testResults 在 before/after 两边都是
    # 空字典，如果只比较字典是否相等会被误判成"结果一致"，必须先确认两边都真的跑完了测试。
    # testStatus != PASSED usually means Maven aborted before ever reaching the target module
    # (e.g. a dependency module pulled in via -am had no test matching -Dtest). In that case
    # testResults is an empty dict on both sides, and comparing dicts alone would misread that
    # as "the same result" — both runs must have actually completed their tests first.
    if previous.get("testStatus") != "PASSED" or current.get("testStatus") != "PASSED":
        return "FAILED_BEHAVIORAL_EQUIVALENCE"
    if verification_failure_reason(previous, False, expected_test_classes) is not None:
        return "FAILED_BEHAVIORAL_EQUIVALENCE"
    if verification_failure_reason(current, False, expected_test_classes) is not None:
        return "FAILED_BEHAVIORAL_EQUIVALENCE"
    if previous.get("testResults") != current.get("testResults"):
        return "FAILED_BEHAVIORAL_EQUIVALENCE"
    # pitStatus 是 NOT_RUN 说明这次调用压根没请求 PIT（正常，不算失败）；FAILED/UNAVAILABLE
    # 说明请求了但没跑成——两边都不跑，mutants 字典两边都是空的，mutation_regressed() 比较
    # 空字典会 vacuously 判定"没有退化"，必须先确认两边真的产出了 PIT 报告，才能相信这个
    # 判定。这是全量 109 个 MCI 跑批里发现的问题：95 个 SUCCESS 全部 pitStatus=FAILED，
    # 一次真正的变异测试都没跑成，之前这里没查 pitStatus，只看了 mutation_regressed。
    # pitStatus == NOT_RUN means this call never asked for PIT at all (fine, not a failure);
    # FAILED/UNAVAILABLE means it was requested but never completed — with no report on
    # either side, the mutants dicts are both empty and mutation_regressed() would vacuously
    # read that as "no regression", so pitStatus must be checked before trusting that verdict.
    # Found via the full 109-MCI batch: all 95 SUCCESS results had pitStatus=FAILED, meaning
    # not a single one had a real mutation-testing run behind it — this wasn't checked before.
    if previous.get("pitStatus") not in ("PASSED", "NOT_RUN") or current.get("pitStatus") not in ("PASSED", "NOT_RUN"):
        return "FAILED_FUNCTIONAL_INTEGRITY"
    if run_pit and (
        verification_failure_reason(previous, True, expected_test_classes) is not None
        or verification_failure_reason(current, True, expected_test_classes) is not None
    ):
        return "FAILED_FUNCTIONAL_INTEGRITY"
    if pit_regressed:
        return "FAILED_FUNCTIONAL_INTEGRITY"
    if not goal_achieved:
        return "FAILED_REFACTORING_GOAL"
    return "SUCCESS"


def build_instructions(direct_llm_baseline: bool) -> str:
    if not direct_llm_baseline:
        # 分阶段路径没有单一 prompt，每一步各用各的。这里把它们串起来只为算 promptHash：
        # 任何一份阶段 prompt 改动都会让哈希变化，跑批之间的可比性因此仍然成立。
        # The staged path has no single prompt; each step uses its own. Concatenating them
        # here only serves promptHash, so that editing any stage prompt changes the hash and
        # runs stay comparable.
        return "\n\n".join(
            RefactoringAgent._prompt(stage, variant)
            for stage, variant in (("ENCAPSULATION", "helper"), ("ENCAPSULATION", "attribute"),
                                   ("INTEGRATION", "before"), ("INTEGRATION", "local"),
                                   ("INTEGRATION", "attribute"))
        )
    # 不告诉模型哪些行是检测器认定的公共 stub，只给源码和一句通用目标——用来衡量
    # "结构化 MCI 上下文"这一项本身值多少。
    # Does not tell the model which lines the detector marked as the shared stub; only
    # the source and a generic goal — used to measure how much the structured MCI
    # context itself is worth.
    return (
        "You refactor Java test code to eliminate duplicated Mockito mock setup logic that "
        "recurs across test methods in the supplied files, while preserving each test's "
        "behavior and intent. You may edit any supplied file and/or create a new shared "
        "helper/fixture file when that is the safest way to do the refactor. Return JSON "
        'only with this shape: {"canRefactor":true,"reason":"...","summary":"...","caveat":"",'
        '"edits":[{"path":"relative/Existing.java","oldString":"exact text","newString":"replacement","replaceAll":false}],'
        '"newFiles":[{"path":"relative/New.java","content":"complete new file"}]}. '
        "Each edit's oldString must match exactly one location unless replaceAll is true. Even "
        "with reservations, still attempt your best, safest refactor and record the concern in "
        '"caveat" rather than refusing outright — only set canRefactor=false when no edit could '
        "possibly apply. Do not use markdown fences."
    )


def build_request(project_root: Path, selected: list[dict], files: dict[Path, str], direct_llm_baseline: bool) -> str:
    if not direct_llm_baseline:
        # 分阶段路径每一步有自己的 payload；这份紧凑描述只用于修复轮次的 originalRequest。
        # Each staged step builds its own payload; this compact description only feeds the
        # repair rounds' originalRequest.
        return json.dumps({
            "selectedMockCloneInstances": [
                {"mockedClass": instance.get("mockedClass", ""),
                 "sequenceCount": instance.get("sequenceCount", 0),
                 "sharedStatementLineCount": instance.get("sharedStatementLineCount", 0)}
                for instance in selected
            ],
            "files": [path.as_posix() for path in files],
        }, ensure_ascii=False)
    payload = {
        "projectRootName": project_root.name,
        "userInstruction": "",
        "sourceFiles": [{"path": path.as_posix(), "content": content} for path, content in files.items()],
    }
    return json.dumps(payload, ensure_ascii=False)


def combined_usage(attempts: list[dict]) -> dict[str, int]:
    totals = {field: 0 for field in USAGE_FIELDS}
    for attempt in attempts:
        usage = attempt.get("usage")
        for field in USAGE_FIELDS:
            totals[field] += getattr(usage, field, 0) or 0
    return totals


def usage_as_dict(usage) -> dict:
    return usage.__dict__ if hasattr(usage, "__dict__") else usage


def load_replay_index(replay_path: Path) -> tuple[dict[str, dict], Path]:
    """加载上一轮的报告，按 mciId 建索引，供 --replay 复用已经生成过的候选，
    不用为了重新验证 harness/PIT 再花一次 token 调用模型。
    Loads a previous run's report indexed by mciId, so --replay can reuse an
    already-generated candidate without spending a token on the model again just to
    re-verify the harness/PIT."""
    old_report = json.loads(replay_path.read_text(encoding="utf-8"))
    old_run_dir = REPO_ROOT / ".clonedemocker" / "runs" / old_report["runId"]
    by_id = {r["mciId"]: r for r in old_report.get("results", []) if "mciId" in r}
    return by_id, old_run_dir


def generate_proposal(run, raw: dict, mci_id: str, provider, model: str, direct_llm_baseline: bool,
                       replay: tuple[dict[str, dict], Path] | None = None) -> dict:
    """只做模型调用（或者 --replay 时套用上一轮已经存好的 diff）+ 校验，不碰任何
    workspace / Either calls the model, or — in --replay mode — reapplies a previous
    run's already-saved diff, plus validation; never touches any workspace either way.
    `selected`/`files` are always computed from the detector's own data regardless of
    direct_llm_baseline, so the host-side goal check can still judge the result even
    when the model itself wasn't shown the MCI structure."""
    selected = RefactoringAgent._select_instances(raw, [mci_id], None)
    if not selected:
        return {"ok": False, "reason": "MCI not found / 未找到该 MCI", "attempts": []}
    files = RefactoringAgent._affected_files(run.project_root, selected)
    if not files:
        return {"ok": False, "reason": "No readable source files / 没有可读取的源文件", "attempts": []}

    instructions = build_instructions(direct_llm_baseline)

    if replay is not None:
        by_id, old_run_dir = replay
        previous = by_id.get(mci_id)
        if previous is None or previous.get("classification") == "MODEL_DECLINED":
            reason = previous.get("reason", "Not present in the replayed run / 上一轮没有这个 MCI") if previous else \
                "MCI absent from the replayed run / 上一轮没有这个 MCI"
            return {"ok": False, "reason": reason, "attempts": []}
        diff_path = old_run_dir / "refactoring" / previous["proposalId"] / "changes.diff"
        if not diff_path.is_file():
            return {"ok": False, "reason": f"changes.diff missing for replay / 上一轮的 changes.diff 找不到了: {diff_path}", "attempts": []}
        replacements = replay_replacements(diff_path.read_text(encoding="utf-8"), files)
        if not replacements:
            return {"ok": False, "reason": "Replayed diff produced no replacements / 重放上一轮的 diff 没有得到有效改动", "attempts": []}
        return {
            "ok": True, "selected": selected, "files": files, "replacements": replacements,
            "instructions": instructions, "request": build_request(run.project_root, selected, files, direct_llm_baseline),
            "proposal": {"canRefactor": True, "summary": "replayed from a previous run / 复用上一轮的候选"},
            "attempts": [], "replayedFromProposalId": previous["proposalId"],
        }

    request = build_request(run.project_root, selected, files, direct_llm_baseline)
    stage_log: list[dict] = []
    if direct_llm_baseline:
        result = provider.generate(instructions, request, model)
        attempts = [{"attempt": 1, "responseId": result.response_id, "usage": result.usage}]
        proposal = RefactoringAgent._parse_json(result.text)
    else:
        # 每个 MCI 一次封装 + 每条 sequence 一次集成；"没有共享 stub"那条分支由代码直接做，
        # 不调模型。跟产品路径 RefactoringAgent.run() 共用同一个生成器，两边不会走偏。
        # One encapsulation per MCI plus one integration per sequence; the "no shared
        # stubbing" branch is done in code without a model call. Shares the one generator
        # with the product path's RefactoringAgent.run(), so the two cannot drift.
        proposal, results, stage_log = RefactoringAgent._generate_staged(
            provider, model, run.project_root, selected, files)
        attempts = [{"attempt": index + 1, "responseId": item.response_id, "usage": item.usage}
                    for index, item in enumerate(results)]
    if not proposal.get("canRefactor", False):
        return {"ok": False, "reason": proposal.get("reason", "Model declined / 模型拒绝重构"),
                "attempts": attempts, "stageLog": stage_log}
    replacements, edit_errors = RefactoringAgent._apply_edits(
        run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))
    # 编辑本身有歧义（oldString 不唯一/没匹配上）时立刻重试，这时候 harness 还没跑过，
    # 重试成本很低，跟 studio/refactoring_agent.py 的 run() 用的是同一套逻辑。
    # Retry immediately when the edits themselves are ambiguous (oldString not unique /
    # not found) — the harness hasn't run yet so retrying here is cheap, mirroring the
    # same logic in studio/refactoring_agent.py's run().
    for _ in range(2):
        if not edit_errors and replacements:
            break
        repair_input = json.dumps({
            "originalRequest": json.loads(request),
            "currentProposal": proposal,
            "editErrors": edit_errors or ["canRefactor was true but no edits or newFiles were provided"],
        }, ensure_ascii=False)
        # 修复面对的是一份完整提案加错误列表，跟分阶段生成"只看一个测试方法"的任务形状
        # 不同，所以用专门的修复指令，不是把阶段 prompt 拼起来再发一遍。
        # Repair works on a complete proposal plus an error list — a different shape from
        # staged generation's "one test method at a time" — so it uses the dedicated repair
        # instructions rather than re-sending the concatenated stage prompts.
        result = provider.generate(
            RefactoringAgent._repair_instructions()
            + "\nFix the previous proposal's edits/newFiles using the errors below.",
            repair_input, model,
        )
        attempts.append({"attempt": len(attempts) + 1, "responseId": result.response_id, "usage": result.usage})
        proposal = RefactoringAgent._parse_json(result.text)
        if not proposal.get("canRefactor", False):
            break
        replacements, edit_errors = RefactoringAgent._apply_edits(
            run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))

    if not proposal.get("canRefactor", False):
        return {"ok": False, "reason": proposal.get("reason", "Model declined / 模型拒绝重构"), "attempts": attempts}
    if not replacements or edit_errors:
        return {
            "ok": False,
            "reason": "Model's edits could not be applied / 模型给出的编辑无法应用: " + "; ".join(edit_errors),
            "attempts": attempts,
        }
    return {
        "ok": True, "selected": selected, "files": files, "replacements": replacements,
        "instructions": instructions, "request": request, "proposal": proposal, "attempts": attempts,
        "caveat": proposal.get("caveat", ""), "stageLog": stage_log,
    }


def write_diff(proposal_root: Path, files: dict, replacements: dict) -> None:
    proposal_root.mkdir(parents=True, exist_ok=True)
    diff_parts: list[str] = []
    for relative, new_content in replacements.items():
        # files.get(..., "")：新建文件在 files 里没有原始内容，用空串当"改动前"，
        # difflib 会正确地把整份新内容渲染成一堆新增行。
        # files.get(..., ""): a brand-new file has no original content in `files`; an
        # empty "before" makes difflib correctly render the whole new content as added lines.
        original = files.get(relative, "")
        diff_parts.extend(difflib.unified_diff(
            original.splitlines(keepends=True), new_content.splitlines(keepends=True),
            fromfile=f"a/{relative.as_posix()}", tofile=f"b/{relative.as_posix()}",
        ))
    (proposal_root / "changes.diff").write_text("".join(diff_parts), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-root", required=True, help="被验证的真实项目路径 / real subject project path")
    parser.add_argument("--limit", type=int, default=2, help="先跑几个 MCI 做试点 / how many MCIs to pilot first")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--api-profile", default="default")
    parser.add_argument("--run-pit", action="store_true", default=True)
    parser.add_argument("--no-pit", dest="run_pit", action="store_false", help="跳过 PIT，只做编译+测试 / skip PIT, compile+test only")
    parser.add_argument("--use-mock", action="store_true", help="不调用真实模型，先验证流程能否跑通 / skip the real model call to test plumbing first")
    parser.add_argument("--resolve-dependencies", action="store_true", default=False)
    parser.add_argument("--direct-llm-baseline", action="store_true", default=False,
                         help="RQ4 对照组：不给模型结构化 MCI，只给源文件和通用目标 / RQ4 arm: skip the "
                              "structured MCI payload, give the model only source files and a generic goal")
    parser.add_argument("--repair", action="store_true", default=True)
    parser.add_argument("--no-repair", dest="repair", action="store_false",
                         help="关闭 Harness 失败后的自动修复（最多两次）/ disable the up-to-two automatic "
                              "repair attempts after a harness failure")
    parser.add_argument("--maven-repo-local", default=None,
                         help="给这次运行的所有 Maven 命令（compile/test/PIT/sanity check/修复后重新验证）"
                              "统一固定一个独立本地仓库（-Dmaven.repo.local），Maven 不会自己按项目目录选"
                              "仓库，不传就还是默认共享的 ~/.m2/repository；多个项目并行验证时（比如同时"
                              "跑 dubbo 和 druid）应各自传一个独立目录，避免共享仓库被并发写坏 / pins an "
                              "isolated local Maven repository (-Dmaven.repo.local) for every Maven command "
                              "in this run (compile/test/PIT/sanity check/post-repair re-validation); Maven "
                              "never infers a repository from the project directory on its own, so omitting "
                              "this keeps the default shared ~/.m2/repository — pass a separate directory per "
                              "project when validating several in parallel (e.g. dubbo and druid at once) to "
                              "avoid concurrent writes corrupting a shared repository")
    parser.add_argument("--replay", default=None,
                         help="复用上一轮已经跑过的报告（pilot-*.json），对每个 MCI 重放它保存的 "
                              "changes.diff 而不是重新调用模型——用于只想重新验证 harness/PIT 行为、"
                              "不想为此再花 token 的场景（比如修了 harness 的 bug 之后要重新验证结果）。"
                              "重放模式下会强制关闭修复循环（不管 --repair 传没传），因为修复本身要调用"
                              "模型；上一轮里 MODEL_DECLINED 或者对应 changes.diff 缺失的 MCI 会原样标记"
                              "为 MODEL_DECLINED，不会去猜 / reuses a previous run's report (pilot-*.json): "
                              "for each MCI, reapplies its saved changes.diff instead of calling the model "
                              "again — for when you only want to re-verify harness/PIT behavior (e.g. after "
                              "fixing a harness bug) without spending tokens on it. Forces the repair loop off "
                              "regardless of --repair, since repairing itself requires a model call; MCIs that "
                              "were MODEL_DECLINED before, or whose changes.diff is missing, are carried over "
                              "as MODEL_DECLINED rather than guessed at")
    parser.add_argument("--only", default=None,
                         help="只跑这些 MCI id（逗号分隔），忽略 --limit——用于针对性重跑某几个失败"
                              "案例（比如改完 prompt/协议之后只想验证之前被拒绝/失败的那几个），不用把"
                              "整批重新跑一遍 / only run these MCI ids (comma-separated), ignoring "
                              "--limit — for targeted reruns of specific failed cases (e.g. after "
                              "changing the prompt/edit protocol, only re-verify the ones that were "
                              "previously declined/failed) without processing the whole batch")
    parser.add_argument("--workspace", default=None,
                         help="复用一个已存在的隔离副本（比如上一次跑完打印出来的 sharedWorkspace），"
                              "跳过复制项目和一次性冷编译，调试时省时间；配合 reset_workspace.py 手动还原 / "
                              "reuse an existing isolated copy (e.g. a previous run's printed sharedWorkspace) "
                              "instead of re-copying the project and cold-compiling again — saves time while "
                              "debugging; pair with reset_workspace.py to restore it manually when needed")
    args = parser.parse_args()

    load_env_file()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    replay = None
    if args.replay:
        replay = load_replay_index(Path(args.replay))
        if args.repair:
            print("  --replay forces repair off (repairing needs a fresh model call) / "
                  "--replay 强制关闭修复循环（修复本身需要调用模型）")
        args.repair = False

    service = DetectionService(REPO_ROOT)
    project_root = str(Path(args.project_root).resolve())

    print(f"[1/3] scan {project_root} ...")
    scan = service.scan(project_root, include_paths=[], exclude_paths=[], package_prefixes=[],
                         resolve_dependencies=args.resolve_dependencies)
    run_id = scan["runId"]
    mock_ids = [item["id"] for item in scan["mockObjects"]]
    print(f"  runId={run_id} mockObjects={len(mock_ids)}")

    print("[2/3] detect (form MCIs) ...")
    detect = service.detect(run_id, mock_ids)
    instances = detect["mockCloneInstances"]
    print(f"  MCIs={len(instances)}")

    if args.only:
        wanted = {mci_id.strip() for mci_id in args.only.split(",") if mci_id.strip()}
        pilot = [instance for instance in instances if instance["id"] in wanted]
        missing = wanted - {instance["id"] for instance in pilot}
        if missing:
            print(f"  WARNING: --only requested MCI ids not found in this scan: {sorted(missing)}")
    else:
        pilot = instances[: args.limit]
    print(f"[3/3] piloting {len(pilot)} MCI(s), use_mock={args.use_mock}, "
          f"direct_llm_baseline={args.direct_llm_baseline}, repair={args.repair}")

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectRoot": project_root,
        "runId": run_id,
        "model": args.model,
        "useMock": args.use_mock,
        "directLlmBaseline": args.direct_llm_baseline,
        "repairEnabled": args.repair,
        "replayedFrom": args.replay,
        "mavenRepoLocal": args.maven_repo_local,
        "totalMcis": len(instances),
        "pilotSize": len(pilot),
        "results": [],
    }

    run, raw = service.load_raw_detection(run_id)
    provider = MockModelProvider() if args.use_mock else RefactoringAgent._openai_provider(args.api_profile)

    runnable = []
    for instance in pilot:
        mci_id = instance["id"]
        test_classes = affected_test_classes(instance)
        modules = affected_modules(instance, run.project_root)
        print(f"  - {mci_id}: {len(test_classes)} test class(es) -> {test_classes}, modules -> {modules}")
        if not test_classes:
            report["results"].append({"mciId": mci_id, "classification": "SKIPPED_NO_TEST_CLASS"})
            continue
        runnable.append((mci_id, test_classes, modules))

    if not runnable:
        report["successRate"] = None
        out_path = RESULTS_DIR / f"pilot-{run_id[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report -> {out_path}")
        return

    if args.workspace:
        workspace = Path(args.workspace)
        if not workspace.is_dir():
            raise SystemExit(f"--workspace path does not exist / --workspace 路径不存在: {workspace}")
        print(f"  reusing existing workspace (no copy, no sanity check) -> {workspace}")
    else:
        batch_id = uuid.uuid4().hex
        workspace = _workspace_root(run.project_root, batch_id)
        print(f"  shared workspace -> {workspace}")
        RefactoringAgent._copy_project(run.project_root, workspace)

        if args.run_pit:
            print("  ensuring PIT can run against JUnit 5 (pitest-junit5-plugin + matching junit-platform-launcher) ...")
            ensure_pit_junit5_support(workspace, args.maven_repo_local)

        print("  [sanity check] full-project compile + test on unmodified copy (no PIT) ..."
              + (f" repoLocal={args.maven_repo_local}" if args.maven_repo_local else ""))
        sanity = ProjectHarness(args.maven_repo_local).validate(workspace, run_pit=False).as_dict()
        sanity_failure = verification_failure_reason(sanity, run_pit=False)
        if sanity_failure is not None:
            print("  ABORT: unmodified project cannot complete full-project regression in the shared workspace / "
                  "未修改的项目无法在共享副本中完成全项目回归")
            report["initialSanityCheck"] = sanity
            report["initialSanityFailure"] = sanity_failure
            report["sharedWorkspace"] = str(workspace)
            for mci_id, test_classes, _ in runnable:
                report["results"].append({
                    "mciId": mci_id, "testClasses": test_classes,
                    "classification": "SKIPPED_BASELINE_FAILED",
                })
            report["pitInvocations"] = 0
            report["successRate"] = None
            out_path = RESULTS_DIR / f"pilot-{run_id[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"report -> {out_path}")
            return

    report["sharedWorkspace"] = str(workspace)

    pit_runs = 0
    for mci_id, test_classes, modules in runnable:
        print(f"  - {mci_id}: {len(test_classes)} test class(es) -> {test_classes}")
        # Full-project regression is the acceptance gate. The selected classes remain an
        # explicit evidence requirement, so a green reactor cannot hide skipped targets.
        mci_harness = ProjectHarness(args.maven_repo_local)
        print(f"    [baseline] full-project compile + test" + (" + PIT" if args.run_pit else "") + " ...")
        pit_runs += 1
        before_state = mci_harness.validate(workspace, args.run_pit).as_dict()
        baseline_failure = verification_failure_reason(before_state, args.run_pit, test_classes)
        if baseline_failure is not None:
            report["results"].append({
                "mciId": mci_id,
                "testClasses": test_classes,
                "classification": "SKIPPED_BASELINE_FAILED",
                "reason": baseline_failure,
                "attempts": [],
                "usage": {field: 0 for field in USAGE_FIELDS},
                "harness": {"before": before_state, "after": None},
            })
            print(f"    -> SKIPPED_BASELINE_FAILED ({baseline_failure})")
            continue

        proposal = generate_proposal(run, raw, mci_id, provider, args.model, args.direct_llm_baseline, replay)
        if not proposal["ok"]:
            report["results"].append({
                "mciId": mci_id, "testClasses": test_classes,
                "classification": "MODEL_DECLINED", "reason": proposal["reason"],
                "attempts": [{"attempt": a["attempt"], "responseId": a["responseId"], "usage": usage_as_dict(a["usage"])}
                             for a in proposal.get("attempts", [])],
                "usage": combined_usage(proposal.get("attempts", [])),
            })
            print(f"    -> MODEL_DECLINED ({proposal['reason']})")
            continue

        selected = proposal["selected"]
        files = proposal["files"]
        replacements = proposal["replacements"]
        attempts = list(proposal["attempts"])
        prompt_hash = hashlib.sha256(proposal["instructions"].encode("utf-8")).hexdigest()[:16]

        # None 表示这个路径在改动前根本不存在（模型新建的文件）——恢复原状时要删掉它，
        # 不是写回空字符串。
        # None means the path didn't exist before this change at all (a file the model
        # created) — restoring means deleting it, not writing back an empty string.
        pre_image = {
            relative: ((workspace / relative).read_text(encoding="utf-8") if (workspace / relative).is_file() else None)
            for relative in replacements
        }
        for relative, new_content in replacements.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8", newline="\n")

        proposal_id = uuid.uuid4().hex
        proposal_root = run.run_directory / "refactoring" / proposal_id
        write_diff(proposal_root, files, replacements)

        print(f"    [after] full-project compile + test" + (" + PIT" if args.run_pit else "") + " ...")
        pit_runs += 1
        after_state = mci_harness.validate(workspace, args.run_pit).as_dict()
        goal_achieved = RefactoringAgent._goal_check(selected, files, replacements)
        pit_regressed = args.run_pit and mutation_regressed(before_state, after_state)
        classification = classify_transition(
            before_state, after_state, goal_achieved, pit_regressed,
            args.run_pit, test_classes,
        )
        first_pass_classification = classification

        # Harness 失败（编译不过或测试行为变了）时把机器诊断交回模型，最多修复两次；
        # 目标未达成或变异体回归不属于"模型能靠日志修的问题"，不进入修复循环。
        # On a harness failure (compile broken or test behavior changed), feed machine
        # diagnostics back to the model for up to two repair attempts; an unmet
        # refactoring goal or a mutant regression isn't something a log excerpt fixes, so
        # those don't enter the repair loop.
        repair_rounds = 0
        while (
            args.repair
            and classification in {"FAILED_SYNTACTIC_VALIDITY", "FAILED_BEHAVIORAL_EQUIVALENCE"}
            and repair_rounds < 2
        ):
            repair_rounds += 1
            repair_input = json.dumps({
                "originalRequest": json.loads(proposal["request"]),
                "currentProposal": proposal["proposal"],
                "harnessDiagnostics": after_state.get("diagnostics", []),
            }, ensure_ascii=False)
            result = provider.generate(
                RefactoringAgent._repair_instructions()
                + "\nRepair the previous proposal using the harness diagnostics.",
                repair_input, args.model,
            )
            attempts.append({"attempt": len(attempts) + 1, "responseId": result.response_id, "usage": result.usage})
            repaired = RefactoringAgent._parse_json(result.text)
            if not repaired.get("canRefactor", False):
                break
            repaired_replacements, repaired_errors = RefactoringAgent._apply_edits(
                run.project_root, files, repaired.get("edits", []), repaired.get("newFiles", []))
            if not repaired_replacements or repaired_errors:
                break
            for relative, original_content in pre_image.items():
                if original_content is None:
                    (workspace / relative).unlink(missing_ok=True)
                else:
                    (workspace / relative).write_text(original_content, encoding="utf-8", newline="\n")
            replacements = repaired_replacements
            proposal["proposal"] = repaired
            pre_image = {
                relative: ((workspace / relative).read_text(encoding="utf-8") if (workspace / relative).is_file() else None)
                for relative in replacements
            }
            for relative, new_content in replacements.items():
                target = workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new_content, encoding="utf-8", newline="\n")
            write_diff(proposal_root, files, replacements)
            pit_runs += 1
            after_state = mci_harness.validate(workspace, args.run_pit).as_dict()
            goal_achieved = RefactoringAgent._goal_check(selected, files, replacements)
            pit_regressed = args.run_pit and mutation_regressed(before_state, after_state)
            classification = classify_transition(
                before_state, after_state, goal_achieved, pit_regressed,
                args.run_pit, test_classes,
            )

        # 无论最终成功还是失败，都把这个 MCI 动过的文件恢复原状，下一个 MCI 从同一份
        # 未改动基线开始，实验之间互相独立。
        # Restore whatever this MCI touched regardless of the final outcome, so the next
        # MCI always starts from the same unmodified baseline and results stay independent
        # of each other.
        for relative, original_content in pre_image.items():
            if original_content is None:
                (workspace / relative).unlink(missing_ok=True)
            else:
                (workspace / relative).write_text(original_content, encoding="utf-8", newline="\n")

        report["results"].append({
            "mciId": mci_id,
            "testClasses": test_classes,
            "classification": classification,
            "firstPassClassification": first_pass_classification,
            "repairRounds": repair_rounds,
            "goalAchieved": goal_achieved,
            "mutationRegressed": pit_regressed,
            "proposalId": proposal_id,
            "promptHash": prompt_hash,
            "replayedFromProposalId": proposal.get("replayedFromProposalId"),
            "modelCaveat": proposal.get("caveat", ""),
            "attempts": [{"attempt": a["attempt"], "responseId": a["responseId"], "usage": usage_as_dict(a["usage"])}
                         for a in attempts],
            "usage": combined_usage(attempts),
            "harness": {"before": before_state, "after": after_state},
        })
        print(f"    -> {classification}"
              + (f" (first pass: {first_pass_classification}, {repair_rounds} repair round(s))" if repair_rounds else ""))

    report["pitInvocations"] = pit_runs
    success = sum(1 for r in report["results"] if r.get("classification") == "SUCCESS")
    first_pass_success = sum(1 for r in report["results"] if r.get("firstPassClassification") == "SUCCESS")
    attempted = len([r for r in report["results"] if r.get("classification") not in {"SKIPPED_NO_TEST_CLASS", "SKIPPED_BASELINE_FAILED"}])
    report["successRate"] = (success / attempted) if attempted else None
    report["firstPassSuccessRate"] = (first_pass_success / attempted) if attempted else None
    print(f"\nsuccess {success}/{attempted} (first pass {first_pass_success}/{attempted}), PIT invocations={pit_runs}")

    out_path = RESULTS_DIR / f"pilot-{run_id[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report -> {out_path}")


if __name__ == "__main__":
    main()
