---
name: parallel-streams
description: Split an approved plan into parallel work streams that different agent sessions can run at the same time without merge conflicts or duplicated work. Produces a dependency map (table plus diagram) and one self-contained brief per stream, ready to paste into a fresh session. Use when asked to "split the plan into streams", "parallelize this plan", "what can I run in parallel", "hand this plan to several agents", or "show me the stream map".
---

<!-- parallel-streams 1.8.0 — https://github.com/timetodel/parallel-streams
     Shipped as a skill: this directory is the whole thing. Update by copying a newer
     copy of it over this one; changes are listed in the repository's CHANGELOG.md.
     Project rules come from the profile `.parallel-streams.md` in the repository root.
     The coordination channel — the machinery behind the commands this skill prints — is
     the `coordination/` subdirectory. It is optional: a profile with no `## Coordination`
     section leaves the skill working exactly as it did before. See `coordination/README.md`. -->

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

**Coordination channel.** If the profile has a `## Coordination` section, the project has a way for
running sessions to reach each other — sessions cannot see each other's context, and a plan edited
after a session started never reaches it. Copy those commands verbatim into blocks 4 and 9 of every
brief, filled in for that stream: announce the stream on start, send a finding to a live neighbour,
close what arrives, ask who owns a task before proposing work outside your own, release the stream
before saying done. No such section — skip it entirely and say nothing about coordination; the
skill carries the protocol, never the mechanism.

Each stream's address in that channel is `<wave>/<stream number>` — the wave id from the plan's file
name and the number from column 1 of the table. Names of branches and folders drift within a wave;
the number in the plan does not, so it is the only address that keeps working.

A project with no waves and no plan file is supported too: the channel supplies the wave itself —
from work already running next to it, or from today's date when nothing is — and hands out the next
free stream number. The channel's own machinery ships with this skill, in `coordination/`; how it is
installed into a project: `coordination/README.md`.

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
- **Review** — the gate from the profile, at a depth set by what the stream touches, never by how
  hard the work felt to write:

  | What the stream touches | Depth |
  |---|---|
  | Money, access control, secrets, personal data, anything reachable from outside | the profile's deepest gate, plus a security review |
  | A change that spreads — a sweep, a migration, a rename — or a new contract other streams will call | deep |
  | One area, following a pattern that already exists in the repository | the profile's lighter gate |
  | A diff that cannot change behaviour: prose, translated strings, comments, a version number, a file moved unchanged | `none`, with the reason in the same cell |

  `none` is a statement about the diff, not about the plan: the brief carrying it must also carry
  the line that restores the gate if the work turns out to touch code. A profile may forbid `none`
  outright — then every stream carries a gate. Both columns take `none` as an answer; neither takes
  a blank. Depth rules and the fallback wording: `references/brief-template.md`.
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

Which streams are *in flight* is the half that guessing gets wrong: a worktree left behind by a
closed session looks exactly like a running one. If the profile declares a coordination channel,
ask it — it answers from the sessions' own claims, not from directory names.

## Step 5. Write one brief per stream

Each brief is self-contained: the user pastes it into a fresh session that has none of this
conversation. Every brief appears in the reply itself, under a heading that says when to open it —
the exact shape is in *Output format* below. Fixed block order, nothing skipped, nothing reordered:

1. **Title** — an action, not an object ("Add the retry queue", not "Retry queue").
2. **Context** — 3-5 lines: what and why, pointing at the plan section.
3. **Dependencies** — what must be merged first, or "none, start now".
4. **How to work** — create the isolated workspace first, then announce the stream on the
   coordination channel if the profile has one (the announcement is what makes this session
   reachable at all, and it is worthless after the fact — put it before any edit); delegate reading
   and research to subagents instead of doing it inline, and delegate each task's implementation the
   same way, with one subagent reviewing the finished task against the brief — whether what was
   asked got done, not a hunt for logic bugs, which is the gate's job — once per task, not once per round of
   fixes, or once at the end of the whole stream when the stream is two or three small tasks of the
   same kind — and a fresh subagent per task rather than an earlier one woken again, at the model tiers
   from the profile; the session handles branch, commits, pull request, and merge on its own, and
   only asks about the decisions in block 8. When the channel exists, this block also carries: how a finding reaches a live
   neighbour, that arriving records must be closed, and — before proposing any work outside this
   stream's own tasks — how to ask who owns that task. The person approving cannot know a task was
   planned for another stream; they will say yes. This block also settles what reaches the person:
   a subagent's report is raw material for the session, never text to forward — subagents are asked
   for maximum precision, and none of it is pasted into the chat, whole, summarised, or reworded
   with the code names left in. The person hears the session's own words, and only three times: one
   line per task saying what it is starting, a self-contained question at every fork, and a summary
   at the end.
5. **What to do** — the concrete steps from the plan that belong to this stream.
6. **Escalation** — mandatory line, either the trigger and the reason, or "not needed" and why.
7. **Review** — mandatory line: which review gate runs before the merge, or `none` with its reason
   and the condition that brings the gate back.
8. **Decide with me before implementing** — the real forks *from this plan* for this stream, plus
   any user-visible wording, in plain language, decision before code; and the standing invitation to
   object to the brief itself before starting, free of cost even when the objection overturns an
   instruction, paired with the requirement to mark verified apart from assumed in every report.
9. **Done when** — tests, gates, review completed, pull request merged; and, where the profile has a
   coordination channel, the stream released — releasing is what forces the question "is everything
   that arrived actually handled", which nothing else in the flow asks.

Template and the rules behind blocks 4 and 6-8: `references/brief-template.md`.

## Step 6. Self-check before showing anything

Map:

- [ ] table has exactly the six columns, in order?
- [ ] "Waits for" and "Blocks" verified against each other, not eyeballed?
- [ ] "Escalation" and "Review" answered for every stream — a value, or `none` with its reason, never blank?
- [ ] each review depth traced to what the stream touches, not to how hard the work looked?
- [ ] diagram present, generated by the renderer, `--check` passed?
- [ ] columns really are start moments — everything in one column can start at once?
- [ ] if the plan is partly done, state noted under the diagram?

Each brief:

- [ ] delegation line present — research *and* implementation to subagents, with the model tiers,
      the reviewing subagent reading the finished task against the brief rather than hunting bugs,
      one reviewer per finished task — or one for the whole stream, when it is two or three small
      tasks of the same kind — and a fresh subagent per task?
- [ ] reporting line present — subagent reports stay inside the session, subagents are still asked
      for maximum precision, and the person gets the session's own words: a line per task, a
      self-contained question at each fork, a summary at the end?
- [ ] explicit escalation line — yes or no, with a reason?
- [ ] explicit review line — a gate, or `none` with its reason plus the line that restores the gate
      if the diff turns out to touch code?
- [ ] forks are concrete, taken from the plan, not generic advice?
- [ ] block 8 carries the invitation to object before starting — free even when it overturns an
      instruction — and the verified-apart-from-assumed line for reports?
- [ ] title names an action?
- [ ] a reader with no other context could execute it?
- [ ] profile has a coordination channel — announce-on-start (before any edit) in block 4, release
      in block 9, and this stream's own address filled in, not a placeholder?

Delivery:

- [ ] map and briefs printed in this reply, copyable without opening a file?
- [ ] every brief under a heading naming its launch moment — `start now`, or the streams it waits for?

Any "no" — fix it before showing. Never show a draft.

## Output format

The map and the briefs are printed in this reply. They are the answer itself, not a document
written beside it: the person copies each brief out of the window already in front of them, into a
new session, without opening a file first.

1. One-paragraph summary: how many streams, why this split, and whether a profile was found.
2. The table.
3. The diagram, plus the state line if the plan is partly done.
4. One block per stream, in stream order, each under a heading of the form `Stream N — start now`
   when nothing blocks it, or `Stream N — after stream M merges` when something does, naming every
   stream it waits for (`Stream 8 — after streams 5, 6, 7 merge`). The heading carries the launch
   moment, so the reader sees from the headings alone which sessions to open today; the name of the
   work is the brief's own title, the first line inside the block. Each brief goes in a fenced block
   so one gesture copies it whole. Skipped when only the map was asked for.
5. One closing line: which streams can be opened right now.

Asked for a file as well — write it, and print everything here too.

Do not restate the launch order in prose — the table, the diagram and the headings already carry it.
