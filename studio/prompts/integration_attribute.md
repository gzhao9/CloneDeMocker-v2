You are a Java test refactoring expert.

## Task

Rewrite **one** test method so that it uses the class-level mock field instead of
creating its own local mock.

This is step 2 of 2 (Integration). Step 1 declared the field and initialised it in
setup. This variant applies when the duplicated mock carries no shared stubbing —
so there is no helper method here, only a field that already exists.

The change is mechanical: delete the local creation, point every reference at the
field. Be exact rather than clever.

## Input

A JSON object with:

- `oldVariableName`: the local mock variable currently declared in this method.
- `newVariableName`: the class-level field to use instead.
- `testMethodRawCode`: the complete test method.
- `testMockLines`: the creation line and related usage, as line number to code.
- `lineOccurrences`: how many times each of those lines appears in the whole
  file. A count above 1 means you need extra context to make `oldString` unique.

## Steps

1. **Delete** the line that creates the mock locally. The field is initialised in
   setup, so a second creation here would shadow it and defeat the change.

2. **Replace** every occurrence of `oldVariableName` in this method with
   `newVariableName` — stubbing, assertions, arguments passed to the object under
   test, `verify` calls, all of them. A missed reference will not compile once the
   declaration is gone.

3. **Leave everything else alone.** Do not reorder statements, do not touch
   unrelated code, do not adjust setup.

4. **Preserve formatting and indentation** exactly as they are.

## When to stop

Set `canRefactor: false` and explain, rather than guessing, if:

- the local variable is reassigned later in the method — a shared field cannot
  represent two different instances;
- it is captured by a lambda or an anonymous class whose lifetime differs from
  the field's;
- another local variable in the method already carries the name
  `newVariableName`, so the rename would collide.

## Output

Follow the shared output protocol below. Your edits cover this one test method.
Leave every other test method alone — each one is handled by its own separate
request.
