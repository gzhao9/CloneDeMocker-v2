from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from studio.detection_service import DetectionError, DetectionService
from studio.harness import (BuildScope, HarnessEvidence, ProjectHarness, ensure_pit_junit5_support,
                             mutation_regressed, verification_failure_reason)
from studio.mechanical import rename_local_mock_to_field
from studio.model_provider import MockModelProvider, ModelProvider, ModelResult, ModelUsage, OpenAIModelProvider
from studio.payloads import (PayloadError, encapsulation_payload, integration_payload, route_encapsulation,
                             route_integration, verbatim_failures)
from studio.verification_ledger import VerificationLedger

_PROMPT_DIRECTORY = Path(__file__).parent / "prompts"
_PROMPT_FILES = {
    ("ENCAPSULATION", "helper"): "encapsulation_helper.md",
    ("ENCAPSULATION", "attribute"): "encapsulation_attribute.md",
    ("INTEGRATION", "before"): "integration_before.md",
    ("INTEGRATION", "local"): "integration_local.md",
    ("INTEGRATION", "attribute"): "integration_attribute.md",
}


_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")


def _baseline_test_failure_keys(evidence: Any) -> set[str]:
    return {key for key, status in evidence.test_results.items() if status in {"FAILED", "ERROR"}}


def _test_regression_reason(baseline: Any, candidate: Any) -> str | None:
    """Compare test identities against a non-clean baseline without accepting new failures."""
    before, after = baseline.test_results, candidate.test_results
    if not after:
        return "Candidate produced no test results / 候选未产生测试结果"
    for key, status in before.items():
        candidate_status = after.get(key)
        if candidate_status is None:
            return f"A baseline test did not run in the candidate: {key}"
        if status == "PASSED" and candidate_status != "PASSED":
            return f"A previously passing test regressed: {key} ({candidate_status})"
    for key, status in after.items():
        if status in {"FAILED", "ERROR"} and before.get(key) not in {"FAILED", "ERROR"}:
            return f"Candidate introduced a new failing test: {key}"
    return None


def _long_path(path: Path) -> Path:
    """规避 Windows 默认 260 字符 MAX_PATH 限制（真实项目 + 我们自己 run/proposal 目录嵌套
    很容易超限，报 WinError 206）。用扩展长度前缀 \\\\?\\，之后所有 / 拼接都会带着它。
    Works around Windows' default 260-char MAX_PATH limit (real projects nested under our
    own run/proposal directories easily exceed it, raising WinError 206). Uses the \\\\?\\
    extended-length prefix; every subsequent `/` join inherits it automatically.
    """
    if os.name != "nt":
        return path
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\" + resolved)


def _workspace_root(project_root: Path, proposal_id: str) -> Path:
    """
    隔离编译副本放在被检测项目所在目录的同级（而不是我们工具自己的 .clonedemocker 下，
    也不是笼统的系统临时目录）。实测发现：如果放副本的路径含非 ASCII 字符（比如云同步文件夹
    的中文名，我们工具自己就常年放在这种目录下），Windows 上 file.encoding=UTF-8 与
    sun.jnu.encoding=GBK 不一致，会让 javac/Maven 在处理真实项目（比如 dubbo）的深层源码路径时
    静默编译失败——不报任何具体错误，只有笼统的 "Compilation failure"；开启 LongPathsEnabled
    也无法解决，因为这不是路径长度问题。被检测项目自己的目录（例如 C:\\Java_projects\\Apache\\dubbo）
    通常是纯 ASCII 路径，放在它同级既规避编码问题，也不会把大体积的项目副本同步进我们工具所在的
    云同步目录。
    The isolated compile copy lives next to the analyzed project's own directory (not under this
    tool's .clonedemocker folder, and not in a generic system temp dir either). Empirically: if the
    copy's path contains non-ASCII characters (e.g. a cloud-sync folder's Chinese name — where this
    tool itself happens to live), the mismatch between file.encoding=UTF-8 and Windows'
    sun.jnu.encoding=GBK makes javac/Maven silently fail to compile deep source paths in a real
    project (e.g. dubbo) — no specific diagnostic at all, just a generic "Compilation failure".
    Enabling LongPathsEnabled does not fix this, since it is not a path-length problem. The analyzed
    project's own directory (e.g. C:\\Java_projects\\Apache\\dubbo) is typically a plain ASCII path,
    so a sibling of it avoids both the encoding mismatch and syncing large project copies into this
    tool's own cloud-synced directory.
    """
    return project_root.parent / ".clonedemocker-workspaces" / proposal_id


class AgentStage(StrEnum):
    """与论文重构步骤对应的 Agent 状态 / Agent states aligned with the paper."""

    ENCAPSULATION = "ENCAPSULATION"
    INTEGRATION = "INTEGRATION"
    HARNESS_VALIDATION = "HARNESS_VALIDATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RefactoringAgent:
    """生成隔离候选补丁并保留可复核 diff / Produces an isolated, reviewable candidate patch."""

    def __init__(self, detection: DetectionService, provider: ModelProvider | None = None,
                 harness: ProjectHarness | None = None) -> None:
        self.detection = detection
        self.provider = provider
        # 可注入自定义 Harness（例如按测试范围裁剪的版本），默认沿用全量 compile/test/PIT。
        # Harness is injectable (e.g. a test-scoped variant); defaults to the full compile/test/PIT one.
        self.harness = harness if harness is not None else ProjectHarness()

    def run(self, run_id: str, selected_mci_ids: list[str], model: str,
            user_instruction: str = "", run_pit: bool = False, api_profile: str = "default",
            use_mock: bool = False, sequence_selection: dict[str, list[int]] | None = None,
            max_retries: int = 2, use_cache: bool = True,
            progress_callback: Callable[[str, int, str], None] | None = None,
            baseline_evidence_override: HarnessEvidence | None = None,
            workspace_id: str | None = None,
            reuse_verification: bool = True) -> dict[str, Any]:
        """
        跑完一个 MCI。传了 `workspace_id` 就复用整批共享的那份隔离副本，并在结束时（无论
        成功还是失败）把自己动过的文件恢复原状——否则下一个 MCI 的 baseline 会把上一个 MCI
        的改动当成"项目本来的样子"，各个 MCI 的结果就不再互相独立了。
        Runs one MCI. With `workspace_id` it reuses the batch's shared isolated copy and, on
        success or failure alike, restores the files it touched — otherwise the next MCI's
        baseline would read the previous MCI's changes as "how the project already was", and
        the MCIs would stop being independent of one another.
        """
        restore_slot: dict[str, Callable[[], None]] = {}
        try:
            return self._run(run_id, selected_mci_ids, model, user_instruction, run_pit, api_profile,
                             use_mock, sequence_selection, max_retries, use_cache, progress_callback,
                             baseline_evidence_override, workspace_id, reuse_verification,
                             restore_slot)
        finally:
            if workspace_id and "restore" in restore_slot:
                restore_slot["restore"]()

    def _run(self, run_id: str, selected_mci_ids: list[str], model: str,
             user_instruction: str, run_pit: bool, api_profile: str,
             use_mock: bool, sequence_selection: dict[str, list[int]] | None,
             max_retries: int, use_cache: bool,
             progress_callback: Callable[[str, int, str], None] | None,
             baseline_evidence_override: HarnessEvidence | None,
             workspace_id: str | None,
             reuse_verification: bool,
             restore_slot: dict[str, Callable[[], None]]) -> dict[str, Any]:
        def progress(phase: str, percent: int, detail: str) -> None:
            if progress_callback:
                progress_callback(phase, percent, detail)

        def validate(root: Path, pit: bool, start: int, end: int, prefix: str,
                     scope: BuildScope | None = None) -> Any:
            if isinstance(self.harness, ProjectHarness):
                span = max(1, end - start)
                return self.harness.validate(
                    root, pit,
                    progress_callback=lambda phase, percent, detail: progress(
                        f"{prefix}_{phase}", start + round(span * percent / 100), detail
                    ),
                    scope=scope,
                )
            return self.harness.validate(root, pit)

        # 论文 RQ3 要的是"重构一个 MCI 要多久"。这个数只能在跑的当下记，事后无从还原，
        # 所以从这里就开始计时，并把生成与各验证阶段分开——它们的量级差了一两个数量级，
        # 合成一个总数会让这个指标失去意义。
        # RQ3 asks how long refactoring one MCI takes. That can only be measured while it
        # happens, so timing starts here, keeping generation and each verification phase
        # apart: they differ by an order of magnitude or two, and one combined figure would
        # say nothing.
        run_started_at = time.time()
        timings: dict[str, float] = {}
        progress("PREPARING", 2, "Resolving selected MCI source and test scope")
        run, raw = self.detection.load_raw_detection(run_id)
        selected = self._select_instances(raw, selected_mci_ids, sequence_selection)
        if not selected:
            raise DetectionError("Select at least one MCI / 请至少选择一个 MCI")

        files = self._affected_files(run.project_root, selected)
        if not files:
            raise DetectionError("Selected MCIs contain no readable source files / 所选 MCI 没有可读取的源文件")

        cache_key = self._cache_key(selected, files, user_instruction, run_pit)
        cache_record = self._read_cache(cache_key) if use_cache else None

        # 只是构造 provider（顺带校验 API key 是否配好），还没有发起任何调用。放在跑
        # baseline 之前是因为它是毫秒级的配置检查——没配 key 的用户应该立刻被告知，
        # 而不是白等一次几十分钟的冷编译才看到"API 配置不存在"。
        # This only constructs the provider (and validates that an API key is configured);
        # no call is made yet. It sits before the baseline because it is a millisecond-level
        # config check — a user with no key should be told immediately, rather than sitting
        # through a cold compile of tens of minutes only to be told the profile is missing.
        # 调试模式：跳过真实模型调用，不消耗 token。
        # Debug mode: skip the real model call so no tokens are spent.
        provider: ModelProvider | None = None

        def get_provider() -> ModelProvider:
            nonlocal provider
            if provider is None:
                provider = MockModelProvider() if use_mock else (self.provider or self._openai_provider(api_profile))
            return provider
        proposal_id = uuid.uuid4().hex
        proposal_root = _long_path(run.run_directory / "refactoring" / proposal_id)
        proposal_root.mkdir(parents=True)

        # 先在隔离副本上跑 baseline，再调模型。baseline 跟模型产出毫无关系，它衡量的是
        # "这个项目在当前环境里本来能不能编译、测试能不能过"；一旦它不干净，后面无论
        # 模型写出什么都没有可比的参照物，deterministic_verified 也必然为 False。
        # 之前的顺序是先调模型再跑 baseline，结果环境坏掉时（比如隔离副本里 parent POM
        # 解析不了）会先花掉一次生成调用，再把机器诊断当成"模型的错"喂回去修两轮——
        # 三次调用全部注定失败。实测一次这样的空转烧掉 82363 tokens，而正常一次成功重构
        # 只要 3554。把 baseline 提到前面，环境问题的代价就从几万 token 变成 0。
        # Run the baseline on the isolated copy *before* calling the model. The baseline has
        # nothing to do with the model's output — it measures whether this project compiles
        # and passes its tests in this environment at all; once it is not clean there is no
        # reference point for anything the model produces, and deterministic_verified is
        # guaranteed to be False anyway. The previous order called the model first and only
        # then ran the baseline, so a broken environment (e.g. a parent POM that won't
        # resolve inside the isolated copy) burned one generation call and then fed the
        # machine diagnostics back as if they were the model's fault for two more repair
        # rounds — three calls, all doomed. One such spin measured 82363 tokens, against
        # 3554 for a successful refactoring. Moving the baseline first takes the cost of an
        # environment problem from tens of thousands of tokens down to zero.
        # 范围必须在 baseline 之前就算出来，否则它没得裁。这正是原来那个"预检 baseline"
        # 的死结：它在用户选 MCI 之前就跑，那时根本不知道涉及哪些模块，只能编译全部
        # 124 个（Dubbo），一次二十几分钟。把 baseline 推迟到这里，它才第一次有条件只
        # 编译真正相关的那几个模块。
        # The scope has to be known before the baseline, or there is nothing to narrow it
        # with. That was the deadlock in the old pre-flight baseline: it ran before the user
        # had picked an MCI, so it could not know which modules were involved and compiled
        # all 124 of them (Dubbo), taking twenty-odd minutes. Deferring the baseline to here
        # is what first makes it possible to compile only the modules that matter.
        target_classes, target_modules = self._target_test_scope(run.project_root, selected, files)
        scope = BuildScope(modules=tuple(target_modules), test_classes=tuple(target_classes))

        workspace = _workspace_root(run.project_root, workspace_id or proposal_id)
        if workspace.exists() and workspace_id:
            # 整批共用一份副本：后续 MCI 直接复用已经编译好的 target/，走增量编译。
            # 每个 MCI 各复制一份的话，`_copy_project` 排除了 target/，等于每个 MCI 都要
            # 冷编译一次——N 个 MCI 就是 N 次。
            # One copy shared by the batch: later MCIs reuse the compiled target/ and build
            # incrementally. With a copy per MCI, `_copy_project` excludes target/, so every
            # MCI pays a cold compile — N MCIs, N cold compiles.
            progress("WORKSPACE_REUSED", 6, "Reusing the batch's shared workspace")
        else:
            progress("COPYING", 6, "Creating isolated workspace")
            self._copy_project(run.project_root, workspace)
        if run_pit:
            ensure_pit_junit5_support(workspace, getattr(self.harness, "maven_repo_local", None))
        # 同一个模块里的 MCI 共用同一份未改动源码，它们的基线编译与测试跑的是字节完全相同
        # 的输入，结论注定一致。账本让这件事只做一次——一批 99 个 MCI 若集中在少数几个模块，
        # 省下的就是几十次完整的编译加测试。
        # MCIs in one module share the same unmodified source, so their baseline compile and
        # test run over byte-identical input and are bound to agree. The ledger does it once:
        # for 99 MCIs concentrated in a handful of modules, that saves dozens of full runs.
        ledger = VerificationLedger(self.detection.repository_root)
        source_fingerprint = VerificationLedger.fingerprint(files)
        verification_reused: dict[str, str] = {}
        baseline_key = VerificationLedger.key(
            "baseline", source_fingerprint, scope.describe(), run_pit, self._generation())
        recorded_baseline = ledger.read(baseline_key) if reuse_verification else None

        if baseline_evidence_override is not None:
            progress("BASELINE_REUSED", 24, "Reusing source-matched baseline evidence")
            baseline_evidence = baseline_evidence_override
        elif recorded_baseline is not None:
            progress("BASELINE_REUSED", 24,
                     f"Reusing a baseline verified at {recorded_baseline['recordedAt']}")
            baseline_evidence = HarnessEvidence.from_dict(recorded_baseline["evidence"])
            verification_reused["baseline"] = recorded_baseline["recordedAt"]
        else:
            progress("BASELINE", 12, f"Running baseline for {scope.describe()}")
            baseline_evidence = validate(workspace, run_pit, 8, 34, "BASELINE", scope)
            ledger.write(baseline_key, baseline_evidence.as_dict(), "baseline")
        expected_test_classes = getattr(self.harness, "expected_test_classes", lambda: [])()
        baseline_failure = self._baseline_failure_reason(
            baseline_evidence, run_pit, expected_test_classes,
        )
        # A project may have a stable, external-environment failure (DNS, ports, clock)
        # before this tool changes any file. Treat it as a recorded comparison baseline,
        # not as a burden the user must repair. PIT cannot be compared in that state.
        known_baseline_failure = (
            baseline_evidence.compile_status.value == "PASSED"
            and baseline_evidence.test_status.value == "FAILED"
            and bool(baseline_evidence.test_results)
        )
        effective_run_pit = run_pit
        if known_baseline_failure:
            baseline_failure = None
            effective_run_pit = False
        if baseline_failure is not None:
            return self._environment_failure(proposal_id, baseline_failure, baseline_evidence)

        baseline_target = None
        observed_baseline_classes = {key.split("#", 1)[0] for key in baseline_evidence.test_results}
        validate_targets = getattr(self.harness, "validate_targets", None)
        needs_target_receipt = bool(
            target_classes and (known_baseline_failure or not set(target_classes).issubset(observed_baseline_classes))
        )
        if needs_target_receipt and callable(validate_targets):
            progress("TARGET_BASELINE", 28, "Full regression stopped early; running selected target tests")
            baseline_target = validate_targets(workspace, target_classes, target_modules)
            target_failure = verification_failure_reason(baseline_target, False, target_classes)
            if target_failure is not None:
                return self._environment_failure(
                    proposal_id, "Selected target tests do not provide a valid baseline: " + target_failure,
                    baseline_evidence,
                )
        elif needs_target_receipt and known_baseline_failure:
            return self._environment_failure(
                proposal_id, "Harness cannot prove that selected target tests executed / Harness 无法证明目标测试已执行",
                baseline_evidence,
            )

        # 修复循环需要一份"原始请求"做上下文。分阶段之后不存在单一请求了，所以这里给它
        # 一份紧凑的任务描述（选了哪些 MCI、涉及哪些文件），而不是把整包源码再发一遍。
        # The repair loop needs an "original request" for context. Staged generation has no
        # single request, so this is a compact description of the task (which MCIs, which
        # files) rather than shipping the whole source again.
        request = json.dumps({
            "selectedMockCloneInstances": [
                {"mockedClass": instance.get("mockedClass", ""),
                 "sequenceCount": instance.get("sequenceCount", 0),
                 "sharedStatementLineCount": instance.get("sharedStatementLineCount", 0)}
                for instance in selected
            ],
            "files": [path.as_posix() for path in files],
            "userInstruction": user_instruction,
        }, ensure_ascii=False)
        repair_history: list[dict[str, Any]] = []
        stage_log: list[dict[str, Any]] = []
        cache_hit = cache_record is not None
        if cache_record is not None:
            progress("CACHE", 38, "Loading matching verified proposal from cache")
            proposal = cache_record["proposal"]
            result = ModelResult(
                text=json.dumps(proposal, ensure_ascii=False), response_id="cache-" + cache_key[:12],
                model=str(cache_record.get("model") or model), usage=ModelUsage(), raw=None,
            )
            model_results: list[ModelResult] = []
        else:
            progress("GENERATING", 38, "Running encapsulation and integration stages")
            generation_started_at = time.time()
            proposal, model_results, stage_log = self._generate_staged(
                get_provider(), model, run.project_root, selected, files, user_instruction, progress)
            timings["generation"] = round(time.time() - generation_started_at, 2)
            result = (model_results[-1] if model_results else
                      ModelResult(json.dumps(proposal, ensure_ascii=False), "mechanical-only", model,
                                  ModelUsage(), None))
        if not proposal.get("canRefactor", False):
            return self._failure(proposal_id, proposal.get("reason", "Model declined the refactoring"), result)

        replacements, edit_errors = self._apply_edits(
            run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))
        if cache_hit and (edit_errors or not replacements):
            progress("CACHE_FALLBACK", 40, "Cached edits no longer apply; generating a fresh proposal")
            cache_hit = False
            proposal, fresh_results, stage_log = self._generate_staged(
                get_provider(), model, run.project_root, selected, files, user_instruction, progress)
            model_results.extend(fresh_results)
            if fresh_results:
                result = fresh_results[-1]
            replacements, edit_errors = self._apply_edits(
                run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))
        # 编辑歧义和后续 Harness 修复共用同一个明确预算；0 表示只接受首次生成。
        # Edit ambiguity and later harness repairs share one explicit budget; zero means
        # the initial generation is the only model attempt.
        remaining_retries = max(0, max_retries)
        while remaining_retries > 0:
            if not edit_errors and replacements:
                break
            attempt_number = max_retries - remaining_retries + 1
            remaining_retries -= 1
            repair_input = json.dumps({
                "originalRequest": json.loads(request),
                "currentProposal": proposal,
                "editErrors": edit_errors or ["canRefactor was true but no edits or newFiles were provided"],
            }, ensure_ascii=False)
            repair_instruction = self._repair_instructions() + (
                f"\nRepair attempt {attempt_number} of {max_retries}; {remaining_retries} retries remain after this call. "
                "Only correct edits/newFiles so they apply exactly to the supplied test sources. "
                "Do not change production code, build files, or unrelated tests. Return a complete replacement JSON proposal."
            )
            result = get_provider().generate(
                repair_instruction,
                repair_input, model)
            model_results.append(result)
            repair_history.append({"attempt": attempt_number, "type": "EDIT_APPLICATION",
                                   "prompt": repair_instruction, "input": json.loads(repair_input),
                                   "response": result.text})
            proposal = self._parse_json(result.text)
            if not proposal.get("canRefactor", False):
                break
            replacements, edit_errors = self._apply_edits(
                run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))

        if not proposal.get("canRefactor", False):
            return self._failure(proposal_id, proposal.get("reason", "Model declined the refactoring"), result)
        if not replacements or edit_errors:
            return self._failure(
                proposal_id,
                "Model's edits could not be applied / 模型给出的编辑无法应用: " + "; ".join(edit_errors),
                result,
            )

        candidate_files = proposal_root / "candidate-files"
        diff_parts: list[str] = []
        workspace_baseline: dict[Path, str | None] = {}

        def remember_workspace_paths(paths: dict[Path, str]) -> None:
            for relative in paths:
                if relative not in workspace_baseline:
                    target = workspace / relative
                    workspace_baseline[relative] = target.read_text(encoding="utf-8") if target.is_file() else None

        def restore_workspace_baseline() -> None:
            for relative, original_content in workspace_baseline.items():
                target = workspace / relative
                if original_content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(original_content, encoding="utf-8", newline="\n")

        restore_slot["restore"] = restore_workspace_baseline
        remember_workspace_paths(replacements)
        for relative, new_content in replacements.items():
            original = files.get(relative, "")
            target = candidate_files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n"：Windows 上 write_text 默认会把 \n 转成 os.linesep（\r\n），
            # 把本来是 LF 的源码写出来会变成 CRLF，触发 dubbo 等项目的 Spotless 格式检查失败。
            # newline="\n": on Windows, write_text otherwise translates \n to os.linesep
            # (\r\n), turning LF source into CRLF and tripping formatting checks like
            # dubbo's Spotless.
            target.write_text(new_content, encoding="utf-8", newline="\n")
            diff_parts.extend(difflib.unified_diff(
                original.splitlines(keepends=True), new_content.splitlines(keepends=True),
                fromfile=f"a/{relative.as_posix()}", tofile=f"b/{relative.as_posix()}",
            ))
        diff_text = "".join(diff_parts)
        (proposal_root / "changes.diff").write_text(diff_text, encoding="utf-8")

        # 候选改动写进上面那个已经跑过 baseline 的隔离副本；原项目不会被写入。
        # The candidate changes go into the same isolated copy the baseline already ran in;
        # the source project is never written.
        for relative, new_content in replacements.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8", newline="\n")
        # 候选侧的键要带上补丁本身的指纹：源码相同但补丁不同，验证结论完全可以不同。
        # 命中的情形是同一份补丁被再次验证（缓存复用、或断点后补跑到同一个 MCI）。
        # The candidate key carries the patch fingerprint too: the same sources with a
        # different patch can verify differently. A hit means this exact patch was verified
        # before, from a cache reuse or from resuming onto the same MCI.
        candidate_key = VerificationLedger.key(
            "candidate", source_fingerprint, scope.describe(), effective_run_pit,
            self._generation(), VerificationLedger.fingerprint(replacements))
        recorded_candidate = ledger.read(candidate_key) if reuse_verification else None
        if recorded_candidate is not None:
            progress("CANDIDATE_REUSED", 82,
                     f"Reusing a candidate verified at {recorded_candidate['recordedAt']}")
            candidate_evidence = HarnessEvidence.from_dict(recorded_candidate["evidence"])
            verification_reused["candidate"] = recorded_candidate["recordedAt"]
        else:
            progress("CANDIDATE", 55, f"Running candidate regression for {scope.describe()}")
            candidate_evidence = validate(workspace, effective_run_pit, 52, 82, "CANDIDATE", scope)
            ledger.write(candidate_key, candidate_evidence.as_dict(), "candidate")
        candidate_target = None
        if baseline_target is not None:
            progress("TARGET_CANDIDATE", 70, "Running selected target tests on the candidate")
            candidate_target = self.harness.validate_targets(workspace, target_classes, target_modules)

        initial_regression = _test_regression_reason(baseline_evidence, candidate_evidence) if known_baseline_failure else None
        initial_target_regression = (_test_regression_reason(baseline_target, candidate_target)
                                     if baseline_target is not None and candidate_target is not None else None)
        cached_candidate_failed = (
            candidate_evidence.compile_status.value == "FAILED"
            or (not known_baseline_failure and candidate_evidence.test_status.value == "FAILED")
            or bool(initial_regression or initial_target_regression)
        )
        if cache_hit and cached_candidate_failed:
            progress("CACHE_FALLBACK", 72, "Cached proposal failed revalidation; generating a fresh proposal")
            cache_hit = False
            proposal, fresh_results, stage_log = self._generate_staged(
                get_provider(), model, run.project_root, selected, files, user_instruction, progress)
            model_results.extend(fresh_results)
            if fresh_results:
                result = fresh_results[-1]
            replacements, edit_errors = self._apply_edits(
                run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))
            while remaining_retries > 0 and (edit_errors or not replacements):
                attempt_number = max_retries - remaining_retries + 1
                remaining_retries -= 1
                repair_input = json.dumps({
                    "originalRequest": json.loads(request), "currentProposal": proposal,
                    "editErrors": edit_errors or ["canRefactor was true but no effective edits were provided"],
                }, ensure_ascii=False)
                repair_instruction = self._repair_instructions() + (
                    f"\nRepair attempt {attempt_number} of {max_retries}; {remaining_retries} retries remain after this call. "
                    "Correct only the edit application errors and return a complete replacement JSON proposal."
                )
                result = get_provider().generate(repair_instruction, repair_input, model)
                model_results.append(result)
                repair_history.append({"attempt": attempt_number, "type": "CACHE_FALLBACK_EDIT",
                                       "prompt": repair_instruction, "input": json.loads(repair_input),
                                       "response": result.text})
                proposal = self._parse_json(result.text)
                replacements, edit_errors = self._apply_edits(
                    run.project_root, files, proposal.get("edits", []), proposal.get("newFiles", []))
            if edit_errors or not replacements:
                return self._failure(proposal_id, "Fresh proposal after cache failure could not be applied: "
                                     + "; ".join(edit_errors), result)
            restore_workspace_baseline()
            remember_workspace_paths(replacements)
            for relative, new_content in replacements.items():
                target = workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new_content, encoding="utf-8", newline="\n")
            candidate_evidence = validate(workspace, effective_run_pit, 52, 82, "CANDIDATE", scope)
            candidate_target = (self.harness.validate_targets(workspace, target_classes, target_modules)
                                if baseline_target is not None else None)

        # Harness 失败时把机器诊断交回模型，最多修复两次。
        # Feed machine diagnostics back to the model for at most two repair attempts.
        while remaining_retries > 0:
            regression = _test_regression_reason(baseline_evidence, candidate_evidence) if known_baseline_failure else None
            target_regression = (_test_regression_reason(baseline_target, candidate_target)
                                 if baseline_target is not None and candidate_target is not None else None)
            if (candidate_evidence.compile_status.value != "FAILED"
                    and (known_baseline_failure or candidate_evidence.test_status.value != "FAILED")
                    and not regression and not target_regression):
                break
            attempt_number = max_retries - remaining_retries + 1
            remaining_retries -= 1
            repair_input = json.dumps({
                "originalRequest": json.loads(request),
                "currentProposal": proposal,
                "harnessDiagnostics": candidate_evidence.diagnostics,
                "targetTestDiagnostics": candidate_target.diagnostics if candidate_target else [],
                "baselineKnownFailures": sorted(_baseline_test_failure_keys(baseline_evidence)),
            }, ensure_ascii=False)
            repair_instruction = self._repair_instructions() + (
                f"\nRepair attempt {attempt_number} of {max_retries}; {remaining_retries} retries remain after this call. "
                "Repair only candidate-introduced failures shown in candidate or target-test diagnostics. "
                "Do not change production code, build files, or unrelated tests; do not attempt to fix baseline-known environment failures. "
                "Return a complete replacement JSON proposal.\nHarness diagnostics follow."
            )
            result = get_provider().generate(repair_instruction, repair_input, model)
            model_results.append(result)
            repair_history.append({"attempt": attempt_number, "type": "HARNESS",
                                   "prompt": repair_instruction, "input": json.loads(repair_input),
                                   "response": result.text})
            repaired = self._parse_json(result.text)
            if not repaired.get("canRefactor", False):
                proposal = repaired
                break
            repaired_files, repaired_errors = self._apply_edits(
                run.project_root, files, repaired.get("edits", []), repaired.get("newFiles", []))
            if not repaired_files or repaired_errors:
                break
            proposal, replacements = repaired, repaired_files
            restore_workspace_baseline()
            remember_workspace_paths(replacements)
            for relative, new_content in replacements.items():
                (workspace / relative).write_text(new_content, encoding="utf-8", newline="\n")
            candidate_evidence = validate(workspace, effective_run_pit, 52, 82, "CANDIDATE", scope)
            if baseline_target is not None:
                candidate_target = self.harness.validate_targets(workspace, target_classes, target_modules)

        # 重新生成最终候选文件和 diff，使界面展示的是最后一次修复结果。
        # Rebuild candidate files and diff so the UI shows the final repair attempt.
        diff_parts = []
        for relative, new_content in replacements.items():
            original = files.get(relative, "")
            target = candidate_files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8", newline="\n")
            diff_parts.extend(difflib.unified_diff(original.splitlines(keepends=True), new_content.splitlines(keepends=True),
                                                   fromfile=f"a/{relative.as_posix()}", tofile=f"b/{relative.as_posix()}"))
        diff_text = "".join(diff_parts)
        (proposal_root / "changes.diff").write_text(diff_text, encoding="utf-8")
        goal_achieved = self._goal_check(selected, files, replacements)
        # Product policy: a test-only refactoring may cause small PIT measurement
        # fluctuations, but must not lower the mutation score by more than five
        # percentage points.  Keep the mutant-identity comparison as evidence in
        # the report, rather than silently discarding it.
        baseline_score = baseline_evidence.mutation_score
        candidate_score = candidate_evidence.mutation_score
        mutation_score_delta = (
            candidate_score - baseline_score
            if baseline_score is not None and candidate_score is not None else None
        )
        identity_regressed = effective_run_pit and mutation_regressed(
            baseline_evidence.as_dict(), candidate_evidence.as_dict())
        pit_regressed = effective_run_pit and mutation_score_delta is not None and mutation_score_delta < -0.05
        # run_pit 为 True 时，两边的 pitStatus 都必须真的是 PASSED；否则 mutants 字典两边
        # 都是空的，mutation_regressed() 比较空字典会 vacuously 判定"没有退化"，等于从没
        # 验证过这一层。基于 validation/run_pilot.py 那次全量跑批发现的同一个问题在这里
        # 补上，避免这个产品路径也悄悄把"PIT 根本没跑成"当成"PIT 通过了"。
        # When run_pit is True, both sides' pitStatus must genuinely be PASSED; otherwise
        # both mutants dicts are empty and mutation_regressed() vacuously reads that as "no
        # regression" — meaning this tier was never actually verified. Applying the same fix
        # found via validation/run_pilot.py's full batch run here too, so this product path
        # doesn't silently treat "PIT never ran" as "PIT passed".
        pit_ran_cleanly = not effective_run_pit or (
            baseline_evidence.pit_status.value == "PASSED" and candidate_evidence.pit_status.value == "PASSED"
        )
        candidate_failure = (_test_regression_reason(baseline_evidence, candidate_evidence)
                             if known_baseline_failure else verification_failure_reason(
                                 candidate_evidence, effective_run_pit, expected_test_classes))
        target_failure = None
        if baseline_target is not None:
            if candidate_target is None:
                target_failure = "Candidate target tests were not executed / 候选目标测试未执行"
            else:
                target_failure = verification_failure_reason(candidate_target, False, target_classes)
                target_failure = target_failure or _test_regression_reason(baseline_target, candidate_target)
        candidate_failure = candidate_failure or target_failure
        deterministic_verified = (
            baseline_failure is None
            and candidate_failure is None
            and (known_baseline_failure or baseline_evidence.test_results == candidate_evidence.test_results)
            and pit_ran_cleanly
            and not pit_regressed
            and goal_achieved
        )
        progress("AUDIT", 88, "Reviewing deterministic evidence")
        if cache_hit and deterministic_verified:
            audit = dict(((cache_record.get("response") or {}).get("harness") or {}).get("aiAudit") or {
                "status": "CACHED", "risk": "LOW", "reason": "Previously audited cache entry revalidated",
            })
        else:
            audit = self._audit_refactoring(
                get_provider(), model, selected, diff_text, baseline_evidence.as_dict(), candidate_evidence.as_dict(),
                deterministic_verified, use_mock,
            )
        if audit.get("modelResult") is not None:
            model_results.append(audit.pop("modelResult"))
        ai_concern = audit.get("risk") not in {"LOW", "SKIPPED"}
        equivalent = deterministic_verified and not ai_concern
        usage = self._combined_usage(model_results)
        validation_reason = (
            candidate_failure or "Baseline non-regression check passed; pre-existing environment failures were unchanged"
            if known_baseline_failure else self._validation_reason(
                candidate_evidence, goal_achieved, pit_regressed, audit,
                effective_run_pit, expected_test_classes,
            )
        )

        response = {
            "proposalId": proposal_id,
            "stage": AgentStage.COMPLETED,
            "reason": proposal.get("reason", ""),
            "summary": proposal.get("summary", ""),
            "diff": diff_text,
            "changedFiles": [path.as_posix() for path in replacements],
            "harness": {
                "baseline": baseline_evidence.as_dict(),
                "candidate": candidate_evidence.as_dict(),
                "equivalent": equivalent,
                "goalAchieved": goal_achieved,
                "mutationRegressed": pit_regressed,
                "mutationIdentityRegressed": identity_regressed,
                "mutationScoreDelta": mutation_score_delta,
                "mutationScoreThreshold": 0.05,
                "baselineKnownFailures": sorted(_baseline_test_failure_keys(baseline_evidence)),
                "comparisonMode": "BASELINE_NON_REGRESSION" if known_baseline_failure else "CLEAN_BASELINE",
                "pitSkippedForBaselineFailure": bool(run_pit and known_baseline_failure),
                "targetBaseline": baseline_target.as_dict() if baseline_target else None,
                "targetCandidate": candidate_target.as_dict() if candidate_target else None,
                "targetTestClasses": target_classes,
                "aiAudit": audit,
            },
            "validationReason": validation_reason,
            "caveat": proposal.get("caveat", ""),
            "usage": usage,
            "modelCalls": len(model_results),
            "model": result.model,
            "responseId": result.response_id,
            "cache": {"hit": cache_hit, "key": cache_key, "revalidated": cache_hit},
            "repairHistory": repair_history,
            "repairAttemptsUsed": max_retries - remaining_retries,
            "repairAttemptsRemaining": remaining_retries,
            # 每一步走了哪条分支、是否就地重试过、哪一步被跳过及原因。没有它，分阶段流水线
            # 对外就是一个黑盒——只能看到最终 diff，看不出这条 sequence 是模型做的、代码做的，
            # 还是压根没做成。
            # Which branch each step took, whether it retried in place, and which steps were
            # skipped and why. Without it the staged pipeline is a black box from the outside:
            # only the final diff is visible, with no way to tell whether a sequence was done
            # by the model, done in code, or never done at all.
            "stageLog": stage_log,
            # 哪几层是复用的、复用的是什么时候的记录。报告里必须能区分本次实测与复用，
            # 否则「通过验证」这句话就没法核实。
            # Which tiers were reused and from when. A report has to distinguish measured from
            # replayed, or its claim of passing verification cannot be checked.
            "verificationReused": verification_reused,
            # generation 是模型那几次调用的墙钟；baseline/candidate 来自 harness 自己的
            # 分阶段计时；total 是这个 MCI 从头到尾的墙钟，包含复制工作区和审计。
            # generation is the wall clock across the model calls; baseline/candidate come
            # from the harness's own per-phase timing; total is this MCI end to end, copying
            # the workspace and the audit included.
            "timings": {
                **timings,
                "baseline": baseline_evidence.durations,
                "candidate": candidate_evidence.durations,
                "total": round(time.time() - run_started_at, 2),
            },
        }
        (proposal_root / "proposal.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        # 新建文件在 files 里没有原始内容，用 None 当哈希占位——apply() 据此判断"这个路径
        # 这次必须还不存在"，而不是去比对一份根本不存在的原始内容的哈希。
        # New files have no original content in `files`; None marks that as a hash
        # placeholder — apply() uses it to require "this path must not exist yet" instead
        # of comparing against a hash of content that was never there.
        manifest = {
            path.as_posix(): (hashlib.sha256(files[path].encode("utf-8")).hexdigest() if path in files else None)
            for path in replacements
        }
        (proposal_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if equivalent:
            self._write_cache(cache_key, {
                "version": 1, "key": cache_key, "proposal": proposal, "response": response,
                "model": result.model, "sourceHashes": manifest,
            })
        progress("COMPLETED", 100, "Proposal validated and ready for review")
        return response

    def apply(self, run_id: str, proposal_id: str, force: bool = False) -> dict[str, Any]:
        """审核后应用候选文件 / Applies candidate files after explicit UI review."""
        run = self.detection.load_run(run_id)
        if not re.fullmatch(r"[0-9a-f]{32}", proposal_id or ""):
            raise DetectionError("Invalid proposal ID / 无效的候选补丁 ID")
        proposal_root = (run.run_directory / "refactoring" / proposal_id).resolve()
        expected_parent = (run.run_directory / "refactoring").resolve()
        if proposal_root.parent != expected_parent or not proposal_root.is_dir():
            raise DetectionError("Proposal not found / 未找到候选补丁")
        # 校验用普通路径，实际读写换成扩展长度路径，避免深层项目触发 Windows MAX_PATH。
        # Validation uses the plain path; actual I/O switches to the extended-length form
        # to avoid Windows' MAX_PATH limit on deeply nested projects.
        proposal_root = _long_path(proposal_root)
        proposal = json.loads((proposal_root / "proposal.json").read_text(encoding="utf-8"))
        verified = bool((proposal.get("harness") or {}).get("equivalent"))
        if not verified and not force:
            raise DetectionError(
                "Proposal did not pass every verification gate; use an explicit forced apply after reviewing "
                "the evidence / 提案未通过全部验证门禁；审阅证据后才可明确选择强制应用"
            )
        manifest = json.loads((proposal_root / "manifest.json").read_text(encoding="utf-8"))

        pending: list[tuple[Path, str]] = []
        for relative_text, expected_hash in manifest.items():
            relative = Path(relative_text)
            target = (run.project_root / relative).resolve()
            try:
                target.relative_to(run.project_root)
            except ValueError as error:
                raise DetectionError("Proposal path escaped the project / 候选路径超出项目") from error
            if expected_hash is None:
                if target.exists():
                    raise DetectionError(
                        f"Proposal wanted to create a new file but it already exists; regenerate it / "
                        f"候选想新建的文件已经存在，请重新生成: {relative_text}"
                    )
            else:
                current = target.read_text(encoding="utf-8")
                if hashlib.sha256(current.encode("utf-8")).hexdigest() != expected_hash:
                    raise DetectionError(f"Source changed after proposal; regenerate it / 源码在生成补丁后已变化: {relative_text}")
            candidate = (proposal_root / "candidate-files" / relative).read_text(encoding="utf-8")
            pending.append((target, candidate))

        for target, candidate in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".clonedemocker.tmp")
            temporary.write_text(candidate, encoding="utf-8", newline="\n")
            os.replace(temporary, target)
        applied = {"proposalId": proposal_id, "applied": True, "forced": force,
                   "changedFiles": list(manifest), "message": "Patch applied / 补丁已应用"}
        (proposal_root / "applied.json").write_text(json.dumps(applied, ensure_ascii=False, indent=2), encoding="utf-8")
        return applied

    def cache_status(self, run_id: str, selected_mci_ids: list[str], user_instruction: str = "",
                     run_pit: bool = False,
                     sequence_selection: dict[str, list[int]] | None = None) -> dict[str, Any]:
        """Return only an exact, source-bound cache match; never a fuzzy solution."""
        run, raw = self.detection.load_raw_detection(run_id)
        selected = self._select_instances(raw, selected_mci_ids, sequence_selection)
        files = self._affected_files(run.project_root, selected)
        if not selected or not files:
            return {"available": False}
        key = self._cache_key(selected, files, user_instruction, run_pit)
        record = self._read_cache(key)
        response = (record or {}).get("response") or {}
        return {
            "available": record is not None,
            "key": key,
            "summary": response.get("summary", "") if record else "",
            "changedFiles": response.get("changedFiles", []) if record else [],
            "validated": bool(((response.get("harness") or {}).get("equivalent"))) if record else False,
        }

    def cache_status_many(self, run_id: str, selected_mci_ids: list[str], user_instruction: str = "",
                          run_pit: bool = False,
                          sequence_selection: dict[str, list[int]] | None = None) -> list[dict[str, Any]]:
        """
        一次性查一批 MCI 的缓存状态，检测数据只加载一次。

        原来是每个 MCI 各调一次 cache_status，而它每次都重新读取并解析整份检测 JSON——
        Dubbo 那份 14 MB，99 个 MCI 就是把同一个文件解析 99 遍。实测这让"点下按钮到弹出
        确认框"之间空等 7 秒，期间界面没有任何反馈，看起来像卡死。
        Checks a batch of MCIs in one pass, loading the detection data once. Previously each
        MCI called cache_status, which re-read and re-parsed the whole detection JSON every
        time — 14 MB for Dubbo, parsed 99 times over. Measured, that left seven seconds of
        silence between the click and the dialog, which reads as a freeze.
        """
        run, raw = self.detection.load_raw_detection(run_id)
        sequence_selection = sequence_selection or {}
        results: list[dict[str, Any]] = []
        for mci_id in selected_mci_ids:
            narrowed = {mci_id: sequence_selection[mci_id]} if mci_id in sequence_selection else None
            selected = self._select_instances(raw, [mci_id], narrowed)
            files = self._affected_files(run.project_root, selected)
            if not selected or not files:
                results.append({"mciId": mci_id, "available": False})
                continue
            key = self._cache_key(selected, files, user_instruction, run_pit)
            record = self._read_cache(key)
            response = (record or {}).get("response") or {}
            results.append({
                "mciId": mci_id,
                "available": record is not None,
                "key": key,
                "summary": response.get("summary", "") if record else "",
                "changedFiles": response.get("changedFiles", []) if record else [],
                "validated": bool(((response.get("harness") or {}).get("equivalent"))) if record else False,
            })
        return results

    def clear_cache(self, run_id: str, selected_mci_ids: list[str], user_instruction: str = "",
                    run_pit: bool = False,
                    sequence_selection: dict[str, list[int]] | None = None) -> dict[str, Any]:
        """
        删掉这些 MCI 对应的缓存条目，让下一次运行真的重新生成。

        跟"这次不读缓存"不是一回事：只是绕过，下次它还在，还会再命中。用户说"清除缓存
        重新生成"要的是前者。
        Deletes these MCIs' cache entries so the next run genuinely regenerates. Not the same
        as bypassing the cache for one run: a bypassed entry is still there and still hits
        next time, and "clear the cache and regenerate" asks for the former.
        """
        run, raw = self.detection.load_raw_detection(run_id)
        removed = 0
        for mci_id in selected_mci_ids:
            selected = self._select_instances(raw, [mci_id], sequence_selection)
            files = self._affected_files(run.project_root, selected)
            if not selected or not files:
                continue
            key = self._cache_key(selected, files, user_instruction, run_pit)
            path = self._cache_directory() / f"{key}.json"
            if path.exists():
                path.unlink(missing_ok=True)
                removed += 1
        return {"cleared": removed}

    def _cache_directory(self) -> Path:
        path = self.detection.repository_root / ".clonedemocker" / "proposal-cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _generation() -> str:
        """
        当前这一代提示词与流水线的指纹。任何一份阶段 prompt 或编辑协议改动，指纹就变。

        缓存里装的是模型在某一版指令下给出的答案。指令改了，那些答案就不再代表这套系统
        的输出，留着只会让下一次跑批混进上一版的结果——尤其危险的是它不花 token，所以
        不会有任何迹象提醒你用的是旧答案。
        A fingerprint of the current prompt-and-pipeline generation. Editing any stage prompt
        or the edit protocol changes it.

        The cache holds answers the model gave under one version of the instructions. Once
        those change, the answers no longer represent this system's output, and keeping them
        lets a later batch silently mix in results from the previous version — the more
        dangerous because a cache hit spends no tokens, so nothing signals that the answer is
        stale.
        """
        digest = hashlib.sha256(b"staged-v1")
        for name in sorted(path.name for path in _PROMPT_DIRECTORY.glob("*.md")):
            digest.update(name.encode("utf-8"))
            digest.update((_PROMPT_DIRECTORY / name).read_bytes())
        return digest.hexdigest()[:16]

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        path = self._cache_directory() / f"{key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if value.get("version") != 1 or value.get("key") != key:
            return None
        if value.get("generation") != self._generation():
            return None
        if not bool((((value.get("response") or {}).get("harness") or {}).get("equivalent"))):
            return None
        return value

    def _write_cache(self, key: str, value: dict[str, Any]) -> None:
        generation = self._generation()
        value = {**value, "generation": generation}
        directory = self._cache_directory()
        # 只保留当前这一代：写入的同时把上一代的条目清掉，缓存目录不会随着 prompt 迭代
        # 越积越多，也不会留下一堆永远不会再命中、却看不出为什么的文件。
        # Keep only the current generation: pruning older entries on write stops the cache
        # directory from growing with every prompt iteration and from holding files that can
        # never match again for reasons nothing on disk explains.
        for existing in directory.glob("*.json"):
            try:
                if json.loads(existing.read_text(encoding="utf-8")).get("generation") != generation:
                    existing.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                existing.unlink(missing_ok=True)
        path = directory / f"{key}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _cache_key(instances: list[dict[str, Any]], files: dict[Path, str],
                   user_instruction: str, run_pit: bool) -> str:
        # schema 2：改为按 MCI 的身份特征做键，不再把整个检测器 blob 序列化进去。源码本身
        # 已经由 sources 的哈希覆盖，所以把 blob 也塞进来只会让键随检测器字段的增减而无谓
        # 变动——检测器加一个与重构无关的字段，全部缓存就凭空失效一次。
        # schema 2: keyed on each MCI's identifying features rather than a serialized copy of
        # the detector blob. The source is already covered by the `sources` hashes, so folding
        # the blob in only made the key churn whenever a detector field came or went — one
        # unrelated new field would invalidate every cache entry for nothing.
        payload = {
            "schema": 2,
            "instances": sorted(
                [{"mockedClass": str(instance.get("mockedClass", "")),
                  "sequenceCount": instance.get("sequenceCount", 0),
                  "sharedStatementLineCount": instance.get("sharedStatementLineCount", 0),
                  "sequences": sorted(
                      f'{sequence.get("filePath", "")}::{sequence.get("testMethodName", "")}'
                      f'::{sequence.get("variableName", "")}::{sequence.get("mockObjectId", "")}'
                      for sequence in instance.get("sequences", []))}
                 for instance in instances],
                key=lambda entry: entry["mockedClass"]),
            "sources": {path.as_posix(): hashlib.sha256(content.encode("utf-8")).hexdigest()
                        for path, content in sorted(files.items(), key=lambda item: item[0].as_posix())},
            "instruction": user_instruction.strip(),
            "runPit": bool(run_pit),
            "policy": {"fullProjectGate": True, "targetSupplement": True, "pitTolerance": 0.05},
            "pipeline": "staged-v1",
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _target_test_scope(project_root: Path, instances: list[dict[str, Any]],
                           files: dict[Path, str]) -> tuple[list[str], list[str]]:
        classes: set[str] = set()
        modules: set[str] = set()
        for relative, content in files.items():
            package_match = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", content)
            package_name = package_match.group(1) if package_match else ""
            class_name = relative.stem
            classes.add(f"{package_name}.{class_name}" if package_name else class_name)
            parent = (project_root / relative).parent
            while parent != project_root and project_root in parent.parents:
                if (parent / "pom.xml").is_file() or (parent / "build.gradle").is_file() or (parent / "build.gradle.kts").is_file():
                    modules.add(parent.relative_to(project_root).as_posix())
                    break
                parent = parent.parent
        return sorted(classes), sorted(modules)

    @staticmethod
    def _select_instances(raw: dict[str, Any], selected_ids: list[str],
                           sequence_selection: dict[str, list[int]] | None = None) -> list[dict[str, Any]]:
        # 允许在一个已形成的 MCI 内部再挑选子集 sequence（例如 9 条只重构 7 条），
        # 不传该 MCI 的条目时默认保留全部 sequence，向后兼容。
        # Lets callers narrow an already-formed MCI to a subset of sequences
        # (e.g. refactor 7 of 9); an MCI absent from the map keeps every sequence.
        wanted = set(selected_ids)
        sequence_selection = sequence_selection or {}
        result: list[dict[str, Any]] = []
        for mocked_class, instances in raw.get("detectedMockClones", {}).items():
            for index, instance in enumerate(instances):
                mci_id = f"{mocked_class}::{index + 1}"
                if mci_id not in wanted:
                    continue
                allowed_ids = sequence_selection.get(mci_id)
                if allowed_ids is None:
                    result.append(instance)
                    continue
                allowed = set(allowed_ids)
                narrowed = dict(instance)
                narrowed["sequences"] = [
                    sequence for sequence in instance.get("sequences", [])
                    if sequence.get("mockObjectId") in allowed
                ]
                if narrowed["sequences"]:
                    result.append(narrowed)
        return result

    # 这些目录下的 .java 不是项目源码：`.clonedemocker-workspaces` 是本工具自己的一次性
    # 副本，`target`/`build` 是构建产物。检测器如果把它们扫进去（工具早期版本会把副本建在
    # 被测项目里面，就发生过），MCI 里就会混进副本路径。
    # 这在全量构建时是隐形的，裁剪之后会直接炸：`_target_test_scope()` 会把
    # `.clonedemocker-workspaces/<id>/dubbo-remoting-api` 当成一个模块交给 `-pl`，Maven 报
    # `Could not find the selected project in the reactor` 然后整个编译失败。实测一次跑批里
    # 123 个 MCI 有 29 个（24%）同时指向真实文件和它的副本。
    # A .java under these is not project source: `.clonedemocker-workspaces` holds this tool's
    # own disposable copies, `target`/`build` are build output. When the detector indexes them
    # (an earlier version placed copies inside the analyzed project, and this did happen), copy
    # paths end up inside an MCI.
    # That stays invisible under a full-reactor build and breaks outright once scoped:
    # `_target_test_scope()` offers `.clonedemocker-workspaces/<id>/dubbo-remoting-api` to `-pl`,
    # Maven answers `Could not find the selected project in the reactor`, and the compile fails.
    # Measured on one batch: 29 of 123 MCIs (24%) pointed at both a real file and its copy.
    _NON_SOURCE_DIRECTORIES = frozenset({".clonedemocker-workspaces", ".clonedemocker", "target", "build"})

    @classmethod
    def _is_project_source(cls, relative: Path) -> bool:
        return not (cls._NON_SOURCE_DIRECTORIES & set(relative.parts))

    @classmethod
    def _affected_files(cls, project_root: Path, instances: list[dict[str, Any]]) -> dict[Path, str]:
        files: dict[Path, str] = {}
        for instance in instances:
            for sequence in instance.get("sequences", []):
                raw_path = Path(sequence.get("filePath", ""))
                path = raw_path if raw_path.is_absolute() else project_root / raw_path
                try:
                    resolved = path.resolve(strict=True)
                    relative = resolved.relative_to(project_root)
                except (OSError, ValueError):
                    continue
                if resolved.suffix == ".java" and cls._is_project_source(relative):
                    files[relative] = resolved.read_text(encoding="utf-8")
        return files

    @staticmethod
    def _goal_check(instances: list[dict[str, Any]], files: dict[Path, str], replacements: dict[Path, str]) -> bool:
        """
        轻量版"重构目标是否真的达成"检查：不重新跑检测器，只确认每个 MCI 里被标记为
        共享/可复用的 mock 语句（shareableMockLines），在改动后的文件里重复出现的次数
        确实比改动前少——而不是只信任模型自己写的 summary。按空白归一化后做逐行精确
        比较，用于容忍模型对缩进/空格的无关重排。
        Lightweight "was the refactoring goal actually achieved" check: instead of
        re-running the detector, this confirms that each MCI's shared/reusable mock
        statements (shareableMockLines) actually occur fewer times in the patched files
        than before — rather than trusting the model's own summary. Lines are compared
        exactly after whitespace normalization, to tolerate incidental
        indentation/spacing reflow from the model.

        shareableMockLines 是按物理位置分类的（Attribute/@Before/@After/Helper Method），
        不是按是否 mock 相关分类的——同一个 mock 的 sequence 里可能混进跟 mock 毫无关系的
        语句（比如把这个 mock 塞进某个普通 List 的那一行）。这类语句即使在文件别处因为完
        全无关的原因（例如另一个测试方法自己 clear() 之后重新构造同名变量）而重复出现，也
        不代表模型没有完成"消除 mock 克隆"这个目标，所以这里必须交叉引用同一 sequence 的
        rawStatementInfo，只保留 isMockRelated 为真的行，再做前后出现次数的比较。
        shareableMockLines is bucketed by physical location (Attribute/@Before/@After/
        Helper Method), not by mock-relatedness — a mock's sequence can include lines with
        nothing to do with mocking (e.g. the line that adds this mock into a plain List).
        Such a line can recur elsewhere in the file for reasons entirely unrelated to mock
        cloning (e.g. a different test method rebuilding a same-named local variable after
        its own clear()), and that recurrence says nothing about whether the model actually
        eliminated the mock clone. So this cross-references each sequence's rawStatementInfo
        and keeps only lines where isMockRelated is true before comparing occurrence counts.
        """

        def normalize(line: str) -> str:
            return " ".join(line.split())

        for instance in instances:
            shared_lines: set[str] = set()
            for sequence in instance.get("sequences", []):
                raw_statement_info = sequence.get("rawStatementInfo") or {}
                for line_key, value in (sequence.get("shareableMockLines") or {}).items():
                    info = raw_statement_info.get(line_key)
                    if not isinstance(info, dict) or not info.get("isMockRelated"):
                        continue
                    normalized = normalize(str(value))
                    if normalized:
                        shared_lines.add(normalized)
            if not shared_lines:
                continue
            for relative, new_content in replacements.items():
                original = files.get(relative)
                if original is None:
                    continue
                original_lines = [normalize(line) for line in original.splitlines()]
                new_lines = [normalize(line) for line in new_content.splitlines()]
                for shared in shared_lines:
                    before = original_lines.count(shared)
                    after = new_lines.count(shared)
                    if before > 1 and after >= before:
                        return False
        return True

    @staticmethod
    def _prompt(stage: str, variant: str) -> str:
        """按分支取对应的阶段 prompt，并接上所有阶段共用的编辑协议。
        Loads the stage prompt for this branch and appends the edit protocol every stage
        shares."""
        name = _PROMPT_FILES.get((stage, variant))
        if name is None:
            raise DetectionError(f"No prompt for {stage}/{variant} / 没有对应的 prompt: {stage}/{variant}")
        body = (_PROMPT_DIRECTORY / name).read_text(encoding="utf-8")
        protocol = (_PROMPT_DIRECTORY / "_edit_protocol.md").read_text(encoding="utf-8")
        return f"{body}\n\n---\n\n{protocol}"

    @staticmethod
    def _generate_staged(provider: ModelProvider, model: str, project_root: Path,
                         instances: list[dict[str, Any]], files: dict[Path, str],
                         user_instruction: str = "",
                         progress: Callable[[str, int, str], None] | None = None,
                         ) -> tuple[dict[str, Any], list[ModelResult], list[dict[str, Any]]]:
        """
        按论文的两步走生成提案：每个 MCI 先做一次封装，再对它的每条 sequence 各做一次集成。

        这样做的理由都来自真实失败案例：一次性把整个 MCI 的全部源文件交给模型，它会因为
        "文件太大、怕改坏没动的部分"而整体放弃（Dubbo 的 `ExtensionLoader::1`、
        `EventListener::1`），也会只处理其中一部分重复、漏掉另一组（`Invoker::3`、
        `Directory<DemoService>::1`）。逐条 sequence 调用时每次只看一个测试方法，前一种
        失败没有了土壤，后一种在结构上不可能发生——每条 sequence 都被强制单独处理一次。

        "没有共享 stub"那条分支（论文 2.3）完全不调模型：它只是删掉局部创建、把引用改名到
        类级别字段，没有需要判断的语义。代码做得更快更便宜，而且可复现——同一份输入永远
        得到同一份输出，不会在两次跑批之间漂移。前置条件不成立时才退回模型。

        Produces a proposal along the paper's two steps: one encapsulation call per MCI,
        then one integration call per sequence.

        Both reasons come from real failures: handed an entire MCI's source files at once,
        the model gives up outright because the file is "too large to reproduce reliably"
        (Dubbo's `ExtensionLoader::1`, `EventListener::1`), and it silently handles only
        part of the duplication (`Invoker::3`, `Directory<DemoService>::1`). Per-sequence
        calls show it one test method at a time, which removes the ground for the first and
        makes the second structurally impossible — every sequence gets its own pass.

        The "no shared stubbing" branch (the paper's 2.3) never calls the model: it deletes
        a local creation and renames references to a class-level field, with no semantic
        judgement involved. Code is faster, cheaper and reproducible — the same input always
        yields the same output, with no drift between batch runs. It falls back to the model
        only when a precondition does not hold.
        """
        self = RefactoringAgent  # 静态方法内沿用同名调用风格 / keep the same call style inside a static method
        working = dict(files)
        model_results: list[ModelResult] = []
        stage_log: list[dict[str, Any]] = []
        edits: list[dict[str, Any]] = []
        new_files: list[dict[str, Any]] = []
        summaries: list[str] = []
        caveats: list[str] = []

        total_steps = sum(1 + len(instance.get("sequences") or []) for instance in instances) or 1
        completed = 0

        def advance(detail: str) -> None:
            # 带上"第几步 / 共几步"。一个 MCI 有二十几条 sequence 时，只说"正在处理
            # Connection::testFoo"看不出还剩多少——百分比又被压在生成阶段那 12% 的窄区间里，
            # 几乎不动。数出来的分数是这里唯一能表达进度的东西。
            # Carries "step N of M". With two dozen sequences in one MCI, naming the current one
            # says nothing about how many remain, and the percentage is squeezed into the
            # generation stage's narrow 12-point band where it barely moves. The counted
            # fraction is the only thing here that can express progress.
            nonlocal completed
            completed += 1
            if progress is not None:
                progress("GENERATING", 38 + round(12 * completed / total_steps),
                         f"[{completed}/{total_steps}] {detail}")

        def call(stage: str, variant: str, payload: dict[str, Any], label: str
                 ) -> tuple[dict[str, Any], dict[Path, str]] | None:
            """
            跑一步，并在这一步的编辑应用不上时就地重试一次。

            重试放在这里而不是留给外层的修复循环，是因为这时 harness 还没跑过：错误是
            "oldString 没匹配上/不唯一"这种纯局部的问题，上下文只有这一个测试方法那么大，
            修起来又快又准。等它冒泡到外层，外层看到的是一份拼装好的完整提案，既分不清
            是哪一步出的问题，也要带着整包上下文重来。
            Runs one step and retries in place when its edits do not apply. The retry belongs
            here rather than in the outer repair loop because the harness has not run yet:
            the error is purely local ("oldString not found / not unique") and its context is
            just this one test method, so it is cheap and precise to fix. By the time it
            reaches the outer loop, that loop sees one assembled proposal, cannot tell which
            step went wrong, and has to redo everything with the full context.
            """
            failures = verbatim_failures(payload, working)
            if failures:
                stage_log.append({"stage": stage, "variant": variant, "label": label,
                                  "skipped": "verbatim check failed", "detail": failures})
                return None

            instructions = self._prompt(stage, variant)
            text = json.dumps(payload, ensure_ascii=False)
            for attempt in (1, 2):
                result = provider.generate(instructions, text, model)
                model_results.append(result)
                parsed = self._parse_json(result.text)
                entry = {"stage": stage, "variant": variant, "label": label, "attempt": attempt,
                         "canRefactor": bool(parsed.get("canRefactor", False)),
                         "reason": parsed.get("reason", ""), "responseId": result.response_id}
                if not parsed.get("canRefactor", False):
                    stage_log.append(entry)
                    return None
                applied, errors = self._apply_edits(project_root, working, parsed.get("edits", []),
                                                    parsed.get("newFiles", []))
                if applied and not errors:
                    stage_log.append(entry)
                    return parsed, applied
                entry["editErrors"] = errors or ["canRefactor was true but no edit changed anything"]
                stage_log.append(entry)
                if attempt == 2:
                    return None
                instructions = self._repair_instructions()
                text = json.dumps({"originalPayload": payload, "currentProposal": parsed,
                                   "editErrors": entry["editErrors"]}, ensure_ascii=False)
            return None

        for instance in instances:
            mocked = str(instance.get("mockedClass", "")).rsplit(".", 1)[-1]
            variant = route_encapsulation(instance)
            try:
                payload = encapsulation_payload(project_root, instance, working, user_instruction)
            except PayloadError as error:
                stage_log.append({"stage": "ENCAPSULATION", "label": mocked, "skipped": str(error)})
                advance(f"Skipped {mocked}: {error}")
                continue

            advance(f"Encapsulating {mocked}")
            outcome = call("ENCAPSULATION", variant, payload, mocked)
            if outcome is None:
                continue
            proposal, applied = outcome
            working.update(applied)
            edits.extend(proposal.get("edits", []))
            new_files.extend(proposal.get("newFiles", []))
            if proposal.get("summary"):
                summaries.append(str(proposal["summary"]))
            if proposal.get("caveat"):
                caveats.append(str(proposal["caveat"]))

            reusable = str(proposal.get("reusableCode", ""))
            field_name = str(proposal.get("newFieldName", "")).strip().rstrip(";")

            for sequence in instance.get("sequences") or []:
                name = sequence.get("testMethodName", "")
                advance(f"Integrating {mocked}::{name}")
                sequence_variant = route_integration(instance, sequence)

                if sequence_variant == "attribute" and field_name:
                    done, reason = self._mechanical_integration(project_root, sequence, working, field_name)
                    if done is not None:
                        edits.append(done)
                        working[Path(done["path"])] = working[Path(done["path"])].replace(
                            done["oldString"], done["newString"], 1)
                        stage_log.append({"stage": "INTEGRATION", "variant": "attribute-mechanical",
                                          "label": name, "canRefactor": True})
                        continue
                    stage_log.append({"stage": "INTEGRATION", "variant": "attribute",
                                      "label": name, "fellBackToModel": reason})

                try:
                    payload = integration_payload(project_root, instance, sequence, working, reusable, field_name)
                except PayloadError as error:
                    stage_log.append({"stage": "INTEGRATION", "label": name, "skipped": str(error)})
                    continue

                outcome = call("INTEGRATION", sequence_variant, payload, name)
                if outcome is None:
                    continue
                proposal, applied = outcome
                working.update(applied)
                edits.extend(proposal.get("edits", []))
                new_files.extend(proposal.get("newFiles", []))
                if proposal.get("caveat"):
                    caveats.append(str(proposal["caveat"]))

        combined = {
            "canRefactor": bool(edits or new_files),
            "reason": ("" if edits or new_files else
                       "No stage produced an applicable edit / 没有任何阶段产出可应用的编辑"),
            "summary": "; ".join(summaries)[:600],
            "caveat": "; ".join(dict.fromkeys(caveats))[:600],
            "edits": edits,
            "newFiles": new_files,
        }
        return combined, model_results, stage_log

    @staticmethod
    def _mechanical_integration(project_root: Path, sequence: dict[str, Any], files: dict[Path, str],
                                 field_name: str) -> tuple[dict[str, Any] | None, str]:
        """把"局部 mock 改成类级别字段"这一步交给代码。返回可直接应用的编辑，或放弃原因。
        Hands the "local mock becomes a class-level field" step to code. Returns a directly
        applicable edit, or the reason for bailing."""
        from studio.payloads import _relative, _test_method

        relative = _relative(project_root, sequence.get("filePath", ""))
        if relative is None or relative not in files:
            return None, "source file is not available"
        content = files[relative]
        method = _test_method(sequence, content)
        if method is None:
            return None, "could not locate the test method"
        rewritten, bail = rename_local_mock_to_field(method, str(sequence.get("variableName", "")), field_name)
        if rewritten is None:
            return None, bail or "mechanical rewrite declined"
        if content.count(method) != 1:
            return None, "test method text is not unique in the file"
        return {"path": relative.as_posix(), "oldString": method, "newString": rewritten,
                "replaceAll": False}, ""

    @staticmethod
    def _repair_instructions() -> str:
        """
        修复轮次用的指令。修复面对的是一份已经成型的完整提案加上机器诊断，跟分阶段生成
        时"只看一个测试方法"的任务形状不同，所以它不复用任何一份阶段 prompt——只带上
        所有阶段共用的编辑协议，避免在这里重复一遍规则。
        Instructions for a repair round. Repair works on a complete proposal plus machine
        diagnostics, a different shape of task from staged generation's "one test method at
        a time", so it reuses no stage prompt — only the edit protocol every stage shares,
        which keeps the rules stated once.
        """
        protocol = (_PROMPT_DIRECTORY / "_edit_protocol.md").read_text(encoding="utf-8")
        return (
            "You are repairing a Java test refactoring that removes duplicated Mockito setup.\n"
            "You are given the original task, the current proposal, and why it failed. Return a "
            "complete replacement proposal in the same JSON shape — not a patch on top of it.\n"
            "Preserve behaviour. Do not change production code, build files, or unrelated tests.\n\n"
            "---\n\n" + protocol
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise DetectionError(f"Model returned invalid JSON / 模型返回了无效 JSON: {error}") from error

    @staticmethod
    def _apply_edits(root: Path, allowed: dict[Path, str], edits: list[dict[str, Any]],
                      new_files: list[dict[str, Any]]) -> tuple[dict[Path, str], list[str]]:
        """
        把模型返回的"搜索替换"编辑 + 新建文件应用到 `allowed`（选中 MCI 涉及的原始文件）
        上，重建出完整的改动后内容——跟业界主流 coding agent（Claude Code 自带的 Edit
        工具、Aider 的 SEARCH/REPLACE block）同一套纪律：oldString 必须在当前内容里唯一
        匹配，除非显式传 replaceAll=true；不唯一/找不到都直接报错，不去猜是哪一处，也不
        悄悄应用一部分——要么整份提案全部生效，要么带着具体错误原因整体失败，交给上层
        （首次生成后的即时重试、或者 harness 失败后的修复循环）用这些错误反过来喂给模型
        重试。返回 (改动后文件内容, 错误列表)；错误列表非空时第一项永远是空字典。
        Applies the model's search/replace edits and new-file creations onto `allowed` (the
        selected MCI's original files), reconstructing full post-change content — the same
        discipline mainstream coding agents use (Claude Code's own Edit tool, Aider's
        SEARCH/REPLACE blocks): oldString must match exactly once in the current content
        unless replaceAll=true is set explicitly; not-unique or not-found is a hard error,
        never a silent guess or partial application — either the whole proposal takes effect
        or it fails as a whole with concrete reasons the caller (an immediate retry right
        after generation, or the harness-failure repair loop) can feed back to the model.
        Returns (post-edit file contents, errors); the first return is always an empty dict
        when errors is non-empty.
        """
        working: dict[Path, str] = dict(allowed)
        touched: set[Path] = set()
        errors: list[str] = []

        def resolve_relative(path_text: Any) -> Path | None:
            if not isinstance(path_text, str) or not path_text:
                return None
            relative = Path(path_text)
            try:
                (root / relative).resolve().relative_to(root.resolve())
            except ValueError:
                return None
            return relative

        for entry in new_files:
            path_text = entry.get("path", "")
            content = entry.get("content")
            relative = resolve_relative(path_text)
            if relative is None:
                errors.append(f"newFiles path is missing or escapes the project root: {path_text!r}")
                continue
            if not isinstance(content, str) or not content:
                errors.append(f"newFiles entry for {path_text!r} has no content")
                continue
            if relative in allowed or relative in touched or (root / relative).exists():
                errors.append(f"newFiles path already exists; edit it instead of creating it: {path_text!r}")
                continue
            # 新类名如果和某个文件已 import 的简单名相同，那个文件里的所有引用仍会解析到被
            # import 的类，新方法一律"找不到符号"。这种冲突只有编译器会抱怨，而编译是整条
            # 链路上最贵的一步——实测一次这样的撞名烧掉 22249 tokens 才换来一句报错。
            # 这里在阶段内就拦下，交给同一步的重试去改名，代价是一次小 payload 的调用。
            # A new class whose simple name a file already imports loses every reference in that
            # file to the imported type, so its methods are all "cannot find symbol". Only the
            # compiler objects to this, and compiling is the most expensive step in the chain —
            # one such collision cost 22249 tokens to surface a single error. Catching it here
            # hands it to the in-stage retry for a rename, at the price of one small call.
            new_type = relative.stem
            clashing = sorted(
                other.as_posix() for other, text in allowed.items()
                if re.search(r"(?m)^\s*import\s+(?:static\s+)?[\w.]+\.%s\s*;" % re.escape(new_type), text)
            )
            if clashing:
                errors.append(
                    f"newFiles class {new_type!r} collides with a type already imported by "
                    f"{', '.join(clashing)}; an explicit import outranks same-package resolution, "
                    f"so every use of that name there would still mean the imported class. "
                    f"Choose a different class name."
                )
                continue
            working[relative] = content
            touched.add(relative)

        for entry in edits:
            path_text = entry.get("path", "")
            old_string = entry.get("oldString")
            new_string = entry.get("newString")
            replace_all = bool(entry.get("replaceAll", False))
            relative = resolve_relative(path_text)
            if relative is None:
                errors.append(f"edits path is missing or escapes the project root: {path_text!r}")
                continue
            if relative not in working:
                errors.append(f"edits path is not one of the supplied (or newly created) files: {path_text!r}")
                continue
            if not isinstance(old_string, str) or not old_string:
                errors.append(f"edits entry for {path_text!r} is missing a non-empty oldString")
                continue
            if not isinstance(new_string, str):
                errors.append(f"edits entry for {path_text!r} is missing newString")
                continue
            content = working[relative]
            count = content.count(old_string)
            if count == 0:
                errors.append(f"oldString not found in {path_text!r} (it may have shifted after an earlier edit)")
                continue
            if count > 1 and not replace_all:
                errors.append(
                    f"oldString matches {count} locations in {path_text!r}; add more surrounding context "
                    f"to make it unique, or set replaceAll=true"
                )
                continue
            working[relative] = content.replace(old_string, new_string) if replace_all \
                else content.replace(old_string, new_string, 1)
            touched.add(relative)

        if errors:
            return {}, errors

        # 没有实际改动的路径不算数（模型原样返回、或者编辑最终等于没变），避免"没有
        # 实际改动也被判为成功"。
        # Paths with no real change don't count (the model echoed content back unchanged,
        # or the edits net out to a no-op) — otherwise "no actual change" could pass as success.
        replacements = {
            path: content for path, content in working.items()
            if path in touched and content != allowed.get(path, "")
        }
        return replacements, []

    @staticmethod
    def _copy_project(source: Path, destination: Path) -> None:
        ignored = shutil.ignore_patterns(".git", ".gradle", ".idea", "target", "build", "node_modules", ".clonedemocker")
        # 源项目里个别文件名本身就很长（例如 Spring Boot 的 *.imports 元数据文件），
        # 读取那一侧也可能超出 Windows MAX_PATH，所以源和目的都要走扩展长度路径。
        # Some source files have very long names on their own (e.g. Spring Boot's
        # *.imports metadata files), so the read side can hit Windows' MAX_PATH too;
        # both source and destination need the extended-length form.
        shutil.copytree(_long_path(source), _long_path(destination), ignore=ignored)

    @staticmethod
    def _openai_provider(profile: str) -> ModelProvider:
        # 多 key 使用环境变量 JSON：{"default":"sk-...","research":"sk-..."}。
        # Multiple keys use an environment JSON map: {"default":"sk-...","research":"sk-..."}.
        configured = os.environ.get("CLONEDEMOCKER_OPENAI_KEYS", "").strip()
        try:
            keys = json.loads(configured) if configured else {}
        except json.JSONDecodeError as error:
            raise DetectionError("CLONEDEMOCKER_OPENAI_KEYS is invalid JSON / 多 API key 配置不是有效 JSON") from error
        key = str(keys.get(profile, "")).strip() or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise DetectionError(f"API profile is not configured / API 配置不存在: {profile}")
        return OpenAIModelProvider(key, os.environ.get("OPENAI_BASE_URL") or None)

    @staticmethod
    def _combined_usage(results: list[Any]) -> dict[str, int]:
        return {field: sum(getattr(result.usage, field, 0) for result in results) for field in _USAGE_FIELDS}

    @staticmethod
    def _validation_reason(evidence: Any, goal_achieved: bool = True, pit_regressed: bool = False,
                           audit: dict[str, Any] | None = None, run_pit: bool = False,
                           expected_test_classes: list[str] | None = None) -> str:
        evidence_failure = verification_failure_reason(evidence, run_pit, expected_test_classes)
        if evidence_failure is not None:
            return evidence_failure
        if "FAILED" in {evidence.compile_status.value, evidence.test_status.value, evidence.pit_status.value}:
            return "Harness failed after repair attempts / 自动修复后 Harness 仍失败"
        if "UNAVAILABLE" in {evidence.compile_status.value, evidence.test_status.value}:
            return "Build harness unavailable for this project / 当前项目无法运行构建 Harness"
        if pit_regressed:
            return "Mutation score decreased by more than the allowed five percentage points / 变异得分下降超过允许的五个百分点"
        if not goal_achieved:
            return "Harness passed but the duplicated mock logic was not actually reduced / Harness 通过，但重复的 mock 逻辑并未实际减少"
        if audit and audit.get("risk") not in {"LOW", "SKIPPED"}:
            return "Independent AI audit raised a refactoring concern / 独立 AI 审查提出了重构风险"
        return "Harness passed / Harness 已通过"

    @staticmethod
    def _audit_refactoring(provider: ModelProvider, model: str, selected: list[dict[str, Any]], diff_text: str,
                           baseline: dict[str, Any], candidate: dict[str, Any], deterministic_verified: bool,
                           use_mock: bool) -> dict[str, Any]:
        """Runs an independent, evidence-aware risk review after machine gates pass."""
        if use_mock:
            return {"status": "SKIPPED", "risk": "SKIPPED", "reason": "Debug mode does not call an AI auditor"}
        if not deterministic_verified:
            return {"status": "SKIPPED", "risk": "SKIPPED", "reason": "Deterministic verification did not pass"}
        prompt = (
            "You are an independent Java test-refactoring reviewer. Do not propose edits. "
            "Assess whether the supplied diff genuinely removes the selected duplicated Mockito setup "
            "without introducing a semantic, lifecycle, scope, or readability risk. Return JSON only: "
            '{"risk":"LOW|MEDIUM|HIGH","reason":"concise evidence-based explanation"}.'
        )
        evidence = json.dumps({
            "selectedMockCloneInstances": selected,
            "diff": diff_text,
            "baseline": baseline,
            "candidate": candidate,
        }, ensure_ascii=False)
        result = provider.generate(prompt, evidence, model)
        try:
            review = RefactoringAgent._parse_json(result.text)
            risk = str(review.get("risk", "HIGH")).upper()
            if risk not in {"LOW", "MEDIUM", "HIGH"}:
                risk = "HIGH"
            return {
                "status": "COMPLETED", "risk": risk, "reason": str(review.get("reason", "No review reason returned")),
                "modelResult": result,
            }
        except DetectionError:
            return {
                "status": "FAILED", "risk": "HIGH", "reason": "AI auditor returned invalid JSON", "modelResult": result,
            }

    @staticmethod
    def _baseline_failure_reason(evidence: Any, run_pit: bool,
                                 expected_test_classes: list[str] | None = None) -> str | None:
        """
        基线本身是否干净到可以当参照物。

        这里的三个条件不是新增的门槛，而是 deterministic_verified 早就要求过的同一批
        baseline 前提（编译过、测试过、开了 PIT 时 PIT 也过）——只是原来要等模型跑完
        才检查。所以把它们提到前面不改变任何判定结果，只是提前失败、不花冤枉钱。
        Whether the baseline is clean enough to serve as a reference point. These three
        conditions are not a new bar: they are the same baseline preconditions
        deterministic_verified has always required (compiles, tests pass, and PIT passes
        when PIT is enabled) — they were merely checked after the model had already run.
        Hoisting them changes no verdict, it only fails earlier and spends nothing.
        """
        failure = verification_failure_reason(evidence, run_pit, expected_test_classes)
        if failure is None:
            return None
        return (
            "The unchanged isolated copy cannot provide verification evidence: " + failure + " / "
            "未修改的隔离副本无法提供可验证的基线证据：" + failure
        )

    @staticmethod
    def _environment_failure(proposal_id: str, reason: str, baseline_evidence: Any) -> dict[str, Any]:
        """
        环境问题导致的失败：一次模型调用都没发生，所以 usage 全 0、modelCalls 为 0。

        跟 _failure 分开是因为两者对用户的含义完全不同——_failure 是"模型没能给出可用的
        补丁"（该看 diff 和模型的理由），这里是"你的环境还没准备好"（该看 baseline 诊断、
        去修环境）。harness.baselineBroken 就是给界面区分这两种情况用的。
        A failure caused by the environment: not a single model call happened, so usage is
        all zeros and modelCalls is 0. Kept separate from _failure because the two mean
        entirely different things to the user — _failure is "the model could not produce a
        usable patch" (look at the diff and the model's reason), whereas this is "your
        environment isn't ready" (look at the baseline diagnostics and fix the environment).
        harness.baselineBroken is what lets the UI tell the two apart.
        """
        return {
            "proposalId": proposal_id,
            "stage": AgentStage.FAILED,
            "reason": reason,
            "summary": "",
            "diff": "",
            "changedFiles": [],
            "harness": {
                "baseline": baseline_evidence.as_dict(),
                "candidate": None,
                "equivalent": False,
                "baselineBroken": True,
            },
            "validationReason": reason,
            "caveat": "",
            "usage": {field: 0 for field in _USAGE_FIELDS},
            "modelCalls": 0,
            "model": "",
            "responseId": "",
        }

    @staticmethod
    def _failure(proposal_id: str, reason: str, result: Any) -> dict[str, Any]:
        return {"proposalId": proposal_id, "stage": AgentStage.FAILED, "reason": reason, "diff": "",
                "changedFiles": [], "harness": None, "usage": asdict(result.usage),
                "model": result.model, "responseId": result.response_id}
