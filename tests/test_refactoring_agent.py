import json
import tempfile
import unittest
from pathlib import Path

from app.detection_service import DetectionError, DetectionService
from app.model_provider import ModelResult, ModelUsage
from app.refactoring_agent import RefactoringAgent
from app.harness import HarnessEvidence, HarnessStatus


class FakeProvider:
    def __init__(self, replacement: str):
        self.replacement = replacement

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        response = {"canRefactor": True, "reason": "safe", "summary": "helper extracted",
                    "files": [{"path": "src/Test.java", "newContent": self.replacement}]}
        return ModelResult(json.dumps(response), "fake-response", model, ModelUsage(10, 0, 5, 0, 15), None)


class RepairingProvider:
    def __init__(self):
        self.calls = 0

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        self.calls += 1
        value = 2 if self.calls == 1 else 3
        response = {"canRefactor": True, "reason": "repaired", "summary": "helper extracted",
                    "files": [{"path": "src/Test.java", "newContent": f"class Test {{ int value = {value}; }}\n"}]}
        return ModelResult(json.dumps(response), f"fake-{self.calls}", model, ModelUsage(10, 0, 5, 0, 15), None)


class RecordingProvider:
    def __init__(self, replacement: str):
        self.replacement = replacement
        self.captured_input: str | None = None

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        self.captured_input = input_text
        response = {"canRefactor": True, "reason": "safe", "summary": "helper extracted",
                    "files": [{"path": "src/Test.java", "newContent": self.replacement}]}
        return ModelResult(json.dumps(response), "fake-response", model, ModelUsage(10, 0, 5, 0, 15), None)


class SequencedHarness:
    def __init__(self):
        self.calls = 0

    def validate(self, project_root: Path, run_pit: bool = False) -> HarnessEvidence:
        self.calls += 1
        status = HarnessStatus.FAILED if self.calls == 2 else HarnessStatus.PASSED
        return HarnessEvidence(status, status, HarnessStatus.NOT_RUN, diagnostics=["compile error"] if status == HarnessStatus.FAILED else [])


class RefactoringAgentTest(unittest.TestCase):
    def test_writes_isolated_candidate_and_keeps_original_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "tool"
            project = Path(temporary) / "subject"
            source = project / "src" / "Test.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Test { int oldValue = 1; }\n", encoding="utf-8")
            service = DetectionService(repository)
            run_id = "a" * 32
            run_dir = service.runs_root / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"projectRoot": str(project)}), encoding="utf-8")
            raw = {"detectedMockClones": {"demo.Dependency": [{"sequences": [{"filePath": str(source)}]}]}}
            (run_dir / "mock-clone-instances.json").write_text(json.dumps(raw), encoding="utf-8")

            replacement = "class Test { int newValue = 2; }\n"
            agent = RefactoringAgent(service, FakeProvider(replacement))
            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra")

            self.assertEqual("COMPLETED", result["stage"])
            self.assertIn("-class Test { int oldValue = 1; }", result["diff"])
            self.assertEqual("class Test { int oldValue = 1; }\n", source.read_text(encoding="utf-8"))
            candidate = run_dir / "refactoring" / result["proposalId"] / "candidate-files" / "src" / "Test.java"
            self.assertEqual(replacement, candidate.read_text(encoding="utf-8"))
            applied = agent.apply(run_id, result["proposalId"])
            self.assertTrue(applied["applied"])
            self.assertEqual(replacement, source.read_text(encoding="utf-8"))

    def test_retries_after_harness_failure_and_keeps_final_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "tool"
            project = Path(temporary) / "subject"
            source = project / "src" / "Test.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Test { int value = 1; }\n", encoding="utf-8")
            service = DetectionService(repository)
            run_id = "b" * 32
            run_dir = service.runs_root / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"projectRoot": str(project)}), encoding="utf-8")
            (run_dir / "mock-clone-instances.json").write_text(json.dumps({
                "detectedMockClones": {"demo.Dependency": [{"sequences": [{"filePath": str(source)}]}]}
            }), encoding="utf-8")
            provider = RepairingProvider()
            agent = RefactoringAgent(service, provider)
            agent.harness = SequencedHarness()

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra")

            self.assertEqual(2, result["modelCalls"])
            self.assertEqual(2, provider.calls)
            self.assertIn("+class Test { int value = 3; }", result["diff"])

    def test_sequence_selection_narrows_instance_to_chosen_subset(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "tool"
            project = Path(temporary) / "subject"
            source = project / "src" / "Test.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Test { int value = 1; }\n", encoding="utf-8")
            service = DetectionService(repository)
            run_id = "c" * 32
            run_dir = service.runs_root / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"projectRoot": str(project)}), encoding="utf-8")
            (run_dir / "mock-clone-instances.json").write_text(json.dumps({
                "detectedMockClones": {"demo.Dependency": [{"sequences": [
                    {"mockObjectId": 0, "filePath": str(source), "testMethodName": "testFirst"},
                    {"mockObjectId": 1, "filePath": str(source), "testMethodName": "testSecond"},
                    {"mockObjectId": 2, "filePath": str(source), "testMethodName": "testThird"},
                ]}]}
            }), encoding="utf-8")
            provider = RecordingProvider("class Test { int value = 2; }\n")
            agent = RefactoringAgent(service, provider)

            result = agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra",
                                sequence_selection={"demo.Dependency::1": [0, 2]})

            self.assertEqual("COMPLETED", result["stage"])
            payload = json.loads(provider.captured_input)
            sent_sequences = payload["selectedMockCloneInstances"][0]["sequences"]
            self.assertEqual({0, 2}, {sequence["mockObjectId"] for sequence in sent_sequences})

    def test_sequence_selection_excluding_every_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "tool"
            project = Path(temporary) / "subject"
            source = project / "src" / "Test.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Test { int value = 1; }\n", encoding="utf-8")
            service = DetectionService(repository)
            run_id = "d" * 32
            run_dir = service.runs_root / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"projectRoot": str(project)}), encoding="utf-8")
            (run_dir / "mock-clone-instances.json").write_text(json.dumps({
                "detectedMockClones": {"demo.Dependency": [{"sequences": [
                    {"mockObjectId": 0, "filePath": str(source), "testMethodName": "testFirst"},
                ]}]}
            }), encoding="utf-8")
            agent = RefactoringAgent(service, RecordingProvider("class Test { int value = 2; }\n"))

            with self.assertRaises(DetectionError):
                agent.run(run_id, ["demo.Dependency::1"], "gpt-5.6-terra",
                          sequence_selection={"demo.Dependency::1": [999]})

    def test_model_input_strips_duplicate_method_source_but_keeps_other_fields(self):
        project_root = Path("subject")
        files = {Path("src/Test.java"): "class Test {}\n"}
        instances = [{
            "sequences": [{
                "mockObjectId": 0,
                "testMethodRawCode": "class Test { void testA() {} }",
                "shareableMockLines": {"5": "Mockito.mock(Dependency.class);"},
                "rawStatementInfo": {
                    "5": {
                        "code": "Mockito.mock(Dependency.class);",
                        "locationContext": {
                            "methodName": "testA",
                            "methodRawCode": "class Test { void testA() {} }",
                        },
                    }
                },
            }]
        }]

        request = RefactoringAgent._model_input(project_root, instances, files, "")
        payload = json.loads(request)
        sequence = payload["selectedMockCloneInstances"][0]["sequences"][0]

        self.assertNotIn("testMethodRawCode", sequence)
        self.assertNotIn("methodRawCode", sequence["rawStatementInfo"]["5"]["locationContext"])
        self.assertEqual(0, sequence["mockObjectId"])
        self.assertEqual("Mockito.mock(Dependency.class);", sequence["rawStatementInfo"]["5"]["code"])
        # The original detection data passed in must not be mutated.
        self.assertIn("testMethodRawCode", instances[0]["sequences"][0])
        self.assertIn("methodRawCode", instances[0]["sequences"][0]["rawStatementInfo"]["5"]["locationContext"])

    def test_validate_replacements_rejects_unchanged_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {Path("src/Test.java"): "class Test {}\n"}
            unchanged = [{"path": "src/Test.java", "newContent": "class Test {}\n"}]
            changed = [{"path": "src/Test.java", "newContent": "class Test { int x; }\n"}]

            self.assertEqual({}, RefactoringAgent._validate_replacements(root, allowed, unchanged))
            self.assertEqual(
                {Path("src/Test.java"): "class Test { int x; }\n"},
                RefactoringAgent._validate_replacements(root, allowed, changed),
            )

    def test_goal_check_detects_unreduced_duplication(self):
        instances = [{
            "sequences": [{"shareableMockLines": {"5": "Mockito.mock(Dependency.class);"}}]
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


if __name__ == "__main__":
    unittest.main()
