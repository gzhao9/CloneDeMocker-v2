import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.detection_service import DetectionError, DetectionService


DETECTION = {
    "detectedMockClones": {"demo.Foo": [{"sequences": []}, {"sequences": []}]},
    "detectedMockObjects": [{"rawMockObjectId": 0}, {"rawMockObjectId": 1}, {"rawMockObjectId": 2}],
}


class SavedDetectionTest(unittest.TestCase):
    """data/ 里已有检测结果时，界面可以跳过扫描和检测直接进入重构。
    With a detection already in data/, the UI can skip scanning and detection."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.repo = base / "tool"
        self.project = base / "projects" / "demo-1.0"
        self.project.mkdir(parents=True)
        self.service = DetectionService(self.repo)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, meta=None):
        data = self.repo / "data" / "demo-1.0"
        data.mkdir(parents=True)
        (data / "detection.json").write_text(json.dumps(DETECTION), encoding="utf-8")
        if meta is not None:
            (data / "detection-meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_nothing_saved_means_nothing_to_offer(self):
        self.assertFalse(self.service.cached_detection(str(self.project))["available"])

    def test_the_summary_counts_what_the_dialog_shows(self):
        self.save({"projectRoot": str(self.project), "detectedAt": "2026-09-20T14:47:00+00:00"})
        summary = self.service.cached_detection(str(self.project))
        self.assertTrue(summary["available"])
        self.assertEqual(2, summary["mciCount"])
        self.assertEqual(3, summary["mockObjectCount"])
        self.assertTrue(summary["projectRootMatches"])

    def test_a_detection_from_another_location_is_flagged(self):
        self.save({"projectRoot": str(self.project.parent / "elsewhere")})
        self.assertFalse(self.service.cached_detection(str(self.project))["projectRootMatches"])

    def test_restoring_builds_a_run_that_refactoring_can_read(self):
        self.save({"scanSeconds": 139.6})
        restored = self.service.restore_from_data(str(self.project))
        self.assertEqual(["demo.Foo::1", "demo.Foo::2"], [item["id"] for item in restored["mockCloneInstances"]])
        run, raw = self.service.load_raw_detection(restored["runId"])
        self.assertEqual(self.project.resolve(), run.project_root)
        self.assertEqual(DETECTION, raw)
        self.assertTrue((run.run_directory / "detection-meta.json").is_file())

    def test_scan_records_how_often_resolution_degraded(self):
        # JavaParser 在自引用泛型上栈溢出时检测器退回语法匹配（druid），次数要跟着 meta 走。
        # The detector falls back to syntactic matching when JavaParser overflows on recursive
        # generics (druid); the count has to travel with the meta.
        warning = ("[WARN] Symbol resolution degraded to syntactic matching at 17 site(s) because JavaParser"
                   " overflowed the stack on recursive generic types.")

        def run(command, cwd, progress_callback=None):
            Path(command[command.index("scan") + 2]).write_text("[]", encoding="utf-8")
            return "[PROGRESS] SCAN 1/1\n" + warning + "\n"

        with patch.object(DetectionService, "_detector_jar", return_value=Path("detector.jar")), \
                patch.object(DetectionService, "_run", side_effect=run):
            result = self.service.scan(str(self.project), [], [], [], False)
        meta = json.loads((self.service.runs_root / result["runId"] / "detection-meta.json").read_text(encoding="utf-8"))
        self.assertEqual(17, meta["resolutionDegradedSites"])

    def test_restoring_without_a_saved_detection_fails_clearly(self):
        with self.assertRaises(DetectionError):
            self.service.restore_from_data(str(self.project))


if __name__ == "__main__":
    unittest.main()
