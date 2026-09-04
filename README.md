# parallel-streams

**Turn one approved plan into several coding sessions that run at the same time and don't collide.**

[![tests](https://github.com/timetodel/parallel-streams/actions/workflows/tests.yml/badge.svg)](https://github.com/timetodel/parallel-streams/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![skill](https://img.shields.io/badge/Claude%20Code-skill-orange.svg)](https://code.claude.com/docs/en/skills)
[![version](https://img.shields.io/badge/version-1.11.0-brightgreen.svg)](CHANGELOG.md)

You open six agent sessions on one repository because the plan is big and the model is fast. An hour
later two of them have rewritten the same file, a third built a helper the fourth had already
built under a different name, and the fifth was blocked from the very first minute — it needed a
migration that nobody had merged yet.

Parallel agents don't fail because the model is weak. They fail because nobody worked out **what
can actually run at the same time**, and nobody wrote each session a brief it could execute alone.

That is this skill's entire job.

```
you: split the plan into parallel streams

agent:
   S1 ────┬─► S2 ────┬───► S4 ──┬─► S6 ──┬─► S8
          │          │          │        │
          └─► S3 ────┼─┬────────┘        │
                     │ │                 │
                     └─┼─► S5 ───────────┤
                       │                 │
                       └─► S7 ───────────┘

   A column is a start moment. Open S1 now; when it merges, S2 and S3 go
   in parallel; and so on. Plus one paste-ready brief per stream.
```

---

## Russian version

A full Russian translation of the skill, its references and both templates lives in
[`localization/ru/`](localization/ru/) — install that directory instead of the English one when the
sessions talk to their owner in Russian. Section headings in the profile stay English either way;
the skill looks them up by name. The coordination channel is Russian by origin there, not translated.

## Install

### 1. Ask your agent (no commands to remember)

Paste this into any agent session that can read the web and write files:

> Install the skill from https://github.com/timetodel/parallel-streams

The repository carries [INSTALL.md](INSTALL.md) — step-by-step instructions written **for the
agent**, not for you. It will fetch the skill folder, drop it in the right place, and tell you the
command to run.

### 2. By hand

Copy `skills/parallel-streams/` into the repository you actually run plans in, and restart the
session:

```bash
git clone https://github.com/timetodel/parallel-streams
cp -r parallel-streams/skills/parallel-streams <your-repo>/.claude/skills/
```

| Location | Scope |
|---|---|
| `<repo>/.claude/skills/parallel-streams/` | **default** — everyone working in that repository, and it ships with the repo |
| `~/.claude/skills/parallel-streams/` | you, in every project — only if you want it everywhere |

**It ships as a skill, not as a plugin, on purpose.** A plugin installs per user and then follows
you into every project you open; this one is only useful where plans are big enough to split.
Living in the repository also means the version is the one your teammates get, and updating it is a
commit like any other. Updating: copy the directory over the old one.

Requirements: an agent that supports skills, and Python 3.9+ for the diagram renderer (standard
library only — no packages, no network).

---

## Use

Point it at a plan you have already approved:

```
split docs/plans/2026-03-team-workspaces.md into parallel streams
```

Other phrasings that trigger it: *parallelize this plan*, *what can I run in parallel*, *hand this
plan to several agents*, *show me the stream map*.

Ask for **just the map** ("show me how this would split") and you get the table and the diagram,
without the briefs.

## What you get

### 1. A dependency map — table, then diagram

| Stream | Name | Waits for | Blocks | Escalation | Review |
|---|---|---|---|---|---|
| 1 | Ship the workspace schema and migration | nothing | 2, 3 | none — direct implementation of an approved model | high |
| 3 | Enforce workspace roles on every request | 1 | 6, 7 | whole stream — prove no path reaches a workspace without a membership check | high + security |
| 7 | Charge per seat | 3 | 8 | steps 1-2 — money math, and the seat rules disagree between phases | high + security |

*"Waits for" and "Blocks" are verified against each other, not eyeballed. Both escalation and
review are mandatory for every row — `none` with a reason is an answer, a blank is a defect.
Escalation is written as a span — where the expensive mode starts and where it ends — because
nothing turns it off on its own. Review depth follows what the stream touches — money and access
control at the top, a docs-only diff at `none` — never how hard the work felt to write.*

The diagram is **generated and self-verified**, never typed. See [why that matters](#the-diagram-is-generated-not-drawn).

### 2. One self-contained brief per stream

Copy-paste into a fresh session. It has no memory of the conversation that produced it, and it
doesn't need one.

The briefs arrive in the reply, not in a file you have to open, and each one sits under a heading
that says when to open it — `Stream 1 — start now`, `Stream 3 — after stream 1 merges` — so the
list of headings reads as today's launch order:

```markdown
# Enforce workspace roles on every request

## Context
Workspaces are only as safe as the check that runs before the handler. This stream resolves the
acting member for every request carrying a workspace id and enforces the role matrix. Plan: phase 3.

## Dependencies
Wait until stream 1 (schema and migration) is merged into the main branch.

## How to work
Create an isolated workspace first — sessions share one project directory, and without isolation
they overwrite each other's checkout. Branch, commits, pull request, and merge are yours to do
without asking. Ask only about the decisions listed below.

Delegate the reading — sweeps, prior art, broad searches go to a subagent, so you get a few lines
back instead of a dozen files parked in this session's context. Delegate the writing too: a fresh
implementation subagent per task, one reviewing subagent per finished task — reading it against
this brief, not hunting for logic bugs — its findings back in a single round of fixes. Research, mechanical work and review lists on the cheap fast tier; new logic
on the session's own; review never below the session's tier. Every brief you write for a subagent
names two addresses: the detailed report to a file, five to ten lines in plain language back — that
answer is shown to me directly.

Stream 2 is adding endpoints in the same area. Different files, but rebase before you open the PR.

## What to do
1. Resolve the acting member on every request that carries a workspace id.
2. Enforce the matrix: only the owner may delete a workspace or change billing; ...
3. Return "not found" — never "forbidden" — to a user with no membership.

## Escalation
This stream needs the deeper mode from the start: it is a "prove nothing leaks" job. ...
The span is the whole stream, so I ask you to turn it off in my final summary, in its own line.

## Review
Run the project's review gate at high depth. This stream is access control — security review too.

## Decide with me before implementing
- Phase 3 leaves open whether removing the last owner is blocked or promotes the oldest admin.
- Exact wording of the message a member sees when an action is above their role.
Plain language, decision first, code after.

This brief can be wrong. See a flaw in it — a step that cannot work, an instruction the code
contradicts, a task already done — say so before you start; objecting costs you nothing, including
when it overturns something I wrote. In every report, mark what you verified by reading or running
it apart from what you are assuming.

## Done when
- [ ] every workspace-scoped route resolves a membership before the handler runs
- [ ] a non-member receives "not found", verified by a test
- [ ] tests, gates, review, pull request merged
```

Full worked example: [an approved plan](examples/example-plan.md) → [what comes out of it](examples/example-output.md).

---

## How it decides what can run in parallel

Step order inside a plan is **not** a dependency. Chapter 2 follows chapter 1 because that is how a
human reads, not because the work is sequential. The real question is: *if both sessions started
right now, what would break?*

**Counts as a dependency**

- **Shared edit surface** — both change the same file or tightly-coupled module.
- **Produced artifact** — one needs a table, endpoint, config key, generated client, or package the
  other creates.
- **Single-owner version** — both bump the same schema version, protocol version, or lockfile. Two
  bumps of one counter can't be merged; the second is simply wrong.
- **Contract change** — one changes a signature or payload the other calls.

**Does not count**

- Same package, different files. That is merge *risk*: the brief says "rebase first", and both
  streams still run. Serializing here is the most common way a split loses its point.
- "It feels safer in order." If you can't name what breaks, there is no dependency.

The traps that look independent and aren't — two fields on one model, a setting written by one and
read by another, two commands in one registry, a rename racing new code — are catalogued in
[dependency-analysis.md](skills/parallel-streams/references/dependency-analysis.md), along with how
to cut a cycle when you find one.

## The diagram is generated, not drawn

An ASCII diagram drifts by one character and the drift is invisible to whoever wrote it. It is only
ever caught by the reader — the person you were trying to help.

So the skill doesn't draw one. It runs the bundled renderer, which computes every character
position and then **verifies its own output**:

```console
$ python render_map.py streams.txt --check
render_map: check passed
   S1 ──────┬─► S3 ──┬───► S6 ──┬─► S8 ────┬─► S9 ───┬─► S11 ────► S12
            │        │          │          │         │
   S2 ──────┼─► S5 ──┼───► S7 ──┘          │         │
            │        │                     │         │
            └─► S4 ──┘                     │         │
                                           │         │
                                           └─► S10 ──┘
```

Input is one line per stream — `6: 3, 4` means stream 6 waits for streams 3 and 4. The renderer is
a standalone script with no dependencies; you can use it outside this skill for any DAG you want
drawn as text.

`--check` verifies that each stream appears exactly once, that every column is aligned to the
character, and that no line runs through a label. A failed check means the diagram never reaches
you. Bad input is diagnosed rather than drawn: unknown stream ids, duplicate declarations, and
dependency cycles each get a specific error — and a cycle is a finding about your split, not about
the renderer.

## Configure it for your project

Drop a `.parallel-streams.md` in your repository root and every brief starts speaking your
project's language — your isolation model, your test command, your review gate, your merge policy:

```markdown
## Isolation
One git worktree per stream, branched from a fresh origin/main.

## Delegation
Research and mechanical tasks go to a subagent on the cheap fast tier; new logic on the session's
own tier; review never below the session's tier.

## Escalation
"Deep mode". A session asks for it in a message of its own and waits; when the flagged steps are
done it stops and asks for it to be turned off.

## Tests
`npm test` for the package you touched.

## Review
Run the project's review command over the stream's diff. Default depth: high. A diff that cannot
change behaviour may answer `none` with the reason.

## Security review
Required for: billing, authentication, permissions, tokens, public network surface.

## Persistent rules
`CLAUDE.md` — the file every session here loads on start. It carries what must hold however a task
arrived; this profile repeats it for the briefs.
```

Full field list: [configuration.md](skills/parallel-streams/references/configuration.md).
Start from [the template](examples/profiles/TEMPLATE.md), or read
[a real one](examples/profiles/real-world.md) from the project this skill was built in.

Without a profile it uses sane defaults and says so once.

---

## The coordination channel (optional)

Splitting the plan is not the whole problem. Once the sessions are running, they are blind to each
other: none of them can see another's context, and a plan edited after they started never reaches
them — each read it once, at its own start. So a finding one session makes for another has no way
to arrive, and "who owns this task?" has no one to answer it.

The channel is the part that answers those. It ships inside the skill, in `coordination/`, and it
is **off until you install it**:

```
pwsh skills/parallel-streams/coordination/install.ps1
```

That gives a project five commands, which the skill then writes into every brief it produces:

| Command | What it does |
|---|---|
| `-Mode Claim` | announce this stream on start — otherwise a running session is indistinguishable from one nobody opened |
| `-Mode Add` | send a finding to a live neighbour, addressed by stream number (`wave6/3`), not by branch name |
| `-Mode Done` | close what arrived, so it stops coming back after each context compaction |
| `-Mode Release` | hand the stream back before saying done — refused while the inbox still has open entries |
| `-Mode Streams` | ask who owns which task, before proposing work outside your own |

The board and the claims live in the repository's shared internal directory, so every worktree sees
them at once, they belong to no branch, and they never need merging.

A profile with no `## Coordination` section leaves all of this out: the skill prints no channel
commands and behaves exactly as it did before. Full description, including one known limitation
stated in the open: [coordination/README.md](skills/parallel-streams/coordination/README.md).

Requirements: PowerShell 7 (the tool and both hooks are `.ps1`; it runs on Windows, macOS and
Linux) and a git repository.

---

## What it is, and what it isn't

**It is** a planning step: it reads a plan, works out the dependency structure, and writes the
briefs. The output is text you can read, argue with, and paste.

**It isn't** an orchestrator. It doesn't spawn sessions, doesn't watch them, doesn't merge for you.
That is deliberate — you stay the one who decides how many sessions to open and when. The map tells
you what's possible; the calendar is yours. The optional coordination channel does not change that:
it lets running sessions reach each other, it does not run them.

**It needs a plan.** Not a perfect one, but something concrete enough to have parts. "Rewrite the
billing system" doesn't split; a plan with phases, steps, and named artifacts does.

## When the rule is in the skill and sessions break it anyway

The briefs this skill writes reach the sessions it cut, at the moment they are pasted. Nothing else.
A task typed straight into a chat, a plan someone wrote by hand, a session opened tomorrow to fix
one thing — none of them load `.parallel-streams.md`, and none of them load this skill. A rule that
lives only here is not a rule those sessions ever saw, and they will report success without it.

That is why rules come in three carriers, saying one text:

| Carrier | Reaches | Blind spot |
|---|---|---|
| The file your harness loads on start (`CLAUDE.md`, `AGENTS.md`) | every session in the project | not task-specific |
| The plan the sessions work from | anyone who opens the plan, including hand-cut tasks | a session working without a plan |
| `.parallel-streams.md` → the briefs | sessions this skill briefed | everything else |

Symptom to recognise: the skill is current, the profile is current, both say the rule plainly — and
sessions still do the thing the rule forbids. Look at what those particular sessions actually read.
The block to paste, and the failure that produced this section, are in
`skills/parallel-streams/references/persistent-rules.md`.

Since 1.9.0 the skill checks for this itself: it looks for the file named by `## Persistent rules`
before writing briefs, checks a plan in the `## Plans` folder for a rules section, and says in one
line when either is missing. It never writes to those files uninvited.

## FAQ

**Does this work outside Claude Code?**
The skill is a markdown file with instructions and a Python script. Any agent that can read a file
and run a command can follow it. Nothing here is tied to one vendor's package manager — the whole
delivery mechanism is "copy a directory into your repository".

**How many streams should I actually open?**
As many as the leftmost column, if your machine and your review capacity allow. The map is the
ceiling, not the target. Two well-briefed sessions beat six that keep colliding.

**What if the plan is half done already?**
The skill checks the merge history and adds a line under the diagram: what's merged, what's in
flight, what hasn't started. If it can't verify that reliably, it says so instead of guessing.

**Why does the brief tell the session to delegate its reading and its writing?**
Because splitting a plan into streams quietly kills the habit. A session with a whole stream to
itself feels parallel enough and stops spawning subagents — so it reads with its own hands, and
every file it opens stays in a context that is re-sent on every step until the stream ends. The same
happens to implementation, and naming only the reading half made it worse: one wave kept every
research subagent and ran no implementers at all, hand-writing hundreds of lines that then scrolled
past the person the stream reports to. So the brief names both halves, and names them as permission:
no session should have to ask whether it may look something up or hand a task to an implementer. It
also fixes the model tier per kind of work, so neighbouring sessions don't each pick by taste.

**Why does the brief make the session tell its subagents where to put the detail?**
Because a subagent's last answer is shown to you directly: it arrives in the session as its own
message and is displayed whole, ahead of anything the session decides — and that subagent was asked
for maximum precision, so the message is paths, names and code. Observed 2026-09-04: a session
editing this kit wrote forty-four messages, not one of which carried any code, and the person still
received "a message full of code" — its own implementer's report. No rule was broken: the rule about
never forwarding reports governs what the session *forwards*, and this text went around it. So each
brief now names two addresses instead of one — the detailed report to a file the session reads
itself, five to ten lines in plain language back as the answer. The demand for precision is
unchanged; only its address is.

It fixes the *shape* too, because the intuitive shape is the expensive one: one reviewer per
finished task rather than per round of fixes, a fresh subagent per task rather than an old one woken
again with its whole conversation re-sent, and the review's findings handled in one round instead of
one at a time. A wave that invented its own shape spent most of a day's budget on machinery rather
than on code.

**Why does the brief invite the session to object to it?**
Because a brief reads as settled — it was written by whoever is paying, it arrives before any of the
work, and disagreeing with it looks like refusing to start. So a session that spots a step which
cannot work tends to build around it silently. The brief now says the opposite out loud, and says
it before the work rather than after: objecting is free, including when it overturns an instruction.
It comes with the other half of the deal — every report marks what was verified apart from what is
assumed, which is what turns an objection into something checkable instead of an opinion. One
session given that permission stopped four wrong conclusions in a single evening, two of them
instructions from the person who wrote its brief.

**Why must every brief say something about escalation and review?**
Because those are the two lines that were always meant to be there and were always the first to be
dropped. Making them mandatory — including one deliberate repetition in the done-when checklist —
is the fix that stuck.

**Who turns the expensive mode off?**
You do, by hand — which is exactly why the brief has to ask. The mode is a switch: finishing the
flagged step does not flip it back, so a brief naming only the trigger leaves the expensive mode
running over the routine work that follows, where it buys nothing and charges on every step.
Since 1.9.1 escalation is written as a *span*, and the brief carries both of its ends: ask to turn
the mode on before the span, then stop at the end of it and ask to turn it off. Off is a stop, not a
remark inside a report — you do not track the mode, and by the time a mention is read the session
has moved on. Waiting is the cheap side: a stopped session spends nothing.

**Does that mean every stream has to be reviewed?**
The *line* is mandatory, the gate is not. A stream whose diff cannot change behaviour — docs,
translated strings, a version bump — answers `none` with the reason, and that counts as answered.
What it may not do is decide by feel: depth comes from what the stream touches, because authors
rate their own work as simple with striking consistency. Every `none` is paired with a fallback —
if the work does end up touching executable code, the gate comes back — so "documentation only"
can't quietly become a code change that skipped review. Teams that review everything on principle
say so in the profile, and then `none` is never written at all.

**Do I have to install the coordination channel?**
No. The skill splits plans without it, exactly as it did before — leave the `## Coordination`
section out of the profile and it never mentions the channel. Install it when the answer to "how
does a session tell its neighbour what it just found?" is "it can't", which is the moment several
sessions on one repository stop being independent.

**The channel is PowerShell — I'm on macOS.**
PowerShell 7 is a cross-platform install (`brew install powershell`, `apt install powershell`); the
scripts avoid Windows-only calls and their tests run on Linux in this repository's own CI. If you
would rather not add it, skip the channel — nothing else in the skill needs it.

**Do I have to use git worktrees?**
No, but you need *some* isolation. Sessions sharing one working directory fight over the active
branch, and the one that loses does it silently. Set your model in the profile.

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The most useful
contribution is a dependency trap the skill misses: a real case where two streams looked
independent and weren't.

```bash
python -m pytest tests
```

## Origin

Built for, and hammered into shape by, a commercial product where twenty-plus agent sessions
routinely run against one repository at the same time. Every rule in it — the mandatory escalation
line, the generated diagram, the "one column = one start moment" reading, the refusal to serialize
streams that merely share a package — exists because its absence cost a day of work at least once.

By [@timetodel](https://github.com/timetodel). MIT licensed — take it, fork it, make it yours.
