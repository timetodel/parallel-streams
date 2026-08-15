# parallel-streams profile — a real one

This is the profile the skill was built against: a commercial desktop-plus-cloud product where
twenty-plus sessions regularly run at the same time against one repository. It is included
unedited in shape, because a template shows you the fields and a real profile shows you the tone.

---

## Isolation
One git worktree per stream, branched from a fresh `origin/main`. Every session shares one project
directory, and the active branch is a property of that directory — a neighbouring session
switching branches will silently pull the rug out from under this one. A worktree makes that
impossible.

## Stream size
One pull request under 400 lines of diff, or a short chain of small ones inside one branch.
Anything larger does not get a real review.

## Tests
The package's own test suite. Heavy suites — anything needing a GPU, a live database, or the
vendor platform — run on the build box, never on the development machine, which is busy and single.

## Gates
Lint, types, and format for the languages the stream touched. The full set runs on the build box
via the project's gate script; a red gate blocks the merge exactly like red CI.

## Review
Run the project's review command over the stream's diff before the pull request. Default depth:
high. Small mechanical work following an existing pattern: medium. Never assign the cloud-billed
top tier from a template — that one is the owner's decision, not a default.

## Security review
Additionally required for: billing and pricing, licence enforcement, access control, tokens and
key storage, anything crossing the network boundary, and anything listed in the project's registry
of settled security decisions.

## Delegation
Anything that ends in a summary rather than an edit goes to a subagent: sweeps, prior art, consumer
lists, broad searches. A stream runs for hours, and files read inline sit in its context for all of
them. Tiers are fixed, not chosen per case: research and summarising on the cheap fast model;
implementation delegated against an existing pattern on whatever the session itself runs; review,
audit, and diagnosing a failure never below the model that wrote the code. A session that wants to
deviate asks the owner.

## Escalation
The project calls it "deep mode", and it is a session-level switch the owner turns on. A brief that
needs it says so in its own line, up front, and the session waits for a yes rather than starting
the flagged step without it. Triggers: sweeps where missing one site is the defect; proofs that
nothing leaks; forks between architectures with no obviously right answer; reconnaissance into an
unfamiliar area.

## Merge
The session opens and merges its own pull request once CI is green. Squash merge. Never commit to
the trunk directly.

## Conventions
Branch: `feat/<topic>` or `fix/<topic>`. Commits: imperative mood, conventional prefix, written in
the project's working language.

## Settled decisions
`docs/decisions/INDEX.md` — one line per closed fork, with the rejected option and why it was
rejected. Check it before proposing an architectural change or a simplification that looks
obviously right; most entries exist precisely because the rejected option looked obviously right.

## Brief language
The language the owner is speaking in that session.
