"""
给 data/ 里已经存好的重构结果补 PIT 与 AI 审计，不重新生成补丁。
Adds PIT and AI-audit evidence to refactoring results already stored under data/, without
regenerating any patch.

为什么不直接用界面/rerun_mcis.py 打开 runPit 重跑：提案缓存的键里含 runPit，打开 PIT 会让缓存
全部失效，模型重新生成补丁，data/ 里原有的补丁就被换掉了。这里改为把 data/<project>/.../diffs 里
存下的补丁原样套回源码，用产品同一套按模块裁剪的 harness 跑 baseline 与 candidate 两次 PIT。
Why not just rerun through the UI / rerun_mcis.py with runPit: the proposal-cache key includes
runPit, so turning PIT on misses the cache, the model regenerates the patch, and the patch stored
in data/ is replaced. Instead this reapplies the stored diff verbatim and runs PIT on baseline and
candidate through the product's own module-scoped harness.

    uv run python validation/pit_replay.py audit dubbo-3.3.6
    uv run python validation/pit_replay.py pit dubbo-3.3.6

原始证据落在 <setup>/pit/：每个 MCI 一份 <mci>.json（两侧完整 harness 证据，含逐个变异体的结果），
以及 PIT 生成的 mutations.xml 原文件。可断点续跑：已有 pit/<mci>.json 的 MCI 会跳过。
Raw evidence lands in <setup>/pit/: one <mci>.json per MCI (full harness evidence for both sides,
including every mutant's outcome) plus PIT's own mutations.xml files. Resumable: an MCI whose
pit/<mci>.json exists is skipped.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from studio.canonical_store import merge as merge_canonical, safe_mci_filename  # noqa: E402
from studio.detection_service import DetectionService  # noqa: E402
from studio.harness import BuildScope, ensure_pit_junit5_support, mutation_regressed  # noqa: E402
from studio.long_paths import long_path  # noqa: E402
from studio.refactoring_agent import RefactoringAgent, _workspace_root  # noqa: E402
from studio.verification_ledger import VerificationLedger  # noqa: E402
from validation.diff_utils import apply_unified_diff, split_diff_by_file  # noqa: E402

SETUP = "CloneDeMocker+Terra-5.6"
PIT_TOLERANCE = 0.05


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_env() -> None:
    for candidate in (REPOSITORY_ROOT / ".env", REPOSITORY_ROOT.parent / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"'))


class Project:
    def __init__(self, name: str) -> None:
        self.name = name
        self.data_dir = REPOSITORY_ROOT / "data" / name
        self.setup_dir = self.data_dir / "refactoring" / SETUP
        self.results_path = self.setup_dir / "refactoring-results.json"
        meta = json.loads((self.data_dir / "detection-meta.json").read_text(encoding="utf-8"))
        self.project_root = Path(meta["projectRoot"])
        self.detection = DetectionService(REPOSITORY_ROOT)
        self.agent = RefactoringAgent(self.detection)
        restored = self.detection.restore_from_data(str(self.project_root))
        self.run, self.raw = self.detection.load_raw_detection(restored["runId"])

    def results(self) -> dict[str, dict[str, Any]]:
        return json.loads(self.results_path.read_text(encoding="utf-8"))["results"]

    def save(self, fields: dict[str, dict[str, Any]]) -> None:
        # 只改这次负责的字段，其余一律取盘上的最新值——审计和 PIT 可能先后写同一份结果。
        # Only the fields this pass owns change; everything else comes from what is on disk now,
        # since the audit and PIT passes may both write the same results.
        payload = json.loads(self.results_path.read_text(encoding="utf-8"))
        current = payload["results"]
        entries = [{**current[mci_id], **changes, "mciId": mci_id} for mci_id, changes in fields.items()]
        merge_canonical(project=self.name, repository_root=REPOSITORY_ROOT, entries=entries,
                        model=payload.get("model", ""))

    def selection(self, mci_id: str) -> tuple[list[dict[str, Any]], dict[Path, str]]:
        selected = self.agent._select_instances(self.raw, [mci_id], None)
        return selected, self.agent._affected_files(self.run.project_root, selected)

    def diff_text(self, entry: dict[str, Any]) -> str:
        return (self.setup_dir / entry["diffFile"]).read_text(encoding="utf-8")


def replay(diff_text: str, files: dict[Path, str]) -> dict[Path, str]:
    """Like diff_utils.replay_replacements, but files the patch created are applied to an empty
    original instead of being dropped — otherwise a patch that adds a helper class would replay
    without it."""
    replacements: dict[Path, str] = {}
    for relative, hunks in split_diff_by_file(diff_text).items():
        replacements[relative] = apply_unified_diff(files.get(relative, ""), hunks)
    return replacements


# ── audit ─────────────────────────────────────────────────────────────────────────────────

def audit(args: argparse.Namespace) -> int:
    _load_env()
    project = Project(args.project)
    results = project.results()
    pending = {mci_id: entry for mci_id, entry in results.items()
               if entry.get("classification") == "SUCCESS" and entry.get("aiAuditRisk") is None}
    print(f"{project.name}: {len(pending)} SUCCESS entries without an AI audit", flush=True)
    provider = RefactoringAgent._openai_provider(args.api_profile)
    updated: dict[str, dict[str, Any]] = {}
    for index, (mci_id, entry) in enumerate(pending.items(), 1):
        selected, _ = project.selection(mci_id)
        harness = dict(entry.get("harness") or {})
        verdict = RefactoringAgent._audit_refactoring(
            provider, args.model, selected, project.diff_text(entry),
            harness.get("baseline") or {}, harness.get("candidate") or {}, True, False)
        model_result = verdict.pop("modelResult", None)
        # 事后补做的审计要能和运行当下做的区分开。
        # A backfilled audit must stay distinguishable from one made during the run.
        verdict.update({"backfilledAt": _now(), "model": args.model,
                        "usage": RefactoringAgent._combined_usage([model_result]) if model_result else {}})
        harness["aiAudit"] = verdict
        harness["aiAuditConcern"] = verdict.get("risk") == "HIGH"
        updated[mci_id] = {"harness": harness, "aiAuditRisk": verdict.get("risk")}
        print(f"[{index}/{len(pending)}] {verdict.get('risk'):8} {mci_id}", flush=True)
        if index % 10 == 0:
            project.save(updated)
    if updated:
        project.save(updated)
    return 0


# ── pit ───────────────────────────────────────────────────────────────────────────────────

def _clear_pit_reports(workspace: Path) -> None:
    for directory in list(long_path(workspace).rglob("pit-reports")):
        shutil.rmtree(directory, ignore_errors=True)


def _collect_pit_reports(workspace: Path, target: Path) -> list[str]:
    copied: list[str] = []
    root = long_path(workspace)
    for report in root.rglob("pit-reports/**/mutations.xml"):
        relative = report.relative_to(root)
        destination = long_path(target / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report, destination)
        copied.append(relative.as_posix())
    return copied


def pit(args: argparse.Namespace) -> int:
    project = Project(args.project)
    agent = project.agent
    results = project.results()
    pit_dir = project.setup_dir / "pit"
    pit_dir.mkdir(exist_ok=True)
    todo = [mci_id for mci_id, entry in results.items() if entry.get("classification") == "SUCCESS"]
    if args.only:
        todo = [mci_id for mci_id in todo if mci_id in set(args.only.split(","))]
    if args.limit:
        todo = todo[: args.limit]

    workspace = _workspace_root(project.run.project_root, f"pit-replay-{project.name}")
    if not workspace.exists():
        print(f"copying project -> {workspace}", flush=True)
        agent._copy_project(project.run.project_root, workspace)
    ensure_pit_junit5_support(workspace, getattr(agent.harness, "maven_repo_local", None))
    ledger = VerificationLedger(REPOSITORY_ROOT)
    generation = agent._verification_generation()
    started = time.time()
    updated: dict[str, dict[str, Any]] = {}

    for index, mci_id in enumerate(todo, 1):
        raw_name = safe_mci_filename(mci_id)[: -len(".diff")]
        raw_path = pit_dir / f"{raw_name}.json"
        if raw_path.is_file():
            continue
        entry = results[mci_id]
        mci_started = time.time()
        selected, files = project.selection(mci_id)
        target_classes, target_modules = agent._target_test_scope(project.run.project_root, selected, files)
        scope = BuildScope(modules=tuple(target_modules), test_classes=tuple(target_classes))
        stored_diff = project.diff_text(entry)
        replacements = replay(stored_diff, files)
        fingerprint = VerificationLedger.fingerprint(files)
        # 由套回后的文件重新生成 diff，必须与存下的 diff 逐字节相同：证明补丁完整地落在了与
        # 当初相同的源码上。（账本键带流水线代次，代码更新后查不中，不能拿来核对。）
        # Regenerating the diff from the replayed files must reproduce the stored diff byte for
        # byte, proving the patch landed whole on the same source as before. (Ledger keys carry
        # the pipeline generation and stop matching after a code update, so they cannot check this.)
        regenerated = "".join(line for relative, content in replacements.items()
                              for line in difflib.unified_diff(
                                  files.get(relative, "").splitlines(keepends=True),
                                  content.splitlines(keepends=True),
                                  fromfile=f"a/{relative.as_posix()}", tofile=f"b/{relative.as_posix()}"))
        replay_matches_ledger = regenerated == stored_diff

        baseline_key = VerificationLedger.key("baseline", fingerprint, scope.describe(), True, generation)
        baseline_reports = pit_dir / "baselines" / baseline_key[:16]
        recorded = ledger.read(baseline_key)
        if recorded is not None and baseline_reports.is_dir():
            baseline = recorded["evidence"]
            baseline_reused = recorded["recordedAt"]
        else:
            _clear_pit_reports(workspace)
            evidence = agent.harness.validate(workspace, True, scope=scope)
            ledger.write(baseline_key, evidence.as_dict(), "baseline")
            baseline = json.loads(json.dumps(evidence.as_dict(), default=str))
            baseline_reused = None
            _collect_pit_reports(workspace, baseline_reports)

        candidate_reports = pit_dir / "candidates" / raw_name
        workspace_files = long_path(workspace)
        pre_image = {relative: ((workspace_files / relative).read_text(encoding="utf-8")
                                if (workspace_files / relative).is_file() else None)
                     for relative in replacements}
        try:
            for relative, content in replacements.items():
                target = workspace_files / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            _clear_pit_reports(workspace)
            candidate_evidence = agent.harness.validate(workspace, True, scope=scope)
            candidate = json.loads(json.dumps(candidate_evidence.as_dict(), default=str))
            _collect_pit_reports(workspace, candidate_reports)
        finally:
            for relative, content in pre_image.items():
                target = workspace_files / relative
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_text(content, encoding="utf-8", newline="\n")

        base_score, cand_score = baseline.get("mutationScore"), candidate.get("mutationScore")
        delta = cand_score - base_score if base_score is not None and cand_score is not None else None
        pit_ran = str(baseline.get("pitStatus")) == "PASSED" and str(candidate.get("pitStatus")) == "PASSED"
        summary = {
            "pitStatus": {"baseline": str(baseline.get("pitStatus")), "candidate": str(candidate.get("pitStatus"))},
            "pitRanCleanly": pit_ran,
            "baselineMutationScore": base_score,
            "candidateMutationScore": cand_score,
            "mutationScoreDelta": delta,
            "mutationTotal": {"baseline": baseline.get("mutationTotal"), "candidate": candidate.get("mutationTotal")},
            "mutationCounts": {"baseline": baseline.get("mutationCounts"), "candidate": candidate.get("mutationCounts")},
            "mutationRegressed": bool(pit_ran and delta is not None and delta < -PIT_TOLERANCE),
            "mutationIdentityRegressed": bool(pit_ran and mutation_regressed(baseline, candidate)),
            "replayDiffIdentical": replay_matches_ledger,
            "baselineReusedFrom": baseline_reused,
            "recordedAt": _now(),
            "seconds": round(time.time() - mci_started, 1),
            "rawEvidence": f"pit/{raw_path.name}",
            "baselineReports": baseline_reports.relative_to(project.setup_dir).as_posix(),
            "candidateReports": candidate_reports.relative_to(project.setup_dir).as_posix(),
        }
        raw_path.write_text(json.dumps({"mciId": mci_id, "scope": scope.describe(), **summary,
                                        "baseline": baseline, "candidate": candidate},
                                       ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        updated[mci_id] = {"pit": summary, "mutationScoreDelta": delta,
                           "mutationRegressed": summary["mutationRegressed"] if pit_ran else None}
        project.save(updated)
        elapsed = time.time() - started
        print(f"[{index}/{len(todo)} {elapsed:6.0f}s +{summary['seconds']:5.0f}s] "
              f"pit={summary['pitStatus']['baseline']}/{summary['pitStatus']['candidate']} "
              f"delta={delta if delta is None else round(delta, 4)} regressed={summary['mutationRegressed']} "
              f"diffIdentical={replay_matches_ledger} {mci_id}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    audit_parser = commands.add_parser("audit", help="backfill AI audits for SUCCESS entries lacking one")
    audit_parser.add_argument("project")
    audit_parser.add_argument("--model", default="gpt-5.6-terra")
    audit_parser.add_argument("--api-profile", default="default")
    audit_parser.set_defaults(handler=audit)
    pit_parser = commands.add_parser("pit", help="replay stored diffs and run PIT on baseline and candidate")
    pit_parser.add_argument("project")
    pit_parser.add_argument("--only", default="")
    pit_parser.add_argument("--limit", type=int, default=0)
    pit_parser.set_defaults(handler=pit)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
