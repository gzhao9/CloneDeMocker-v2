from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.detection_service import DetectionError, DetectionService
from app.harness import ProjectHarness, ensure_pit_junit5_support, mutation_regressed
from app.model_provider import MockModelProvider, ModelProvider, OpenAIModelProvider


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
            use_mock: bool = False, sequence_selection: dict[str, list[int]] | None = None) -> dict[str, Any]:
        run, raw = self.detection.load_raw_detection(run_id)
        selected = self._select_instances(raw, selected_mci_ids, sequence_selection)
        if not selected:
            raise DetectionError("Select at least one MCI / 请至少选择一个 MCI")

        files = self._affected_files(run.project_root, selected)
        if not files:
            raise DetectionError("Selected MCIs contain no readable source files / 所选 MCI 没有可读取的源文件")

        # 调试模式：跳过真实模型调用，不消耗 token。
        # Debug mode: skip the real model call so no tokens are spent.
        provider = MockModelProvider() if use_mock else (self.provider or self._openai_provider(api_profile))
        proposal_id = uuid.uuid4().hex
        proposal_root = _long_path(run.run_directory / "refactoring" / proposal_id)
        proposal_root.mkdir(parents=True)
        request = self._model_input(run.project_root, selected, files, user_instruction)
        result = provider.generate(self._instructions(), request, model)
        model_results = [result]
        proposal = self._parse_json(result.text)
        if not proposal.get("canRefactor", False):
            return self._failure(proposal_id, proposal.get("reason", "Model declined the refactoring"), result)

        replacements = self._validate_replacements(run.project_root, files, proposal.get("files", []))
        if not replacements:
            return self._failure(proposal_id, "Model returned no valid file replacements / 模型没有返回有效文件修改", result)

        candidate_files = proposal_root / "candidate-files"
        diff_parts: list[str] = []
        for relative, new_content in replacements.items():
            original = (run.project_root / relative).read_text(encoding="utf-8")
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

        # 在完整隔离副本中执行 Harness；原项目不会被写入。
        # Run the harness in a complete isolated copy; the source project is never written.
        workspace = _workspace_root(run.project_root, proposal_id)
        self._copy_project(run.project_root, workspace)
        if run_pit:
            ensure_pit_junit5_support(workspace, getattr(self.harness, "maven_repo_local", None))
        baseline_evidence = self.harness.validate(workspace, run_pit)
        for relative, new_content in replacements.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8", newline="\n")
        candidate_evidence = self.harness.validate(workspace, run_pit)

        # Harness 失败时把机器诊断交回模型，最多修复两次。
        # Feed machine diagnostics back to the model for at most two repair attempts.
        for _ in range(2):
            if "FAILED" not in {candidate_evidence.compile_status.value, candidate_evidence.test_status.value}:
                break
            repair_input = json.dumps({
                "originalRequest": json.loads(request),
                "currentProposal": proposal,
                "harnessDiagnostics": candidate_evidence.diagnostics,
            }, ensure_ascii=False)
            result = provider.generate(self._instructions() + "\nRepair the previous proposal using the harness diagnostics.", repair_input, model)
            model_results.append(result)
            repaired = self._parse_json(result.text)
            if not repaired.get("canRefactor", False):
                proposal = repaired
                break
            repaired_files = self._validate_replacements(run.project_root, files, repaired.get("files", []))
            if not repaired_files:
                break
            proposal, replacements = repaired, repaired_files
            for relative, original_content in files.items():
                (workspace / relative).write_text(original_content, encoding="utf-8", newline="\n")
            for relative, new_content in replacements.items():
                (workspace / relative).write_text(new_content, encoding="utf-8", newline="\n")
            candidate_evidence = self.harness.validate(workspace, run_pit)

        # 重新生成最终候选文件和 diff，使界面展示的是最后一次修复结果。
        # Rebuild candidate files and diff so the UI shows the final repair attempt.
        diff_parts = []
        for relative, new_content in replacements.items():
            original = files[relative]
            target = candidate_files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8", newline="\n")
            diff_parts.extend(difflib.unified_diff(original.splitlines(keepends=True), new_content.splitlines(keepends=True),
                                                   fromfile=f"a/{relative.as_posix()}", tofile=f"b/{relative.as_posix()}"))
        diff_text = "".join(diff_parts)
        (proposal_root / "changes.diff").write_text(diff_text, encoding="utf-8")
        usage = self._combined_usage(model_results)
        goal_achieved = self._goal_check(selected, files, replacements)
        pit_regressed = run_pit and mutation_regressed(baseline_evidence.as_dict(), candidate_evidence.as_dict())
        # run_pit 为 True 时，两边的 pitStatus 都必须真的是 PASSED；否则 mutants 字典两边
        # 都是空的，mutation_regressed() 比较空字典会 vacuously 判定"没有退化"，等于从没
        # 验证过这一层。基于 validation/run_pilot.py 那次全量跑批发现的同一个问题在这里
        # 补上，避免这个产品路径也悄悄把"PIT 根本没跑成"当成"PIT 通过了"。
        # When run_pit is True, both sides' pitStatus must genuinely be PASSED; otherwise
        # both mutants dicts are empty and mutation_regressed() vacuously reads that as "no
        # regression" — meaning this tier was never actually verified. Applying the same fix
        # found via validation/run_pilot.py's full batch run here too, so this product path
        # doesn't silently treat "PIT never ran" as "PIT passed".
        pit_ran_cleanly = not run_pit or (
            baseline_evidence.pit_status.value == "PASSED" and candidate_evidence.pit_status.value == "PASSED"
        )
        equivalent = (
            baseline_evidence.compile_status.value == "PASSED"
            and baseline_evidence.test_status.value == "PASSED"
            and candidate_evidence.compile_status.value == "PASSED"
            and candidate_evidence.test_status.value == "PASSED"
            and baseline_evidence.test_results == candidate_evidence.test_results
            and pit_ran_cleanly
            and not pit_regressed
            and goal_achieved
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
            },
            "validationReason": self._validation_reason(candidate_evidence, goal_achieved, pit_regressed),
            "usage": usage,
            "modelCalls": len(model_results),
            "model": result.model,
            "responseId": result.response_id,
        }
        (proposal_root / "proposal.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {path.as_posix(): hashlib.sha256(content.encode("utf-8")).hexdigest()
                    for path, content in files.items() if path in replacements}
        (proposal_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return response

    def apply(self, run_id: str, proposal_id: str) -> dict[str, Any]:
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
        manifest = json.loads((proposal_root / "manifest.json").read_text(encoding="utf-8"))

        pending: list[tuple[Path, str]] = []
        for relative_text, expected_hash in manifest.items():
            relative = Path(relative_text)
            target = (run.project_root / relative).resolve()
            try:
                target.relative_to(run.project_root)
            except ValueError as error:
                raise DetectionError("Proposal path escaped the project / 候选路径超出项目") from error
            current = target.read_text(encoding="utf-8")
            if hashlib.sha256(current.encode("utf-8")).hexdigest() != expected_hash:
                raise DetectionError(f"Source changed after proposal; regenerate it / 源码在生成补丁后已变化: {relative_text}")
            candidate = (proposal_root / "candidate-files" / relative).read_text(encoding="utf-8")
            pending.append((target, candidate))

        for target, candidate in pending:
            temporary = target.with_name(target.name + ".clonedemocker.tmp")
            temporary.write_text(candidate, encoding="utf-8", newline="\n")
            os.replace(temporary, target)
        applied = {"proposalId": proposal_id, "applied": True,
                   "changedFiles": list(manifest), "message": "Patch applied / 补丁已应用"}
        (proposal_root / "applied.json").write_text(json.dumps(applied, ensure_ascii=False, indent=2), encoding="utf-8")
        return applied

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

    @staticmethod
    def _affected_files(project_root: Path, instances: list[dict[str, Any]]) -> dict[Path, str]:
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
                if resolved.suffix == ".java":
                    files[relative] = resolved.read_text(encoding="utf-8")
        return files

    @staticmethod
    def _model_input(project_root: Path, instances: list[dict[str, Any]], files: dict[Path, str], user_instruction: str) -> str:
        payload = {
            "projectRootName": project_root.name,
            "userInstruction": user_instruction,
            "selectedMockCloneInstances": RefactoringAgent._strip_duplicate_source(instances),
            "sourceFiles": [{"path": path.as_posix(), "content": content} for path, content in files.items()],
        }
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > 500_000:
            raise DetectionError("Selected source context is too large; choose fewer MCIs / 源码上下文过大，请减少 MCI")
        return text

    @staticmethod
    def _strip_duplicate_source(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        同一段测试方法源码本来就完整地在 payload 的 sourceFiles[].content 里；每个
        sequence 顶层的 testMethodRawCode，以及 rawStatementInfo 每一行里嵌套的
        locationContext.methodRawCode，又各自把它原样重复了一遍，等于同一段代码发了
        三次。这里只去掉后两份，不影响其余检测元数据（mockObjectId、行号、abstracted
        语句等），也不修改传入的原始检测数据。
        The same test-method source already appears in full via the payload's
        sourceFiles[].content; each sequence's top-level testMethodRawCode, and the
        nested locationContext.methodRawCode on every rawStatementInfo entry, each
        repeat it verbatim again — the same code sent three times. This strips only
        those two extra copies, leaves every other detection field intact (mockObjectId,
        line numbers, abstracted statements, ...), and never mutates the original
        detection data passed in.
        """
        stripped_instances: list[dict[str, Any]] = []
        for instance in instances:
            instance_copy = dict(instance)
            sequences = []
            for sequence in instance.get("sequences", []):
                sequence_copy = {key: value for key, value in sequence.items() if key != "testMethodRawCode"}
                raw_statement_info = sequence.get("rawStatementInfo")
                if isinstance(raw_statement_info, dict):
                    stripped_statements = {}
                    for line, statement in raw_statement_info.items():
                        if not isinstance(statement, dict):
                            stripped_statements[line] = statement
                            continue
                        statement_copy = dict(statement)
                        location_context = statement.get("locationContext")
                        if isinstance(location_context, dict):
                            location_copy = dict(location_context)
                            location_copy.pop("methodRawCode", None)
                            statement_copy["locationContext"] = location_copy
                        stripped_statements[line] = statement_copy
                    sequence_copy["rawStatementInfo"] = stripped_statements
                sequences.append(sequence_copy)
            instance_copy["sequences"] = sequences
            stripped_instances.append(instance_copy)
        return stripped_instances

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
        """

        def normalize(line: str) -> str:
            return " ".join(line.split())

        for instance in instances:
            shared_lines: set[str] = set()
            for sequence in instance.get("sequences", []):
                for value in (sequence.get("shareableMockLines") or {}).values():
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
    def _instructions() -> str:
        return """You refactor Java test mock clones using the paper's two steps: Encapsulation and Integration.
Preserve behavior. Change only supplied files. Return JSON only with this shape:
{"canRefactor":true,"reason":"...","summary":"...","files":[{"path":"relative/path.java","newContent":"complete file"}]}.
If safe refactoring is impossible, return canRefactor=false, explain the concrete reason, and return an empty files array.
Do not use markdown fences. Keep complete source text in newContent."""

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise DetectionError(f"Model returned invalid JSON / 模型返回了无效 JSON: {error}") from error

    @staticmethod
    def _validate_replacements(root: Path, allowed: dict[Path, str], values: list[dict[str, Any]]) -> dict[Path, str]:
        replacements: dict[Path, str] = {}
        for value in values:
            relative = Path(value.get("path", ""))
            content = value.get("newContent")
            original = allowed.get(relative)
            # original is None 表示这个路径不在允许列表里；content == original 表示模型
            # 原样返回了源码——两种都不算有效改动，避免"没有实际改动也被判为成功"。
            # original is None means the path isn't in the allowed set; content == original
            # means the model echoed the source back unchanged — neither counts as a real
            # edit, which would otherwise let "no actual change" be judged a success.
            if (
                original is not None
                and isinstance(content, str)
                and content != original
                and (root / relative).resolve().is_relative_to(root)
            ):
                replacements[relative] = content
        return replacements

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
        fields = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
        return {field: sum(getattr(result.usage, field, 0) for result in results) for field in fields}

    @staticmethod
    def _validation_reason(evidence: Any, goal_achieved: bool = True, pit_regressed: bool = False) -> str:
        if "FAILED" in {evidence.compile_status.value, evidence.test_status.value, evidence.pit_status.value}:
            return "Harness failed after repair attempts / 自动修复后 Harness 仍失败"
        if "UNAVAILABLE" in {evidence.compile_status.value, evidence.test_status.value}:
            return "Build harness unavailable for this project / 当前项目无法运行构建 Harness"
        if pit_regressed:
            return "A mutant killed in the baseline survived in the candidate / 一个在 baseline 中被杀死的变异体在 candidate 中存活了"
        if not goal_achieved:
            return "Harness passed but the duplicated mock logic was not actually reduced / Harness 通过，但重复的 mock 逻辑并未实际减少"
        return "Harness passed / Harness 已通过"

    @staticmethod
    def _failure(proposal_id: str, reason: str, result: Any) -> dict[str, Any]:
        return {"proposalId": proposal_id, "stage": AgentStage.FAILED, "reason": reason, "diff": "",
                "changedFiles": [], "harness": None, "usage": asdict(result.usage),
                "model": result.model, "responseId": result.response_id}
