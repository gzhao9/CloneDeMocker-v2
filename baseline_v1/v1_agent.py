"""CloneDeMocker V1, replayed inside the V2 pipeline so both are judged the same way.

What is V1 here: the original paper's notebook (`REFACTORING/Refactory with LLM.ipynb`) --
its five prompts, copied verbatim into `prompts/`, its input shaping (`extract_mock_info`,
`str(dict)` payloads), its branch rules (1.1 vs 1.2 on `sharedStatements`, 2.1/2.2/2.3 on
`sharedStatementLineCount` and each sequence's `shareableMockLines`) and its outputs: one
reusable method or `@Mock` field per MCI, and one hand-written diff hunk per test method.
There is no harness: no edit-application retry and no repair loop (the pair runner calls it
with `max_retries=0`).

What had to be added, because V1 never wrote code back to files: a *mechanical* writer that
turns V1's outputs into edited files, with no model involved. It is the smallest step that
makes V1's output checkable at all:

  * each hunk is located by content, ignoring indentation (V1 hunks carry no line numbers),
    and must match exactly once -- otherwise the MCI fails, it is never guessed;
  * the reusable method (1.1) is inserted before the closing brace of each affected test
    class; V1 never said where it goes, and a helper must be visible from every call site;
  * a field-mode MCI (1.2) gets what V1's own `generate_global_mock_java_snippet` handed the
    user -- `@Mock` plus the field declaration -- and `import org.mockito.Mock;` if absent.
    V1 dropped `beforeInit`; so does this.

Everything after generation -- applying edits, the diff, baseline/candidate verification,
the audit, classification, timings -- is V2's code, unchanged.

Model calls go through V2's provider (Responses API) rather than V1's chat-completions call
with temperature 0.2, so both groups use the same model endpoint, token accounting and call
timing. V1 also sent 1.2 to gpt-4o-mini; here every call uses the one model under test.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from studio.model_provider import ModelProvider, ModelResult
from studio.refactoring_agent import RefactoringAgent

PROMPTS = Path(__file__).resolve().parent / "prompts"
P11 = "1.1 generate helper methods.md"
P12 = "1.2 generate attributes mock.md"
P21 = "2.1 Refactor attribute's MO in @before.md"
P22 = "2.2 Refactor local MO in Test cases.md"
P23 = "2.3 Modify local MO in Test cases to attribute.md"


class V1ApplyError(Exception):
    """V1's output could not be written back mechanically."""


# ---------------------------------------------------------------- V1 notebook, ported as-is

def extract_mock_info(instance: dict[str, Any]) -> dict[str, Any]:
    """Notebook cell 2, unchanged in behaviour."""
    mocked_class = instance["mockedClass"]
    abstracted = set()
    for stmt in instance.get("sharedStatements", {}):
        abstracted.add(stmt)
    test_cases_mocke_sequences = list()
    mock_or_spy: set[str] = set()
    file_set = set()
    method_or_class = ""
    test_raw_code = None
    for seq in instance.get("sequences"):
        tms_raw = [stms["code"] for stms in seq.get("rawStatementInfo", []).values() if stms['isMockRelated']]
        test_cases_mocke_sequences.append('\n'.join(tms_raw))
        if test_raw_code is None:
            test_raw_code = {raw_stmst.get("locationContext", {}).get("methodRawCode", "")
                             for raw_stmst in seq['rawStatementInfo'].values()}
        file_set.add(seq["filePath"])
        for stms in seq.get("rawStatementInfo", {}).values():
            if stms['isMockRelated']:
                if "SPY" in stms["type"]:
                    mock_or_spy.add("SPY")
                elif "MOCK" in stms["type"]:
                    mock_or_spy.add("MOCK")
        if len(mock_or_spy) != 1:
            mock_or_spy = {"Mock"}
    if len(file_set) == 1:
        method_or_class = "method level"
    elif len(file_set) > 1:
        method_or_class = "class level, This method should be wrapped in a static class. but not included the import"
    return {"scope": method_or_class, "mock or spy": mock_or_spy, "mockedClass": mocked_class,
            "Need stub statements": sorted(abstracted), "releated mock code": test_cases_mocke_sequences,
            "test raw code": test_raw_code}


def _joined_raw_code(raw_code: Any) -> str:
    if isinstance(raw_code, (set, list)):
        raw_code = sorted(raw_code, key=lambda x: 0 if "@before" in x.lower() else 1)
        raw_code = "\n".join(raw_code)
    return raw_code


def _integration_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Notebook cell 7's seq_data."""
    try:
        mock_lines = row.get("test cases mock sequences", {})
        insertion = list(mock_lines.values())[1] if len(mock_lines) > 1 else ""
    except Exception:  # noqa: BLE001 - the notebook swallowed the same errors
        insertion = ""
    return {
        "variable name": row.get("variable name", ""),
        "test raw code": _joined_raw_code(row.get("test raw code", "")),
        "shared statements": row.get("shared statements", {}),
        "test cases mock sequences": row.get("test cases mock sequences", {}),
        "reuseable method code": row.get("reuseable method code", ""),
        "recommended insertion line": insertion,
    }


def _field_payload(row: dict[str, Any], variable_name: str) -> dict[str, Any]:
    """Notebook cell 9's seq_data."""
    return {
        "old variable name": row.get("variable name", ""),
        "test raw code": _joined_raw_code(row.get("test raw code", "")),
        "test cases mock sequences": row.get("test cases mock sequences", {}),
        "new variable name": variable_name,
    }


def _load_field_reply(clean: str) -> tuple[dict[str, Any], bool]:
    """Parse a 1.2 reply, tolerating whitespace around keys. Returns (data, normalized?).

    V1's prompt 1.2 spells the key `"newFieldValueName "` (trailing space) while V1's parser
    reads `newFieldValueName`. gpt-4o-mini happened to drop the space; a model that copies the
    prompt literally fails on a typo that says nothing about refactoring. Stripping key
    whitespace is a parser fix only -- no prompt change, no retry -- and it is flagged.
    """
    data = json.loads(clean)
    stripped = {str(k).strip(): v for k, v in data.items()}
    return stripped, stripped.keys() != data.keys()


def _global_mock_snippet(clean: str) -> str:
    """Notebook cell 8: what V1 handed the user for a field-mode MCI."""
    data, _ = _load_field_reply(clean)
    field = data["field"]
    new_name = data["newFieldValueName"]
    return f"""// === Declare in class scope ===
@Mock
{field}
// === Replace local variable in test with ==={new_name}
"""


_NOT_JSON = "V1 integration reply is not the JSON the prompt asks for"


def _parse_hunk_reply(text: str) -> tuple[bool, list[str], str]:
    """Notebook cell 12's parse of an integration reply: (canRefactor, diff lines, reason)."""
    try:
        data = json.loads(text.split('```json')[-1].split('```')[0].replace('```', "").strip())
        return bool(data.get("canRefactor", False)), list(data.get("diff") or []), str(data.get("reason", ""))
    except Exception:  # noqa: BLE001
        return False, [], _NOT_JSON


# ---------------------------------------------------------------- mechanical write-back

def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _parse_hunks(diff: list[str]) -> list[list[tuple[str, str]]]:
    hunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for raw in diff:
        line = raw.rstrip("\n")
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = []
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line[:1] in (" ", "-", "+"):
            current.append((line[0], line[1:]))
        else:
            current.append((" ", line))          # context line that lost its leading space
    if current:
        hunks.append(current)
    return [h for h in hunks if any(tag != " " for tag, _ in h)]


def _squash(text: str) -> str:
    # V1 sent code to the model as str(dict), and Python's repr escapes ' as \'; the model
    # copies that form back. Compare with the escape undone, on both sides.
    return re.sub(r"\s+", "", text).replace("\\'", "'")


def _method_range(lines: list[str], method: str) -> tuple[int, int] | None:
    """Line span of the test method's declaration through its closing brace."""
    pattern = re.compile(r"\b" + re.escape(method) + r"\s*\(")
    for i, line in enumerate(lines):
        if pattern.search(line) and not line.rstrip().endswith(";") and "=" not in line.split("(")[0]:
            depth, opened = 0, False
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                opened = opened or "{" in lines[j]
                if opened and depth <= 0:
                    return i, j
            return None
    return None


def _anchor_at(lines: list[str], start: int, keys: list[str]) -> list[tuple[int, int]] | None:
    """Map each hunk line (whitespace-squashed) onto one or more consecutive file lines.

    V1's hunks are written against the detector's `methodRawCode`, where a statement the
    source wraps over several lines appears on one. So one hunk line may cover a run of file
    lines; blank file lines in between are skipped. Returns [(first, last)] per key or None.
    """
    spans: list[tuple[int, int]] = []
    i = start
    for key in keys:
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            return None
        first, acc = i, ""
        while i < len(lines):
            acc += _squash(lines[i])
            i += 1
            if acc == key:
                spans.append((first, i - 1))
                break
            if not key.startswith(acc):
                return None
        else:
            return None
    return spans


def _apply_hunk(text: str, hunk: list[tuple[str, str]], where: str, method: str | None = None) -> str:
    lines = text.split("\n")
    old = [(tag, body) for tag, body in hunk if tag in (" ", "-") and body.strip()]
    if not old:
        raise V1ApplyError(f"{where}: hunk has no context or removed lines to anchor it")
    keys = [_squash(body) for _, body in old]
    matches = [spans for start in range(len(lines)) if lines[start].strip()
               for spans in [_anchor_at(lines, start, keys)] if spans]
    if len(matches) > 1 and method:
        # Mock clones repeat across tests, so context can recur; V1 wrote each hunk for one
        # named test method, so look only inside it.
        bounds = _method_range(lines, method)
        if bounds:
            matches = [m for m in matches if bounds[0] <= m[0][0] and m[-1][1] <= bounds[1]]
    if len(matches) != 1:
        fallback = _apply_by_removed_lines(lines, hunk, method)
        if fallback is not None:
            FUZZY_USED.append(where)
            return fallback
        raise V1ApplyError(f"{where}: hunk context matches {len(matches)} places (needs exactly 1)")
    spans = matches[0]
    first, last = spans[0][0], spans[-1][1]

    # Rebuild the span: context keeps the file's own lines (wrapping included), removed lines
    # go, added lines take the indentation of the nearest anchored line plus the hunk's own
    # relative indentation. Blank file lines between anchored lines are kept.
    out: list[str] = []
    pointer = 0
    ref_file, ref_hunk = _indent(lines[first]), _indent(old[0][1])
    for tag, body in hunk:
        if tag in (" ", "-") and body.strip():
            a, b = spans[pointer]
            pointer += 1
            ref_file, ref_hunk = _indent(lines[a]), _indent(body)
            if tag == " ":
                out.extend(lines[a:b + 1])
            nxt = spans[pointer][0] if pointer < len(spans) else b + 1
            out.extend(lines[j] for j in range(b + 1, nxt) if not lines[j].strip())
        elif tag == "+":
            if not body.strip():
                out.append("")
                continue
            extra = max(0, len(_indent(body)) - len(ref_hunk))
            out.append(ref_file + " " * extra + body.strip())
    return "\n".join(lines[:first] + out + lines[last + 1:])


NL = chr(10)


def _unit(lines: list[str]) -> str:
    """The file's own indentation unit, so inserted code follows its style (tabs or spaces)."""
    tabs = sum(line.startswith("\t") for line in lines)
    spaces = sum(line.startswith("    ") for line in lines)
    return "\t" if tabs > spaces else "    "
FUZZY_USED: list[str] = []


def _apply_by_removed_lines(lines: list[str], hunk: list[tuple[str, str]], method: str | None) -> str | None:
    """Fallback when V1's context lines do not match the source (it omits or rewrites one).

    Locate the hunk by its removed lines alone -- as a developer applying V1's guide would --
    inside the named test method, requiring one contiguous, unique match; replace them with
    the hunk's added lines. A hunk with no removed lines has nothing to anchor on and fails.
    """
    removed = [_squash(body) for tag, body in hunk if tag == "-" and body.strip()]
    added = [body for tag, body in hunk if tag == "+"]
    if not removed or not method:
        return None
    bounds = _method_range(lines, method)
    if not bounds:
        return None
    found = [spans for start in range(bounds[0], bounds[1] + 1) if lines[start].strip()
             for spans in [_anchor_at(lines, start, removed)] if spans and spans[-1][1] <= bounds[1]]
    if len(found) == 1:
        spans = found[0]
    else:
        # The removed lines may be scattered through the method (V1 lists them in one hunk
        # although other statements sit between them). Each must still match exactly once.
        spans = []
        for key in removed:
            hits = [s for start in range(bounds[0], bounds[1] + 1) if lines[start].strip()
                    for s in [_anchor_at(lines, start, [key])] if s and s[0][1] <= bounds[1]]
            if len(hits) != 1:
                return None
            spans.append(hits[0][0])
        spans.sort()
        if any(a[1] >= b[0] for a, b in zip(spans, spans[1:])):
            return None
    first = spans[0][0]
    indent = _indent(lines[first])
    base = min((len(_indent(b)) for b in added if b.strip()), default=0)
    new = [(indent + " " * max(0, len(_indent(b)) - base) + b.strip()) if b.strip() else "" for b in added]
    drop = {i for a, b in spans for i in range(a, b + 1)}
    out: list[str] = []
    for i, line in enumerate(lines):
        if i == first:
            out.extend(new)
        if i not in drop:
            out.append(line)
    return NL.join(out)


def _insert_before_class_end(text: str, block: str, where: str) -> str:
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "}":
            unit = _unit(lines)
            body = [(unit + l.replace("    ", unit)) if l.strip() else "" for l in block.strip("\n").split("\n")]
            return "\n".join(lines[:i] + [""] + body + lines[i:])
    raise V1ApplyError(f"{where}: no closing brace to insert the reusable method before")


def _insert_field(text: str, block: str, where: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.search(r"\bclass\s+\w+", line) and not line.strip().startswith(("//", "*", "/*")):
            j = i
            while j < len(lines) and "{" not in lines[j]:
                j += 1
            if j == len(lines):
                break
            unit = _unit(lines)
            body = [unit + l.strip() for l in block.strip("\n").split("\n") if l.strip()]
            text = "\n".join(lines[: j + 1] + body + lines[j + 1:])
            if "import org.mockito.Mock;" not in text:
                text = re.sub(r"(?m)^(package [^\n]+;\n)", r"\1\nimport org.mockito.Mock;", text, count=1)
            return text
    raise V1ApplyError(f"{where}: no class declaration to add the @Mock field to")


# ---------------------------------------------------------------- the generator

def generate_v1(provider: ModelProvider, model: str, project_root: Path,
                instances: list[dict[str, Any]], files: dict[Path, str],
                progress: Callable[[str, int, str], None] | None = None,
                ) -> tuple[dict[str, Any], list[ModelResult], list[dict[str, Any]]]:
    """Same contract as RefactoringAgent._generate_staged: (proposal, model results, stage log)."""
    results: list[ModelResult] = []
    stage_log: list[dict[str, Any]] = []

    def call(prompt_name: str, payload: dict[str, Any], stage: str, label: str) -> str:
        prompt = (PROMPTS / prompt_name).read_text(encoding="utf-8")
        result = provider.generate(prompt, str(payload), model)      # V1 sent str(dict)
        results.append(result)
        stage_log.append({"stage": stage, "variant": prompt_name.split(" ", 1)[0], "label": label,
                          "attempt": 1, "responseId": result.response_id})
        return result.text.strip()

    def relative(path_text: str) -> Path:
        path = Path(path_text)
        if path.is_absolute():
            path = path.resolve().relative_to(project_root.resolve())
        return path

    working = dict(files)
    key_normalized = False
    FUZZY_USED.clear()

    def declined(reason: str) -> tuple[dict[str, Any], list[ModelResult], list[dict[str, Any]]]:
        return {"canRefactor": False, "reason": "V1: " + reason}, results, stage_log

    def unusable(reason: str) -> tuple[dict[str, Any], list[ModelResult], list[dict[str, Any]]]:
        # The model answered but the answer cannot be used as V1 used it (the notebook crashed
        # or gave up on these). That is V1's output failing, not the model declining.
        return {"canRefactor": True, "edits": [], "newFiles": [], "summary": "V1 output unusable",
                "v1ApplyError": reason}, results, stage_log

    for instance in instances:
        label = str(instance.get("mockedClass", ""))
        info = extract_mock_info(instance)
        if len(instance['sharedStatements']) == 0:
            payload = {"mockedClass": info["mockedClass"], "releated mock code": info["releated mock code"],
                       "test raw code": info["test raw code"]}
            reply = call(P12, payload, "ENCAPSULATION", label)
        else:
            reply = call(P11, info, "ENCAPSULATION", label)
        clean = reply.replace("```json", "").replace("```", "").strip()
        field_mode = instance.get("sharedStatementLineCount", 0) == 0
        try:
            if field_mode:
                reusable = _global_mock_snippet(clean)
                data, normalized = _load_field_reply(clean)
                key_normalized = key_normalized or normalized
                value_name = data["newFieldValueName"].replace(";", "")
            else:
                reusable = json.loads(clean)["code"]
                value_name = ""
        except Exception as exc:  # noqa: BLE001 - the notebook gave up on the MCI here too
            return unusable(f"encapsulation reply unusable ({type(exc).__name__}: {exc})")

        hunks: list[tuple[Path, list[str], str]] = []
        for index, seq in enumerate(instance.get("sequences", []), start=1):
            row = {
                "file path": seq["filePath"],
                "test method name": seq["testMethodName"],
                "variable name": seq.get("variableName", ""),
                "test raw code": {raw.get("locationContext", {}).get("methodRawCode", "")
                                  for raw in seq['rawStatementInfo'].values()},
                "shared statements": seq.get("shareableMockLines", {}),
                "test cases mock sequences": seq.get("testMockLines", {}),
                "reuseable method code": reusable,
            }
            if not field_mode:
                prompt = P22 if len(row.get("shared statements", {})) == 0 else P21
                reply = call(prompt, _integration_payload(row), "INTEGRATION", f"{label}#{index}")
            else:
                reply = call(P23, _field_payload(row, value_name), "INTEGRATION", f"{label}#{index}")
            can, diff, reason = _parse_hunk_reply(reply)
            if reason == _NOT_JSON:
                return unusable(f"integration reply for {seq['testMethodName']} is not the JSON the prompt asks for")
            if not can:
                return declined(reason or f"integration declined for {seq['testMethodName']}")
            hunks.append((relative(seq["filePath"]), diff, seq["testMethodName"]))

        try:
            targets = sorted({path for path, _, _ in hunks}, key=str)
            for path in targets:
                if path not in working:
                    raise V1ApplyError(f"{path}: not among the MCI's files")
                if field_mode:
                    field = _load_field_reply(clean)[0]["field"]
                    working[path] = _insert_field(working[path], "@Mock\n" + field, str(path))
                else:
                    working[path] = _insert_before_class_end(working[path], reusable, str(path))
            for path, diff, method in hunks:
                for hunk in _parse_hunks(diff):
                    working[path] = _apply_hunk(working[path], hunk, f"{path}#{method}", method)
        except V1ApplyError as exc:
            # Not a model refusal: V1 answered, but its answer cannot be written back.
            return {"canRefactor": True, "edits": [], "newFiles": [],
                    "summary": "V1 output could not be applied", "v1ApplyError": str(exc)}, results, stage_log

    edits = [{"path": path.as_posix(), "oldString": files[path], "newString": content}
             for path, content in working.items() if content != files.get(path)]
    return {"canRefactor": True, "edits": edits, "newFiles": [], "v1KeyNormalized": key_normalized,
            "v1FuzzyApply": list(FUZZY_USED),
            "summary": "CloneDeMocker V1 (original prompts, no harness)"}, results, stage_log


class V1RefactoringAgent(RefactoringAgent):
    """V2's pipeline with V1's generator. Verification, diffing and classification are V2's.

    `_generation` is deliberately *not* overridden: the verification ledger keys on it, and
    sharing that key is what lets V1 reuse the baseline V2 just recorded for the same MCI
    (identical source, scope and harness). V1's proposals must then stay out of V2's proposal
    cache, which the pair runner ensures with `use_cache=False`.
    """

    def _generate_staged(self, provider, model, project_root, instances, files, user_instruction="",
                         progress=None):
        proposal, results, stage_log = generate_v1(provider, model, project_root, instances, files, progress)
        self.v1_apply_error = proposal.get("v1ApplyError")
        self.v1_key_normalized = bool(proposal.get("v1KeyNormalized"))
        self.v1_fuzzy_apply = list(proposal.get("v1FuzzyApply") or [])
        return proposal, results, stage_log

    def _apply_edits(self, root, allowed, edits, new_files):
        # Surface why V1's output could not be written back, instead of V2's generic
        # "no edits were provided", so the failure reason in the dataset is the real one.
        if getattr(self, "v1_apply_error", None) and not edits and not new_files:
            return {}, ["V1 output could not be written back: " + self.v1_apply_error]
        return RefactoringAgent._apply_edits(root, allowed, edits, new_files)
