from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


IGNORED_DIRECTORIES = {".git", ".gradle", ".idea", "build", "target", "node_modules"}

# 检测结果旁边那份说明：从哪个项目、什么范围、花了多久得来的。run 目录和 data/<project>/
# 里各有一份，名字相同，导出时原样复制过去。
# The note beside a detection result: which project, what scope, how long it took. One copy
# sits in the run directory and one in data/<project>/, under the same name.
DETECTION_META = "detection-meta.json"


# 检测器在 JavaParser 解析自引用泛型栈溢出、退回语法匹配时，结束前打印这一行。写进 meta，
# 数字才会跟着检测结果进 data/，而不是只留在会被截断的输出里。
# The detector prints this line when JavaParser overflowed the stack on recursive generics and
# resolution fell back to syntactic matching. Kept in meta so the count travels with the
# detection into data/ rather than living only in output that gets truncated.
_DEGRADED_RESOLUTION = re.compile(r"degraded to syntactic matching at (\d+) site")


def _resolution_degraded_sites(output: str) -> int:
    match = _DEGRADED_RESOLUTION.search(output)
    return int(match.group(1)) if match else 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        progress_callback: Callable[[int, int, str], None] | None = None,
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
            "-Dfile.encoding=UTF-8",
            "-Dsun.stdout.encoding=UTF-8",
            "-Dsun.stderr.encoding=UTF-8",
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
        # 计时从 jar 就绪之后开始：检测器自身的一次性构建不算检测耗时。
        # Timed once the jar is ready: a one-off build of the detector is not detection time.
        started = time.time()
        output = self._run(command, root, progress_callback)
        scan_seconds = round(time.time() - started, 2)

        mock_objects = json.loads((run_directory / "mock-objects.json").read_text(encoding="utf-8"))
        self._write_meta(run_directory, {
            "projectRoot": str(root),
            "scope": scope,
            "resolveDependencies": resolve_dependencies,
            "mockObjectsScanned": len(mock_objects),
            "timingSource": "measured",
            "scanSeconds": scan_seconds,
            "resolutionDegradedSites": _resolution_degraded_sites(output),
            "scannedAt": _now(),
        })
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
        started = time.time()
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
        detect_seconds = round(time.time() - started, 2)
        result = json.loads(
            (run.run_directory / "mock-clone-instances.json").read_text(encoding="utf-8")
        )
        instances = self._indexed_instances(result)
        meta = self._read_meta(run.run_directory)
        meta.update({
            "selectedMockObjects": len(selected_mock_ids),
            "mciCount": len(instances),
            "detectSeconds": detect_seconds,
            "detectedAt": _now(),
            "runId": run_id,
        })
        if isinstance(meta.get("scanSeconds"), (int, float)):
            meta["totalSeconds"] = round(meta["scanSeconds"] + detect_seconds, 2)
        self._write_meta(run.run_directory, meta)
        # data/ 里还没有这个项目的检测结果时自动存一份，下次打开就能跳过检测。已经有的不覆盖：
        # 已有的重构结果按 MCI 编号挂在那份检测上，换掉它编号就可能对不上。
        # With no detection for this project in data/ yet, save one so the next session can skip
        # detection. An existing one is left alone: stored refactoring results are keyed by MCI
        # numbers from that detection, and replacing it could make the numbers stop matching.
        saved_to_data = False
        data_directory = self.data_directory(run.project_root)
        if not (data_directory / "detection.json").is_file():
            data_directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run.run_directory / "mock-clone-instances.json", data_directory / "detection.json")
            shutil.copyfile(run.run_directory / DETECTION_META, data_directory / DETECTION_META)
            saved_to_data = True
        return {
            "runId": run_id,
            "selectedMockObjectCount": len(selected_mock_ids),
            "mockCloneInstances": instances,
            "rawResult": result,
            "diagnostics": output[-12000:],
            "savedToData": saved_to_data,
        }

    def data_directory(self, project_root: Path) -> Path:
        """data/<项目目录名>/，与导出重构结果时用的名字一致。
        data/<project directory name>/, the same name the refactoring export uses."""
        return self.repository_root / "data" / project_root.name

    def cached_detection(self, project_root: str) -> dict[str, Any]:
        """data/ 里是否已有这个项目的检测结果，以及界面确认框要展示的概况。
        Whether data/ already holds a detection for this project, plus the summary the UI's
        confirmation dialog shows."""
        root = self._project_root(project_root)
        directory = self.data_directory(root)
        detection_path = directory / "detection.json"
        if not detection_path.is_file():
            return {"available": False, "project": root.name}
        meta_path = directory / DETECTION_META
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        raw = json.loads(detection_path.read_text(encoding="utf-8"))
        recorded_root = meta.get("projectRoot")
        return {
            "available": True,
            "project": root.name,
            "path": detection_path.relative_to(self.repository_root).as_posix(),
            "mciCount": len(self._indexed_instances(raw)),
            "mockObjectCount": len(raw.get("detectedMockObjects") or []),
            "detectedAt": meta.get("detectedAt") or datetime.fromtimestamp(
                detection_path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "recordedProjectRoot": recorded_root,
            # 检测结果里的 filePath 是绝对路径；项目换了位置，这份结果就定位不到源文件。
            # The detection's filePaths are absolute; if the project moved, they no longer
            # point at the sources.
            "projectRootMatches": recorded_root is None or Path(recorded_root).resolve() == root,
        }

    def restore_from_data(self, project_root: str) -> dict[str, Any]:
        """
        用 data/ 里存好的检测结果开一个新 run，跳过扫描和检测。

        后面的重构只认 run 目录（run.json + mock-clone-instances.json），所以这里照原样搭一个
        出来，而不是让重构另开一条读 data/ 的路。没有 mock-objects.json，第 2 步的 mock 对象
        列表在这种 run 上是空的。
        Opens a new run from the detection stored in data/, skipping scan and detection.
        Refactoring reads only the run directory (run.json + mock-clone-instances.json), so one
        is assembled here rather than giving refactoring a second path that reads data/. There
        is no mock-objects.json, so step 2's mock object list is empty for such a run.
        """
        root = self._project_root(project_root)
        directory = self.data_directory(root)
        detection_path = directory / "detection.json"
        if not detection_path.is_file():
            raise DetectionError(f"No saved detection in data/{root.name} / data/{root.name} 中没有已保存的检测结果")
        run_id = uuid.uuid4().hex
        run_directory = self.runs_root / run_id
        run_directory.mkdir(parents=True)
        (run_directory / "run.json").write_text(json.dumps(
            {"projectRoot": str(root), "restoredFrom": detection_path.relative_to(self.repository_root).as_posix()},
            ensure_ascii=False, indent=2), encoding="utf-8")
        result = self._rebase_paths(json.loads(detection_path.read_text(encoding="utf-8")), root)
        (run_directory / "mock-clone-instances.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                                  encoding="utf-8")
        if (directory / DETECTION_META).is_file():
            shutil.copyfile(directory / DETECTION_META, run_directory / DETECTION_META)
        return {
            "runId": run_id,
            "restoredFrom": detection_path.relative_to(self.repository_root).as_posix(),
            "mockCloneInstances": self._indexed_instances(result),
        }

    @staticmethod
    def _rebase_paths(result: Any, root: Path) -> Any:
        """
        把检测结果里另一台机器的绝对路径改到本机的项目根目录下。
        Rewrites absolute paths recorded on another machine onto this machine's project root.

        A detection saved on Windows (D:\\...\\cloudstack\\server\\...) restored on Linux, or on a
        machine with the checkout elsewhere, otherwise finds no source files. A path is rebased
        at the outermost segment named like the local root that yields a real file, and only when the recorded path does not
        exist here and the rebased one does, so a restore on the recording machine is unchanged.
        """
        cache: dict[str, str] = {}

        def exists(path: Path) -> bool:
            try:
                return path.exists()
            except OSError:   # e.g. a code line starting with // read as a UNC path on Windows
                return False

        def rebase(text: str) -> str:
            if text in cache:
                return cache[text]
            new = text
            if (re.match(r"^(?:[A-Za-z]:[\\/]|/(?!/))", text) and "\n" not in text
                    and not exists(Path(text))):
                parts = re.split(r"[\\/]+", text)
                # The root's name can recur inside the tree (…\java\org\apache\cloudstack\…),
                # so try every occurrence, outermost first, and keep the first that exists.
                hits = [i for i, part in enumerate(parts) if part == root.name]
                candidate = next((c for i in hits if exists(c := root.joinpath(*parts[i + 1:]))), None)
                if candidate is not None:
                    new = str(candidate)
            cache[text] = new
            return new

        def walk(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            if isinstance(value, list):
                return [walk(item) for item in value]
            return rebase(value) if isinstance(value, str) else value

        return walk(result)

    @staticmethod
    def _indexed_instances(result: dict[str, Any]) -> list[dict[str, Any]]:
        instances: list[dict[str, Any]] = []
        for mocked_class, values in result.get("detectedMockClones", {}).items():
            for index, instance in enumerate(values):
                item = dict(instance)
                item["id"] = f"{mocked_class}::{index + 1}"
                instances.append(item)
        return instances

    @staticmethod
    def _read_meta(run_directory: Path) -> dict[str, Any]:
        path = run_directory / DETECTION_META
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    @staticmethod
    def _write_meta(run_directory: Path, meta: dict[str, Any]) -> None:
        (run_directory / DETECTION_META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def mock_preview(self, run_id: str, mock_id: int) -> dict[str, Any]:
        run = self._load_run(run_id)
        objects = json.loads((run.run_directory / "mock-objects.json").read_text(encoding="utf-8"))
        item = next((value for value in objects if value.get("rawMockObjectId") == mock_id), None)
        if item is None:
            raise DetectionError("Mock object not found / 未找到 Mock 对象")
        statements = item.get("statements") or []
        return {"variableName": item.get("variableName"), "mockedClass": item.get("mockedClass"),
                "filePath": self._summarize_mock(run.project_root, item)["filePath"],
                "snippets": [{"code": statement.get("locationContext", {}).get("methodRawCode") or statement.get("code", ""),
                              "methodName": statement.get("locationContext", {}).get("methodName", ""),
                              "line": statement.get("line"), "target": statement.get("code", "")}
                             for statement in statements if statement.get("isMockRelated")]}

    def mci_preview(self, run_id: str, mci_id: str) -> dict[str, Any]:
        _, raw = self.load_raw_detection(run_id)
        for mocked_class, instances in raw.get("detectedMockClones", {}).items():
            for index, instance in enumerate(instances, start=1):
                if f"{mocked_class}::{index}" != mci_id:
                    continue
                sequences = instance.get("sequences") or []
                return {"id": mci_id, "sharedStatements": instance.get("sharedStatements") or [],
                        "occurrences": [{"filePath": sequence.get("filePath"), "methodName": sequence.get("testMethodName"),
                                         "variableName": sequence.get("variableName"),
                                         "code": sequence.get("testMethodRawCode", ""),
                                         "sharedLines": list((sequence.get("shareableMockLines") or {}).values())}
                                        for sequence in sequences]}
        raise DetectionError("MCI not found / 未找到 MCI")

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
            command = [executable]
            maven_repository = os.environ.get("CLONEDEMOCKER_MAVEN_REPO", "").strip()
            if not maven_repository and os.environ.get("USERPROFILE"):
                maven_repository = str(Path(os.environ["USERPROFILE"]) / ".m2" / "repository")
            if maven_repository:
                command.append(f"-Dmaven.repo.local={maven_repository}")
            command.extend(["-DskipTests", "package"])
            self._run(command, self.detector_root)
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
    def _run(command: list[str], cwd: Path, progress_callback: Callable[[int, int, str], None] | None = None) -> str:
        process = subprocess.Popen(command, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            if progress_callback and line.startswith("[PROGRESS] SCAN "):
                # 格式是 "[PROGRESS] SCAN done/total [文件名]"。文件名是后加的，所以按可选处理，
                # 旧 jar 产出的两段式输出仍然能解析——否则换一个 jar 进度就整条断掉。
                # The format is "[PROGRESS] SCAN done/total [file]". The name was added later and
                # stays optional so an older jar's two-field output still parses; otherwise
                # swapping the jar would break progress entirely.
                try:
                    fields = line.split(maxsplit=3)
                    done, total = fields[2].split("/")
                    current_file = fields[3].strip() if len(fields) > 3 else ""
                    progress_callback(int(done), int(total), current_file)
                except (ValueError, IndexError):
                    pass
        returncode = process.wait()
        output = "".join(lines)
        if returncode != 0:
            raise DetectionError(
                f"Command failed ({returncode}) / 命令执行失败\n{output[-12000:]}"
            )
        return output
