# The project profile

The skill produces briefs that another session will execute. Everything project-specific in those
briefs — how sessions are isolated, what a "green build" means, which review runs before a merge —
comes from a profile file, so the skill itself stays portable.

**Location:** `.parallel-streams.md` in the repository root.
**Optional:** without it the defaults below apply, and the summary says so once.

## Fields

| Section | What it sets | Default when absent |
|---|---|---|
| `## Isolation` | How a session gets a workspace nobody else can move | git worktree per stream |
| `## Stream size` | The size a single stream should stay under | one reviewable pull request |
| `## Tests` | Command that proves the stream works | the project's documented test command |
| `## Gates` | Lint, types, formatting, and where heavy checks run | the project's documented checks |
| `## Review` | Review gate before the pull request, its depths, and whether a stream may answer `none` | a review pass before merge, `none` only for a diff that cannot change behaviour |
| `## Security review` | Extra pass, and what triggers it | money, access control, secrets, network boundaries |
| `## Delegation` | What a session hands to a subagent, and the model tier per kind of work | research and each task's implementation to subagents, implementation reviewed by another; cheap tier for research and mechanical tasks, session tier for new logic, never below the session tier for review |
| `## Escalation` | What this project calls the deeper mode, and how to ask for it | described generically in the brief |
| `## Merge` | Who merges, squash or rebase, whether CI must be green first | the session merges its own pull request once checks pass |
| `## Conventions` | Branch naming, commit message language and prefixes | the repository's existing style |
| `## Settled decisions` | Path to a registry of decisions already closed | none |
| `## Brief language` | Language to write the briefs in | the language the user is speaking |
| `## Persistent rules` | The file every session in this project loads on start (`CLAUDE.md`, `AGENTS.md`, …), which must carry the rules that hold even when a task did not come from this skill | absent — the skill says so once in the summary and offers the block in `references/persistent-rules.md` |
| `## Coordination` | The commands running sessions use to reach each other, copied verbatim into blocks 4 and 9 of every brief | absent — the skill says nothing about coordination |
| `## Plans` | The folder where wave plans live, backtick-quoted | absent — the channel's nudge guard stays disconnected |

Unknown sections are ignored, so a profile can carry notes for humans too.

## Example

```markdown
# parallel-streams profile

## Isolation
One git worktree per stream, branched from a fresh origin/main. Sessions share one project
directory — without a worktree they overwrite each other's checkout.

## Stream size
One pull request under 400 lines of diff, or a short chain of small ones in one branch.

## Tests
`npm test` for the package you touched. Integration tests only when the stream changes an endpoint.

## Gates
`npm run lint && npm run typecheck` locally. The full matrix runs in CI — do not run it on the
development machine.

## Review
Run the project's review command over the stream's diff before opening the pull request.
Default depth: high. Mechanical work following an existing pattern: medium. A stream whose diff
cannot change behaviour — docs, translated strings, a version bump — may answer `none` with the
reason; write "every stream carries a gate" here instead if this project never skips.

## Security review
Additionally required for: billing, authentication, permissions, token handling, anything exposed
to the public network.

## Delegation
Reading and research go to a subagent, not into the session's own context. So does the
implementation of each task: one subagent writes it, another reviews it against the task, and the
session accepts the result. Tiers: research — the cheap fast model; a mechanical task, where a
worked example sits next to it and the job is "do the same for this case" — the cheap fast model
too; a task with new logic — whatever the session runs on; review and audit — the strongest model
available, never below the tier the session runs on.

## Escalation
This project calls it "deep mode". The session asks for it in a separate message and waits for a
yes — it never starts a flagged step without it.

## Merge
The session opens and merges its own pull request once CI is green. Squash merge. Never merge into
the trunk directly.

## Conventions
Branch: `feat/<topic>` or `fix/<topic>`. Commits: imperative mood, conventional prefix.

## Settled decisions
`docs/decisions/INDEX.md` — check before proposing an architectural change; rejected options are
recorded there with their reasons.

## Persistent rules
`CLAUDE.md` in the repository root. It carries what must hold however a task arrived — who the
person is and what they do not read, that a subagent's report never reaches them unedited, and the
few moments a session writes at. This profile repeats those rules for the briefs; the two are kept
identical, and `CLAUDE.md` wins if they ever drift.

## Brief language
English.
```

## Coordination and Plans

These two sections come from the coordination channel — the optional machinery in
`coordination/`. Its installer writes both for you, filled in, so they are the one part of the
profile you should not hand-write: the commands in `## Coordination` are copied verbatim into every
brief, and a command that drifted from the tool is worse than no command at all.

Leaving both sections out is a supported way to run the skill — it then behaves exactly as it did
before the channel existed. Installing and removing: `coordination/README.md`.

## Why a profile is not enough on its own

A profile reaches a session through a brief, and a brief reaches exactly the sessions this skill
cut. A task typed by hand into a chat, a plan someone wrote themselves, a session opened tomorrow
for one small fix — none of them ever load the profile. Rules that must survive that gap belong in
the persistent rules file as well, and the plan carries them a third time for the sessions that
open a plan and nothing else. What goes where, the block to paste, and the failure that produced
this section: `persistent-rules.md`.

## Notes

- A profile is worth writing once per repository. It is the difference between briefs that read like
  a generic checklist and briefs that read like they came from a teammate.
- Keep it short. Everything here is repeated into every brief; a bloated profile bloats every stream.
- If the repository already documents these things (a contributing guide, a CLAUDE.md, an agents
  file), the profile can point at that file instead of restating it.
