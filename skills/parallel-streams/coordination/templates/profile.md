# Project profile for splitting a plan into streams

Read by the `parallel-streams` skill and folded into every task brief a session receives.
Keep it short: everything here repeats in every stream.

The skill looks up section headings in English — do not translate them; the content is written in
the project's language. A section that is missing falls back to a shared default value. Full list —
`.claude/skills/parallel-streams/references/configuration.md`.

Below is a template: fill in each section for your project, starting with tests and gates.

## Isolation

A separate worktree per stream, off a fresh `origin/main`. Sessions share one project folder, and the
active branch is a property of that folder: a neighbouring session switching it silently derails this
one's work. A worktree makes that impossible. The session's first action is creating the worktree,
before any edit.

## Stream size

One PR under 400 lines of diff, or a short chain of small PRs inside one branch.

## Tests

<the command that proves the stream works; what runs in full and what runs targeted>

## Gates

<linters, types, formatting: what checks them and where — on the dev machine or on a build runner>

## Review

<what review runs before the PR and at what depth; can it answer "not needed", and under what condition>

## Security review

<what requires an extra check: money, access, secrets, the network boundary>

## Delegation

Reading and researching other code goes to a subagent, not done by hand. Writing code also goes to a
subagent: one writes, another reviews against the task brief, the session accepts the result. Models:
research and mechanical work from an existing pattern — cheap and fast; a task with new logic — the
same tier as the session; review and analysis — no weaker than the session's own model.

## Escalation

<what this project calls its deeper mode and how a session asks for it>

## Merge

The session opens and merges its own PR once checks are green. No direct commits to the main branch.

## Conventions

Branch `feat/<topic>` or `fix/<topic>`. Commits and PRs — imperative mood, conventional prefix. Code
comments in the project's language, identifiers in English.

## Settled decisions

<the file with closed decisions, if one exists; opened before any architectural proposal>

## Brief language

English.
