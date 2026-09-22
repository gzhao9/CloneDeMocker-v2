"""
临时脚本：在另一台机器上补跑指定的 MCI，再把结果合并回来。
Temporary script: re-run selected MCIs on another machine, then merge the results back.

起因：本机代理（Clash Verge TUN + fake-ip）会把任何主机名都"解析成功"，Spring Security 的
NimbusReactiveJwtDecoderTests.decodeWhenInvalidUrl 期望 https://s 抛 UnknownHostException，于是
baseline 就失败，用到它的 5 个 MCI 只能判为 ENVIRONMENT_NOT_READY。在没有 fake-ip 的机器上补跑即可。
Why: this host's proxy (Clash Verge TUN + fake-ip) "resolves" every host name, while Spring
Security's NimbusReactiveJwtDecoderTests.decodeWhenInvalidUrl expects https://s to raise
UnknownHostException, so the baseline fails and the 5 MCIs using it land in ENVIRONMENT_NOT_READY.
Re-running them on a machine without fake-ip settles them.

走的是与界面相同的后端（studio.server 的 start_refactoring / refactoring_export），不跑 PIT。
Uses the same backend as the UI (studio.server's start_refactoring / refactoring_export), no PIT.

── 补跑方（队友） / On the re-running machine ────────────────────────────────────────────
  前提 / Prerequisites: Python 3.11+ and uv, JDK 17+ on PATH, a JDK 25 that Gradle can find
  (Spring Security 7.1.1 requests a JDK 25 toolchain), OPENAI_API_KEY in the environment or in a
  .env next to this repository. The directory must be named spring-security-7.1.1, since saved
  detection results are looked up by directory name:

    git clone --depth 1 --branch 7.1.1 https://github.com/spring-projects/spring-security.git spring-security-7.1.1
    uv run python validation/rerun_mcis.py run /path/to/spring-security-7.1.1 \\
        --ids-file validation/spring_security_dns_mcis.txt

  完成后把 data/spring-security-7.1.1/refactoring/CloneDeMocker+Terra-5.6/ 整个目录打包发回。
  Afterwards send back the whole data/spring-security-7.1.1/refactoring/CloneDeMocker+Terra-5.6/.

── 合并方 / On the merging machine ─────────────────────────────────────────────────────
    uv run python validation/rerun_mcis.py merge <unpacked CloneDeMocker+Terra-5.6 dir> \\
        --ids-file validation/spring_security_dns_mcis.txt \\
        --project-root D:\\Java_projects\\Spring\\spring-security-7.1.1

  只合并 --ids-file 里列出的 MCI（不给就合并目录里全部），合并后重算 CCTR。
  Merges only the MCIs listed in --ids-file (all of them when omitted), then recomputes CCTR.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))


def _load_env() -> None:
    for candidate in (REPOSITORY_ROOT / ".env", REPOSITORY_ROOT.parent / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _read_ids(ids: str, ids_file: str | None) -> list[str]:
    values = [value for value in ids.split(",") if value.strip()] if ids else []
    if ids_file:
        values += [line.strip() for line in Path(ids_file).read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#")]
    return list(dict.fromkeys(value.strip() for value in values))


def _remap_paths(value: Any, old_root: str, new_root: Path) -> Any:
    """
    检测结果里的 filePath 是记录时那台机器的绝对路径。换一台机器（甚至换一个系统）就把前缀换成
    本机项目根，剩下的部分按本机分隔符重新拼。
    The detection's filePaths are absolute paths from the recording machine. On another machine
    (or OS) the prefix becomes this machine's project root and the rest is re-joined with the local
    separator.
    """
    if isinstance(value, dict):
        return {key: _remap_paths(item, old_root, new_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_paths(item, old_root, new_root) for item in value]
    if isinstance(value, str) and value.lower().startswith(old_root.lower()):
        rest = value[len(old_root):].replace("\\", "/").strip("/")
        return str(new_root.joinpath(*rest.split("/"))) if rest else str(new_root)
    return value


def _sync_git(message: str) -> None:
    try:
        subprocess.run(["git", "add", "data/"], cwd=REPOSITORY_ROOT, check=True)
        status = subprocess.run(["git", "status", "--porcelain", "data/"], cwd=REPOSITORY_ROOT, capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", message], cwd=REPOSITORY_ROOT, check=True)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPOSITORY_ROOT, check=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=REPOSITORY_ROOT, check=True)
            print(f"  [git] synced data: {message}", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"  [git] sync warning: {error}", flush=True)


def run(args: argparse.Namespace) -> int:
    _load_env()
    import studio.server as server
    from studio.canonical_store import classify_agent_result
    from studio.preflight import inspect_project

    project_root = Path(args.project_root).expanduser().resolve()
    preflight = inspect_project(project_root)
    print(f"preflight: {preflight['severity']}", flush=True)
    for finding in preflight["findings"]:
        print(f"  - {finding['code']}: {finding['message']}", flush=True)
    if preflight["severity"] == "BLOCKER":
        return 2
    if not args.use_mock and not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set / 未设置 OPENAI_API_KEY", flush=True)
        return 2

    restored = server.DETECTION.restore_from_data(str(project_root))
    run_directory = server.DETECTION.runs_root / restored["runId"]
    meta_path = run_directory / "detection-meta.json"
    recorded_root = json.loads(meta_path.read_text(encoding="utf-8")).get("projectRoot") if meta_path.is_file() else None
    if recorded_root and Path(recorded_root) != project_root:
        detection_path = run_directory / "mock-clone-instances.json"
        detection = json.loads(detection_path.read_text(encoding="utf-8"))
        detection_path.write_text(json.dumps(_remap_paths(detection, recorded_root, project_root),
                                             ensure_ascii=False), encoding="utf-8")
        print(f"remapped detection paths: {recorded_root} -> {project_root}", flush=True)

    available = [item["id"] for item in restored["mockCloneInstances"]]
    wanted = _read_ids(args.ids, args.ids_file) or available
    unknown = [value for value in wanted if value not in set(available)]
    if unknown:
        print("unknown MCI ids / 未知的 MCI：" + ", ".join(unknown), flush=True)
        return 2
    print(f"runId={restored['runId']} MCIs={len(wanted)} model={args.model}", flush=True)

    job_id = server.start_refactoring({"runId": restored["runId"], "selectedMciIds": wanted,
                                       "model": args.model, "runPit": bool(getattr(args, "run_pit", False)),
                                       "useMock": args.use_mock})["jobId"]
    reported: set[int] = set()
    last_synced_count = 0
    started = time.time()
    while True:
        job = server.refactoring_status(job_id)
        for index, item in enumerate(job["items"]):
            if index in reported or item["state"] not in {"COMPLETED", "FAILED"}:
                continue
            reported.add(index)
            result = item.get("result") or {}
            label = classify_agent_result(result) if result else f"ERROR: {item.get('error', '')[:300]}"
            print(f"[{len(reported)}/{len(wanted)} {time.time() - started:5.0f}s] {label:28} {item['mciId']}", flush=True)
            if result and label != "SUCCESS":
                print(f"    reason: {str(result.get('validationReason') or result.get('reason') or '')[:400]}", flush=True)
        if reported and len(reported) != last_synced_count:
            last_synced_count = len(reported)
            try:
                server.refactoring_export({"jobId": job_id, "runId": restored["runId"], "cctr": False})
                if last_synced_count % 3 == 0:
                    _sync_git(f"update data (batch PIT progress: {last_synced_count}/{len(wanted)})")
            except Exception as error:  # noqa: BLE001
                # 中途导出失败不中断补跑，最后一次还会再导出；但要让人看见。
                # A mid-run export failure does not stop the rerun, and the next poll exports again;
                # it still has to be visible.
                print(f"  [export] failed: {error}", flush=True)
        if job["state"] != "RUNNING":
            break
        time.sleep(10)

    summary = server.refactoring_export({"jobId": job_id, "runId": restored["runId"], "cctr": True})
    print(f"written {summary.get('writtenThisCall')} -> {summary.get('directory')}", flush=True)
    _sync_git(f"complete refactoring with PIT ({summary.get('writtenThisCall')})")
    return 0


def merge(args: argparse.Namespace) -> int:
    from studio.canonical_store import merge as merge_canonical

    source = Path(args.source).resolve()
    payload = json.loads((source / "refactoring-results.json").read_text(encoding="utf-8"))
    results: dict[str, dict[str, Any]] = payload.get("results") or {}
    wanted = _read_ids(args.ids, args.ids_file)
    missing = [value for value in wanted if value not in results]
    if missing:
        print("not in the source results / 来源结果中没有：" + ", ".join(missing), flush=True)
        return 2
    selected = {key: value for key, value in results.items() if not wanted or key in wanted}
    entries = [{**entry, "mciId": mci_id} for mci_id, entry in selected.items()]
    diff_lookup = {mci_id: source / entry["diffFile"] for mci_id, entry in selected.items() if entry.get("diffFile")}
    project = args.project or payload.get("project")
    summary = merge_canonical(project=project, repository_root=REPOSITORY_ROOT, entries=entries,
                              diff_lookup=diff_lookup, model=payload.get("model", ""),
                              use_mock=any(entry.get("useMock") for entry in entries))
    print(f"merged {summary['writtenThisCall']} into {summary['directory']} "
          f"({summary['successes']}/{summary['totalMcis']} SUCCESS)", flush=True)
    for mci_id, entry in selected.items():
        print(f"  {entry.get('classification'):28} {mci_id}", flush=True)
    if args.project_root:
        import studio.server as server
        cctr = server.run_cctr(project, Path(args.project_root), summary["setupDirectory"])
        print(f"cctr: {cctr}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="re-run selected MCIs through the backend")
    run_parser.add_argument("project_root")
    run_parser.add_argument("--ids", default="", help="comma-separated MCI ids")
    run_parser.add_argument("--ids-file", help="one MCI id per line")
    run_parser.add_argument("--model", default="gpt-5.6-terra")
    run_parser.add_argument("--use-mock", action="store_true", help="local debug provider, no API call")
    run_parser.add_argument("--run-pit", action="store_true", help="run PIT mutation testing")
    run_parser.set_defaults(handler=run)

    merge_parser = commands.add_parser("merge", help="merge a results directory sent back from another machine")
    merge_parser.add_argument("source", help="the CloneDeMocker+<model> directory that was sent back")
    merge_parser.add_argument("--ids", default="")
    merge_parser.add_argument("--ids-file")
    merge_parser.add_argument("--project", help="data/<project> name, defaults to the one recorded in the results")
    merge_parser.add_argument("--project-root", help="local checkout, to recompute CCTR after merging")
    merge_parser.set_defaults(handler=merge)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
