from __future__ import annotations

import json
import hashlib
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_DIRECTORIES = {".git", ".gradle", ".idea", "build", "target", "node_modules"}


class DetectionError(RuntimeError):
    """检测服务错误，包含可展示给用户的命令输出。 / Detection error with user-facing command output."""


@dataclass(frozen=True)
class DetectionRun:
    run_id: str
    project_root: Path
    run_directory: Path


class DetectionService:
    """
    对应论文 Detection 阶段：Detection Scope、Mock Logic Extraction、
    Frequent Stub Set Mining 与 Mock Clone Instance Formation。

    Implements the paper's Detection stage: Detection Scope, Mock Logic
    Extraction, Frequent Stub Set Mining, and Mock Clone Instance Formation.
    """

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.detector_root = self.repository_root / "DETECTION"
        self.runs_root = self.repository_root / ".clonedemocker" / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def source_tree(self, project_root: str) -> dict[str, Any]:
        root = self._project_root(project_root)

        def visit(directory: Path) -> dict[str, Any] | None:
            children: list[dict[str, Any]] = []
            try:
                entries = sorted(directory.iterdir(), key=lambda path: (path.is_file(), path.name.lower()))
            except OSError:
                return None
            for entry in entries:
                if entry.is_dir() and entry.name not in IGNORED_DIRECTORIES:
                    child = visit(entry)
                    if child and child["children"]:
                        children.append(child)
                elif entry.is_file() and entry.suffix == ".java":
                    children.append({
                        "name": entry.name,
                        "path": entry.relative_to(root).as_posix(),
                        "type": "file",
                    })
            if not children:
                return None
            relative = "." if directory == root else directory.relative_to(root).as_posix()
            return {"name": directory.name, "path": relative, "type": "directory", "children": children}

        return visit(root) or {
            "name": root.name, "path": ".", "type": "directory", "children": []
        }

    def scan(
        self,
        project_root: str,
        include_paths: list[str],
        exclude_paths: list[str],
        package_prefixes: list[str],
        resolve_dependencies: bool,
    ) -> dict[str, Any]:
        root = self._project_root(project_root)
        run_id = uuid.uuid4().hex
        run_directory = self.runs_root / run_id
        run_directory.mkdir(parents=True)

        scope = {
            "includePaths": include_paths,
            "excludePaths": exclude_paths,
            "packagePrefixes": package_prefixes,
        }
        (run_directory / "scope.json").write_text(
            json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_directory / "run.json").write_text(
            json.dumps({"projectRoot": str(root)}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        command = [
            self._java(),
            "-jar",
            str(self._detector_jar()),
            "scan",
            str(root),
            str(run_directory / "mock-objects.json"),
            "--scope",
            str(run_directory / "scope.json"),
        ]
        if not resolve_dependencies:
            command.append("--skip")
        output = self._run(command, root)

        mock_objects = json.loads((run_directory / "mock-objects.json").read_text(encoding="utf-8"))
        return {
            "runId": run_id,
            "scope": scope,
            "mockObjects": [self._summarize_mock(root, item) for item in mock_objects],
            "diagnostics": output[-12000:],
        }

    def detect(self, run_id: str, selected_mock_ids: list[int]) -> dict[str, Any]:
        run = self._load_run(run_id)
        ids_path = run.run_directory / "selected-mock-ids.json"
        ids_path.write_text(json.dumps(selected_mock_ids), encoding="utf-8")
        output = self._run(
            [
                self._java(),
                "-jar",
                str(self._detector_jar()),
                "detect",
                str(run.run_directory / "mock-objects.json"),
                str(run.run_directory / "mock-clone-instances.json"),
                "--mock-ids",
                str(ids_path),
            ],
            run.project_root,
        )
        result = json.loads(
            (run.run_directory / "mock-clone-instances.json").read_text(encoding="utf-8")
        )
        instances: list[dict[str, Any]] = []
        for mocked_class, values in result.get("detectedMockClones", {}).items():
            for index, instance in enumerate(values):
                item = dict(instance)
                item["id"] = f"{mocked_class}::{index + 1}"
                instances.append(item)
        return {
            "runId": run_id,
            "selectedMockObjectCount": len(selected_mock_ids),
            "mockCloneInstances": instances,
            "rawResult": result,
            "diagnostics": output[-12000:],
        }

    def load_raw_detection(self, run_id: str) -> tuple[DetectionRun, dict[str, Any]]:
        run = self._load_run(run_id)
        result_path = run.run_directory / "mock-clone-instances.json"
        if not result_path.exists():
            raise DetectionError("Run has no detection result / 该运行还没有检测结果")
        return run, json.loads(result_path.read_text(encoding="utf-8"))

    def load_run(self, run_id: str) -> DetectionRun:
        """读取受路径约束的运行记录 / Loads a path-confined run record."""
        return self._load_run(run_id)

    def _load_run(self, run_id: str) -> DetectionRun:
        if not run_id or any(character not in "0123456789abcdef" for character in run_id):
            raise DetectionError("Invalid run ID / 无效运行 ID")
        directory = (self.runs_root / run_id).resolve()
        if directory.parent != self.runs_root.resolve() or not directory.is_dir():
            raise DetectionError("Detection run not found / 未找到检测运行")
        metadata = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        return DetectionRun(run_id, self._project_root(metadata["projectRoot"]), directory)

    @staticmethod
    def _summarize_mock(project_root: Path, item: dict[str, Any]) -> dict[str, Any]:
        context = item.get("classContext") or {}
        raw_path = Path(context.get("filePath") or "")
        try:
            file_path = raw_path.resolve().relative_to(project_root).as_posix()
        except (OSError, ValueError):
            file_path = str(raw_path)
        test_methods = sorted({
            statement.get("locationContext", {}).get("methodName", "")
            for statement in item.get("statements", [])
            if statement.get("locate") == "Test Case"
            or any("Test" in annotation for annotation in
                   statement.get("locationContext", {}).get("methodAnnotations", []) or [])
        } - {""})
        return {
            "id": item.get("rawMockObjectId"),
            "variableName": item.get("variableName"),
            "variableType": item.get("variableType"),
            "mockedClass": item.get("mockedClass"),
            "mockRole": item.get("mockRole"),
            "packageName": context.get("packageName"),
            "className": context.get("className"),
            "filePath": file_path,
            "testMethods": test_methods,
            "statementCount": len(item.get("statements", [])),
        }

    def _detector_jar(self) -> Path:
        matches = sorted((self.detector_root / "target").glob("*-jar-with-dependencies.jar"))
        inputs = sorted((self.detector_root / "src").rglob("*.java")) + [self.detector_root / "pom.xml"]
        digest = hashlib.sha256()
        for path in inputs:
            digest.update(path.relative_to(self.detector_root).as_posix().encode())
            digest.update(path.read_bytes())
        fingerprint = digest.hexdigest()
        stamp = self.repository_root / ".clonedemocker" / "detector-source.sha256"
        built_from_current_source = stamp.is_file() and stamp.read_text(encoding="ascii").strip() == fingerprint
        if not matches or not built_from_current_source:
            executable = "mvn.cmd" if os.name == "nt" else "mvn"
            self._run([executable, "-DskipTests", "package"], self.detector_root)
            matches = sorted((self.detector_root / "target").glob("*-jar-with-dependencies.jar"))
            stamp.write_text(fingerprint, encoding="ascii")
        if not matches:
            raise DetectionError("Detector JAR was not produced / 未生成检测器 JAR")
        return matches[-1]

    @staticmethod
    def _project_root(value: str) -> Path:
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise DetectionError(f"Project directory does not exist / 项目目录不存在: {root}")
        return root

    @staticmethod
    def _java() -> str:
        return "java.exe" if os.name == "nt" else "java"

    @staticmethod
    def _run(command: list[str], cwd: Path) -> str:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != 0:
            raise DetectionError(
                f"Command failed ({completed.returncode}) / 命令执行失败\n{completed.stdout[-12000:]}"
            )
        return completed.stdout
