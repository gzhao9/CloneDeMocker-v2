import json
import tempfile
import unittest
from pathlib import Path

from studio.canonical_store import classify_agent_result, entry_from_agent_result, merge


def agent_result(**overrides):
    base = {
        "stage": "COMPLETED",
        "proposalId": "p" * 32,
        "model": "gpt-test",
        "modelCalls": 3,
        "usage": {"total_tokens": 4211},
        "timings": {"generation": 12.5, "total": 340.2},
        "changedFiles": ["src/test/java/A.java"],
        "cache": {"hit": False},
        "harness": {
            "equivalent": True,
            "goalAchieved": True,
            "mutationRegressed": False,
            "baseline": {"compileStatus": "PASSED", "testStatus": "PASSED"},
            "candidate": {"compileStatus": "PASSED", "testStatus": "PASSED", "scope": "1 module(s): mod-a"},
        },
    }
    base.update(overrides)
    return base


class ClassifyTest(unittest.TestCase):
    def test_a_verified_result_is_a_success(self):
        self.assertEqual("SUCCESS", classify_agent_result(agent_result()))

    def test_a_broken_baseline_is_an_environment_problem_not_a_model_failure(self):
        """基线就没跑通的时候模型根本没被调用，把它算成"模型拒绝"会污染成功率。
        With a broken baseline the model was never called; counting it as a refusal would
        pollute the success rate."""
        result = agent_result(stage="FAILED", harness={"baselineBroken": True})
        self.assertEqual("ENVIRONMENT_NOT_READY", classify_agent_result(result))

    def test_a_declined_refactoring_is_reported_as_such(self):
        self.assertEqual("MODEL_DECLINED", classify_agent_result(agent_result(stage="FAILED", harness={})))

    def test_each_failed_tier_maps_to_its_own_label(self):
        compile_failed = agent_result()
        compile_failed["harness"]["candidate"]["compileStatus"] = "FAILED"
        self.assertEqual("FAILED_SYNTACTIC_VALIDITY", classify_agent_result(compile_failed))

        tests_failed = agent_result()
        tests_failed["harness"]["candidate"]["testStatus"] = "FAILED"
        self.assertEqual("FAILED_BEHAVIORAL_EQUIVALENCE", classify_agent_result(tests_failed))

        mutants_lost = agent_result()
        mutants_lost["harness"]["mutationRegressed"] = True
        self.assertEqual("FAILED_FUNCTIONAL_INTEGRITY", classify_agent_result(mutants_lost))

        goal_missed = agent_result()
        goal_missed["harness"]["goalAchieved"] = False
        self.assertEqual("FAILED_REFACTORING_GOAL", classify_agent_result(goal_missed))


class EntryTest(unittest.TestCase):
    def test_keeps_the_figures_the_paper_needs(self):
        entry = entry_from_agent_result("demo.Foo::1", agent_result())
        self.assertEqual(4211, entry["totalTokens"])
        self.assertEqual(340.2, entry["totalSeconds"])
        self.assertEqual(12.5, entry["generationSeconds"])
        self.assertEqual("1 module(s): mod-a", entry["scope"])
        self.assertEqual("SUCCESS", entry["classification"])


class MergeTest(unittest.TestCase):
    """合并而不是覆盖，是"断点之后补跑剩下的"能成立的前提。
    Merging rather than overwriting is what makes resuming a partial batch possible."""

    def test_a_later_run_splices_into_the_earlier_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            merge("dubbo", repo, [entry_from_agent_result("demo.A::1", agent_result())])
            summary = merge("dubbo", repo, [entry_from_agent_result("demo.B::1", agent_result())])

            self.assertEqual(1, summary["writtenThisCall"])
            self.assertEqual(2, summary["totalMcis"])
            stored = json.loads((repo / "data" / "dubbo" / "refactoring-results.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual({"demo.A::1", "demo.B::1"}, set(stored["results"]))

    def test_rerunning_one_mci_replaces_only_that_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            merge("dubbo", repo, [entry_from_agent_result("demo.A::1", agent_result()),
                                  entry_from_agent_result("demo.B::1", agent_result())])
            failed = agent_result(stage="FAILED", harness={})
            merge("dubbo", repo, [entry_from_agent_result("demo.A::1", failed)])

            stored = json.loads((repo / "data" / "dubbo" / "refactoring-results.json")
                                .read_text(encoding="utf-8"))["results"]
            self.assertEqual("MODEL_DECLINED", stored["demo.A::1"]["classification"])
            self.assertEqual("SUCCESS", stored["demo.B::1"]["classification"])

    def test_a_rerun_without_a_diff_keeps_the_one_already_stored(self):
        """补跑时可能拿不到 diff（比如命中缓存），这时不该把上一次存好的抹掉。
        A top-up run may arrive without a diff (a cache hit, say), which must not erase the one
        already on disk."""
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            diff = repo / "changes.diff"
            diff.write_text("--- a\n+++ b\n", encoding="utf-8")
            merge("dubbo", repo, [entry_from_agent_result("demo.A::1", agent_result())],
                  diff_lookup={"demo.A::1": diff})
            merge("dubbo", repo, [entry_from_agent_result("demo.A::1", agent_result())])

            stored = json.loads((repo / "data" / "dubbo" / "refactoring-results.json")
                                .read_text(encoding="utf-8"))["results"]
            self.assertIn("diffFile", stored["demo.A::1"])
            self.assertTrue((repo / "data" / "dubbo" / stored["demo.A::1"]["diffFile"]).is_file())

    def test_writes_a_csv_alongside_the_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            merge("dubbo", repo, [entry_from_agent_result("demo.A::1", agent_result())])
            rows = (repo / "data" / "dubbo" / "refactoring-results.csv").read_text(
                encoding="utf-8").splitlines()
            self.assertIn("totalSeconds", rows[0])
            self.assertIn("demo.A::1", rows[1])


if __name__ == "__main__":
    unittest.main()
