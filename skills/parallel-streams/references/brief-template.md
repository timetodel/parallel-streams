# The stream brief

A brief is the deliverable. The map explains the split to the person; the brief is what actually
gets pasted into a fresh session and executed.

**Self-contained is the whole requirement.** The session that receives it has none of this
conversation: no plan in context, no map, no reasoning about why the split looks like this. If a
brief only makes sense to someone who read the map, it is not finished.

**Where the brief appears, and under what heading.** It is printed in the reply, in a fenced block,
under a heading that states its launch moment: `Stream 3 — after stream 1 merges`, or
`Stream 1 — start now`. That is the form the person reads and copies from — the window is already
open, and a file would mean opening a second one to reach the same text. The heading answers *when
to open this session*; what the session delivers is the brief's own title, the first line inside the
block. With the launch moment in every heading, the list of headings is itself the launch order.

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

Delegate the reading. Anything that ends in a summary rather than an edit — "find every place
that…", "how is this already done here", "list the consumers of X", a broad search — goes to a
subagent, and you get a few lines back instead of a dozen files parked in this session's context,
which is re-sent on every step until the stream ends. Read directly only when you already know the
file and need one part of it.

Delegate the writing too. Each task under *What to do* goes to an implementation subagent, which
writes the code and its tests in its own context and hands back a summary; one reviewing subagent
then reads the finished task **against this brief** and returns one list: for each point of the
task, whether it was done, done differently, or missed, plus anything it noticed on the way. It
reads the brief, the implementer's report, and only the parts of the code it needs to answer that.
Reading the whole diff hunting for logic bugs is not its job — that is the review gate in block 7,
which is built for it and never sees this brief. That list goes back to an implementation subagent
in a single round, and you accept the result from its report — the reviewer is called once per task,
not once per round of fixes. Start a fresh subagent for each task rather
than waking one you used earlier: waking it re-sends its whole accumulated conversation, so the
fifth wake-up pays for the first four again.
[+ when this stream is two or three small tasks of the same kind: «This stream is short and
 uniform — one reviewing subagent reads the whole stream once at the end, instead of task by
 task.»]
You stay the conductor: you hold the plan, talk to the
person, and run the branch, commits, pull request and merge. Write code with your own hands only for
a change small enough that briefing a subagent would cost more than the edit.

Model tier: research and summarising — the cheap fast one; a mechanical task, where a worked
example sits next to it and the job is "do the same for this case" — the cheap fast one too;
working through a review list against code that already exists — the cheap fast one as well, unless
a finding reopens a design choice; a task with new logic — the tier this session runs on; review,
audit, or diagnosing a failure — no weaker than the tier this session runs on, even when the cheap
tier wrote the code.

What a subagent hands back is raw material for you, not text for me. Ask your subagents for maximum
precision — paths, names, line numbers, exact strings, the more the better — and keep all of it
inside this session. None of it is pasted into our chat: not whole, not summarised, not reworded
with the code names left in. I hear from you, in your own words, and only three times: one line
before each task saying what you are starting (no permission needed, no answer expected); a
question at every fork, self-contained — what we are deciding, how the options differ for me, what
follows from each — with no "see above", because this conversation gets compacted and whatever the
question leaned on disappears; and a summary at the end, plus one after the review gate. No running
status: I can see the work moving without being told. Translating is not hiding — when a decision
needs specifics (how long, what breaks, where the risk is, what you do not know), give them in
full, in plain words. Say the bad parts plainly: what is unfinished, what broke, what you did not
verify. Both summaries end the same way — what changed, in plain words, and then either "nothing
needed from you" or a numbered list of what I do myself. A report that leaves the next move
unclear on either side has not finished the work.

[+ merge-risk note when applicable: «Stream 3 is working in this package too — rebase before you
open the pull request.»]

## What to do
1. ...
2. ...

## Escalation
[«This stream needs <deeper mode>: <trigger and why>. Turn it on and tell me — I will continue.»
 OR «Not needed: <reason>.»]

## Review
[«Run <review gate from the profile> over this stream's changes before the pull request.»
 OR «No review gate: <why this diff cannot change behaviour>. If anything here does end up
 touching executable code, run <the profile's default gate> before the pull request anyway.»]
[+ when applicable: «This stream touches <money / access control / secrets / network boundary> —
run a security review as well.»]

## Decide with me before implementing
- [concrete fork 1, taken from the plan]
- [concrete fork 2, if any]
- [exact user-visible wording, if this stream produces any]
Plain language, decision first, code after.

This brief can be wrong. See a flaw in it — a step that cannot work, an instruction the code
contradicts, a task already done — say so before you start; objecting costs you nothing, including
when it overturns something I wrote. In every report, mark what you verified by reading or running
it apart from what you are assuming.

## Done when
- [ ] tests pass
- [ ] project gates are green
- [ ] the review above ran and its findings are resolved — or it said `none`, and the finished diff
      really did stay non-executable
- [ ] pull request opened and merged the usual way
```

The review item appears twice on purpose — once as a section, once in the done-when list. It is the
step most often skipped, and the checklist is where the session sees it at the exact moment it is
about to declare the work finished.

## Block 4: how to work — and why delegation lives there

The first paragraph is about the session's autonomy: it gets its own workspace, and it runs branch,
commits, pull request, and merge without asking. The other two are about who reads and who writes,
and they are the ones that get left out.

**A stream that reads with its own hands pays for it on every later step.** Files opened inline
stay in the session's context, and that context is re-sent on every turn until the stream ends. A
subagent reads the same files in its own context and hands back the conclusion. On a stream that
runs for hours, this is the difference between a summary and a warehouse.

The reason this line is mandatory rather than obvious: splitting a plan into parallel streams
already satisfies the urge to parallelise, and sessions stop reaching for subagents entirely — they
have a whole stream to themselves and the brief never told them otherwise. That was observed in
practice, which is why the brief now says it out loud, and why the phrasing is permission as well
as instruction: a session should not have to ask whether it may spawn a research subagent.

Delegate: sweeps ("find every place that…"), prior art ("how is this already done here"), consumer
lists, broad searches, cross-file comparisons — anything whose product is a summary.
Do not delegate: a known file you need one part of. A subagent costs more than the read it replaces
when the read was going to be small.

**The writing has to be named too, or it stops being delegated.** Observed 2026-08-20: a wave whose
briefs named only research kept its research subagents and lost its implementers entirely — every
stream wrote hundreds of lines by hand, and all of that code travelled through the chat in front of
the person who does not read code, instead of staying inside a subagent's context. Naming only half
of the delegation reads as permission for the other half. Delegated writing also buys a second pair
reader on the work: the implementer, the reviewer and the session accepting the result are three
different subagents, and the reviewer reads the brief without having formed the implementer's
picture of what it asked for.

Delegate: each task in the brief, as one implementation subagent plus a separate reviewing subagent,
and every round of fixes after the review. Do not delegate: an edit small enough that writing the
brief would take longer than making it.

Model tiers, unless the profile says otherwise:

| Subagent work | Tier |
|---|---|
| Research, reading, sweeps, summarising | the cheap fast one |
| Mechanical task: a worked example sits next to it, the job is "do the same for this case" | the cheap fast one |
| Task with new logic: no example to copy, a choice between options, or money, permissions, or schema involved | the tier this session runs on |
| Review, audit, diagnosing someone else's failure | no weaker than the tier this session runs on, even when the cheap tier wrote the code |

Fix the tiers in writing rather than asking per case. Asking costs an interruption every time,
including for two-minute research, and letting each session pick by taste is exactly how neighbours
end up running the same class of work at three different tiers.

When unsure whether a task is mechanical, take the session tier: quality outranks the saving.
Never review on the cheap tier — a cheap implementer is safe precisely because a stronger model
checks the work.

**Delegation has a shape, and the wrong shape costs more than the work it replaces.** Observed
2026-08-26: a wave spent most of its budget on machinery rather than on code. The reviewer was
called again after every round of fixes instead of once per finished task, which roughly doubled the
count on its own; the same subagents were woken five times each, re-sending their entire accumulated
conversation every time; rounds of fixes ran on the session's own tier although working through a
review list is mechanical; and findings went back one at a time, each opening its own round. None of
that was written down anywhere, so every session invented its own shape — and the expensive shape is
the intuitive one. Hence the three rules above: one reviewer per finished task, a fresh subagent per
task, the whole list of fixes in one round on the cheap tier.

**A short, uniform stream is reviewed once at the end.** Two or three small tasks of the same kind
get read in one sitting anyway, and a finding in the first of them has almost nothing built on top
of it yet. Reviewing task by task earns its cost on a long stream, where the later tasks stand on
the earlier ones and a late finding means redoing whatever was built on the mistake — and on a mixed
stream, where a single reviewer at the end would have to hold several unrelated kinds of work at
once. Judge by the stream, and write the answer into the brief: left unsaid, the brief reads as
task-by-task for a two-task stream too.

**The reviewing subagent checks the brief, not the logic.** Two subagents of the same model do not
catch each other's blind spots: same engine, same reasoning, one pass, and nothing weighs the
reviewer's findings before they turn into another round of fixes. What the reviewer does have is a
position the implementer cannot take — it never formed a picture of what the brief meant, so it
reads the words that are there instead of the intent it remembers. That makes it good at exactly one
thing: whether the finished work matches what was asked. Point missed, point solved differently, a
test that asserts nothing, a fork in block 8 decided on its own. Hunting logic bugs it is both weak
and expensive — it would have to read the whole diff, which costs what the gate costs, and its noise
buys real rounds of fixes. Keeping it to the brief is also what makes it cheap: brief, report, and
only the code it needs to answer. And it answers the one question the gate cannot — the gate sees
the diff from outside and has never read the brief, so "they forgot the third point" is a sentence
only this reviewer will ever say.

**Delegation is not escalation.** A research subagent is one worker answering one question, started
by the session on its own. Escalation (block 6) is a deeper, more expensive mode the person has to
turn on. Keeping them separate is what stops a session from asking for the expensive mode to do a
routine lookup.

**Delegating the reading does not decide what the person reads.** Observed 2026-08-27: a stream
sent a research subagent, got back exactly what it should have — file paths, line numbers, the
exact strings in the code — and pasted the whole thing into the chat of a person who does not read
code. It had broken no rule: the briefs said who does the reading and who does the writing, and
said nothing at all about what leaves the session. Naming the delegation without naming its output
reads as "show everything you receive".

Two halves, and both are needed. **Ask subagents for more precision, not less** — their report is
the session's working material, and trimming it at the source makes worse decisions while the
person still gets a retelling. **Translate at the exit** instead: the person hears the session's
own words, never a forwarded report, because only the session knows what question is open, what
decision is on the table, and what is noise right now.

Translating is not hiding, and the brief has to say so, or a session hearing "no details" starts
smoothing over the bad news. Specifics a decision needs — how long, what breaks, where the risk
sits, what is still unknown — are given in full, in plain words. So is everything that went wrong.

**Three moments, and nothing between them.** A line per task before it starts is the only point
where the person can say "not that" before an hour is spent — the cost of the line is one sentence,
and it replaces the permission question nobody wanted. A fork question has to stand on its own,
because conversations get compacted and everything the question referred to is gone by the time it
is read. A summary at the end, and one after the review gate, both ending in actions rather than
findings: what changed, then either nothing needed, or a numbered list of what the person does.
Running status in between is what the person can already see happening.

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

The line is mandatory; the gate is not. A stream whose diff cannot change behaviour answers `none`,
and that is an answer. Silence is the defect — a session deciding by feel at the moment it is tired
and wants to merge.

**Depth follows what the stream touches, not how hard the work felt.** Authors rate their own work
as simple with striking consistency; what the diff touches is the part both sides can see.

| What the stream touches | Depth |
|---|---|
| Money, access control, secrets, personal data, anything reachable from outside | the profile's deepest gate, plus a security review |
| A change that spreads — a sweep, a migration, a rename — or a new contract other streams will call | deep |
| One area, following a pattern that already exists in the repository | the profile's lighter gate |
| A diff that cannot change behaviour: prose, translated strings, comments, a version number, a file moved unchanged | `none`, with the reason |

**`none` describes the diff, not the intention.** A stream planned as documentation-only that ends
up editing a function is a code stream that skipped its review — and the plan is never the thing
that decides, because the session discovers what it actually had to touch hours after the brief was
written. That is why every `none` is paired with the fallback line: touch executable code, and the
profile's default gate runs before the pull request. Written into the brief it costs one sentence;
left out, `none` becomes the answer every ambiguous stream reaches for.

**A profile may forbid `none` outright** — regulated code, an audit trail, a team that reviews
everything on principle. Then the last row of the table does not apply and every stream carries a
gate.

Do not assign the heaviest, most expensive tier by default either. That one belongs to a deliberate
decision by the person paying for it, not to a template.

## Block 8: forks

A fork is a place where the plan does not determine the answer: two viable implementations, a format
choice, a question the plan itself left open. Also every piece of wording a human will see — error
text, button label, log line — shown to the person *before* it reaches the code.

Generic advice is not a fork. "Discuss the architecture with me" is noise; "the plan leaves open
whether the retry counter is per-item or per-batch — that changes the storage shape" is a fork.

If a stream genuinely has none, write "No open forks — the plan settles everything in this stream."

**The brief itself is one of the things that can be wrong.** The list above raises the forks the
plan left open; the paragraph under it raises defects in the brief, which is a different thing and
does not happen on its own. A brief reads as settled — it was written by whoever is paying for the
work, it arrives before any of it, and disagreeing looks like refusing to start. Observed
2026-08-24: one session that had been given the permission explicitly stopped four wrong conclusions
in a single evening, two of them instructions from the person who wrote its brief.

Three parts, and dropping any one of them ends it:

- **The invitation arrives before the work.** Afterwards the session has sunk cost in the wrong
  path, and an objection means throwing away what it just built.
- **The promise names the expensive case.** "Say if something is unclear" reads as covering typos.
  What has to be free is the objection that overturns an instruction — that is the one nobody
  raises unpaid.
- **Reports separate verified from assumed.** Otherwise neither side can tell which claims carry
  weight, and an objection is just an opinion against an instruction. Marking the assumption is also
  what makes a session catch its own error before building on it.

One refusal undoes all three. Answering "just do it" to a well-founded objection ends the practice:
the next one never arrives, and its absence is silent. The signal that it is working is errors
caught *before* the action — an error caught afterwards always reads as an excuse, and often is one.
