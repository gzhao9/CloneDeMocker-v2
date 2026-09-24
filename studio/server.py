from __future__ import annotations

import argparse
import json
import locale
import mimetypes
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from studio.canonical_store import entry_from_agent_result, merge as merge_canonical
from studio.detection_service import DetectionError, DetectionService
from studio.preflight import inspect_project
from studio.refactoring_agent import RefactoringAgent


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent / "web"
DETECTION = DetectionService(REPOSITORY_ROOT)
REFACTORING = RefactoringAgent(DETECTION)
SCAN_JOBS: dict[str, dict] = {}
SCAN_JOBS_LOCK = threading.Lock()
REFACTOR_JOBS: dict[str, dict] = {}
REFACTOR_JOBS_LOCK = threading.Lock()


def start_scan(payload: dict) -> dict[str, str]:
    job_id = uuid.uuid4().hex
    with SCAN_JOBS_LOCK:
        SCAN_JOBS[job_id] = {"state": "RUNNING", "completed": 0, "total": 0, "createdAt": time.time()}

    def update(completed: int, total: int, current_file: str = "") -> None:
        with SCAN_JOBS_LOCK:
            SCAN_JOBS[job_id].update({"completed": completed, "total": total,
                                      "currentFile": current_file})

    def work() -> None:
        try:
            result = DETECTION.scan(project_root=payload.get("projectRoot", ""),
                include_paths=payload.get("includePaths", []), exclude_paths=payload.get("excludePaths", []),
                package_prefixes=payload.get("packagePrefixes", []),
                resolve_dependencies=bool(payload.get("resolveDependencies", False)), progress_callback=update)
            with SCAN_JOBS_LOCK:
                SCAN_JOBS[job_id].update({"state": "COMPLETED", "result": result})
        except Exception as error:
            with SCAN_JOBS_LOCK:
                SCAN_JOBS[job_id].update({"state": "FAILED", "error": str(error)})
    threading.Thread(target=work, daemon=True).start()
    return {"jobId": job_id}


def scan_status(job_id: str) -> dict:
    with SCAN_JOBS_LOCK:
        job = SCAN_JOBS.get(job_id)
        if job is None:
            raise DetectionError("Scan job not found / 未找到扫描任务")
        return dict(job)


def _sequence_selection(payload: dict) -> dict[str, list[int]]:
    return {
        str(mci_id): [int(value) for value in ids]
        for mci_id, ids in (payload.get("sequenceSelection") or {}).items()
    }


def start_refactoring(payload: dict) -> dict[str, str]:
    """Start a durable, pollable per-MCI queue instead of holding one HTTP request open."""
    selected = [str(value) for value in payload.get("selectedMciIds", [])]
    if not selected:
        raise DetectionError("Select at least one MCI / 请至少选择一个 MCI")
    job_id = uuid.uuid4().hex
    items = [{"mciId": mci_id, "state": "QUEUED", "phase": "QUEUED", "percent": 0, "detail": ""}
             for mci_id in selected]
    with REFACTOR_JOBS_LOCK:
        REFACTOR_JOBS[job_id] = {
            "state": "RUNNING", "completed": 0, "total": len(items), "current": 0,
            "items": items, "createdAt": time.time(),
            # 导出时按这两项决定结果落进 data/ 的哪个配置目录。
            # The export uses these two to pick the setup directory under data/.
            "model": payload.get("model", "gpt-5.6-terra"),
            "useMock": bool(payload.get("useMock", False)),
        }

    def work() -> None:
        try:
            selections = _sequence_selection(payload)
            # 整批共用一份隔离副本。每个 MCI 各复制一份的话，`_copy_project` 排除了 target/，
            # 于是每个 MCI 都要冷编译一次（Dubbo 124 个模块，一次二十几分钟）——N 个 MCI 就是
            # N 次。共享之后只有第一个 MCI 付冷编译，后面都是增量。每个 MCI 结束时会把自己动过
            # 的文件恢复原状，所以结果仍然互相独立。
            # One isolated copy shared by the whole batch. With a copy per MCI, `_copy_project`
            # excludes target/, so every MCI pays a cold compile (124 Dubbo modules, twenty-odd
            # minutes) — N MCIs, N cold compiles. Shared, only the first MCI pays it and the rest
            # are incremental. Each MCI restores the files it touched on the way out, so the
            # results stay independent of one another.
            workspace_id = f"batch-{job_id}"
            for index, mci_id in enumerate(selected):
                with REFACTOR_JOBS_LOCK:
                    item = REFACTOR_JOBS[job_id]["items"][index]
                    item.update({"state": "RUNNING", "phase": "PREPARING", "percent": 1})
                    REFACTOR_JOBS[job_id]["current"] = index

                def update(phase: str, percent: int, detail: str, item_index: int = index) -> None:
                    with REFACTOR_JOBS_LOCK:
                        REFACTOR_JOBS[job_id]["items"][item_index].update(
                            {"phase": phase, "percent": percent, "detail": detail}
                        )

                try:
                    result = REFACTORING.run(
                        run_id=payload.get("runId", ""), selected_mci_ids=[mci_id],
                        model=payload.get("model", "gpt-5.6-terra"),
                        user_instruction=payload.get("instruction", ""),
                        run_pit=bool(payload.get("runPit", False)),
                        api_profile=payload.get("apiProfile", "default"),
                        use_mock=bool(payload.get("useMock", False)),
                        max_retries=max(0, min(5, int(payload.get("maxRetries", 2)))),
                        sequence_selection={mci_id: selections[mci_id]} if mci_id in selections else None,
                        use_cache=bool(payload.get("useCache", True)), progress_callback=update,
                        workspace_id=workspace_id,
                    )
                    # baseline 不再跨 MCI 复用：裁剪之后它是按这个 MCI 涉及的模块跑的，换一个
                    # 模块的 MCI 就用不了了。共享 workspace 让重跑它变成增量编译，本来也不贵。
                    # The baseline is no longer reused across MCIs: once scoped, it covers this
                    # MCI's modules, so an MCI in a different module cannot use it. The shared
                    # workspace makes re-running it an incremental compile anyway.
                    item_state = "COMPLETED" if str(result.get("stage")) == "COMPLETED" else "FAILED"
                    with REFACTOR_JOBS_LOCK:
                        REFACTOR_JOBS[job_id]["items"][index].update(
                            {"state": item_state, "phase": item_state, "percent": 100, "result": result}
                        )
                except Exception as error:
                    with REFACTOR_JOBS_LOCK:
                        REFACTOR_JOBS[job_id]["items"][index].update(
                            {"state": "FAILED", "phase": "FAILED", "percent": 100, "error": str(error)}
                        )
                with REFACTOR_JOBS_LOCK:
                    REFACTOR_JOBS[job_id]["completed"] = index + 1
                # 每完成一个 MCI 就增量写入 data/。长批次可能运行数小时，不能把所有结果都
                # 押在最后一次手动点击上；同一个 mciId 的重复导出是覆盖式、可安全重试的。
                # Persist after every MCI. A long batch can run for hours, so durable data must
                # not depend on one final manual click. Re-exporting the same mciId is an
                # idempotent replacement and is safe to retry.
                try:
                    export_summary = refactoring_export({
                        "jobId": job_id, "runId": payload.get("runId", ""), "cctr": False,
                    })
                    with REFACTOR_JOBS_LOCK:
                        REFACTOR_JOBS[job_id]["export"] = export_summary
                        REFACTOR_JOBS[job_id].pop("exportError", None)
                except Exception as export_error:
                    with REFACTOR_JOBS_LOCK:
                        REFACTOR_JOBS[job_id]["exportError"] = str(export_error)
            # 批次结束后再算一次 CCTR；逐项保存时跳过它，避免每个 MCI 都重复分析整份数据。
            # Compute CCTR once at the end; per-item saves skip it to avoid re-analyzing the
            # full dataset after every MCI.
            try:
                export_summary = refactoring_export({
                    "jobId": job_id, "runId": payload.get("runId", ""), "cctr": True,
                })
                with REFACTOR_JOBS_LOCK:
                    REFACTOR_JOBS[job_id]["export"] = export_summary
                    REFACTOR_JOBS[job_id].pop("exportError", None)
            except Exception as export_error:
                with REFACTOR_JOBS_LOCK:
                    REFACTOR_JOBS[job_id]["exportError"] = str(export_error)
            with REFACTOR_JOBS_LOCK:
                REFACTOR_JOBS[job_id]["state"] = "COMPLETED"
        except Exception as error:
            with REFACTOR_JOBS_LOCK:
                REFACTOR_JOBS[job_id].update({"state": "FAILED", "error": str(error)})
    threading.Thread(target=work, daemon=True).start()
    return {"jobId": job_id}


def refactoring_status(job_id: str, summary: bool = False) -> dict:
    """
    summary 模式下不带每一项的完整 result。

    界面每 800ms 轮询一次，而每个已完成项的 result 里装着 diff、前后两份 harness 证据和
    stageLog。实测 99 个 MCI 的批次刚跑到第 3 个，单次响应就已经 493 KB——约 37 MB/分钟，
    而进度显示只用得上状态和百分比。完整结果在作业结束时单取一次就够了。
    Omits each item's full result in summary mode. The UI polls every 800ms while every
    completed item's result carries a diff, both harness evidence sets and the stage log.
    Measured on a 99-MCI batch only three items in, one response was already 493 KB — roughly
    37 MB per minute — when the progress display needs nothing but state and percentage. The
    full results are worth one fetch, once the job is done.
    """
    with REFACTOR_JOBS_LOCK:
        job = REFACTOR_JOBS.get(job_id)
        if job is None:
            raise DetectionError("Refactoring job not found / 未找到重构任务")
        job = dict(job)
        if summary:
            job["items"] = [{key: value for key, value in item.items() if key != "result"}
                            for item in job.get("items", [])]
            job["summary"] = True
        return job


def run_cctr(project: str, project_root: Path, setup: str) -> dict:
    """
    在写完 data/ 之后算一次 CCTR（论文 RQ2.2 的可读性指标）。

    走子进程而不是 import：validation/ 是研究侧工具，studio/ 是产品侧，让产品反过来依赖
    研究脚本会把这条边界倒过来，也会破坏"每个 validation 脚本都能独立命令行运行"这条约束。
    子进程还顺带把 tree-sitter 的任何解析意外关在外面——CCTR 失败不该让已经写好的数据显得
    像是没写成。
    Computes CCTR (the paper's RQ2.2 readability metric) after data/ has been written.

    A subprocess rather than an import: validation/ is research tooling and studio/ is the
    product, so importing one from the other inverts that boundary and breaks the rule that
    every validation script stays runnable on its own. It also contains any tree-sitter parsing
    mishap — a CCTR failure should not make data that was written successfully look as if it
    was not.
    """
    script = REPOSITORY_ROOT / "validation" / "cctr_analysis.py"
    if not script.is_file():
        return {"ran": False, "reason": "cctr_analysis.py not found"}
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--project", project, "--project-root", str(project_root),
             "--setup", setup],
            cwd=str(REPOSITORY_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ran": False, "reason": f"{type(error).__name__}: {error}"}
    if completed.returncode != 0:
        return {"ran": False, "reason": (completed.stderr or completed.stdout or "")[-600:]}
    rows = 0
    cctr_path = REPOSITORY_ROOT / "data" / project / "refactoring" / setup / "cctr.json"
    if cctr_path.is_file():
        try:
            rows = len(json.loads(cctr_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            rows = 0
    return {"ran": True, "methods": rows}


def refactoring_export(payload: dict) -> dict:
    """
    把一个已完成作业的结果并入 data/<project>/。

    在此之前，界面跑出来的结果只活在这个进程的内存里：服务一重启就全没了，而一批 99 个
    MCI 要跑几个小时，期间任何一次重启都意味着从头再来。落盘之后，多次运行会按 mciId 拼
    成同一份数据集——断在第 60 个，补跑剩下的，两次结果自动合并。
    Merges a finished job results into data/<project>/. Until now UI results lived only in this
    process memory: a restart lost all of them, and a 99-MCI batch runs for hours during which
    any restart meant starting over. On disk, runs splice by mciId into one dataset — stop at
    the 60th, run the rest later, and the two merge.
    """
    job_id = str(payload.get("jobId") or "")
    with REFACTOR_JOBS_LOCK:
        job = REFACTOR_JOBS.get(job_id)
        if job is None:
            raise DetectionError("Refactoring job not found / 未找到重构任务")
        items = [dict(item) for item in job.get("items", [])]
        job_model = str(job.get("model") or payload.get("model") or "")
        job_mock = bool(job.get("useMock"))

    run_id = str(payload.get("runId") or "")
    run = DETECTION.load_run(run_id)
    project = str(payload.get("project") or "").strip() or run.project_root.name

    # 目录按请求时选的模型定，而不是 API 回传的名字：后者可能是带日期的快照名，同一个配置
    # 会因此被拆进好几个目录。回传的名字仍然留在每条记录的 model 里。分组是防御性的，保证
    # 调试桩和真实模型的结果不会写进同一个目录。
    # The directory follows the model requested, not the name the API echoes back: that may be
    # a dated snapshot, which would split one setup across several directories. The echoed
    # name stays in each entry's model field. Grouping is defensive, keeping debug-stub and
    # real-model results out of one directory.
    groups: dict[tuple[str, bool], tuple[list[dict], dict[str, Path]]] = {}
    for item in items:
        result = item.get("result")
        if not result:
            continue
        mci_id = item["mciId"]
        key = (job_model or str(result.get("model") or ""), bool(result.get("useMock", job_mock)))
        entries, diff_lookup = groups.setdefault(key, ([], {}))
        entries.append({**entry_from_agent_result(mci_id, result), "useMock": key[1]})
        proposal_id = result.get("proposalId")
        if proposal_id:
            candidate = run.run_directory / "refactoring" / proposal_id / "changes.diff"
            if candidate.is_file():
                diff_lookup[mci_id] = candidate
    if not groups:
        raise DetectionError("This job produced no results to export / 该任务没有可导出的结果")

    summaries = [
        merge_canonical(
            project=project,
            repository_root=REPOSITORY_ROOT,
            entries=entries,
            detection_source=run.run_directory / "mock-clone-instances.json",
            diff_lookup=diff_lookup,
            model=model,
            use_mock=use_mock,
        )
        for (model, use_mock), (entries, diff_lookup) in groups.items()
    ]
    summary = dict(summaries[0])
    if len(summaries) > 1:
        summary["writtenThisCall"] = sum(item["writtenThisCall"] for item in summaries)
        summary["setup"] = ", ".join(item["setup"] for item in summaries)
        summary["setups"] = summaries
    # CCTR 是从刚写好的 diff 和检测数据里算出来的，所以必须在合并之后跑。它失败不影响
    # 这次导出——数据已经落盘，CCTR 随时可以单独补算。
    # CCTR is derived from the diffs and detection data just written, so it runs after the
    # merge. Its failure does not undo the export: the data is on disk and CCTR can be
    # recomputed on its own at any time.
    if payload.get("cctr", True):
        cctr_runs = [run_cctr(project, run.project_root, item["setupDirectory"]) for item in summaries]
        summary["cctr"] = cctr_runs[0] if len(cctr_runs) == 1 else {
            "ran": all(item.get("ran") for item in cctr_runs),
            "methods": sum(item.get("methods", 0) for item in cctr_runs),
            "reason": "; ".join(item.get("reason", "") for item in cctr_runs if item.get("reason")),
        }
    else:
        summary["cctr"] = {"ran": False, "reason": "skipped"}
    return summary


def refactoring_cache_clear(payload: dict) -> dict:
    """清除所选 MCI 的缓存条目，供界面上"清除缓存重新生成"使用。
    Clears the selected MCIs' cache entries, behind the UI's "clear and regenerate"."""
    return REFACTORING.clear_cache(
        run_id=payload.get("runId", ""),
        selected_mci_ids=[str(value) for value in payload.get("selectedMciIds", [])],
        user_instruction=payload.get("instruction", ""),
        run_pit=bool(payload.get("runPit", False)),
        sequence_selection=_sequence_selection(payload),
    )


def refactoring_cache_status(payload: dict) -> dict:
    selected = [str(value) for value in payload.get("selectedMciIds", [])]
    selections = _sequence_selection(payload)
    matches = REFACTORING.cache_status_many(
        run_id=payload.get("runId", ""), selected_mci_ids=selected,
        user_instruction=payload.get("instruction", ""), run_pit=bool(payload.get("runPit", False)),
        sequence_selection=selections,
    )
    available = [entry for entry in matches if entry.get("available") and entry.get("validated")]
    return {"available": bool(available), "availableCount": len(available), "total": len(matches),
            "matches": matches, "validated": bool(available),
            "changedFiles": sorted({path for entry in available for path in entry.get("changedFiles", [])})}


def pick_directory() -> dict[str, str]:
    """弹出本机文件夹选择对话框，返回所选绝对路径。 / Opens a native folder picker dialog and returns the chosen absolute path."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as error:
        raise DetectionError(
            "Native folder picker unavailable (tkinter not installed) / "
            "本机文件夹选择器不可用（未安装 tkinter），请手动输入路径"
        ) from error

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title="Select Java Project Root / 选择 Java 项目根目录")
    finally:
        root.destroy()
    return {"path": selected or ""}


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
        if parsed.path == "/api/preflight":
            query = parse_qs(parsed.query)
            project_root = query.get("projectRoot", [""])[0]
            # 静态那一级永远跑；构建探针要起 Maven 进程，前端可以先只要静态结果。
            # The static tier always runs; the build probe spawns Maven, so the
            # frontend can ask for the static result alone first.
            run_probe = query.get("buildProbe", ["1"])[0] not in {"0", "false"}
            self._handle(lambda: inspect_project(project_root, run_build_probe=run_probe))
            return
        if parsed.path == "/api/pick-directory":
            self._handle(pick_directory)
            return
        if parsed.path == "/api/detection/cached":
            project_root = parse_qs(parsed.query).get("projectRoot", [""])[0]
            self._handle(lambda: DETECTION.cached_detection(project_root))
            return
        if parsed.path == "/api/detection/scan-status":
            self._handle(lambda: scan_status(parse_qs(parsed.query).get("jobId", [""])[0]))
            return
        if parsed.path == "/api/refactoring/status":
            query = parse_qs(parsed.query)
            self._handle(lambda: refactoring_status(
                query.get("jobId", [""])[0], summary=query.get("summary", [""])[0] == "1"))
            return
        if parsed.path == "/api/detection/mock-preview":
            query = parse_qs(parsed.query)
            self._handle(lambda: DETECTION.mock_preview(query.get("runId", [""])[0], int(query.get("id", ["-1"])[0])))
            return
        if parsed.path == "/api/detection/mci-preview":
            query = parse_qs(parsed.query)
            self._handle(lambda: DETECTION.mci_preview(query.get("runId", [""])[0], query.get("mciId", [""])[0]))
            return
        self._static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()
        if payload is None:
            return
        if parsed.path == "/api/detection/scan":
            self._handle(lambda: start_scan(payload))
            return
        if parsed.path == "/api/detection/detect":
            self._handle(lambda: DETECTION.detect(
                run_id=payload.get("runId", ""),
                selected_mock_ids=[int(value) for value in payload.get("selectedMockIds", [])],
            ))
            return
        if parsed.path == "/api/detection/load-cached":
            self._handle(lambda: DETECTION.restore_from_data(payload.get("projectRoot", "")))
            return
        if parsed.path == "/api/refactoring/run":
            self._handle(lambda: start_refactoring(payload))
            return
        if parsed.path == "/api/refactoring/cache-status":
            self._handle(lambda: refactoring_cache_status(payload))
            return
        if parsed.path == "/api/refactoring/cache-clear":
            self._handle(lambda: refactoring_cache_clear(payload))
            return
        if parsed.path == "/api/refactoring/export":
            self._handle(lambda: refactoring_export(payload))
            return
        if parsed.path == "/api/refactoring/apply":
            self._handle(lambda: REFACTORING.apply(
                run_id=payload.get("runId", ""),
                proposal_id=payload.get("proposalId", ""),
                force=bool(payload.get("force", False)),
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
        # 不带缓存头时浏览器会按启发式规则自行缓存 app.js。开发中这会造成一种很难看出
        # 原因的故障：缓存的旧 JS 配上新的 index.html，旧脚本在顶层访问一个已经被删掉的
        # 元素、抛出 TypeError，它后面所有的事件监听器就都没注册上——界面看着正常，按钮
        # 点下去毫无反应，控制台之外没有任何提示。这个工具是本地开发服务器，没有任何理由
        # 缓存它的静态文件。
        # Without cache headers a browser heuristically caches app.js on its own. In
        # development that produces a failure whose cause is hard to see: cached old JS
        # against a new index.html, the old script touches an element that no longer exists
        # at top level, throws a TypeError, and every event listener below it never
        # registers — the page looks fine and buttons do nothing, with no sign of it outside
        # the console. This is a local development server; there is no reason to cache it.
        self.send_header("Cache-Control", "no-store, must-revalidate")
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
