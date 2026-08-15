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
| `## Review` | Review gate before the pull request, and its levels | a review pass before merge |
| `## Security review` | Extra pass, and what triggers it | money, access control, secrets, network boundaries |
| `## Escalation` | What this project calls the deeper mode, and how to ask for it | described generically in the brief |
| `## Merge` | Who merges, squash or rebase, whether CI must be green first | the session merges its own pull request once checks pass |
| `## Conventions` | Branch naming, commit message language and prefixes | the repository's existing style |
| `## Settled decisions` | Path to a registry of decisions already closed | none |
| `## Brief language` | Language to write the briefs in | the language the user is speaking |

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
Default depth: high. Mechanical work following an existing pattern: medium.

## Security review
Additionally required for: billing, authentication, permissions, token handling, anything exposed
to the public network.

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

## Brief language
English.
```

## Notes

- A profile is worth writing once per repository. It is the difference between briefs that read like
  a generic checklist and briefs that read like they came from a teammate.
- Keep it short. Everything here is repeated into every brief; a bloated profile bloats every stream.
- If the repository already documents these things (a contributing guide, a CLAUDE.md, an agents
  file), the profile can point at that file instead of restating it.
