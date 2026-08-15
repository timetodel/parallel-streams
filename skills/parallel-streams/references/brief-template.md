# The stream brief

A brief is the deliverable. The map explains the split to the person; the brief is what actually
gets pasted into a fresh session and executed.

**Self-contained is the whole requirement.** The session that receives it has none of this
conversation: no plan in context, no map, no reasoning about why the split looks like this. If a
brief only makes sense to someone who read the map, it is not finished.

## Template

```markdown
# [Verb + object — what this stream delivers]

## Context
[3-5 lines: what this is, why it exists, which plan section it comes from]

## Dependencies
[«None — start now» OR «Wait until stream N is merged into the main branch»]

## How to work
Create an isolated workspace first — sessions share one project directory, and without isolation
they overwrite each other's checkout. Branch, commits, pull request, and merge are yours to do
without asking. Ask only about the decisions listed below.

[+ merge-risk note when applicable: «Stream 3 is working in this package too — rebase before you
open the pull request.»]

## What to do
1. ...
2. ...

## Escalation
[«This stream needs <deeper mode>: <trigger and why>. Turn it on and tell me — I will continue.»
 OR «Not needed: <reason>.»]

## Review
Run <review gate from the profile> over this stream's changes before the pull request.
[+ when applicable: «This stream touches <money / access control / secrets / network boundary> —
run a security review as well.»]

## Decide with me before implementing
- [concrete fork 1, taken from the plan]
- [concrete fork 2, if any]
- [exact user-visible wording, if this stream produces any]
Plain language, decision first, code after.

## Done when
- [ ] tests pass
- [ ] project gates are green
- [ ] the review above ran and its findings are resolved
- [ ] pull request opened and merged the usual way
```

The review item appears twice on purpose — once as a section, once in the done-when list. It is the
step most often skipped, and the checklist is where the session sees it at the exact moment it is
about to declare the work finished.

## Block 6: escalation

Every brief states this explicitly. Silence is a defect — the self-check exists to catch it.

Escalate when the stream matches one of these:

- **"Find every place."** A sweep across subsystems, a migration, a broad rename — anywhere missing
  one site *is* the defect.
- **"Prove nothing leaks."** Access control, permissions, isolation, money, anything about to be
  exposed outward or shipped to production. You cannot audit your own work with the same eyes that
  wrote it.
- **"Pick between architectures that are not obviously ranked."** A fork with no known-good answer,
  where independent attempts and a comparison beat the first idea.
- **Unfamiliar territory**, where you do not yet know where to look.

None of those — say so, with the reason: "Not needed: sequential implementation against an approved
plan."

The wording differs by place:

- **in the table** — *when* it kicks in: "before step 3 — sweep for every call site", so the reader
  knows in advance when they will be asked;
- **in the brief** — a *request* addressed to the person, from the session that will do the work.

The mode belongs to the executing session, not to the one writing the briefs. The brief must tell
that session to ask up front and wait, rather than starting the flagged step without it.

## Block 7: review

- Default: run the project's review gate over this stream's changes before the pull request.
- Small, mechanical work following an existing pattern may use a lighter gate — but the line is
  still written explicitly.
- Money, access control, tokens, secrets, network boundaries, or anything in the project's list of
  settled security decisions: add a security review next to it.
- Do not assign the heaviest, most expensive review tier by default. It belongs to a deliberate
  decision by the person paying for it, not to a template.

## Block 8: forks

A fork is a place where the plan does not determine the answer: two viable implementations, a format
choice, a question the plan itself left open. Also every piece of wording a human will see — error
text, button label, log line — shown to the person *before* it reaches the code.

Generic advice is not a fork. "Discuss the architecture with me" is noise; "the plan leaves open
whether the retry counter is per-item or per-batch — that changes the storage shape" is a fork.

If a stream genuinely has none, write "No open forks — the plan settles everything in this stream."
