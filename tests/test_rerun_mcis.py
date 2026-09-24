import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validation import rerun_mcis


class RemapPathsTest(unittest.TestCase):
    def test_windows_prefix_moves_to_the_local_root(self):
        detection = {"detectedMockClones": {"Foo": [{"sequences": [
            {"filePath": r"D:\Java_projects\Spring\spring-security-7.1.1\web\src\test\java\FooTests.java",
             "className": "FooTests"}]}]}}
        local = Path("/home/mate/spring-security-7.1.1")
        remapped = rerun_mcis._remap_paths(detection, r"D:\Java_projects\Spring\spring-security-7.1.1", local)
        path = remapped["detectedMockClones"]["Foo"][0]["sequences"][0]["filePath"]
        self.assertEqual(str(local.joinpath("web", "src", "test", "java", "FooTests.java")), path)
        self.assertEqual("FooTests", remapped["detectedMockClones"]["Foo"][0]["sequences"][0]["className"])

    def test_unrelated_strings_are_left_alone(self):
        self.assertEqual("when(foo()).thenReturn(bar)",
                         rerun_mcis._remap_paths("when(foo()).thenReturn(bar)", r"D:\x", Path("/y")))


class MergeTest(unittest.TestCase):
    def _source(self, temporary: str) -> Path:
        source = Path(temporary) / "sent-back"
        (source / "diffs").mkdir(parents=True)
        (source / "diffs" / "A__1.diff").write_text("--- a\n+++ b\n", encoding="utf-8")
        results = {
            "A::1": {"classification": "SUCCESS", "model": "gpt-5.6-terra", "diffFile": "diffs/A__1.diff"},
            "B::1": {"classification": "SUCCESS", "model": "gpt-5.6-terra"},
        }
        (source / "refactoring-results.json").write_text(json.dumps(
            {"project": "demo", "model": "gpt-5.6-terra", "results": results}), encoding="utf-8")
        return source

    def test_only_listed_ids_are_merged_and_existing_entries_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            target = repository / "data" / "demo" / "refactoring" / "CloneDeMocker+Terra-5.6"
            target.mkdir(parents=True)
            (target / "refactoring-results.json").write_text(json.dumps({"results": {
                "A::1": {"classification": "ENVIRONMENT_NOT_READY"},
                "C::1": {"classification": "SUCCESS"}}}), encoding="utf-8")
            args = argparse.Namespace(source=str(self._source(temporary)), ids="A::1", ids_file=None,
                                      project=None, project_root=None)
            with patch.object(rerun_mcis, "REPOSITORY_ROOT", repository):
                self.assertEqual(0, rerun_mcis.merge(args))
            merged = json.loads((target / "refactoring-results.json").read_text(encoding="utf-8"))["results"]
            self.assertEqual({"A::1", "C::1"}, set(merged))
            self.assertEqual("SUCCESS", merged["A::1"]["classification"])
            self.assertTrue((target / merged["A::1"]["diffFile"]).is_file())

    def test_an_id_missing_from_the_source_stops_the_merge(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(source=str(self._source(temporary)), ids="Z::9", ids_file=None,
                                      project=None, project_root=None)
            with patch.object(rerun_mcis, "REPOSITORY_ROOT", Path(temporary) / "repo"):
                self.assertEqual(2, rerun_mcis.merge(args))


class IdsFileTest(unittest.TestCase):
    def test_the_shipped_list_is_parsed_without_comments(self):
        path = Path(rerun_mcis.__file__).with_name("spring_security_dns_mcis.txt")
        ids = rerun_mcis._read_ids("", str(path))
        self.assertEqual(5, len(ids))
        self.assertTrue(all("::" in value for value in ids))


if __name__ == "__main__":
    unittest.main()
