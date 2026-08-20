# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.4.1] — 2026-08-20

### Changed

- **The implementation tier splits in two: mechanical work goes cheap, new logic stays on the
  session's tier.** Once every task started going to an implementation subagent, a single
  "implementation — the tier this session runs on" row meant a wave of ten streams ran even its
  rename-a-field tasks on the most expensive tier. A task with a worked example next to it, where
  the job is "do the same for this case", now goes to the cheap fast tier; a task with new logic —
  no example to copy, a choice between options, or money, permissions, or schema involved — stays on
  the session's tier, and so does anything the session is unsure about.
- **Review is pinned to the session's tier, not to whoever wrote the code.** With a cheap
  implementer allowed, "no weaker than the tier that wrote the code" would have let a cheap review
  follow cheap work. A cheap implementer is safe precisely because a stronger model checks it.

## [1.4.0] — 2026-08-20

### Changed

- **The brief now delegates the writing, not only the reading.** Block 4 named research subagents
  and said nothing about implementation, and sessions read that as permission to write everything
  themselves: an entire wave kept its research subagents and ran zero implementers, with hundreds of
  hand-written lines passing through the chat in front of a person who does not read code. Each task
  in a brief now goes to an implementation subagent with a separate subagent reviewing it, rounds of
  fixes included; the session conducts, talks to the person, and owns the branch and the merge.
- **"Do not delegate an edit" is gone from the reference.** It was the line that made hand-writing
  look correct. The exception that remains is a change small enough that briefing a subagent would
  cost more than making it.

## [1.3.0] — 2026-08-17

### Changed

- **The map and the briefs are printed in the reply, not parked in a file.** The output format said
  "one copy-paste block per stream" and left where unstated, so sessions kept writing the briefs to
  a document and pointing at it — which turns a copy into: open the file, find the stream, select
  the block. The format now states it: the reply *is* the deliverable, and a file is written only
  when the person asks for one, in addition rather than instead.
- **Every brief's heading carries its launch moment** — `Stream 1 — start now`,
  `Stream 3 — after stream 1 merges`, `Stream 8 — after streams 5, 6, 7 merge`. The heading used to
  repeat the stream's name, which the brief's own title says one line later; what the reader
  actually needs there is which sessions can be opened today. With the moment in every heading, the
  list of headings is the launch order.

## [1.2.0] — 2026-08-16

### Changed

- **Review depth is now read off the stream, not off the author's confidence.** The old rule said
  "default high, lighter for mechanical work", which asked each session to rate the difficulty of
  its own work — a judgement authors make generously and consistently. Depth now comes from what
  the stream touches: money, access control, secrets, personal data or an outside-reachable surface
  at the deepest gate plus a security review; a change that spreads, or a new contract other
  streams call, deep; one area following an existing pattern, lighter.
- **A stream whose diff cannot change behaviour may answer `none`.** Documentation, translated
  strings, comments, a version bump, a file moved unchanged — running a gate over those buys
  nothing, and the previous wording had no way to say so: the lightest thing a brief could write
  was still a gate. The mandatory part was never the gate, it is the *line* — silence is what the
  rule exists to prevent.
- **Every `none` carries a fallback.** `none` describes the diff, not the intention, and a stream
  planned as documentation-only that ends up editing a function is a code stream that skipped its
  review. The brief now states the return condition in the same block: touch executable code and
  the profile's default gate runs before the pull request. The done-when checklist checks the claim
  rather than the plan.

### Added

- Profiles can **forbid `none`** in `## Review` — regulated code, audit trails, teams that review
  everything on principle. Then every stream carries a gate and the skip row never applies.

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
