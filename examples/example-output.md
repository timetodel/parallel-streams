# Example output

This is what the skill produces from [example-plan.md](example-plan.md). Nothing here was written
by hand except the briefs' wording — the diagram came out of `render_map.py --check`.

In a real session all of this arrives in the reply itself, ready to copy; it lives in a file here
only so the README can link to a finished shape.

---

Eight streams. The schema is the only true bottleneck: it defines the tables everything else reads,
so nothing starts before it lands. After that the work forks in two independent directions — the
API branch and the permissions branch — which stay apart until the members screen needs both.
Billing and the switcher never touch each other and run side by side. Profile found at
`.parallel-streams.md`.

| Stream | Name | Waits for | Blocks | Escalation | Review |
|---|---|---|---|---|---|
| 1 | Ship the workspace schema and migration | nothing | 2, 3 | none — direct implementation of an approved model | high |
| 2 | Expose workspace and member endpoints | 1 | 4, 5 | none — the envelope and the error shape already exist | high |
| 3 | Enforce workspace roles on every request | 1 | 6, 7 | whole stream — prove no path reaches a workspace without a membership check | high + security |
| 4 | Add the invitation flow | 2 | 6 | steps 2-3 — prove an invitation cannot be replayed or extended | high + security |
| 5 | Add the workspace switcher | 2 | 8 | none — one component against an existing endpoint | medium |
| 6 | Build the members screen | 3, 4 | 8 | none — assembles behaviour both dependencies already provide | medium |
| 7 | Charge per seat | 3 | 8 | steps 1-2 — money math, and the seat rules disagree between phases 5 and 3 | high + security |
| 8 | Migrate existing accounts and accept the release | 5, 6, 7 | — | at the start — find every place that assumes an account owns projects directly | high |

Stream 8 lists only 5, 6, and 7: those already wait for the rest, so naming 1-4 again would add
noise, not information.

```
   S1 ────┬─► S2 ────┬───► S4 ──┬─► S6 ──┬─► S8
          │          │          │        │
          └─► S3 ────┼─┬────────┘        │
                     │ │                 │
                     └─┼─► S5 ───────────┤
                       │                 │
                       └─► S7 ───────────┘
```

---

## Stream 1 — start now

```markdown
# Ship the workspace schema and migration

## Context
We are introducing workspaces: a container holding projects and members with roles. This stream
lays the foundation only — tables, columns, forward migration, rollback. Nothing reads the new
columns yet. Plan: phase 1.

## Dependencies
None — start now. Every other stream waits for this one, so it is the first thing to merge.

## How to work
Create an isolated workspace first — sessions share one project directory, and without isolation
they overwrite each other's checkout. Branch, commits, pull request, and merge are yours to do
without asking. Ask only about the decisions listed below.

Delegate the reading. Sweeps, prior art, consumer lists, broad searches — anything that ends in a
summary rather than an edit — go to a subagent, so the answer comes back as a few lines instead of
a dozen files parked in this session's context.

Delegate the writing too. Each task below goes to a fresh implementation subagent; one reviewing
subagent then reads the finished task and returns everything it found as one list, which goes back
in a single round of fixes. Research, mechanical work and working through a review list run on the
cheap fast tier; new logic on this session's tier; review never below this session's tier.

## What to do
1. Add the `workspaces` table and the `workspace_members` table, with the role column carrying
   `owner`, `admin`, or `member`.
2. Add the workspace reference to `projects`, nullable, so existing rows stay valid.
3. Write the forward migration and the rollback, and test both against a copy of the schema.

## Escalation
Not needed: direct implementation of an approved data model.

## Review
Run the project's review gate at high depth over this stream's changes before the pull request.
The migration runs against production data later, so a reviewer sees it before it is merged.

## Decide with me before implementing
- The plan makes the workspace reference on projects nullable "for now". Nullable forever, or a
  follow-up that makes it required after the backfill? That choice changes what the migration in
  stream 8 has to do.
Plain language, decision first, code after.

This brief can be wrong. See a flaw in it — a step that cannot work, an instruction the code
contradicts, a task already done — say so before you start; objecting costs you nothing, including
when it overturns something I wrote. In every report, mark what you verified by reading or running
it apart from what you are assuming.

## Done when
- [ ] migration applies and rolls back cleanly on a copy of the schema
- [ ] tests pass
- [ ] project gates are green
- [ ] the review above ran and its findings are resolved
- [ ] pull request opened and merged the usual way
```

## Stream 3 — after stream 1 merges

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

Delegate the reading. Sweeps, prior art, consumer lists, broad searches — anything that ends in a
summary rather than an edit — go to a subagent, so the answer comes back as a few lines instead of
a dozen files parked in this session's context.

Delegate the writing too. Each task below goes to a fresh implementation subagent; one reviewing
subagent then reads the finished task and returns everything it found as one list, which goes back
in a single round of fixes. Research, mechanical work and working through a review list run on the
cheap fast tier; new logic on this session's tier; review never below this session's tier.

Stream 2 is adding endpoints in the same area of the codebase. Different files, but rebase before
you open the pull request.

## What to do
1. Resolve the acting member on every request that carries a workspace id.
2. Enforce the matrix: only the owner may delete a workspace or change billing; an admin may invite
   and remove members; a member may read.
3. Return "not found" — never "forbidden" — to a user with no membership, so the existence of a
   workspace is not confirmed to an outsider.

## Escalation
This stream needs the deeper mode from the start and keeps it to the last step: it is a "prove
nothing leaks" job. Every path that reaches a workspace has to be enumerated, and a single unchecked
route is the whole defect. Turn it on and tell me — I will continue. The span is the whole stream,
so the request to turn it off comes with my final summary, in a line of its own.

## Review
Run the project's review gate at high depth over this stream's changes before the pull request.
This stream is access control — run the security review as well.

## Decide with me before implementing
- Phase 3 leaves open whether removing the last owner is blocked or promotes the oldest admin.
  These behave differently for a workspace whose only owner loses access, so I need the answer
  before the check is written.
- Exact wording of the message a member sees when an action is above their role.
Plain language, decision first, code after.

This brief can be wrong. See a flaw in it — a step that cannot work, an instruction the code
contradicts, a task already done — say so before you start; objecting costs you nothing, including
when it overturns something I wrote. In every report, mark what you verified by reading or running
it apart from what you are assuming.

## Done when
- [ ] every workspace-scoped route resolves a membership before the handler runs
- [ ] a non-member receives "not found", verified by a test
- [ ] tests pass
- [ ] project gates are green
- [ ] the review above ran and its findings are resolved
- [ ] pull request opened and merged the usual way
```

## Stream 7 — after stream 3 merges

```markdown
# Charge per seat

## Context
Workspaces are priced per active seat, counted daily, prorated when a member joins mid-cycle. The
owner sees the seat count and the next invoice estimate. Plan: phase 5.

## Dependencies
Wait until stream 3 (role enforcement) is merged into the main branch — the seat count is derived
from memberships, and only the owner may see billing.

## How to work
Create an isolated workspace first — sessions share one project directory, and without isolation
they overwrite each other's checkout. Branch, commits, pull request, and merge are yours to do
without asking. Ask only about the decisions listed below.

Delegate the reading. Sweeps, prior art, consumer lists, broad searches — anything that ends in a
summary rather than an edit — go to a subagent, so the answer comes back as a few lines instead of
a dozen files parked in this session's context.

Delegate the writing too. Each task below goes to a fresh implementation subagent; one reviewing
subagent then reads the finished task and returns everything it found as one list, which goes back
in a single round of fixes. Research, mechanical work and working through a review list run on the
cheap fast tier; new logic on this session's tier; review never below this session's tier.

## What to do
1. Count active seats daily and prorate a seat added mid-cycle.
2. Free a removed member's seat at the end of the cycle, not immediately.
3. Show the seat count and the next invoice estimate on the billing page, owner only.

## Escalation
This stream needs the deeper mode over steps 1 and 2, where the proration math lands: it is money,
and the plan's own rules disagree — phase 5 counts "active seats" while phase 3 defines membership
states that include pending invitations. Turn it on and tell me — I will continue. Once step 2 is
done I stop and ask you to turn it off, in a line of its own: step 3 is a page rendering numbers
that already exist. I go on after you answer.

## Review
Run the project's review gate at high depth over this stream's changes before the pull request.
This stream touches billing — run the security review as well.

## Decide with me before implementing
- Phase 5 leaves open whether a pending invitation occupies a seat. Counting it bills for people
  who never joined; not counting it lets a workspace invite fifty people and pay for none until
  they accept.
- Whether the invoice estimate is shown to admins or to the owner alone.
Plain language, decision first, code after.

This brief can be wrong. See a flaw in it — a step that cannot work, an instruction the code
contradicts, a task already done — say so before you start; objecting costs you nothing, including
when it overturns something I wrote. In every report, mark what you verified by reading or running
it apart from what you are assuming.

## Done when
- [ ] proration verified against a worked example, including a mid-cycle join and removal
- [ ] tests pass
- [ ] project gates are green
- [ ] the review above ran and its findings are resolved
- [ ] pull request opened and merged the usual way
```

---

Streams 2, 4, 5, 6, and 8 follow the same shape and are omitted here for length.

**Open right now: streams 1 only.** Once it merges, streams 2 and 3 start together; the rest
unlock as their column fills.
