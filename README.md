# parallel-streams

**Turn one approved plan into several coding sessions that run at the same time and don't collide.**

[![tests](https://github.com/timetodel/parallel-streams/actions/workflows/tests.yml/badge.svg)](https://github.com/timetodel/parallel-streams/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![skill](https://img.shields.io/badge/Claude%20Code-skill-orange.svg)](https://code.claude.com/docs/en/skills)
[![version](https://img.shields.io/badge/version-1.1.0-brightgreen.svg)](CHANGELOG.md)

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
| 3 | Enforce workspace roles on every request | 1 | 6, 7 | at the start — prove no path reaches a workspace without a membership check | high + security |
| 7 | Charge per seat | 3 | 8 | before proration — money math, and the seat rules disagree between phases | high + security |

*"Waits for" and "Blocks" are verified against each other, not eyeballed. Both escalation and
review are mandatory for every row — a blank there is treated as a defect.*

The diagram is **generated and self-verified**, never typed. See [why that matters](#the-diagram-is-generated-not-drawn).

### 2. One self-contained brief per stream

Copy-paste into a fresh session. It has no memory of the conversation that produced it, and it
doesn't need one:

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
back instead of a dozen files parked in this session's context. Research on the cheap fast tier;
review never below the tier that wrote the code.

Stream 2 is adding endpoints in the same area. Different files, but rebase before you open the PR.

## What to do
1. Resolve the acting member on every request that carries a workspace id.
2. Enforce the matrix: only the owner may delete a workspace or change billing; ...
3. Return "not found" — never "forbidden" — to a user with no membership.

## Escalation
This stream needs the deeper mode from the start: it is a "prove nothing leaks" job. ...

## Review
Run the project's review gate at high depth. This stream is access control — security review too.

## Decide with me before implementing
- Phase 3 leaves open whether removing the last owner is blocked or promotes the oldest admin.
- Exact wording of the message a member sees when an action is above their role.

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
Research goes to a subagent on the cheap fast tier; review never below the tier that wrote the code.

## Tests
`npm test` for the package you touched.

## Review
Run the project's review command over the stream's diff. Default depth: high.

## Security review
Required for: billing, authentication, permissions, tokens, public network surface.
```

Full field list: [configuration.md](skills/parallel-streams/references/configuration.md).
Start from [the template](examples/profiles/TEMPLATE.md), or read
[a real one](examples/profiles/real-world.md) from the project this skill was built in.

Without a profile it uses sane defaults and says so once.

---

## What it is, and what it isn't

**It is** a planning step: it reads a plan, works out the dependency structure, and writes the
briefs. The output is text you can read, argue with, and paste.

**It isn't** an orchestrator. It doesn't spawn sessions, doesn't watch them, doesn't merge for you.
That is deliberate — you stay the one who decides how many sessions to open and when. The map tells
you what's possible; the calendar is yours.

**It needs a plan.** Not a perfect one, but something concrete enough to have parts. "Rewrite the
billing system" doesn't split; a plan with phases, steps, and named artifacts does.

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

**Why does the brief tell the session to delegate its reading?**
Because splitting a plan into streams quietly kills the habit. A session with a whole stream to
itself feels parallel enough and stops spawning research subagents — so it reads with its own
hands, and every file it opens stays in a context that is re-sent on every step until the stream
ends. The brief names the behaviour, and names it as permission: no session should have to ask
whether it may look something up. It also fixes the model tier per kind of work, so neighbouring
sessions don't each pick by taste.

**Why must every brief say something about escalation and review?**
Because those are the two lines that were always meant to be there and were always the first to be
dropped. Making them mandatory — including one deliberate repetition in the done-when checklist —
is the fix that stuck.

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
