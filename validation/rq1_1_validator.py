"""
RQ1.1 Specification Validation: adapted from the legacy repo's
`DATA/Detected MCI/RQ1.1  Detection Accuracy.py`, same Precision/Recall/F1 logic
(does every detected clone's members actually share its coreStubSet; did clustering
omit any sequence that should have joined a clone), pointed at this project's own
detection JSON instead of the legacy per-project files.

The legacy script's mos_by_group key was (mo["rawMockObjectId"], packageName) despite
its own comment saying "group by mockedClass+package" -- rawMockObjectId is unique per
object, so that key never matches the (mockedClass, packageName) key used everywhere
else in the same script, remaining_ids is always empty, and Recall is structurally
pinned at 1.0 for any input (verified: 0/68 group keys match even on the paper's own
dubbo.json). This version uses mo["mockedClass"] instead, matching the comment's
stated intent and the paper's own textual definition of Recall.

Usage:
    uv run python validation/rq1_1_validator.py --project dubbo \
        --detection data/dubbo/detection.json
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def flat_clones(raw):
    clones = raw.get("detectedMockClones", raw)
    if isinstance(clones, list):
        return clones
    result = []
    for mocked_class, instances in clones.items():
        for instance in instances:
            instance = dict(instance)
            instance.setdefault("mockedClass", mocked_class)
            result.append(instance)
    return result


def tc_key(seq):
    file_path = seq.get("filePath", "")
    method = seq.get("testStandard", seq.get("testMethodName", ""))
    if isinstance(method, list):
        method = "|".join(method)
    return f"{file_path}:{method}"


def validate(data: dict) -> dict:
    clones = flat_clones(data)
    all_mos = {mo["rawMockObjectId"]: mo for mo in data.get("detectedMockObjects", [])}.values()

    mos_by_group = collections.defaultdict(set)
    stmt_by_mo: dict[int, list[str]] = {}
    for mo in all_mos:
        key = (mo["mockedClass"], mo["classContext"]["packageName"])
        mos_by_group[key].add(mo["rawMockObjectId"])
        stmt_by_mo[mo["rawMockObjectId"]] = [
            s.get("abstractedStatement", "")
            for s in mo.get("statements", [])
            if s.get("isMockRelated") and s.get("abstractedStatement")
        ]

    inst_by_group = collections.defaultdict(list)
    for inst in clones:
        key = (inst["mockedClass"], inst["packageName"])
        inst_by_group[key].append(inst)

    sum_tp = sum_fp = sum_fn = 0
    for key, insts in inst_by_group.items():
        remaining_ids = set(mos_by_group.get(key, []))
        tp = fp = 0
        for inst in insts:
            shared = inst.get("sharedStatements", [])
            seqs = inst.get("sequences", inst.get("mockedSequences", []))
            if not shared:
                seen_tc, fp_mo = set(), set()
                for seq in seqs:
                    mid = seq["mockObjectId"]
                    remaining_ids.discard(mid)
                    tck = tc_key(seq)
                    if tck in seen_tc:
                        fp_mo.add(mid)
                    else:
                        seen_tc.add(tck)
                        tp += 1
                fp += len(fp_mo)
            else:
                checked_mo = set()
                for seq in seqs:
                    mid = seq["mockObjectId"]
                    if mid in checked_mo:
                        continue
                    checked_mo.add(mid)
                    remaining_ids.discard(mid)
                    seqvals = seq.get("abstractedStatement", {}).values()
                    if all(stmt in seqvals for stmt in shared):
                        tp += 1
                    else:
                        fp += 1

        abs_counter: collections.Counter = collections.Counter()
        for mid in remaining_ids:
            abs_counter.update(stmt_by_mo.get(mid, []))
        fn_ids = {mid for mid in remaining_ids
                  if any(abs_counter[ab] >= 2 for ab in stmt_by_mo.get(mid, []))}
        for inst in insts:
            shared_set = set(inst.get("sharedStatements", []))
            if not shared_set:
                continue
            for mid in remaining_ids:
                if shared_set.issubset(set(stmt_by_mo.get(mid, []))):
                    fn_ids.add(mid)

        sum_tp += tp
        sum_fp += fp
        sum_fn += len(fn_ids)

    precision = sum_tp / (sum_tp + sum_fp) if (sum_tp + sum_fp) else 0.0
    recall = sum_tp / (sum_tp + sum_fn) if (sum_tp + sum_fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "cloneInstances": len(clones), "truePositives": sum_tp,
        "falsePositives": sum_fp, "falseNegatives": sum_fn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--detection", required=True, help="path to data/<project>/detection.json")
    args = parser.parse_args()

    data = json.loads(Path(args.detection).read_text(encoding="utf-8"))
    result = validate(data)

    out_path = REPO_ROOT / "data" / args.project / "rq1_1-specification-validation.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
