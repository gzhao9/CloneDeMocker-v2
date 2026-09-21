import json
import shutil
import tempfile
import unittest
from pathlib import Path

from studio.canonical_store import classify_agent_result
from studio.detection_service import DetectionError, DetectionService
from studio.model_provider import ModelResult, ModelUsage
from studio.refactoring_agent import RefactoringAgent, _comparable_test_results, _test_regression_reason
from studio.harness import HarnessEvidence, HarnessStatus
from studio.long_paths import long_path

HELPER = "    private static Dependency createDependency() {\n        return Mockito.mock(Dependency.class);\n    }\n"


def java_source(method_names: list[str]) -> str:
    """一份结构真实的测试类：有包声明、有 @Test 方法、mock 语句带着文件自己的缩进。
    分阶段流水线要靠 source_map 把检测器的规范化文本定位回这里，fixture 太单薄就走不通。
    A structurally realistic test class: a package declaration, @Test methods, and mock
    statements carrying the file's own indentation. The staged pipeline relies on
    source_map locating the detector's normalized text back into this, which a thin
    fixture cannot exercise."""
    body = "".join(
        f"    @Test\n"
        f"    void {name}() {{\n"
        f"        Dependency value = Mockito.mock(Dependency.class);\n"
        f"        subject.accept(value, \"{name}\");\n"
        f"    }}\n\n"
        for name in method_names
    )
    return f"package demo;\n\nclass Test {{\n\n{body}}}\n"


def detector_metadata(source_path: Path, source: str, method_names: list[str],
                      shared_statements: list[str] | None = None) -> dict:
    """按真实检测器的字段形状构造 MCI：行号指向 mock 语句，代码文本是检测器那份去缩进的
    规范化视图（而不是文件原文），这样测试才会真的走一遍 source_map 的映射。
    Builds an MCI in the real detector's field shape: line numbers point at the mock
    statement and the code text is the detector's de-indented normalized view rather than
    the file's own, so the test genuinely exercises source_map's mapping."""
    lines = source.splitlines()
    sequences = []
    for index, name in enumerate(method_names):
        signature = f"    void {name}() {{"
        method_line = lines.index(signature) + 1
        sequences.append({
            "mockObjectId": index,
            "filePath": str(source_path),
            "testMethodName": name,
            "className": "Test",
            "packageName": "demo",
            "variableName": "value",
            "mockRole": "mock",
            # 去掉缩进：检测器就是这么给的
            "testMockLines": {str(method_line + 1): "Dependency value = Mockito.mock(Dependency.class);"},
            "shareableMockLines": {},
        })
    return {"detectedMockClones": {"demo.Dependency": [{
        "mockedClass": "demo.Dependency",
        "packageName": "demo",
        "testCaseCount": len(method_names),
        "sequenceCount": len(method_names),
        "sharedStatements": shared_statements if shared_statements is not None else
        ["when(demo.Dependency.get()).thenReturn(java.lang.String)"],
        "sharedStatementLineCount": 1 if shared_statements is None else len(shared_statements),
        "sequences": sequences,
    }]}}


class StagedProvider:
    """读 payload 再作答的假模型：封装阶段插入 helper，集成阶段把测试方法整体换掉。

    故意不返回写死的编辑——`oldString` 必须来自 payload 的 `verbatim` 区才能应用成功，
    所以这个假实现顺带把 payload 契约本身也测到了：契约一旦破了，这里就会失败。
    A fake model that answers by reading the payload: encapsulation inserts the helper,
    integration replaces the whole test method. Deliberately not a canned edit — an
    `oldString` only applies when it comes from the payload's `verbatim` region, so this
    fake also exercises the payload contract itself and fails if that contract breaks.
    """

    def __init__(self, value: str = "createDependency()"):
        self.value = value
        self.calls = 0
        self.stages: list[str] = []
        self.payloads: list[dict] = []

    def _encapsulation(self, payload: dict) -> dict:
        target = payload["verbatim"]["targetFile"]
        closing = target["content"].rstrip()[-1]
        return {"canRefactor": True, "reason": "extracted", "summary": "helper extracted",
                "reusableCode": HELPER, "newFieldName": "value",
                "edits": [{"path": target["path"], "oldString": f"\n{closing}\n",
                           "newString": f"\n{HELPER}{closing}\n", "replaceAll": False}]}

    def _integration(self, payload: dict) -> dict:
        method = payload["verbatim"]["testMethod"]
        rewritten = method["text"].replace("Mockito.mock(Dependency.class)", self.value)
        return {"canRefactor": True, "reason": "integrated", "summary": "call site updated",
                "edits": [{"path": method["path"], "oldString": method["text"],
                           "newString": rewritten, "replaceAll": False}]}

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        self.calls += 1
        if "independent Java test-refactoring reviewer" in instructions:
            self.stages.append("AUDIT")
            body = {"risk": "LOW", "reason": "deterministic evidence is consistent"}
        else:
            payload = json.loads(input_text)
            self.payloads.append(payload)
            stage = payload.get("stage", "REPAIR")
            self.stages.append(stage)
            if stage == "ENCAPSULATION":
                body = self._encapsulation(payload)
            elif stage == "INTEGRATION":
                body = self._integration(payload)
            else:
                body = dict(payload.get("currentProposal") or {})
        return ModelResult(json.dumps(body), f"fake-{self.calls}", model, ModelUsage(10, 0, 5, 0, 15), None)


class ConcernedAuditProvider(StagedProvider):
    """三层判据全过，但审查报 HIGH。
    All three deterministic tiers pass while the audit reports HIGH."""

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        if "independent Java test-refactoring reviewer" not in instructions:
            return super().generate(instructions, input_text, model)
        self.calls += 1
        self.stages.append("AUDIT")
        body = {"risk": "HIGH", "reason": "mock promoted into @BeforeEach",
                "evidence": "+    @BeforeEach"}
        return ModelResult(json.dumps(body), f"fake-{self.calls}", model, ModelUsage(10, 0, 5, 0, 15), None)


class RepairingProvider(StagedProvider):
    """集成阶段第一次给出无法应用的编辑，修复轮次再给正确的。
    Integration first returns an edit that cannot apply; the repair round returns a good one."""

    def __init__(self):
        super().__init__()
        self.integration_calls = 0
        self.last_good: dict | None = None

    def _integration(self, payload: dict) -> dict:
        self.integration_calls += 1
        good = super()._integration(payload)
        self.last_good = good
        if self.integration_calls == 1:
            broken = json.loads(json.dumps(good))
            broken["edits"][0]["oldString"] = "text that is not in the file"
            return broken
        return good

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        payload = json.loads(input_text) if input_text.startswith("{") else {}
        if payload.get("editErrors") and self.last_good is not None:
            self.calls += 1
            self.stages.append("REPAIR")
            merged = dict(payload.get("currentProposal") or {})
            merged["edits"] = self.last_good["edits"]
            merged["canRefactor"] = True
            return ModelResult(json.dumps(merged), f"fake-{self.calls}", model, ModelUsage(), None)
        return super().generate(instructions, input_text, model)


class SequencedHarness:
    def __init__(self):
        self.calls = 0

    def validate(self, project_root: Path, run_pit: bool = False) -> HarnessEvidence:
        self.calls += 1
        status = HarnessStatus.FAILED if self.calls == 2 else HarnessStatus.PASSED
        return HarnessEvidence(
            status, status, HarnessStatus.NOT_RUN,
            diagnostics=["compile error"] if status == HarnessStatus.FAILED else [],
            test_results={"demo.Test#testA": "PASSED"},
        )


class PassingHarness:
    def validate(self, project_root: Path, run_pit: bool = False) -> HarnessEvidence:
        return HarnessEvidence(
            HarnessStatus.PASSED, HarnessStatus.PASSED, HarnessStatus.NOT_RUN,
            test_results={"demo.Test#testA": "PASSED"},
        )


class RefactoringAgentTest(unittest.TestCase):
    def test_known_baseline_failure_allows_only_the_same_failure_set(self):
        baseline = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.FAILED,
                                   test_results={"demo.Test#stable": "PASSED", "demo.Net#dns": "FAILED"})
        unchanged = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.FAILED,
                                    test_results={"demo.Test#stable": "PASSED", "demo.Net#dns": "FAILED"})
        regressed = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.FAILED,
                                    test_results={"demo.Test#stable": "FAILED", "demo.Net#dns": "FAILED"})
        self.assertIsNone(_test_regression_reason(baseline, unchanged))
        self.assertIn("previously passing", _test_regression_reason(baseline, regressed))

    def test_identity_hashes_in_parameterized_test_names_do_not_break_equivalence(self):
        # Spring Security 的 Observation*FilterChainDecoratorTests：参数的 toString() 带 @identityHashCode。
        # Spring Security's Observation*FilterChainDecoratorTests: the argument's toString() carries @identityHashCode.
        name = "demo.DecoratorTests#[2] filter = demo.DecoratorTests$1@{}, expectedFilterNameTag = \"none\""
        baseline = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.PASSED,
                                   test_results={name.format("9214725"): "PASSED", "demo.Test#stable": "PASSED"})
        candidate = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.PASSED,
                                    test_results={name.format("3f2afa8b"): "PASSED", "demo.Test#stable": "PASSED"})
        self.assertEqual(_comparable_test_results(baseline.test_results),
                         _comparable_test_results(candidate.test_results))
        self.assertIsNone(_test_regression_reason(baseline, candidate))
        failed = HarnessEvidence(HarnessStatus.PASSED, HarnessStatus.FAILED,
                                 test_results={name.format("50a095cb"): "FAILED", "demo.Test#stable": "PASSED"})
        self.assertIn("previously passing", _test_regression_reason(baseline, failed))

    def test_names_that_collapse_after_normalization_keep_every_status(self):
        first, second = "demo.T#[1] x = demo.A@1a2b", "demo.T#[1] x = demo.A@3c4d"
        self.assertEqual({"demo.T#[1] x = demo.A@<hash>": ("FAILED", "PASSED")},
                         _comparable_test_results({first: "PASSED", second: "FAILED"}))
        self.assertNotEqual(_comparable_test_results({first: "PASSED", second: "PASSED"}),
                            _comparable_test_results({first: "PASSED", second: "FAILED"}))

    def test_names_without_identity_hashes_compare_exactly_as_before(self):
        results = {"demo.Test#a": "PASSED", "demo.Test#b(String)[1]": "SKIPPED", "user@example.com#c": "PASSED"}
        self.assertEqual({key: (status,) for key, status in results.items()}, _comparable_test_results(results))

    @staticmethod
    def _fixture(temporary: str, run_id: str = "e" * 32, methods: list[str] | None = None,
                 shared_statements: list[str] | None = None, source_directory: str = "src"):
        methods = methods or ["testFirst"]
        repository = Path(temporary) / "tool"
        project = Path(temporary) / "subject"
        source = project / source_directory / "Test.java"
        source.parent.mkdir(parents=True)
        content = java_source(methods)
        source.write_text(content, encoding="utf-8")
        service = DetectionService(repository)
        run_dir = service.runs_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"projectRoot": str(project)}), encoding="utf-8")
        (run_dir / "mock-clone-instances.json").write_text(
            json.dumps(detector_metadata(source, content, methods, shared_statements)), encoding="utf-8")
        return service, source, run_id, run_dir

    def test_writes_isolated_candidate_and_keeps_original_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            service, source, run_id, run_dir = self._fixture(temporary, "a" * 32)
            original = source.read_text(encoding="utf-8")
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra")

            self.assertEqual("COMPLETED", result["stage"])
            self.assertIn("-        Dependency value = Mockito.mock(Dependency.class);", result["diff"])
            self.assertIn("+        Dependency value = createDependency();", result["diff"])
            self.assertEqual(original, source.read_text(encoding="utf-8"))
            candidate = run_dir / "refactoring" / result["proposalId"] / "candidate-files" / "src" / "Test.java"
            patched = candidate.read_text(encoding="utf-8")
            self.assertIn("private static Dependency createDependency()", patched)
            applied = agent.apply(run_id, result["proposalId"], force=True)
            self.assertTrue(applied["applied"])
            self.assertEqual(patched, source.read_text(encoding="utf-8"))

    def test_source_that_passes_max_path_only_inside_the_workspace_is_still_written(self):
        # 源文件本身不到 260 字符，复制进 .clonedemocker-workspaces/<id>/ 后超过——Spring Security
        # saml2/oauth2 的实际情况。以前这里 write_text 报 Errno 2，MCI 以 ERROR 结束且不进导出。
        # The source itself is under 260 characters but passes it once copied into
        # .clonedemocker-workspaces/<id>/, as in Spring Security's saml2/oauth2. write_text used to
        # raise Errno 2 here, ending the MCI as an ERROR that never reached the export.
        # TemporaryDirectory 删不掉超过 MAX_PATH 的树 / TemporaryDirectory cannot remove a tree past MAX_PATH
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, long_path(Path(temporary)), True)
        padding = 245 - len(str(Path(temporary) / "subject" / "src" / "Test.java"))
        if padding < 10:
            self.skipTest("temporary directory is already too deep")
        deep = "src/" + "/".join("d" * 40 for _ in range(padding // 41)) + "/" + "e" * (padding % 41 or 1)
        service, source, run_id, run_dir = self._fixture(temporary, "f" * 32, source_directory=deep)
        self.assertLess(len(str(source)), 260)

        result = RefactoringAgent(service, StagedProvider(), PassingHarness()).run(
            run_id, ["demo.Dependency::1"], "gpt-5.6-terra")

        self.assertEqual("COMPLETED", result["stage"])
        self.assertIn("+        Dependency value = createDependency();", result["diff"])

    def test_runs_one_encapsulation_then_one_integration_per_sequence(self):
        """论文的两步走必须体现在调用结构上，而不只是 prompt 里的一句话。
        The paper's two steps must show up in the call structure, not merely in prose."""
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "e" * 32,
                                                  methods=["testFirst", "testSecond", "testThird"])
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-test")

            self.assertEqual("COMPLETED", result["stage"])
            self.assertEqual(["ENCAPSULATION", "INTEGRATION", "INTEGRATION", "INTEGRATION", "AUDIT"],
                             provider.stages)

    def test_integration_payload_carries_only_one_test_method(self):
        """每次集成只看一个测试方法——大文件整体交给模型正是之前放弃重构的原因。
        Each integration sees one test method; handing over a whole large file is exactly
        what made the model give up before."""
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "e" * 32,
                                                  methods=["testFirst", "testSecond"])
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())
            agent.run(run_id, ["demo.Dependency::1"], "gpt-test")

            integration = [p for p in provider.payloads if p.get("stage") == "INTEGRATION"]
            self.assertEqual(2, len(integration))
            for payload in integration:
                text = payload["verbatim"]["testMethod"]["text"]
                self.assertEqual(1, text.count("@Test"))
                self.assertNotIn("sourceFiles", payload)

    def test_payload_never_ships_detector_normalized_text_as_verbatim(self):
        """检测器给的语句是去缩进的规范化视图；它若进了 verbatim 区，模型照抄就会得到
        一个永远匹配不上的 oldString。
        The detector's statements are a de-indented normalized view; if one reached the
        verbatim region, copying it would yield an `oldString` that can never match."""
        with tempfile.TemporaryDirectory() as temporary:
            service, source, run_id, _ = self._fixture(temporary, "e" * 32)
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())
            agent.run(run_id, ["demo.Dependency::1"], "gpt-test")

            content = source.read_text(encoding="utf-8")
            for payload in provider.payloads:
                for entry in payload["verbatim"].get("mockStatements", []):
                    self.assertIn(entry["text"], content)
                method = payload["verbatim"].get("testMethod")
                if method:
                    self.assertIn(method["text"], content)

    def test_retries_after_harness_failure_and_keeps_final_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "b" * 32)
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, SequencedHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra")

            self.assertIn("REPAIR", provider.stages)
            self.assertIn("+        Dependency value = createDependency();", result["diff"])

    def test_failed_edits_are_retried_inside_their_own_stage(self):
        """编辑应用不上是纯局部问题（oldString 没匹配上），在这一步就地重试比冒泡到外层
        便宜也更准——外层拿到的是拼装好的整份提案，分不清是哪一步出的问题。
        A failed edit is a purely local problem (`oldString` did not match); retrying inside
        the step is cheaper and more precise than letting it bubble up, where the outer loop
        sees one assembled proposal and cannot tell which step went wrong."""
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary)
            provider = RepairingProvider()
            agent = RefactoringAgent(service, provider, SequencedHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-test", max_retries=1)

            integration = [entry for entry in result["stageLog"] if entry.get("stage") == "INTEGRATION"]
            self.assertEqual([1, 2], [entry["attempt"] for entry in integration])
            self.assertIn("editErrors", integration[0])
            self.assertNotIn("editErrors", integration[1])
            # 外层预算只花在 harness 失败上，没有被编辑歧义消耗掉。
            # The outer budget is spent on harness failures only, not on edit ambiguity.
            self.assertEqual(["HARNESS"], [entry["type"] for entry in result["repairHistory"]])
            self.assertEqual(1, result["repairAttemptsUsed"])

    def test_verified_cache_is_source_bound_and_revalidated_without_model_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            service, source, run_id, _ = self._fixture(temporary, "f" * 32)
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())

            first = agent.run(run_id, ["demo.Dependency::1"], "gpt-test")
            calls_after_first = provider.calls
            second = agent.run(run_id, ["demo.Dependency::1"], "gpt-test")

            self.assertTrue(first["harness"]["equivalent"])
            self.assertTrue(second["cache"]["hit"])
            self.assertTrue(second["cache"]["revalidated"])
            self.assertEqual(calls_after_first, provider.calls)
            source.write_text(java_source(["testRenamed"]), encoding="utf-8")
            status = agent.cache_status(run_id, ["demo.Dependency::1"])
            self.assertFalse(status["available"])

    def test_ai_audit_is_advisory_and_cannot_fail_a_verified_refactoring(self):
        """论文的成功判据只有编译、行为、变异三层，审查不在其中。把"抽成 helper""提成
        @BeforeEach 字段"这类工具本身的目标判成失败，是审查提示词与工具目的冲突，不是缺陷；
        而且这种否决过去还会被贴成 FAILED_BEHAVIORAL_EQUIVALENCE，和真的测试行为差异混为一谈。
        The paper's success criteria has three tiers — compilation, behavior, mutation — and
        the audit is not one of them. Failing helper extraction or a @BeforeEach field, which
        are the tool's own goals, reflected an audit prompt at odds with its purpose rather
        than a defect; such a veto was also labelled FAILED_BEHAVIORAL_EQUIVALENCE, making it
        indistinguishable from a genuine difference in test outcomes."""
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "e" * 32)
            agent = RefactoringAgent(service, ConcernedAuditProvider(), PassingHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-test")

            self.assertEqual("HIGH", result["harness"]["aiAudit"]["risk"])
            self.assertTrue(result["harness"]["aiAuditConcern"])
            self.assertTrue(result["harness"]["equivalent"])
            self.assertEqual("SUCCESS", classify_agent_result(result))

    def test_sequence_selection_narrows_instance_to_chosen_subset(self):
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "c" * 32,
                                                  methods=["testFirst", "testSecond", "testThird"])
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra",
                               sequence_selection={"demo.Dependency::1": [0, 2]})

            self.assertEqual("COMPLETED", result["stage"])
            integration = [p for p in provider.payloads if p.get("stage") == "INTEGRATION"]
            self.assertEqual({"testFirst", "testThird"},
                             {p["facts"]["testMethodName"] for p in integration})

    def test_sequence_selection_excluding_every_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "d" * 32)
            agent = RefactoringAgent(service, StagedProvider())

            with self.assertRaises(DetectionError):
                agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra",
                          sequence_selection={"demo.Dependency::1": [999]})

    def test_attribute_branch_is_done_in_code_without_a_model_call(self):
        """没有共享 stub 时，集成只是删掉局部创建再改名——由代码做，可复现且不花 token。
        With no shared stubbing, integration is a delete plus a rename — done in code,
        reproducibly and without spending a token."""
        with tempfile.TemporaryDirectory() as temporary:
            service, _, run_id, _ = self._fixture(temporary, "e" * 32, shared_statements=[])
            provider = StagedProvider()
            agent = RefactoringAgent(service, provider, PassingHarness())

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-test")

            self.assertEqual("COMPLETED", result["stage"])
            self.assertEqual(["ENCAPSULATION", "AUDIT"], provider.stages)
            self.assertIn("-        Dependency value = Mockito.mock(Dependency.class);", result["diff"])

    def test_apply_edits_applies_a_unique_search_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test { int x = 1; }\n"}
            edits = [{"path": "src/Test.java", "oldString": "int x = 1;", "newString": "int x = 2;"}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, edits, [])

            self.assertEqual([], errors)
            self.assertEqual({Path("src/Test.java"): "class Test { int x = 2; }\n"}, replacements)

    def test_apply_edits_rejects_a_no_op_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test {}\n"}
            edits = [{"path": "src/Test.java", "oldString": "class Test {}", "newString": "class Test {}"}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, edits, [])

            self.assertEqual([], errors)
            self.assertEqual({}, replacements)

    def test_apply_edits_rejects_an_ambiguous_old_string_without_replace_all(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "mock(A.class);\nmock(A.class);\n"}
            edits = [{"path": "src/Test.java", "oldString": "mock(A.class);", "newString": "helper();"}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, edits, [])

            self.assertEqual({}, replacements)
            self.assertTrue(any("2 locations" in error for error in errors))

    def test_apply_edits_replace_all_changes_every_occurrence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "mock(A.class);\nmock(A.class);\n"}
            edits = [{"path": "src/Test.java", "oldString": "mock(A.class);", "newString": "helper();", "replaceAll": True}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, edits, [])

            self.assertEqual([], errors)
            self.assertEqual({Path("src/Test.java"): "helper();\nhelper();\n"}, replacements)

    def test_apply_edits_rejects_old_string_not_found(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test {}\n"}
            edits = [{"path": "src/Test.java", "oldString": "does not exist", "newString": "x"}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, edits, [])

            self.assertEqual({}, replacements)
            self.assertTrue(any("not found" in error for error in errors))

    def test_apply_edits_creates_a_new_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test {}\n"}
            new_files = [{"path": "src/Helper.java", "content": "class Helper {}\n"}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, [], new_files)

            self.assertEqual([], errors)
            self.assertEqual({Path("src/Helper.java"): "class Helper {}\n"}, replacements)

    def test_apply_edits_rejects_a_new_file_that_already_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test {}\n"}
            new_files = [{"path": "src/Test.java", "content": "class Test { int x; }\n"}]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, [], new_files)

            self.assertEqual({}, replacements)
            self.assertTrue(any("already exists" in error for error in errors))

    def test_apply_edits_rejects_a_path_that_escapes_the_project_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test {}\n"}
            edits = [{"path": "../outside/Test.java", "oldString": "class Test {}", "newString": "x"}]
            new_files = [{"path": "../outside/New.java", "content": "class New {}\n"}]

            edit_replacements, edit_errors = RefactoringAgent._apply_edits(root, allowed, edits, [])
            new_file_replacements, new_file_errors = RefactoringAgent._apply_edits(root, allowed, [], new_files)

            self.assertEqual({}, edit_replacements)
            self.assertTrue(edit_errors)
            self.assertEqual({}, new_file_replacements)
            self.assertTrue(new_file_errors)

    def test_apply_edits_is_all_or_nothing_across_multiple_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test { int a = 1; int b = 1; }\n"}
            edits = [
                {"path": "src/Test.java", "oldString": "int a = 1;", "newString": "int a = 2;"},
                {"path": "src/Test.java", "oldString": "does not exist", "newString": "x"},
            ]

            replacements, errors = RefactoringAgent._apply_edits(root, allowed, edits, [])

            self.assertEqual({}, replacements)
            self.assertTrue(errors)

    def test_goal_check_detects_unreduced_duplication(self):
        instances = [{
            "sequences": [{
                "shareableMockLines": {"5": "Mockito.mock(Dependency.class);"},
                "rawStatementInfo": {"5": {"isMockRelated": True}},
            }]
        }]
        original = (
            "class Test {\n"
            "  void a() {\n"
            "    Mockito.mock(Dependency.class);\n"
            "  }\n"
            "  void b() {\n"
            "    Mockito.mock(Dependency.class);\n"
            "  }\n"
            "}\n"
        )
        files = {Path("src/Test.java"): original}
        unreduced = {Path("src/Test.java"): original.replace("}\n", "}\n  void c() {}\n", 1)}
        reduced = {Path("src/Test.java"): (
            "class Test {\n"
            "  Dependency dep = Mockito.mock(Dependency.class);\n"
            "  void a() { use(dep); }\n"
            "  void b() { use(dep); }\n"
            "}\n"
        )}

        self.assertFalse(RefactoringAgent._goal_check(instances, files, unreduced))
        self.assertTrue(RefactoringAgent._goal_check(instances, files, reduced))

    def test_goal_check_ignores_non_mock_related_shareable_lines(self):
        instances = [{
            "sequences": [{
                "shareableMockLines": {"5": "list.add(item1);"},
                "rawStatementInfo": {"5": {"isMockRelated": False}},
            }]
        }]
        original = (
            "class Test {\n"
            "  void setup() {\n"
            "    list.add(item1);\n"
            "  }\n"
            "  void testFoo() {\n"
            "    list.clear();\n"
            "    list.add(item1);\n"
            "  }\n"
            "}\n"
        )
        files = {Path("src/Test.java"): original}
        unchanged = {Path("src/Test.java"): original}

        self.assertTrue(RefactoringAgent._goal_check(instances, files, unchanged))


if __name__ == "__main__":
    unittest.main()


class NonSourcePathTest(unittest.TestCase):
    """本工具自己的一次性副本会被旧版建在被测项目里面，检测器会把它当源码扫进去。
    全量构建时这是隐形的；裁剪之后 `-pl` 点名一个不存在的模块，Maven 直接报
    `Could not find the selected project in the reactor`。
    An earlier version placed this tool's disposable copies inside the analyzed project and
    the detector indexed them as source. Invisible under a full-reactor build; once scoped,
    `-pl` names a module that does not exist and Maven fails outright."""

    def test_a_copy_inside_the_project_is_not_project_source(self):
        self.assertFalse(RefactoringAgent._is_project_source(
            Path(".clonedemocker-workspaces/ddc2b48/dubbo-remoting-api/src/test/java/A.java")))

    def test_build_output_is_not_project_source(self):
        self.assertFalse(RefactoringAgent._is_project_source(Path("module/target/generated/A.java")))
        self.assertFalse(RefactoringAgent._is_project_source(Path("module/build/tmp/A.java")))

    def test_real_test_source_is_kept(self):
        self.assertTrue(RefactoringAgent._is_project_source(
            Path("dubbo-remoting/dubbo-remoting-api/src/test/java/A.java")))

    def test_affected_files_skips_a_copy_and_keeps_the_real_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "mod" / "src" / "test" / "java" / "A.java"
            copy = root / ".clonedemocker-workspaces" / "abc" / "mod" / "src" / "test" / "java" / "A.java"
            for path in (real, copy):
                path.parent.mkdir(parents=True)
                path.write_text("class A {}\n", encoding="utf-8")
            instances = [{"sequences": [{"filePath": str(real)}, {"filePath": str(copy)}]}]

            files = RefactoringAgent._affected_files(root, instances)

            self.assertEqual([Path("mod/src/test/java/A.java")], list(files))


class CacheGenerationTest(unittest.TestCase):
    """缓存命中不花 token，所以没有任何迹象提示答案来自上一版指令。prompt 一改，
    旧条目必须失效并被清掉，否则下一批数据里会悄悄混进上一版的结果。
    A cache hit spends no tokens, so nothing signals that an answer came from an older set of
    instructions. When a prompt changes, old entries must stop matching and be removed, or the
    next batch silently mixes in results from the previous version."""

    def _agent(self, temporary: str) -> RefactoringAgent:
        return RefactoringAgent(DetectionService(Path(temporary) / "tool"))

    def test_an_entry_from_another_generation_is_not_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = self._agent(temporary)
            agent._write_cache("k" * 8, {"version": 1, "key": "k" * 8,
                                         "response": {"harness": {"equivalent": True}}})
            self.assertIsNotNone(agent._read_cache("k" * 8))

            stored = agent._cache_directory() / f"{'k' * 8}.json"
            value = json.loads(stored.read_text(encoding="utf-8"))
            value["generation"] = "from-an-older-prompt"
            stored.write_text(json.dumps(value), encoding="utf-8")

            self.assertIsNone(agent._read_cache("k" * 8))

    def test_writing_prunes_entries_from_older_generations(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = self._agent(temporary)
            stale = agent._cache_directory() / "stale.json"
            stale.write_text(json.dumps({"version": 1, "key": "stale", "generation": "older"}),
                             encoding="utf-8")

            agent._write_cache("fresh", {"version": 1, "key": "fresh",
                                         "response": {"harness": {"equivalent": True}}})

            self.assertFalse(stale.exists())
            self.assertTrue((agent._cache_directory() / "fresh.json").exists())

    def test_editing_a_prompt_changes_the_generation(self):
        """读写都走字节，不走文本：Windows 上 `write_text` 会把 `\\n` 换成 `\\r\\n`，
        "恢复原状"会留下一份换行符不同的文件，指纹再也回不到原值。
        Bytes rather than text on both sides: on Windows `write_text` turns `\\n` into
        `\\r\\n`, so "restoring" would leave a file with different line endings and the
        fingerprint would never return to its original value."""
        before = RefactoringAgent._generation()
        prompt = Path(__file__).resolve().parents[1] / "studio" / "prompts" / "_edit_protocol.md"
        original = prompt.read_bytes()
        try:
            prompt.write_bytes(original + b"\nAn extra rule.\n")
            self.assertNotEqual(before, RefactoringAgent._generation())
        finally:
            prompt.write_bytes(original)
        self.assertEqual(before, RefactoringAgent._generation())
