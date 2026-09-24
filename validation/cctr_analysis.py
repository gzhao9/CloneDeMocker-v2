"""
RQ2.2 CCTR (Cognitive Complexity for Test Readability), using the exact same
complexity formula as cctr_level_transform.py (CognitiveComplexityCalculatorTestAware),
applied to this project's own actual before/after refactor results instead of that
script's Upgrade/Downgrade simulation (that script never ran a real refactor, so it
simulates "after" by deleting the lines it thinks got absorbed into shared logic; we
have the real thing -- a model-produced diff, verified by the harness -- so "after"
here is the literal patched method body, not a simulation).

Per SUCCESS MCI, per relevant test method:
    BEFORE = testMethodRawCode straight from the detection JSON (verbatim).
    AFTER  = that same method's body located by name in the diff-applied file
             (re-parsed with tree-sitter so preceding annotations are included,
             matching the shape of testMethodRawCode).
If a diff never touches a given method (e.g. it only changed a shared @BeforeEach),
AFTER == BEFORE and the delta is legitimately 0 -- not an error.

Usage:
    uv run --with tree-sitter --with tree-sitter-java python validation/cctr_analysis.py \
        --project dubbo-3.3.6 --project-root "D:\\Java_projects\\Apache\\dubbo-3.3.6" \
        [--setup CloneDeMocker+Terra-5.6]
Reads data/<project>/detection.json and
      data/<project>/refactoring/<setup>/{refactoring-results.json,diffs/*.diff}.
Writes data/<project>/refactoring/<setup>/cctr.json and cctr.csv -- CCTR depends on that
setup's diffs, so each setup keeps its own. --setup may be omitted when the project has
exactly one setup directory.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from validation.diff_utils import split_diff_by_file, apply_unified_diff  # noqa: E402

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava

JAVA_LANGUAGE = Language(tsjava.language())
parser = Parser(JAVA_LANGUAGE)


class CognitiveComplexityCalculatorTestAware:
    def __init__(self, code, method_name=None):
        self.code = code.encode("utf-8")
        self.method_name = method_name
        self.complexity = 0
        self.nesting_level = 0

    def compute_complexity(self):
        tree = parser.parse(self.code)
        self._analyze_node(tree.root_node)
        return self.complexity

    def _analyze_node(self, node):
        for child in node.children:
            kind = child.type
            text = self.code[child.start_byte:child.end_byte].decode("utf-8")
            if kind in ("if_statement", "for_statement", "while_statement", "do_statement", "switch_statement", "catch_clause"):
                self._increment(child)
            elif kind == "binary_expression" and ("&&" in text or "||" in text):
                self.complexity += 1
            elif kind == "labeled_statement" and any(k in text for k in ("break", "continue", "goto")):
                self.complexity += 1
            elif kind == "method_invocation":
                if self.method_name and self.method_name in text:
                    self.complexity += 1
                if any(x in text for x in ("mock(", "when(", "verify(")):
                    self.complexity += 1
                if "assert" in text or "fail(" in text:
                    self.complexity += 1
            elif kind == "annotation":
                if "@Test" in text:
                    self.complexity += 1
                elif "@ParameterizedTest" in text:
                    self.complexity += 2
                elif "@BeforeEach" in text or "@AfterEach" in text:
                    self.complexity += 1
            self._analyze_node(child)

    def _increment(self, node):
        self.complexity += 1 + self.nesting_level
        self.nesting_level += 1
        self._analyze_node(node)
        self.nesting_level -= 1


def compute_cctr(code_text, method_name=None):
    return CognitiveComplexityCalculatorTestAware(code_text, method_name).compute_complexity()


def find_method_source(file_text, method_name):
    tree = parser.parse(file_text.encode("utf-8"))
    code_bytes = file_text.encode("utf-8")

    def walk(node):
        for child in node.children:
            if child.type == "method_declaration":
                name_node = child.child_by_field_name("name")
                if name_node is not None and code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8") == method_name:
                    return child
            found = walk(child)
            if found is not None:
                return found
        return None

    method_node = walk(tree.root_node)
    if method_node is None:
        return None
    parent = method_node.parent
    start = method_node.start_byte
    if parent is not None:
        siblings = parent.children
        idx = siblings.index(method_node)
        j = idx - 1
        while j >= 0 and siblings[j].type in ("annotation", "marker_annotation", "modifiers"):
            start = siblings[j].start_byte
            j -= 1
    return code_bytes[start:method_node.end_byte].decode("utf-8")


def indexed_mci_ids(raw):
    clones = raw.get("detectedMockClones", {})
    return {f"{mocked_class}::{index + 1}": instance
            for mocked_class, instances in clones.items()
            for index, instance in enumerate(instances)}


def main() -> None:
    parser_arg = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser_arg.add_argument("--project", required=True)
    parser_arg.add_argument("--project-root", required=True, help="the real source checkout, e.g. D:\\Java_projects\\Apache\\dubbo-3.3.6")
    parser_arg.add_argument("--setup", default=None,
                            help="directory under data/<project>/refactoring/, e.g. CloneDeMocker+Terra-5.6")
    args = parser_arg.parse_args()

    project_dir = REPO_ROOT / "data" / args.project
    setups_root = project_dir / "refactoring"
    if args.setup:
        data_dir = setups_root / args.setup
    else:
        candidates = sorted(path for path in setups_root.glob("*") if path.is_dir()) if setups_root.is_dir() else []
        if len(candidates) != 1:
            sys.exit(f"--setup is required; found {[path.name for path in candidates]} under {setups_root}")
        data_dir = candidates[0]
    project_root = Path(args.project_root)
    raw = json.loads((project_dir / "detection.json").read_text(encoding="utf-8"))
    mci_lookup = indexed_mci_ids(raw)
    refactoring = json.loads((data_dir / "refactoring-results.json").read_text(encoding="utf-8"))

    rows = []
    for mci_id, result in refactoring["results"].items():
        if result.get("classification") != "SUCCESS":
            continue
        diff_file = result.get("diffFile")
        if not diff_file:
            continue
        diff_path = data_dir / diff_file
        if not diff_path.is_file():
            continue
        instance = mci_lookup.get(mci_id)
        if instance is None:
            continue

        by_file = split_diff_by_file(diff_path.read_text(encoding="utf-8"))
        reconstructed = {}
        for relative, hunks in by_file.items():
            source_path = project_root / relative
            if not source_path.is_file():
                continue
            before_text = source_path.read_text(encoding="utf-8", errors="replace")
            try:
                after_text = apply_unified_diff(before_text, hunks)
            except Exception:  # noqa: BLE001
                after_text = None
            reconstructed[relative.as_posix()] = after_text

        seen = set()
        for sequence in instance.get("sequences", []):
            raw_path = Path(sequence.get("filePath", ""))
            try:
                relative = raw_path.resolve(strict=False).relative_to(project_root.resolve()).as_posix()
            except ValueError:
                continue
            method_name = sequence.get("testMethodName")
            before_raw = sequence.get("testMethodRawCode", "")
            if not method_name or not before_raw or (relative, method_name) in seen:
                continue
            seen.add((relative, method_name))
            after_text = reconstructed.get(relative)
            if after_text is None:
                continue
            after_raw = find_method_source(after_text, method_name)
            if after_raw is None:
                continue

            before_cctr = compute_cctr(before_raw, method_name)
            after_cctr = compute_cctr(after_raw, method_name)
            rows.append({
                "mciId": mci_id, "file": relative, "method": method_name,
                "beforeCCTR": before_cctr, "afterCCTR": after_cctr,
                "delta": after_cctr - before_cctr,
                "pctReduction": round((before_cctr - after_cctr) / before_cctr * 100, 1) if before_cctr else None,
            })

    out_json = data_dir / "cctr.json"
    out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    out_csv = data_dir / "cctr.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["mciId", "file", "method", "beforeCCTR", "afterCCTR", "delta", "pctReduction"])
        writer.writeheader()
        writer.writerows(rows)

    total_before = sum(r["beforeCCTR"] for r in rows)
    total_after = sum(r["afterCCTR"] for r in rows)
    print(f"{len(rows)} methods across {len({r['mciId'] for r in rows})} SUCCESS MCIs")
    print(f"Total CCTR: {total_before} -> {total_after} ({(total_after-total_before)/total_before*100:.1f}%)" if total_before else "no data")
    print(f"wrote {out_json}\nwrote {out_csv}")


if __name__ == "__main__":
    main()
