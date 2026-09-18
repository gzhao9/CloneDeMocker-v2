from __future__ import annotations

import argparse
import json
import locale
import mimetypes
import os
import platform
import shutil
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.detection_service import DetectionError, DetectionService
from app.refactoring_agent import RefactoringAgent


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent / "web"
DETECTION = DetectionService(REPOSITORY_ROOT)
REFACTORING = RefactoringAgent(DETECTION)


def get_env_diagnostics() -> dict[str, str]:
    """诊断当前操作系统、Java、Maven 以及编码合规状态。"""
    java_home = os.environ.get("JAVA_HOME", "")
    java_exe = shutil.which("java")
    javac_exe = shutil.which("javac")
    mvn_home = os.environ.get("M2_HOME") or os.environ.get("MAVEN_HOME") or ""
    mvn_exe = shutil.which("mvn")
    return {
        "osName": os.name,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "pythonVersion": platform.python_version(),
        "pythonEncoding": sys.getdefaultencoding(),
        "fsEncoding": sys.getfilesystemencoding(),
        "preferredEncoding": locale.getpreferredencoding(False),
        "javaHome": java_home,
        "javaExecutable": java_exe or "Not in PATH",
        "javacExecutable": javac_exe or "Not in PATH",
        "mavenHome": mvn_home,
        "mavenExecutable": mvn_exe or "Not in PATH",
        "eolPolicy": "Strict LF (\\n) enforced for all source writes (Spotless & Unix compliant)",
        "workspacePolicy": "Isolated workspaces created beside target project (.clonedemocker-workspaces) to prevent Windows GBK path failures",
    }


class CloneDeMockerHandler(BaseHTTPRequestHandler):
    """本地 UI/API 入口。 / Local UI and API entry point."""

    server_version = "CloneDeMocker/2.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "version": "2.0"})
            return
        if parsed.path == "/api/env/check":
            self._json(get_env_diagnostics())
            return
        if parsed.path == "/api/tree":
            project_root = parse_qs(parsed.query).get("projectRoot", [""])[0]
            self._handle(lambda: DETECTION.source_tree(project_root))
            return
        self._static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()
        if payload is None:
            return
        if parsed.path == "/api/detection/scan":
            self._handle(lambda: DETECTION.scan(
                project_root=payload.get("projectRoot", ""),
                include_paths=payload.get("includePaths", []),
                exclude_paths=payload.get("excludePaths", []),
                package_prefixes=payload.get("packagePrefixes", []),
                resolve_dependencies=bool(payload.get("resolveDependencies", False)),
            ))
            return
        if parsed.path == "/api/detection/detect":
            self._handle(lambda: DETECTION.detect(
                run_id=payload.get("runId", ""),
                selected_mock_ids=[int(value) for value in payload.get("selectedMockIds", [])],
            ))
            return
        if parsed.path == "/api/refactoring/run":
            self._handle(lambda: REFACTORING.run(
                run_id=payload.get("runId", ""),
                selected_mci_ids=[str(value) for value in payload.get("selectedMciIds", [])],
                model=payload.get("model", "gpt-5.6-terra"),
                user_instruction=payload.get("instruction", ""),
                run_pit=bool(payload.get("runPit", False)),
                api_profile=payload.get("apiProfile", "default"),
                use_mock=bool(payload.get("useMock", False)),
                sequence_selection={
                    str(mci_id): [int(value) for value in ids]
                    for mci_id, ids in (payload.get("sequenceSelection") or {}).items()
                },
            ))
            return
        if parsed.path == "/api/refactoring/apply":
            self._handle(lambda: REFACTORING.apply(
                run_id=payload.get("runId", ""),
                proposal_id=payload.get("proposalId", ""),
            ))
            return
        if parsed.path == "/api/refactoring/discard":
            self._json({"status": "discarded", "proposalId": payload.get("proposalId", "")})
            return
        self._json({"error": "Not found / 未找到接口"}, HTTPStatus.NOT_FOUND)

    def _handle(self, operation) -> None:
        try:
            self._json(operation())
        except (DetectionError, ValueError, OSError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._json(
                {"error": f"Internal error / 内部错误: {type(error).__name__}: {error}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._json({"error": f"Invalid JSON / JSON 无效: {error}"}, HTTPStatus.BAD_REQUEST)
            return None

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[UI] {self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CloneDeMocker v2 local UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), CloneDeMockerHandler)
    print(f"CloneDeMocker UI: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
