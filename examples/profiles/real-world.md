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
high. Small mechanical work following an existing pattern: medium. A stream whose diff cannot
change behaviour — documentation, user-facing wording, a version bump, a file moved unchanged —
answers `none` with the reason, and runs the default gate anyway if it does end up touching code.
Never assign the cloud-billed top tier from a template — that one is the owner's decision, not a
default.

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

The same switch has to be turned off by hand, so the brief names the span, not just the trigger:
when the last flagged step is done the session stops, asks the owner to turn the mode off in a line
of its own, and continues after the answer. The owner does not track the mode, and a session that
merely mentions it inside a report leaves it burning over the routine work that follows.

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

---

The two sections below are not hand-written: the channel's installer puts them there, filled in.
They are what makes the briefs carry working commands instead of a description of coordination.

## Coordination

Coordination channel between sessions. Five commands, folded into every task brief.

- **Announce — the first action after the worktree is created, before any edit:**
  `pwsh scripts/wave-board.ps1 -Mode Claim -Stream <stream number> -StreamName "<name from the plan>" -Tasks "<task numbers>" [-Wave <wave>] [-Plan <plan file>]`.
  Without a claim, the stream is indistinguishable from outside as "not opened yet", and findings
  get addressed to it by branch name — which is already different by the middle of the wave. The wave
  and stream number can be left unnamed: the wave is taken from the plan's file name, or, lacking one,
  from work already running nearby or from today's date; the number handed out is the next free one.
  The claim prints back a map of neighbours: whose streams are nearby and which tasks belong to whom.
- **A finding for a live neighbour:**
  `pwsh scripts/wave-board.ps1 -Mode Add -To <wave/stream> -Title "<one line>" -Where "<where the full text is>"`.
  The address is the stream number in the plan (`wave6/3`), not the branch name. `*` — every stream in
  the same wave, `**` — every session in the project.
- **Incoming — handle it, then close:** `pwsh scripts/wave-board.ps1 -Mode Done -Id <id>`, otherwise
  the entry keeps coming back after every context compaction. Decided it is out of scope — close it
  anyway.
- **Release the stream — before saying "done":** `pwsh scripts/wave-board.ps1 -Mode Release`. It
  refuses while the inbox still has something open: it lists exactly what.
- **Whose piece of work this is:** `pwsh scripts/wave-board.ps1 -Mode Streams [-Task <task number>]`.
  Asking is MANDATORY before proposing work outside your own tasks to its owner: they do not know the
  task was planned for another stream, and will say yes.

The board and the claims registry live in the repository's shared service directory: visible to every
worktree at once, require no merging, and never land inside someone else's claim.

## Plans

The folder where wave plans live: `docs/superpowers/plans/`

This is how the guard recognizes that a wave plan is being edited, and nudges toward the board. No
folder named — the nudge guard does not get connected at all, and work proceeds without plans: the
wave is taken from work already running nearby, or from today's date.
