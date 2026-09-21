import json
import tempfile
import unittest
from pathlib import Path

from studio.verification_ledger import VerificationLedger

PASSED = {"compileStatus": "PASSED", "testStatus": "PASSED", "pitStatus": "NOT_RUN",
          "testResults": {"demo.FooTest#works": "PASSED"}}


class VerificationLedgerTest(unittest.TestCase):
    def ledger(self, temporary: str) -> VerificationLedger:
        return VerificationLedger(Path(temporary))

    def test_a_passing_verification_can_be_read_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = self.ledger(temporary)
            key = VerificationLedger.key("baseline", "srchash", "1 module(s): mod-a", False, "gen1")
            ledger.write(key, PASSED, "baseline")

            record = ledger.read(key)
            self.assertIsNotNone(record)
            self.assertEqual("PASSED", record["evidence"]["compileStatus"])
            self.assertIn("recordedAt", record)

    def test_a_failed_verification_is_never_recorded(self):
        """失败多半是环境性的（依赖没下全、端口被占），记下来会让一次偶发故障永久生效。
        A failure is usually environmental — an unresolved dependency, a busy port — and
        recording it would make one transient fault permanent."""
        with tempfile.TemporaryDirectory() as temporary:
            ledger = self.ledger(temporary)
            key = VerificationLedger.key("baseline", "srchash", "scope", False, "gen1")
            ledger.write(key, {**PASSED, "compileStatus": "FAILED"}, "baseline")
            self.assertIsNone(ledger.read(key))

    def test_a_passing_status_without_test_results_is_not_recorded(self):
        """旧 harness 读不到过长路径下的报告时就是这种证据：状态通过、结果为空。
        What the old harness produced when it could not read reports under an overlong path:
        passing status, no results."""
        with tempfile.TemporaryDirectory() as temporary:
            ledger = self.ledger(temporary)
            key = VerificationLedger.key("baseline", "srchash", "scope", False, "gen1")
            ledger.write(key, {**PASSED, "testResults": {}}, "baseline")
            self.assertIsNone(ledger.read(key))
            ledger.write(key, {**PASSED, "testResults": {"demo.FooTest#x": "SKIPPED"}}, "baseline")
            self.assertIsNone(ledger.read(key))

    def test_a_passing_pit_without_mutation_data_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = self.ledger(temporary)
            key = VerificationLedger.key("baseline", "srchash", "scope", True, "gen1")
            ledger.write(key, {**PASSED, "pitStatus": "PASSED"}, "baseline")
            self.assertIsNone(ledger.read(key))
            ledger.write(key, {**PASSED, "pitStatus": "PASSED", "mutants": {"m1": "KILLED"}, "mutationScore": 1.0},
                         "baseline")
            self.assertIsNotNone(ledger.read(key))

    def test_an_empty_record_already_on_disk_is_not_served(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = self.ledger(temporary)
            key = VerificationLedger.key("baseline", "srchash", "scope", False, "gen1")
            ledger.directory.mkdir(parents=True)
            (ledger.directory / f"{key}.json").write_text(json.dumps(
                {"key": key, "kind": "baseline", "recordedAt": "t", "evidence": {**PASSED, "testResults": {}}}),
                encoding="utf-8")
            self.assertIsNone(ledger.read(key))

    def test_any_changed_input_yields_a_different_key(self):
        """复用只在输入完全一致时才成立：源码、范围、PIT 开关、流水线代次任一不同都不该命中。
        Reuse only holds for identical inputs: a different source, scope, PIT setting or
        pipeline generation must not hit."""
        base = VerificationLedger.key("baseline", "srchash", "scope", False, "gen1")
        self.assertNotEqual(base, VerificationLedger.key("baseline", "OTHER", "scope", False, "gen1"))
        self.assertNotEqual(base, VerificationLedger.key("baseline", "srchash", "OTHER", False, "gen1"))
        self.assertNotEqual(base, VerificationLedger.key("baseline", "srchash", "scope", True, "gen1"))
        self.assertNotEqual(base, VerificationLedger.key("baseline", "srchash", "scope", False, "gen2"))
        self.assertNotEqual(base, VerificationLedger.key("candidate", "srchash", "scope", False, "gen1"))

    def test_the_patch_is_part_of_the_candidate_key(self):
        """同一份源码配不同补丁，验证结论完全可以不同。
        The same sources with a different patch can verify differently."""
        one = VerificationLedger.key("candidate", "src", "scope", False, "gen1", "patchA")
        two = VerificationLedger.key("candidate", "src", "scope", False, "gen1", "patchB")
        self.assertNotEqual(one, two)

    def test_the_fingerprint_follows_file_content(self):
        same_a = VerificationLedger.fingerprint({Path("A.java"): "class A {}"})
        same_b = VerificationLedger.fingerprint({Path("A.java"): "class A {}"})
        changed = VerificationLedger.fingerprint({Path("A.java"): "class A { int x; }"})
        renamed = VerificationLedger.fingerprint({Path("B.java"): "class A {}"})
        self.assertEqual(same_a, same_b)
        self.assertNotEqual(same_a, changed)
        self.assertNotEqual(same_a, renamed)

    def test_records_from_an_older_generation_are_pruned(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = self.ledger(temporary)
            ledger.directory.mkdir(parents=True, exist_ok=True)
            stale = ledger.directory / "stale.json"
            stale.write_text('{"key": "stale", "generation": "older"}', encoding="utf-8")

            removed = ledger.prune("gen-current")

            self.assertEqual(1, removed)
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
