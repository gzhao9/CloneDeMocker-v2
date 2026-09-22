"""
实时监听 Druid 重构结果并即时合并与推送到 Git：
每当有一个新的 MCI 完成（无论是 SUCCESS 还是其它状态）：
1. 立即将该 MCI 的重构结果与 diff 合并进可合并的数据集 data/druid-37.0.0/refactoring/CloneDeMocker+Terra-5.6/
2. 重新计算 CloneDeMocker+Terra-5.6 下的 CCTR
3. 立即 git commit 并 git push 到 origin/main
保证远程仓库随时拥有最新完成的单个 MCI 数据。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from studio.canonical_store import merge as merge_canonical
import studio.server as server


def _sync_git(message: str) -> None:
    try:
        subprocess.run(["git", "add", "data/"], cwd=REPOSITORY_ROOT, check=True)
        status = subprocess.run(["git", "status", "--porcelain", "data/"], cwd=REPOSITORY_ROOT, capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", message], cwd=REPOSITORY_ROOT, check=True)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPOSITORY_ROOT, check=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=REPOSITORY_ROOT, check=True)
            print(f"[WATCHER-GIT] Synced & pushed: {message}", flush=True)
        else:
            print("[WATCHER-GIT] Nothing new to commit.", flush=True)
    except Exception as error:
        print(f"[WATCHER-GIT-ERROR] {error}", flush=True)


def watch() -> None:
    print("[WATCHER] Starting Druid per-MCI sync watcher...", flush=True)
    source_dir = REPOSITORY_ROOT / "data/druid-37.0.0/refactoring/CloneDeMocker+deepseek-chat"
    target_results_file = REPOSITORY_ROOT / "data/druid-37.0.0/refactoring/CloneDeMocker+Terra-5.6/refactoring-results.json"
    project_root = Path("/data/usershare/druid-37.0.0")

    last_processed_ids: set[str] = set()

    # 初始化已处理的 MCI ID 集合
    if target_results_file.is_file():
        try:
            target_data = json.loads(target_results_file.read_text(encoding="utf-8"))
            for mci_id, entry in target_data.get("results", {}).items():
                if entry.get("classification") == "SUCCESS":
                    last_processed_ids.add(mci_id)
        except Exception:
            pass

    print(f"[WATCHER] Initialized with {len(last_processed_ids)} already merged SUCCESS MCIs.", flush=True)

    while True:
        try:
            results_file = source_dir / "refactoring-results.json"
            if results_file.is_file():
                payload = json.loads(results_file.read_text(encoding="utf-8"))
                results = payload.get("results", {})
                new_mcis = [mci_id for mci_id in results.keys() if mci_id not in last_processed_ids]

                for mci_id in new_mcis:
                    entry = results[mci_id]
                    classification = entry.get("classification", "UNKNOWN")
                    print(f"[WATCHER] Detected newly completed MCI: {mci_id} ({classification})", flush=True)

                    # 1. 立即合并进 CloneDeMocker+Terra-5.6
                    diff_file = entry.get("diffFile")
                    diff_lookup = {mci_id: source_dir / diff_file} if diff_file and (source_dir / diff_file).is_file() else None

                    summary = merge_canonical(
                        project="druid-37.0.0",
                        repository_root=REPOSITORY_ROOT,
                        entries=[{**entry, "mciId": mci_id}],
                        diff_lookup=diff_lookup,
                        model="gpt-5.6-terra",
                    )
                    print(f"[WATCHER] Merged into Terra-5.6: {summary['successes']}/{summary['totalMcis']} SUCCESS", flush=True)

                    # 2. 重新计算 CCTR
                    try:
                        cctr = server.run_cctr("druid-37.0.0", project_root, "CloneDeMocker+Terra-5.6")
                        print(f"[WATCHER] CCTR updated: {cctr}", flush=True)
                    except Exception as e:
                        print(f"[WATCHER-CCTR-ERROR] {e}", flush=True)

                    # 3. 立即 Git Commit & Push
                    commit_msg = f"sync completed MCI {mci_id} ({classification}) to Druid dataset"
                    _sync_git(commit_msg)

                    last_processed_ids.add(mci_id)

            # 检查主任务是否结束（通过检查 rerun_mcis 进程）
            proc = subprocess.run(["pgrep", "-f", "rerun_mcis.py run"], capture_output=True, text=True)
            if not proc.stdout.strip():
                print("[WATCHER] Main rerun process finished. Final sync check...", flush=True)
                _sync_git("final sync of Druid refactoring results")
                break

        except Exception as err:
            print(f"[WATCHER-ERROR] {err}", flush=True)

        time.sleep(10)


if __name__ == "__main__":
    watch()
