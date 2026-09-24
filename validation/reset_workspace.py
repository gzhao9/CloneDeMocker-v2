"""手动把一个共享 workspace 还原成项目的原始内容。
Manually restores a shared workspace back to the project's original, unmodified content.

配合 run_pilot.py 的 --workspace 选项使用：调试 run_pilot.py 本身时可以反复复用同一个
workspace（跳过复制项目 + 一次性冷编译），只有在 workspace 被改坏、或者想换一个干净状态
时才手动跑这个脚本还原，而不是每次调试都自动重新复制+冷编译一遍。
Pairs with run_pilot.py's --workspace option: while debugging run_pilot.py itself, the same
workspace can be reused repeatedly (skipping the project copy + one-time cold compile); this
script is run manually only when the workspace has been left in a bad state, or a clean state
is wanted again — not automatically on every debug run.

用法 / Usage:
    uv run python validation/reset_workspace.py \\
        --project-root "C:\\Java_projects\\Apache\\dubbo" \\
        --workspace "C:\\Java_projects\\Apache\\.clonedemocker-workspaces\\<id>"
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from studio.refactoring_agent import RefactoringAgent, _long_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="被验证的真实项目路径 / real subject project path")
    parser.add_argument("--workspace", required=True, help="要还原的隔离副本目录 / the isolated copy to restore")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        raise SystemExit(f"--project-root does not exist / --project-root 不存在: {project_root}")

    workspace = Path(args.workspace)
    if workspace.is_dir():
        print(f"removing {workspace} ...")
        shutil.rmtree(_long_path(workspace))

    print(f"copying {project_root} -> {workspace} ...")
    RefactoringAgent._copy_project(project_root, workspace)
    print("done")


if __name__ == "__main__":
    main()
