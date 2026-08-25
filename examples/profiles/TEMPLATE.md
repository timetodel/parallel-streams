# parallel-streams profile

Copy this to `.parallel-streams.md` in your repository root and fill it in. Delete what does not
apply — every line here is repeated into every brief, so keep it short.

## Isolation
<!-- How a session gets a workspace nobody else can move out from under it.
     Example: one git worktree per stream, branched from a fresh origin/main. -->

## Stream size
<!-- Example: one pull request under 400 lines of diff. -->

## Tests
<!-- The command that proves a stream works. Example: `npm test` for the package you touched. -->

## Gates
<!-- Lint, types, formatting, and where the heavy checks run.
     Example: `make lint typecheck` locally; the full matrix runs in CI, not on your machine. -->

## Review
<!-- The review gate before the pull request, its depths, and whether a stream may answer `none`.
     Example: default depth high; mechanical work following an existing pattern, lighter; a diff
     that cannot change behaviour (docs, translated strings, a version bump), `none` with the
     reason. Reviewing everything on principle? Say so here and `none` is never written. -->

## Security review
<!-- The extra pass and what triggers it.
     Example: billing, authentication, permissions, tokens, anything on the public network. -->

## Delegation
<!-- What a session hands to a subagent instead of reading itself, and the model tier per kind of
     work. Example: research and sweeps go to a subagent on the cheap fast tier; delegated
     implementation runs on the session's own tier; review never below the tier that wrote the
     code. Fix the tiers here so neighbouring sessions don't each pick by taste. -->

## Escalation
<!-- What this project calls the deeper, more expensive mode, and how a session asks for it.
     Example: the session asks in a separate message and waits — it never starts a flagged step
     without it. -->

## Merge
<!-- Who merges, squash or rebase, whether CI must be green first. -->

## Conventions
<!-- Branch naming, commit message language and prefixes. -->

## Settled decisions
<!-- Path to a registry of decisions already closed, if you keep one. -->

## Brief language
<!-- The language the briefs should be written in. -->

## Coordination
<!-- Only if the coordination channel is installed (skills/parallel-streams/coordination).
     The installer writes this section for you, filled in with the five commands. Leave the
     section out entirely and the skill says nothing about coordination — that is a supported
     way to run it. -->

## Plans
<!-- The folder where wave plans live, backtick-quoted, e.g. `docs/plans/`.
     The channel's nudge guard watches that folder; without the line it stays disconnected. -->
