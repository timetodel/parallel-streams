---
name: parallel-streams
description: Split an approved plan into parallel work streams that different agent sessions can run at the same time without merge conflicts or duplicated work. Produces a dependency map (table plus diagram) and one self-contained brief per stream, ready to paste into a fresh session. Use when asked to "split the plan into streams", "parallelize this plan", "what can I run in parallel", "hand this plan to several agents", or "show me the stream map".
---

<!-- parallel-streams 1.16.0 — https://github.com/timetodel/parallel-streams
     Shipped as a skill: this directory is the whole thing. Update by copying a newer
     copy of it over this one; changes are listed in the repository's CHANGELOG.md.
     Project rules come from the profile `.parallel-streams.md` in the repository root.
     The coordination channel — the machinery behind the commands this skill prints — is
     the `coordination/` subdirectory. It is optional: a profile with no `## Coordination`
     section leaves the skill working exactly as it did before. See `coordination/README.md`.
     ‼️ The skill looks for profile section headings IN ENGLISH (`## Isolation`, `## Tests`,
     and so on) — do not translate them, here or in the profile itself. Translate the
     content, never the addresses. -->

# Split a plan into parallel streams

Input: a path to an approved plan (a spec, an implementation plan, an issue list, a roadmap section).
Output: a dependency map, and one brief per stream that a fresh session can execute with no other context.

**Map only** — the user asked "show me the map", "how would this split", "what can run in parallel":
do steps 1-4 and 6. Do not write briefs.
**Full split** — the user asked to split the plan: do steps 0-6.
**Closing the wave** — the user asked "close the wave", "what is left of it", "gather the loose
ends", "is this wave finished": do steps 0, 1 and 7. Do not rebuild the map and do not write briefs.

## Step 0. Load the profile

Look for a profile at `.parallel-streams.md` in the repository root. It states how *this* project
isolates sessions, how big a change may be, which command runs the tests, and which review gate
applies. If it exists, every brief must follow it.

If it does not exist, use these defaults and say once, in the summary, that defaults were used:
isolation by git worktree, one reviewable pull request per stream, tests via the project's
documented command, review before merge. Details and the full field list:
`references/configuration.md`.

**Persistent rules.** The profile's `## Persistent rules` section names the file this project's
sessions load at the start of every session — `CLAUDE.md`, `AGENTS.md`, or whatever this harness
reads. Rules that have to hold whether or not a session was briefed by this skill — how the person
is spoken to, what a subagent's report may turn into, when the session writes at all — belong in
that file, not only in the profile: a session working from a task typed by hand into a chat never
opens the profile, and never opens this skill either.

Check it before writing briefs. Named and present — nothing to do. Named but missing, or not named
at all — say so once in the summary, in one line, and offer the ready-made block in
`references/persistent-rules.md`. Do not write into that file uninvited, and do not stay quiet about
it either: silence here is indistinguishable from "this project has it covered".

**Coordination channel.** If the profile has a `## Coordination` section, the project has a way for
running sessions to reach each other — sessions cannot see each other's context, and a plan edited
after a session started never reaches it. Copy those commands verbatim into blocks 4 and 9 of every
brief, filled in for that stream: announce the stream on start, send a finding to a live neighbour,
close what arrives, ask who owns a task before proposing work outside your own, release the stream
last, once there is nothing left to commit. No such section — skip it entirely and say nothing about
coordination; the skill carries the protocol, never the mechanism.

Announcing is the one thing here that is enforced rather than agreed: with the channel installed, a
commit from a stream that never announced itself is refused, and the refusal hands back the command
that fixes it. So the announcement in block 4 is not a courtesy — a stream that skips it stops at
its first commit.

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

**If the plan sits in the folder named by `## Plans`, it is a rules carrier too.** A brief is
pasted once; the plan is the document a session opens for itself, and a task re-cut by hand from
the plan carries only what the plan carries. Look for a section holding the rules that apply to
every stream. Missing — say so in the summary and offer to add it, built from the profile. Observed
2026-08-31: a wave plan written by hand had no such section, the skill and the profile were both
current and both said a subagent's report is never forwarded, and every session in that wave pasted
its subagents' reports — paths, tables of files, lines of configuration — to a person who does not
read code. Nothing was violating a rule; the rule was simply nowhere those sessions could see it.

**The plan also needs a `## Wave Loose Ends` section — it is an address the channel already sends
things to.** Several of the channel's refusals point at it by name: a finding for a released stream,
a finding for a stream whose worktree is gone, an inbox left behind by a stream that was released.
With no such section, every one of those points at nothing, and the session following the advice
has nowhere to put what it is holding. Missing — say so once in the summary and offer to add it; an
empty section with one line saying what belongs in it is enough.

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

All three questions above ask what a stream **changes**. Ask one more: what does each stream **use
while checking itself**? One local database the suite migrates, a container under a fixed name, a
build lock, a shared cache — two streams running the tests at the same moment share all of it, and
the failure looks nothing like a dependency: red tests in a stream that changed nothing related, or
green ones bought by a neighbour's migration. This is not a "waits for": the answer is to separate
them, which is a line in the brief and a line under the map — never a column, and never an order to
wait. The usual ones, and where each line goes: `references/dependency-analysis.md`.

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
| 1 | Introduce the capability flag | nothing | 4, 5, 6 | steps 3-4 — sweep for every call site | high |
| 2 | Honest failure when the service is down | nothing | 5 | none | medium |
| 9 | Guards and acceptance | 5, 6, 7, 8 | — | whole stream — full inventory | high + security |

- **Waits for / Blocks** are symmetric: B waits for A ⇔ A blocks B. Verify by comparing both
  columns, not by eye.
- **Escalation** — the *span* of the deeper, more expensive mode: where it starts, where it ends,
  and what for — "steps 3-4", "whole stream" — or `none`. Never leave it blank. A span, not a point:
  the mode is switched on by hand and stays on by hand, so a start with no end leaves the expensive
  mode running over the routine work that follows it.
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
- Anything the streams share **while verifying** goes in one line under the table — what to separate
  and how — not in a column and not in "Waits for". Nothing to separate: say nothing. A column here
  would answer `none` in nearly every row, and an entry in "Waits for" would serialize hours of work
  to avoid a fix that takes a line.

### 4.2 Diagram — one connected picture, generated, never typed

Feed the renderer the table you just wrote, and paste its output verbatim:

```
python scripts/render_map.py map.md --format table --check
```

The diagram then comes from the same table the reader is looking at. Retyping the dependencies into
the short notation still works (`4: 1, 2` means stream 4 waits for 1 and 2), but nothing compares
the two afterwards: a table saying one thing and a diagram drawn from another both pass, because
each is checked alone.

`--check` does two jobs here. It verifies the drawing — a diagram that looks right and is off by one
character fails loudly instead. And it settles the table itself: six columns in the fixed order,
"Waits for" and "Blocks" agreeing with each other in both directions, no blank cells where an answer
belongs, no transitive edges, no reference to a stream that is not in the table. None of that is a
question for the checklist any more.

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
   with the code names left in. The session's intent alone is not enough for that: a subagent's last
   answer is shown to the person directly, as its own message, ahead of the session — so the brief
   the session writes for a subagent names two addresses, the full report to a file and a few lines
   in plain language back. The person hears the session's own words, and only three times: one
   line per task saying what it is starting, a self-contained question at every fork, and a summary
   at the end. Asking for the deeper mode — on before its span, off at the end of it — is a fork
   question, not extra status: both stop the work until they are answered.
5. **What to do** — the concrete steps from the plan that belong to this stream.
6. **Escalation** — mandatory. Either "not needed" and why, or two requests: turn it **on**, with
   the trigger and the span it covers, and — at the end of that span — stop and ask to turn it
   **off**, because nothing turns it off on its own.
7. **Review** — mandatory line: which review gate runs before the merge, or `none` with its reason
   and the condition that brings the gate back.
8. **Decide with me before implementing** — the real forks *from this plan* for this stream, plus
   any user-visible wording, in plain language, decision before code; and the standing invitation to
   object to the brief itself before starting, free of cost even when the objection overturns an
   instruction, paired with the requirement to mark verified apart from assumed in every report.
9. **Done when** — tests, gates, review completed, pull request merged; and, where the profile has a
   coordination channel, the stream released — releasing is what forces the question "is everything
   that arrived actually handled", which nothing else in the flow asks. Release happens as the VERY
   LAST step, once there is nothing left to commit in this folder: after it the guard blocks commit,
   push, and pull-request creation alike.

Template and the rules behind blocks 4 and 6-8: `references/brief-template.md`.

## Step 6. Self-check before showing anything

Map:

The renderer settles the mechanical half: run it over the table itself and it answers for the six
columns and their order, the two dependency columns agreeing in both directions, blank cells,
transitive edges, references to streams that are not there, and the drawing. What is left needs
judgement:

- [ ] `render_map.py --format table --check` run over this very table, and it passed?
- [ ] each review depth traced to what the stream touches, not to how hard the work looked?
- [ ] `none` in a cell carries its reason, not just the word?
- [ ] anything the streams share *while verifying* named under the table, with how to separate it —
      or there is genuinely nothing to separate?
- [ ] if the plan is partly done, state noted under the diagram?

Each brief:

Each item is one question. What the answer has to contain is in step 5 and the template — repeating
it here would give one requirement two wordings, and two wordings drift.

- [ ] delegation line present, whole — research and implementation both, model tiers, one reviewing
      subagent per finished task, a fresh one each time?
- [ ] reporting line present, whole — including the two addresses a subagent's own brief must name?
- [ ] escalation line explicit — `none` with a reason, or the span with both of its stops?
- [ ] review line explicit — a gate, or `none` with its reason and what restores the gate?
- [ ] forks are concrete, taken from the plan, not generic advice?
- [ ] block 8 carries the invitation to object before starting — free even when it overturns an
      instruction — and the verified-apart-from-assumed line for reports?
- [ ] title names an action?
- [ ] a reader with no other context could execute it?
- [ ] profile has a coordination channel — announce-on-start (before any edit) in block 4, release
      in block 9, and this stream's own address filled in, not a placeholder?

Delivery:

- [ ] persistent rules file from the profile checked — present, or its absence said once in the
      summary with the ready-made block offered?
- [ ] plan in the `## Plans` folder carries a rules-for-every-stream section — or its absence said
      once, with the offer to add it?
- [ ] plan carries a `## Wave Loose Ends` section — the address the channel's own refusals point at —
      or its absence said once, with the offer to add it?
- [ ] map and briefs printed in this reply, copyable without opening a file?
- [ ] every brief under a heading naming its launch moment — `start now`, or the streams it waits for?

Any "no" — fix it before showing. Never show a draft.

Closing the wave — that mode only:

- [ ] stream states asked of the channel, not guessed from folder names or branch names?
- [ ] every remainder is a separate item carrying ready-made text for a NEW session?
- [ ] anything that could not be found out is named as unknown, not left out?

## Step 7. Close the wave: gather what is left

A wave ends the way it starts — by hand — and nothing in the flow asks the closing question. The map
is built once, at the beginning, and every mechanism here works only while sessions are LIVE.

What that leaves behind is invisible by construction. A task a stream gave up on produces no merge
conflict, no red test, no duplicated work: it is simply gone, and the loss surfaces a wave later, if
at all. A finding addressed to a stream that has since been released stays on the board with nobody
to receive it. An inbox left unopened when its stream was released goes with it.

Ask the channel — never guess, and never read state off folder names:

- `pwsh scripts/wave-board.ps1 -Mode Streams -Wave <wave>` — who ran which stream, which are
  released, which addresses are doubled or lead nowhere at all;
- `pwsh scripts/wave-board.ps1 -Mode Show` — what is still open on the board, and what is stuck
  because its addressee is closed.

Then compare the plan's tasks against the merge history, the same way step 4.3 does, and say plainly
where the comparison is approximate.

Three kinds of remainder, each its own item:

- a task nobody finished and nobody is running now — including one a released stream never closed;
- a finding on the board whose addressee is closed: it will never arrive;
- a stream released with a non-empty inbox: whatever was in it went nowhere.

Every item goes into the plan's `## Wave Loose Ends` section, and each carries ready-made text for
opening a NEW session. A line added to somebody's task is not an option here: the stream that owned
it is gone, and an edit to a plan reaches no live session anyway. No such section — create it, or
say so and offer to.

Print the same list in the reply, and end with the one line that matters: is this wave finished, or
does it have open ends someone must pick up.

## Output format

The map and the briefs are printed in this reply. They are the answer itself, not a document
written beside it: the person copies each brief out of the window already in front of them, into a
new session, without opening a file first.

1. One-paragraph summary: how many streams, why this split, whether a profile was found, and —
   only when something is missing — that the persistent rules file or the plan's rules section
   is not there.
2. The table.
3. The diagram, plus the state line if the plan is partly done.
4. One block per stream, in stream order, each under a heading of the form `Stream N — start now`
   when nothing blocks it, or `Stream N — after stream M merges` when something does, naming every
   stream it waits for (`Stream 8 — after streams 5, 6, 7 merge`). The heading carries the launch
   moment, so the reader sees from the headings alone which sessions to open today; the name of the
   work is the brief's own title, the first line inside the block. Each brief goes in a fenced block
   so one gesture copies it whole. Skipped when only the map was asked for.
5. One closing line: which streams can be opened right now.

Closing the wave prints something else entirely: the state of each stream as the channel reports it,
the remainders grouped by kind, where each one was written down, and one closing line — the wave is
finished, or it has open ends and here is who opens them. No map, no briefs.

Asked for a file as well — write it, and print everything here too.

Do not restate the launch order in prose — the table, the diagram and the headings already carry it.
