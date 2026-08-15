# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-08-15

### Added

- **Delegation is now part of every brief.** Splitting a plan into streams turned out to kill the
  habit of spawning research subagents: a session with a whole stream to itself feels parallel
  enough, reads with its own hands, and carries every opened file in a context that is re-sent on
  every step until the stream ends. The "How to work" block now names the behaviour — sweeps, prior
  art, consumer lists and broad searches go to a subagent — and names it as permission, so no
  session has to ask whether it may look something up.
- **Model tiers are fixed in writing, not chosen per case** — research on the cheap fast tier,
  delegated implementation on the session's own, review never below the tier that wrote the code.
  Overridable through the new `## Delegation` profile section.
- Self-check gained the matching line, so a brief without the delegation rule is a defect.

### Changed

- **Ships as a skill, not as a plugin.** The plugin manifests are gone and the install docs lead
  with the project directory (`<repo>/.claude/skills/parallel-streams/`). A plugin installs per user
  and then follows you into every project you open, while this skill is only useful where plans are
  big enough to split; living in the repository also means your teammates get the version you
  committed. Updating is copying the directory over the old one — there is nothing to unregister.
  Anyone who installed the 1.0.0 plugin: remove it, copy the directory in instead.

## [1.0.0] — 2026-08-15

First public release.

### Added

- **The skill** — splits an approved plan into parallel work streams: dependency map plus one
  self-contained brief per stream.
- **Generated diagrams** — `render_map.py` computes every character position and verifies its own
  output with `--check`. Standard library only, Python 3.9+.
- **Dependency analysis reference** — what counts as a cross-stream dependency, what only looks
  like one, the traps that read as independent, and how to cut a cycle.
- **Brief template** — fixed block order, with escalation and review mandatory in every brief.
- **Project profiles** — `.parallel-streams.md` carries your isolation model, test command, review
  gate, and merge policy into every brief. Template and a real-world profile included.
- **Three install paths** — ask your agent (see `INSTALL.md`), the plugin marketplace, or a
  directory copy.
- **Worked example** — an approved plan and the full split produced from it.
