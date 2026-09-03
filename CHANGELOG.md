# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.10.0] — 2026-09-04

### Fixed

- **A claim can no longer erase a live stream.** Announcing a *different* stream from a folder that
  already holds an open claim is refused **before a single byte is written**, and so is announcing
  an address that an open claim of *another* folder already leads. Both refusals name the rival's
  state, whether its folder is still on disk, and every way out as a whole line you can copy. Three
  observed incidents drove this: on 2026-08-31 a stream vanished from the board when a second stream
  was announced from the same shared folder — its tasks looked untaken, findings were never
  addressed to it, and its own release closed somebody else's record; a session that moved to its
  own worktree left the address doubled, so which session a finding reached was decided by the order
  of a directory listing; and a bare re-announcement moved a stream into a wave named after today's
  date.

- **Moving a stream to another folder is a first-class move, not a collision.** Announce the same
  address from the new folder with `-TakeOver`: the address, the inbox and the branch names the
  stream is remembered by all travel with it, while the abandoned record in the old folder goes
  quiet — it stops leading the address, stops receiving findings, and says so when that session
  tries to release it. The rival's claim file is never touched, so an older copy of the kit in a
  neighbouring worktree still reads it exactly as it did yesterday.

- **A move survives the folder being reused.** Each claim now remembers the moves it made, so a
  folder taken by the next stream no longer resurrects the record it had superseded. A chain of
  moves A→B→C leaves exactly one leader, and the session that lost the address is told where the
  address really went — the *end* of the chain, not the middle folder that has since moved on.

- **A succession edge acts by time, not by topology.** The edge does not apply once it is *proven*
  that the loser's claim began after the moment of the move. This closes the return ring (two
  sessions handing an address back and forth), the chain above, and the case where an address is
  honestly released and the original folder announces on it again.

- **A claim that is superseded the moment it is written says so, and exits non-zero.** A plain
  success would read as "announced, working" to a session that does not exist from the outside.

- **Every printed way out works.** The take-over key is only suggested where there is somebody to
  take the address from; where no record leads it any more, the session is told to announce under a
  free number instead.

### Changed

- **The rule is stated once, everywhere:** one open claim per folder and one leading record per
  address. The brief template, the profile templates, the profile field descriptions and the
  channel's README all carry that wording, together with the sentence that a move keeps the stream.

- **The project-side fix this kit briefly carried in one repository is removed.** It refused a bare
  re-announcement of the same stream and offered a force key that overwrote another stream's claim —
  the overwriting session inherited the victim's branch names and its mail. Both are gone: a bare
  re-announcement passes and keeps its address, and no force key exists anywhere in the tool.

## [1.9.1] — 2026-08-31

### Fixed

- **Escalation is now a span, not a trigger.** The map's escalation column names where the deeper
  mode starts *and* where it ends — "steps 3-4", "whole stream" — so the reader knows in advance how
  long the expensive mode runs.
- **Every brief that asks for the mode also asks for it back off.** Block 6 carries two requests:
  turn it on before the span, and — the moment the last flagged step is done — **stop**, ask in a
  line of its own for the mode to be turned off, and continue only after the answer. A stream that
  needs the mode from first step to last puts the off request at the top of its final summary.
- **Off is a stop, not a remark.** The brief template, the profile field, the profile templates and
  the self-check all say it: mentioning the mode inside a report does not end it. Both escalation
  requests are named as fork questions, so a session honouring "write at three moments" does not
  swallow them as status.

### Why

Observed 2026-08-31, in the project this skill was built in. The person turned the expensive mode on
when a stream asked, the stream did the flagged work — and then kept running in that mode through
sequential implementation, mechanical edits and the merge chore, because nothing had ever told it to
ask for the mode back off. No rule was broken: every brief said how to ask for the mode, none said a
word about ending it. The switch is manual in both directions, and only one of them was written
down.

The asymmetry decides the default. A session stopped for an answer spends nothing; a session
carrying on in the deeper mode spends on every step it takes. So the rule is stop, and the person
never has to track the mode themselves.

## [1.9.0] — 2026-08-31

### Added

- **Rules now have somewhere to live that a brief cannot reach.** New profile section
  `## Persistent rules` names the file every session in the project loads on start — `CLAUDE.md`,
  `AGENTS.md`, whatever the harness reads. Before writing briefs the skill checks that file exists,
  and says in one line when it does not. It never writes there uninvited: it offers a ready block,
  `references/persistent-rules.md`, and waits.
- **The plan is treated as a rules carrier too.** When the plan sits in the folder named by
  `## Plans`, the skill checks it for a section holding the rules that apply to every stream, and
  offers to add one when it is missing. A brief is pasted once; the plan is what a session opens for
  itself, and a task re-cut by hand from the plan carries only what the plan carries.
- **Installation asks about it** (new step 3b), and the closing report tells the user where the
  rules that must survive outside this skill belong.
- **A section for the symptom**, in the README and in the new reference: the skill is current, the
  profile is current, both state the rule plainly — and sessions break it anyway. The answer is
  never the skill's version; it is what those particular sessions actually read.

- **A full Russian translation**, in `localization/ru/` — the skill, all four references, the brief
  template and the profile template. Install that directory instead of the English one for projects
  whose sessions talk to their owner in Russian. Profile section headings stay English in both, since
  the skill looks them up by name; the coordination channel there is Russian by origin rather than
  translated, and the diagram renderer is shared.

### Why

Observed 2026-08-31, in the project this skill was built in. 1.8.0 had settled that a subagent's
report is never forwarded to the person, and both the skill and the profile said so. A wave of nine
sessions pasted their subagents' reports to a person who does not read code anyway — file paths,
tables of dependency files, lines of configuration. The wave plan had been written by hand and
carried no rules section; the repository had no persistent rules file at all. Nothing was disobeying
a rule: the rule existed in two places, and neither was a place those sessions could see. Three
carriers, one text — the persistent file, the plan, the profile — is what closes that.

## [1.8.0] — 2026-08-28

### Added

- **Every brief now settles what reaches the person, not only who does the reading and writing.**
  A subagent's report is raw material for the session and is never forwarded: not whole, not
  summarised, not reworded with the code names left in. The other half matters just as much —
  subagents are asked for *more* precision, not less, because trimming at the source makes worse
  decisions while the person still gets a retelling. Observed 2026-08-27: a stream sent a research
  subagent, got back exactly what it should have — paths, line numbers, exact strings — and pasted
  all of it into the chat of a person who does not read code. It had broken no rule: the briefs
  named the delegation and said nothing about its output, which reads as "show everything you
  receive".
- **Three moments, and nothing between them.** One line per task before it starts (no permission
  asked, no answer expected) is the only point where the person can say "not that" before an hour
  is spent. A fork question must stand on its own — conversations get compacted, and whatever the
  question leaned on is gone by the time it is read. A summary at the end and one after the review
  gate, both ending in actions rather than findings: what changed, then either nothing needed or a
  numbered list of what the person does. No running status: the work is already visible moving.
- **Translating is not hiding**, said out loud in the brief, because a session told "no details"
  starts smoothing over bad news. Specifics a decision needs — how long, what breaks, where the
  risk sits, what is still unknown — are given in full, in plain words, and so is everything that
  went wrong.

### Fixed

- The version badge in the README had been left at 1.5.0 for three releases.

## [1.7.0] — 2026-08-26

### Changed

- **The reviewing subagent now reads the finished task against the brief instead of hunting for
  logic bugs.** Two subagents of the same model do not catch each other's blind spots: same engine,
  same reasoning, one pass, and nothing weighs the reviewer's findings before they turn into another
  round of fixes. What the reviewer does have is a position the implementer cannot take — it never
  formed a picture of what the brief meant, so it reads the words that are there instead of the
  intent it remembers. That makes it good at one thing: whether the finished work matches what was
  asked — point missed, point solved differently, a test that asserts nothing, a fork in block 8
  decided unilaterally. It is also the one question the review gate can never answer, because the
  gate sees the diff from outside and has never read the brief. Keeping the reviewer to the brief is
  what makes it cheap, too: brief, implementer's report, and only the code needed to answer, instead
  of the whole diff at the gate's price.

## [1.6.1] — 2026-08-26

### Changed

- **A short, uniform stream is now reviewed once at the end instead of task by task.** 1.6.0 named
  one reviewer per finished task and left it at that, so a stream of two small tasks of the same
  kind paid for the same machinery as a stream of eight. Two or three small tasks of one kind get
  read in one sitting anyway, and a finding in the first has almost nothing built on top of it yet;
  task-by-task review earns its cost on a long stream, where later tasks stand on earlier ones and a
  late finding means redoing what was built on the mistake, and on a mixed stream, where one
  reviewer at the end would have to hold several unrelated kinds of work at once. The brief now
  carries the answer explicitly — left unsaid, it reads as task-by-task for a two-task stream too.

## [1.6.0] — 2026-08-26

### Added

- **Every brief now invites the session to object to the brief itself, before it starts.** A brief
  reads as settled: written by whoever is paying, delivered before any of the work, and disagreeing
  with it looks like refusing to start — so a session that spots a step which cannot work builds
  around it silently. Block 8 now carries a standing invitation to say so first, explicitly free of
  cost even when the objection overturns an instruction, together with the requirement to mark what
  was verified apart from what is assumed in every report. Observed 2026-08-24: one session given
  that permission stopped four wrong conclusions in a single evening, two of them instructions from
  the person who wrote its brief. All three parts are load-bearing, and one refusal to hear an
  objection ends the practice silently.

### Changed

- **Delegation now has a stated shape, because the intuitive one is the expensive one.** The brief
  named who writes and who reviews but never how many of them, and sessions filled the gap by taste.
  Observed 2026-08-26: a wave spent most of its budget on machinery rather than code — the reviewer
  re-called after every round of fixes instead of once per finished task, the same subagents woken
  five times each with their whole accumulated conversation re-sent every time, and findings handed
  back one at a time so each opened its own round. The brief now states all three: one reviewer per
  finished task, a fresh subagent per task, the review's findings back in a single round.
- **Working through a review list is a cheap-tier job.** The tier table covered new logic and
  mechanical tasks but said nothing about fixing what a review found, so rounds of fixes inherited
  the session's own tier — the most expensive way to apply a list of corrections to code that
  already exists. Unless a finding reopens a design choice, that work is mechanical.
- **The worked example and the README brief match the current skill again.** Both still showed the
  model tiers from 1.1.0, which 1.4.1 had replaced.

## [1.5.0] — 2026-08-25

### Added

- **A coordination channel, shipped inside the skill and off by default.** Splitting a plan leaves
  a second problem untouched: once the sessions are running they are blind to each other, and a
  plan edited after they started never reaches them — each read it once, at its own start. A
  finding one session makes for another had no way to arrive, and "who owns this task?" had no one
  to answer it. `coordination/` now carries the machinery: a board, a claim registry, two hooks, an
  installer and its tests. One command installs it into a project; a profile with no
  `## Coordination` section leaves the skill behaving exactly as before.
- **Five commands the skill writes into every brief** when the profile declares the channel:
  announce the stream on start, send a finding to a live neighbour, close what arrived, release the
  stream before saying done, and ask who owns a task before proposing work outside your own.
  Addresses are stream numbers from the plan (`wave6/3`) — branch names drift within a wave, the
  number does not.
- **The board and the claims live in the repository's shared internal directory**, so every
  worktree sees them at once, they belong to no branch, and they never need merging.

### Changed

- **A project with no waves and no plan file is supported.** The wave used to be required, and a
  session working outside one had nothing to write its claim into. Now the wave comes from the
  plan's file name; failing that, the session joins work already running next to it; failing that,
  a wave is opened under today's date. The stream number can be left out too — the next free one is
  issued.

### Fixed

- **Two sessions starting in the same second no longer take the same stream number.** Found by a
  test run on a build box, along with six more defects of the same family — each silent, each
  ending with a finding delivered to the wrong session while both sides believed all was well.

### Known limitation

- **One branch name can be worn by two streams.** A session that renamed its branch and did not
  announce again keeps the old name in its claim, and that name counts as worn. If a neighbour then
  takes the freed name, a finding addressed by it reaches both. Addressing by `wave/stream` is
  never ambiguous; the full account, and where the fix belongs, is in `coordination/README.md`.

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
