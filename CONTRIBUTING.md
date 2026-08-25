# Contributing

Thanks for looking. This project is small on purpose, and the bar for adding to it is "would this
have saved someone a day of work".

## The most valuable contribution

**A dependency trap the skill misses.** A real case where two streams looked independent and
weren't — two fields on one model, a config key written by one and read by another, a rename racing
new code. Open an issue with what happened and what the map should have said. Those go into
[dependency-analysis.md](skills/parallel-streams/references/dependency-analysis.md), which is the
part of this skill that actually does the work.

Second most valuable: **a dependency graph the renderer draws badly**. Paste the input notation and
the output you got. Layout bugs are easy to fix once they are reproducible.

## Working on the skill text

`skills/parallel-streams/SKILL.md` loads into context on every use, so every line there is a
recurring token cost. Keep it to the procedure; anything that is reference material belongs in
`references/`, which loads only when it is needed.

Rules of thumb:

- State what to do, not why it matters — unless the "why" is what stops the reader skipping the step.
- One instruction per line. If a step has exceptions, they get their own line.
- No project-specific commands, paths, or tool names in the skill. Those belong in the profile
  (`references/configuration.md`). The skill has to work in a repository neither of us has seen.

## Working on the renderer

```bash
python -m pytest tests
```

`skills/parallel-streams/scripts/render_map.py` is standard library only, Python 3.9+, and it stays
that way — it has to run inside whatever environment the agent happens to be in.

If you change layout behaviour, add a case to `tests/test_render_map.py`. The randomized test
(`test_random_dependency_graphs_render_cleanly`) is the safety net: it renders dozens of generated
graphs and asserts the self-check passes on all of them. If your change breaks a seed, that seed is
a bug report — fix the layout, don't relax the check.

Never weaken `check_diagram` to make a diagram pass. Its entire purpose is to fail loudly on output
that looks fine and is off by one character.

## Working on the coordination channel

```bash
python -m pytest skills/parallel-streams/coordination/tests
```

The channel is PowerShell 7 (`coordination/*.ps1`) with its tests in Python. Those tests run the
real scripts against a board and a stand-in project they create themselves — they assert behaviour,
not that files exist, because every failure mode here is silent: a broken channel looks exactly like
"the neighbour had nothing to say".

Without `pwsh` on PATH the whole suite skips itself and the run still reports green. Check that
PowerShell is installed before trusting a clean result.

Two rules that are not negotiable:

- **Never weaken a test to make the channel pass.** If a test and the tool disagree, one of them is
  wrong — decide which, and fix that one.
- **Anything the installer writes into someone else's project** (their settings, their profile,
  their `scripts/` folder) needs a test that proves the foreign content survived it, byte for byte.

## Pull requests

- Small and focused. A PR that changes the skill text *and* the renderer is two PRs.
- Explain the failure your change prevents. "Cleaner" is not a reason on its own.
- Tests green before you open it.
- English for everything in the repository — code, comments, docs, commit messages.

## Code of conduct

Be straightforward and be kind. Disagree with the idea, not the person. That's the whole policy.
