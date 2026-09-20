"""
把一次运行的结果并入 data/<project>/ 这份可长期累积的数据集。

关键语义是**合并而不是覆盖**：按 mciId 逐条写入，这次没跑到的 MCI 原样保留。一批 99 个
MCI 的实验不必一口气跑完——断在第 60 个，补跑剩下 39 个，两次的结果会拼成完整的一份。
这正是断点之后不必从头再来的前提。

之前这套逻辑只存在于 validation/export_canonical.py，且只吃 run_pilot.py 的报告格式。
界面跑出来的结果只活在服务进程的内存里，服务一重启就没了。放在这里是为了让两条路径共用
同一份合并规则，不会各写一套而悄悄分叉。

Merges one run's results into the long-lived dataset under data/<project>/.

The essential semantic is **merge, not overwrite**: entries are written per mciId and any MCI
this run did not touch is kept as it was. A 99-MCI experiment therefore need not complete in
one sitting: stop at the 60th, run the remaining 39 later, and the two runs splice into one
dataset. That is what makes resuming after an interruption possible at all.

This logic previously lived only in validation/export_canonical.py and understood only
run_pilot.py report format, while results produced through the UI existed solely in the server
process memory and vanished with a restart. It lives here so both paths share one set of merge
rules rather than growing two that quietly diverge.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CSV_COLUMNS = ["mciId", "classification", "repairRounds", "goalAchieved", "mutationRegressed",
               "diffFile", "totalSeconds", "generationSeconds", "totalTokens", "cacheHit"]


def safe_mci_filename(mci_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", mci_id) + ".diff"


def classify_agent_result(result: dict[str, Any]) -> str:
    """
    把 agent 自己的判定结果映射成论文使用的分类标签。

    这里不重新比较一遍 harness 证据——那套门禁 agent 已经跑过了（deterministic_verified
    里包含编译、测试身份、PIT 与目标达成四项）。重算一遍只会制造出第二套可以与之不一致的
    判断。这里只做「裁决 -> 标签」的翻译。
    Maps the agent own verdict onto the labels the paper uses. It deliberately does not
    re-compare the harness evidence: the agent already ran those gates (compilation, test
    identity, PIT and goal achievement all feed deterministic_verified), and recomputing them
    would only create a second verdict free to disagree with the first. This is a translation
    from verdict to label, nothing more.
    """
    harness = result.get("harness") or {}
    if str(result.get("stage")) != "COMPLETED":
        return "ENVIRONMENT_NOT_READY" if harness.get("baselineBroken") else "MODEL_DECLINED"
    candidate = harness.get("candidate") or {}
    if str(candidate.get("compileStatus")) != "PASSED":
        return "FAILED_SYNTACTIC_VALIDITY"
    if str(candidate.get("testStatus")) != "PASSED":
        return "FAILED_BEHAVIORAL_EQUIVALENCE"
    if harness.get("mutationRegressed"):
        return "FAILED_FUNCTIONAL_INTEGRITY"
    if not harness.get("goalAchieved", True):
        return "FAILED_REFACTORING_GOAL"
    return "SUCCESS" if harness.get("equivalent") else "FAILED_BEHAVIORAL_EQUIVALENCE"


def entry_from_agent_result(mci_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """把界面跑出来的一条结果压成 data/ 里存的记录，保留论文要用的数字。
    Condenses one UI-produced result into the record stored under data/, keeping the figures
    the paper needs."""
    harness = result.get("harness") or {}
    timings = result.get("timings") or {}
    usage = result.get("usage") or {}
    return {
        "mciId": mci_id,
        "classification": classify_agent_result(result),
        "repairRounds": result.get("repairAttemptsUsed", 0),
        "goalAchieved": harness.get("goalAchieved"),
        "mutationRegressed": harness.get("mutationRegressed"),
        "mutationScoreDelta": harness.get("mutationScoreDelta"),
        "proposalId": result.get("proposalId"),
        "model": result.get("model"),
        "modelCalls": result.get("modelCalls"),
        "usage": usage,
        "totalTokens": usage.get("total_tokens", 0),
        "timings": timings,
        "totalSeconds": timings.get("total"),
        "generationSeconds": timings.get("generation"),
        "verificationReused": result.get("verificationReused") or {},
        "scope": (harness.get("candidate") or {}).get("scope"),
        "cacheHit": bool((result.get("cache") or {}).get("hit")),
        "validationReason": result.get("validationReason", ""),
        "changedFiles": result.get("changedFiles", []),
        "harness": {
            "baseline": harness.get("baseline"),
            "candidate": harness.get("candidate"),
            "equivalent": harness.get("equivalent"),
        },
    }


def merge(project: str, repository_root: Path, entries: list[dict[str, Any]],
          detection_source: Path | None = None,
          diff_lookup: dict[str, Path] | None = None,
          model: str = "") -> dict[str, Any]:
    """
    把 entries 并入 data/<project>/，返回本次写入的摘要。

    detection_source 给了就刷新 detection.json；diff_lookup 是 {mciId: changes.diff 路径}，
    每条 diff 复制成 diffs/<mciId>.diff。两者都可选，因为一次补跑可能只带来结果、不带来
    新的检测数据。
    Merges entries into data/<project>/ and returns a summary of what this call wrote.
    detection_source refreshes detection.json when given; diff_lookup maps mciId to a
    changes.diff copied to diffs/<mciId>.diff. Both are optional, since a top-up run may bring
    results without new detection data.
    """
    out_dir = repository_root / "data" / project
    diffs_dir = out_dir / "diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)

    if detection_source is not None and detection_source.is_file():
        shutil.copyfile(detection_source, out_dir / "detection.json")

    results_path = out_dir / "refactoring-results.json"
    existing: dict[str, Any] = {}
    if results_path.is_file():
        try:
            existing = json.loads(results_path.read_text(encoding="utf-8")).get("results", {})
        except (OSError, json.JSONDecodeError):
            existing = {}

    merged = dict(existing)
    copied = 0
    for entry in entries:
        mci_id = entry["mciId"]
        source = (diff_lookup or {}).get(mci_id)
        if source is not None and source.is_file():
            shutil.copyfile(source, diffs_dir / safe_mci_filename(mci_id))
            entry = {**entry, "diffFile": f"diffs/{safe_mci_filename(mci_id)}"}
            copied += 1
        elif mci_id in existing and existing[mci_id].get("diffFile"):
            # 这次没带 diff，但上一次存过——保留指针，别把已有的数据抹掉。
            # No diff this time but one was stored before: keep the pointer rather than erasing
            # data that is already there.
            entry = {**entry, "diffFile": existing[mci_id]["diffFile"]}
        merged[mci_id] = entry

    success = sum(1 for value in merged.values() if value.get("classification") == "SUCCESS")
    canonical = {
        "project": project,
        "lastUpdated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model or next((entry.get("model") for entry in entries if entry.get("model")), ""),
        "totalMcis": len(merged),
        "mciSuccessRate": success / len(merged) if merged else None,
        "results": merged,
    }
    results_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out_dir / "refactoring-results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for mci_id, entry in merged.items():
            writer.writerow({**entry, "mciId": mci_id})

    return {
        "project": project,
        "directory": str(out_dir),
        "writtenThisCall": len(entries),
        "diffsCopied": copied,
        "totalMcis": len(merged),
        "successes": success,
        "successRate": canonical["mciSuccessRate"],
    }
