## Output protocol

Return JSON only. No markdown fences, no prose outside the JSON.

```json
{
  "canRefactor": true,
  "reason": "why, mandatory when canRefactor is false",
  "summary": "one line describing what changed",
  "caveat": "any reservation you still hold, or an empty string",
  "edits": [
    {"path": "relative/Existing.java",
     "oldString": "exact text copied from the current file",
     "newString": "replacement text",
     "replaceAll": false}
  ],
  "newFiles": [
    {"path": "relative/New.java", "content": "complete new file"}
  ]
}
```

### Edit rules

1. `oldString` must be **copied byte-for-byte** from the file's current text —
   same indentation, same spacing, same line breaks. Do not reformat it, do not
   rewrap long method chains, do not normalise whitespace. The target project may
   enforce its own formatting, so an unrelated reflow can fail the build.
2. `oldString` must match **exactly one** location in that file. If the text you
   want to change also appears elsewhere, either extend `oldString` with enough
   surrounding context (the enclosing `@Test` annotation and method signature are
   usually enough) to make it unique, or set `replaceAll: true` to change every
   occurrence. Use `replaceAll` when the goal is turning N copies of one shared
   statement into N calls to the same extracted helper.
3. When the input carries a `lineOccurrences` map, it tells you how many times
   each line of interest appears in the **whole file**, not just in the method you
   were shown. Any line with a count above 1 needs the extra context from rule 2.
4. Delete what you replace. When a `mock(...)` creation line becomes a helper
   call, the original declaration must be gone — the file must end up with exactly
   one declaration of that variable and no duplicate stubs.
5. Change only what this task asks for. Never touch production code, build files,
   unrelated tests, or lines that configure a *different* mock, even when the mock
   you are working on appears in them as a return value.
6. Create a file through `newFiles` only when the task tells you the refactoring
   is class level. A new file must sit under the same source root as the files you
   were given.

### On declining

Even when you have reservations — the mock's link to the code under test looks
unclear, two instances seem deliberately configured differently, or you cannot
fully prove safety from the supplied source alone — still produce your best and
safest attempt, and record the concern in `caveat`. Every result is compiled,
tested and mutation-tested before it is accepted, so an honest attempt is worth
more than a refusal.

Set `canRefactor: false` only when no edit could possibly apply: the described
mock clone is not present in the text you were given. Say concretely what was
missing.
