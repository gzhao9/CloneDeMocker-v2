import difflib
import json
import tempfile
import unittest
from pathlib import Path

from studio.detection_service import DetectionService
from validation.run_pilot import classify_transition, generate_proposal


class ClassifyTransitionTest(unittest.TestCase):
    def test_rejects_empty_test_results_on_both_sides_as_success(self):
        """回归测试：一次真实运行中，Maven 在到达目标模块前就因为 -am 拉进来的依赖模块
        没有匹配的测试类而中止（testStatus=FAILED），before/after 的 testResults 都是
        空字典。修复前 classify_transition 只比较字典是否相等，把这种情况误判成
        SUCCESS；修复后必须先要求两边 testStatus 都是 PASSED。
        Regression test: in one real run, Maven aborted before ever reaching the target
        module because a dependency module pulled in via -am had no test matching the
        filter (testStatus=FAILED), leaving testResults empty on both sides. Before the
        fix, classify_transition only compared the dicts and misread this as SUCCESS;
        after the fix it must first require testStatus == PASSED on both sides."""
        before = {"compileStatus": "PASSED", "testStatus": "FAILED", "testResults": {}}
        after = {"compileStatus": "PASSED", "testStatus": "FAILED", "testResults": {}}

        classification = classify_transition(before, after, goal_achieved=True, pit_regressed=False)

        self.assertEqual("FAILED_BEHAVIORAL_EQUIVALENCE", classification)

    def test_accepts_matching_non_empty_test_results_as_success(self):
        before = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"}, "pitStatus": "NOT_RUN",
        }
        after = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"}, "pitStatus": "NOT_RUN",
        }

        classification = classify_transition(before, after, goal_achieved=True, pit_regressed=False)

        self.assertEqual("SUCCESS", classification)

    def test_rejects_matching_but_empty_test_results_when_commands_claim_success(self):
        before = {"compileStatus": "PASSED", "testStatus": "PASSED", "testResults": {}, "pitStatus": "NOT_RUN"}
        after = {"compileStatus": "PASSED", "testStatus": "PASSED", "testResults": {}, "pitStatus": "NOT_RUN"}

        classification = classify_transition(before, after, goal_achieved=True, pit_regressed=False)

        self.assertEqual("FAILED_BEHAVIORAL_EQUIVALENCE", classification)

    def test_rejects_a_scope_that_did_not_run_its_selected_class(self):
        before = {
            "compileStatus": "PASSED", "testStatus": "PASSED", "pitStatus": "NOT_RUN",
            "testResults": {"demo.OtherTest#testA": "PASSED"},
        }

        classification = classify_transition(
            before, before, goal_achieved=True, pit_regressed=False,
            expected_test_classes=["demo.TargetTest"],
        )

        self.assertEqual("FAILED_BEHAVIORAL_EQUIVALENCE", classification)

    def test_compile_failure_takes_priority(self):
        before = {"compileStatus": "PASSED", "testStatus": "PASSED", "testResults": {}}
        after = {"compileStatus": "FAILED", "testStatus": "NOT_RUN", "testResults": {}}

        classification = classify_transition(before, after, goal_achieved=True, pit_regressed=False)

        self.assertEqual("FAILED_SYNTACTIC_VALIDITY", classification)

    def test_rejects_pit_that_never_actually_ran_as_success(self):
        """回归测试：全量 109 个 MCI 的真实跑批里，PIT 因为 -am 拉进来的依赖模块产生
        不出变异体而被判定 BUILD FAILURE、整个 reactor 中止——95 个 SUCCESS 的
        pitStatus 全部是 FAILED、mutants 两边都是空字典，mutation_regressed() 比较
        空字典 vacuously 判定"没有退化"，导致这一层从没被真正验证过。
        Regression test: in the full 109-MCI batch, PIT was judged a BUILD FAILURE (and
        aborted the whole reactor) because a dependency module pulled in via -am produced
        no mutants — all 95 SUCCESS results had pitStatus=FAILED with empty mutants dicts
        on both sides, so mutation_regressed() vacuously read that as "no regression" and
        this tier was never actually verified."""
        before = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"},
            "pitStatus": "FAILED", "mutants": {},
        }
        after = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"},
            "pitStatus": "FAILED", "mutants": {},
        }

        classification = classify_transition(before, after, goal_achieved=True, pit_regressed=False)

        self.assertEqual("FAILED_FUNCTIONAL_INTEGRITY", classification)

    def test_pit_not_run_at_all_is_not_treated_as_a_failure(self):
        before = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"}, "pitStatus": "NOT_RUN",
        }
        after = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"}, "pitStatus": "NOT_RUN",
        }

        classification = classify_transition(before, after, goal_achieved=True, pit_regressed=False)

        self.assertEqual("SUCCESS", classification)

    def test_mutation_regression_and_goal_check_still_apply_after_tests_pass(self):
        before = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"}, "pitStatus": "NOT_RUN",
        }
        after = {
            "compileStatus": "PASSED", "testStatus": "PASSED",
            "testResults": {"demo.FooTest#testA": "PASSED"}, "pitStatus": "NOT_RUN",
        }

        self.assertEqual(
            "FAILED_FUNCTIONAL_INTEGRITY",
            classify_transition(before, after, goal_achieved=True, pit_regressed=True),
        )
        self.assertEqual(
            "FAILED_REFACTORING_GOAL",
            classify_transition(before, after, goal_achieved=False, pit_regressed=False),
        )


class ReplayGenerateProposalTest(unittest.TestCase):
    def _make_run(self, temporary: str):
        repository = Path(temporary) / "tool"
        project = Path(temporary) / "subject"
        source = project / "src" / "Test.java"
        source.parent.mkdir(parents=True)
        original = "class Test {\n  int oldValue = 1;\n}\n"
        source.write_text(original, encoding="utf-8")
        service = DetectionService(repository)
        run_id = "a" * 32
        run_dir = service.runs_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"projectRoot": str(project)}), encoding="utf-8")
        raw = {"detectedMockClones": {"demo.Dependency": [{"sequences": [{"filePath": str(source)}]}]}}
        (run_dir / "mock-clone-instances.json").write_text(json.dumps(raw), encoding="utf-8")
        run, raw = service.load_raw_detection(run_id)
        return run, raw, source, original, service.runs_root

    def test_reapplies_a_previously_saved_diff_without_calling_the_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            run, raw, source, original, runs_root = self._make_run(temporary)
            new_content = "class Test {\n  int newValue = 2;\n}\n"
            diff_text = "".join(difflib.unified_diff(
                original.splitlines(keepends=True), new_content.splitlines(keepends=True),
                fromfile="a/src/Test.java", tofile="b/src/Test.java",
            ))
            old_run_dir = runs_root / ("b" * 32)
            proposal_dir = old_run_dir / "refactoring" / "deadbeef"
            proposal_dir.mkdir(parents=True)
            (proposal_dir / "changes.diff").write_text(diff_text, encoding="utf-8")
            replay = ({"demo.Dependency::1": {"classification": "SUCCESS", "proposalId": "deadbeef"}}, old_run_dir)

            class ExplodingProvider:
                def generate(self, *args, **kwargs):
                    raise AssertionError("the model must not be called in replay mode")

            proposal = generate_proposal(run, raw, "demo.Dependency::1", ExplodingProvider(), "gpt-5.6-terra",
                                          direct_llm_baseline=False, replay=replay)

            self.assertTrue(proposal["ok"])
            self.assertEqual([], proposal["attempts"])
            self.assertEqual("deadbeef", proposal["replayedFromProposalId"])
            self.assertEqual(new_content, proposal["replacements"][Path("src/Test.java")])

    def test_carries_over_a_previously_declined_mci_without_calling_the_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            run, raw, source, original, runs_root = self._make_run(temporary)
            old_run_dir = runs_root / ("b" * 32)
            replay = ({"demo.Dependency::1": {"classification": "MODEL_DECLINED", "reason": "unsafe"}}, old_run_dir)

            class ExplodingProvider:
                def generate(self, *args, **kwargs):
                    raise AssertionError("the model must not be called in replay mode")

            proposal = generate_proposal(run, raw, "demo.Dependency::1", ExplodingProvider(), "gpt-5.6-terra",
                                          direct_llm_baseline=False, replay=replay)

            self.assertFalse(proposal["ok"])
            self.assertEqual("unsafe", proposal["reason"])


if __name__ == "__main__":
    unittest.main()
