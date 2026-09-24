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
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CSV_COLUMNS = ["mciId", "classification", "repairRounds", "goalAchieved", "mutationRegressed",
               "aiAuditRisk", "diffFile", "totalSeconds", "generationSeconds", "totalTokens", "cacheHit"]

# 重构结果按"由谁驱动 + 用哪个模型"分开存：data/<project>/refactoring/<配置>/。检测结果是
# 共用的，留在 data/<project>/ 根下。以后换成 Codex+某个模型、或者更贵的模型，各落各的
# 目录，互不覆盖，也能直接并排比较。
# Refactoring results are stored per "what drives it + which model":
# data/<project>/refactoring/<setup>/. Detection is shared and stays at data/<project>/. A
# later Codex+model run, or a pricier model, lands in its own directory, overwriting nothing
# and directly comparable side by side.
HARNESS_CLONEDEMOCKER = "CloneDeMocker"
MOCK_MODEL_LABEL = "MockProvider"
SETUP_DESCRIPTOR = "setup.json"

# 模型 ID -> 目录与报告里用的名字。不在表里的直接用 ID。
# Model ID -> the name used in directories and reports. IDs not listed are used as they are.
MODEL_DISPLAY_NAMES = {
    "gpt-5.6-terra": "Terra 5.6",
}


MAX_MCI_FILENAME = 125


def safe_mci_filename(mci_id: str) -> str:
    """
    Spring Security 的 MCI id 带泛型全名，最长 215 个字符，加上 data/ 下的目录就超过 Windows 的
    260 字符上限，diff 复制直接失败。超长的名字截成可读前缀加 id 的哈希；不超长的保持原样，
    已经存下的 Dubbo diff（最长 121）文件名一个都不变。
    Spring Security's MCI ids carry fully qualified generics, up to 215 characters, which with the
    directory under data/ exceeds Windows' 260-character limit and fails the diff copy. Overlong
    names become a readable prefix plus a hash of the id; shorter ones stay as they were, so no
    stored Dubbo diff (at most 121) changes name.
    """
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", mci_id) + ".diff"
    if len(name) <= MAX_MCI_FILENAME:
        return name
    digest = hashlib.sha1(mci_id.encode("utf-8")).hexdigest()[:12]
    return f"{name[:100]}-{digest}.diff"


def setup_label(harness: str, model: str, use_mock: bool = False) -> str:
    """例如 "CloneDeMocker+Terra 5.6"。调试桩的结果单独归到 MockProvider 下，不能混进真实
    模型的数据。
    E.g. "CloneDeMocker+Terra 5.6". Debug-stub results go under MockProvider on their own and
    must never mix with a real model's data."""
    model_name = MOCK_MODEL_LABEL if use_mock else MODEL_DISPLAY_NAMES.get(model, model or "unknown-model")
    return f"{harness}+{model_name}"


def setup_dirname(label: str) -> str:
    """"CloneDeMocker+Terra 5.6" -> "CloneDeMocker+Terra-5.6"：空格换成连字符，命令行里不用加引号。
    Spaces become hyphens so the path needs no quoting on a command line."""
    return re.sub(r"[^A-Za-z0-9+_.-]", "-", label.strip())


def setup_directory(repository_root: Path, project: str, label: str) -> Path:
    return repository_root / "data" / project / "refactoring" / setup_dirname(label)


def write_setup_descriptor(directory: Path, label: str, harness: str, model: str,
                           use_mock: bool = False) -> None:
    """setup.json 写明这个目录里的结果是谁、用哪个模型做的；目录名只是它的简写。
    setup.json states who produced this directory's results and with which model; the
    directory name is only its shorthand."""
    path = directory / SETUP_DESCRIPTOR
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    descriptor = {
        "label": label,
        "harness": harness,
        "model": model,
        "modelDisplayName": MOCK_MODEL_LABEL if use_mock else MODEL_DISPLAY_NAMES.get(model, model),
        "useMock": use_mock,
        "createdAt": existing.get("createdAt") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps({**existing, **descriptor}, ensure_ascii=False, indent=2), encoding="utf-8")


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
        # 审查是建议而不是判据，但结论必须落盘：上一轮 dubbo 的导出把 aiAudit 丢掉了，
        # 结果 13 个失败案例里没有一条能查到审查到底说了什么，无法复核。
        # The audit is advisory rather than a criterion, but its verdict still has to land on
        # disk: the previous dubbo export dropped aiAudit, so not one of the flagged cases
        # could be checked against what the reviewer actually said.
        "aiAuditRisk": (harness.get("aiAudit") or {}).get("risk"),
        "mutationScoreDelta": harness.get("mutationScoreDelta"),
        "proposalId": result.get("proposalId"),
        "model": result.get("model"),
        "useMock": bool(result.get("useMock")),
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
            "aiAudit": harness.get("aiAudit"),
            "aiAuditConcern": harness.get("aiAuditConcern"),
        },
    }


def merge(project: str, repository_root: Path, entries: list[dict[str, Any]],
          detection_source: Path | None = None,
          diff_lookup: dict[str, Path] | None = None,
          model: str = "", harness: str = HARNESS_CLONEDEMOCKER,
          use_mock: bool = False) -> dict[str, Any]:
    """
    把 entries 并入 data/<project>/refactoring/<harness+model>/，返回本次写入的摘要。

    entries 必须来自同一个配置（同一个 harness、同一个模型）；混着的由调用方先分组。
    detection_source 给了就刷新 data/<project>/detection.json，它旁边的 detection-meta.json
    一并带过去；diff_lookup 是 {mciId: changes.diff 路径}，每条 diff 复制成
    diffs/<mciId>.diff。两者都可选，因为一次补跑可能只带来结果、不带来新的检测数据。
    Merges entries into data/<project>/refactoring/<harness+model>/ and returns a summary of
    what this call wrote. Entries must share one setup (one harness, one model); callers group
    mixed ones first. detection_source refreshes data/<project>/detection.json, carrying its
    detection-meta.json along; diff_lookup maps mciId to a changes.diff copied to
    diffs/<mciId>.diff. Both are optional, since a top-up run may bring results without new
    detection data.
    """
    model = model or next((entry.get("model") for entry in entries if entry.get("model")), "")
    label = setup_label(harness, model, use_mock)
    project_dir = repository_root / "data" / project
    out_dir = setup_directory(repository_root, project, label)
    diffs_dir = out_dir / "diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)
    write_setup_descriptor(out_dir, label, harness, model, use_mock)

    if detection_source is not None and detection_source.is_file():
        shutil.copyfile(detection_source, project_dir / "detection.json")
        meta_source = detection_source.parent / "detection-meta.json"
        if meta_source.is_file():
            shutil.copyfile(meta_source, project_dir / "detection-meta.json")

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
        "setup": label,
        "harness": harness,
        "lastUpdated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
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
        "setup": label,
        "setupDirectory": setup_dirname(label),
        "directory": str(out_dir),
        "writtenThisCall": len(entries),
        "diffsCopied": copied,
        "totalMcis": len(merged),
        "successes": success,
        "successRate": canonical["mciSuccessRate"],
    }
