---
name: parallel-streams
description: Split an approved plan into parallel work streams that different agent sessions can run at the same time without merge conflicts or duplicated work. Produces a dependency map (table plus diagram) and one self-contained brief per stream, ready to paste into a fresh session. Use when asked to "split the plan into streams", "parallelize this plan", "what can I run in parallel", "hand this plan to several agents", or "show me the stream map".
---

# Split a plan into parallel streams

Input: a path to an approved plan (a spec, an implementation plan, an issue list, a roadmap section).
Output: a dependency map, and one brief per stream that a fresh session can execute with no other context.

**Map only** — the user asked "show me the map", "how would this split", "what can run in parallel":
do steps 1-4 and 6. Do not write briefs.
**Full split** — the user asked to split the plan: do all steps.

## Step 0. Load the profile

Look for a profile at `.parallel-streams.md` in the repository root. It states how *this* project
isolates sessions, how big a change may be, which command runs the tests, and which review gate
applies. If it exists, every brief must follow it.

If it does not exist, use these defaults and say once, in the summary, that defaults were used:
isolation by git worktree, one reviewable pull request per stream, tests via the project's
documented command, review before merge. Details and the full field list:
`references/configuration.md`.

## Step 1. Read the whole plan

Open the file and read all of it — every phase, every step. Not from memory, not from a summary.
A dependency you did not read is a merge conflict you will hit later.

## Step 2. Find dependencies between streams

A dependency between streams is not the same thing as step order inside the plan. Count as a
dependency:

- **Shared edit surface** — two streams change the same file or module. One waits.
- **Produced artifact** — one stream uses what another creates: a table, a migration, an endpoint,
  a config key, a generated client, a new package.
- **Single-owner version** — both bump the same versioned thing (schema version, protocol version,
  lockfile, public interface). Never parallel: merge them into one stream or serialize explicitly.

Do **not** count as a dependency: two streams touching the same package but different,
non-overlapping files. That is merge risk, not a dependency — flag it in the brief, do not
serialize the work over it.

Full checklist, including the traps that look independent and are not:
`references/dependency-analysis.md`.

## Step 3. Group into streams

- Maximum parallelism that still respects step 2.
- One stream = one branch = one isolated workspace = one session.
- Size: one reviewable pull request, or a short chain of small ones inside one branch.
- Never split what is physically indivisible — a migration and its only consumer ship together.

## Step 4. Build the map: table first, then diagram

Both, always, in this order.

### 4.1 Table — exactly these six columns

| Stream | Name | Waits for | Blocks | Escalation | Review |
|---|---|---|---|---|---|
| 1 | Introduce the capability flag | nothing | 4, 5, 6 | before step 3 — sweep for every call site | high |
| 2 | Honest failure when the service is down | nothing | 5 | none | medium |
| 9 | Guards and acceptance | 5, 6, 7, 8 | — | at the start — full inventory | high + security |

- **Waits for / Blocks** are symmetric: B waits for A ⇔ A blocks B. Verify by comparing both
  columns, not by eye.
- **Escalation** — *when* to switch that session into a deeper, more expensive mode and what for,
  or `none`. Never leave it blank.
- **Review** — the gate from the profile.
- Do not repeat transitive dependencies: if 9 waits for 5-8 and those already wait for 1-4, list
  only 5, 6, 7, 8, and say in one line below the table why that is enough.

### 4.2 Diagram — one connected picture, generated, never typed

Feed the dependencies to the bundled renderer and paste its output verbatim:

```
python scripts/render_map.py streams.txt --check
```

Input notation is one line per stream — `4: 1, 2` means stream 4 waits for streams 1 and 2.
`--check` verifies the result and fails loudly instead of leaving a diagram that looks fine and
is off by one character.

A column is a start moment: everything in the leftmost column can be opened right now. Reading
rules, the input format, and what to do when Python is unavailable: `references/diagram-rules.md`.

Hand-drawn diagrams drift by a character and the drift is only ever caught by the reader. Do not
hand-draw one.

### 4.3 State, when the plan is partly done

If some of the work already landed, a map without that note misleads. Check the merge history
(`git log --oneline origin/main`) and add one line under the diagram: which streams are merged,
which are in flight, which have not started. If the check is approximate, say so — do not present
a guess as fact.

## Step 5. Write one brief per stream

Each brief is self-contained: the user pastes it into a fresh session that has none of this
conversation. Fixed block order, nothing skipped, nothing reordered:

1. **Title** — an action, not an object ("Add the retry queue", not "Retry queue").
2. **Context** — 3-5 lines: what and why, pointing at the plan section.
3. **Dependencies** — what must be merged first, or "none, start now".
4. **How to work** — create the isolated workspace first; the session handles branch, commits,
   pull request, and merge on its own, and only asks about the decisions in block 8.
5. **What to do** — the concrete steps from the plan that belong to this stream.
6. **Escalation** — mandatory line, either the trigger and the reason, or "not needed" and why.
7. **Review** — mandatory line: which review gate runs before the merge.
8. **Decide with me before implementing** — the real forks *from this plan* for this stream, plus
   any user-visible wording, in plain language, decision before code.
9. **Done when** — tests, gates, review completed, pull request merged.

Template and the rules behind blocks 6-8: `references/brief-template.md`.

## Step 6. Self-check before showing anything

Map:

- [ ] table has exactly the six columns, in order?
- [ ] "Waits for" and "Blocks" verified against each other, not eyeballed?
- [ ] "Escalation" and "Review" filled for every stream, none blank?
- [ ] diagram present, generated by the renderer, `--check` passed?
- [ ] columns really are start moments — everything in one column can start at once?
- [ ] if the plan is partly done, state noted under the diagram?

Each brief:

- [ ] explicit escalation line — yes or no, with a reason?
- [ ] explicit review line?
- [ ] forks are concrete, taken from the plan, not generic advice?
- [ ] title names an action?
- [ ] a reader with no other context could execute it?

Any "no" — fix it before showing. Never show a draft.

## Output format

1. One-paragraph summary: how many streams, why this split, and whether a profile was found.
2. The table.
3. The diagram, plus the state line if the plan is partly done.
4. One copy-paste block per stream, each with a clear heading. Skipped when only the map was asked for.
5. One closing line: which streams can be opened right now.

Do not restate the launch order in prose — the table and the diagram already say it twice.
