# The persistent rules file

A brief written by this skill reaches exactly one session, once, at the moment it is pasted. That
covers the streams you cut today. It does not cover the work that arrives any other way — a task
typed straight into a chat, a plan a person wrote by hand, a session opened tomorrow to fix one
thing. Those sessions never open the profile, and never open this skill.

So the rules that must hold *regardless of how the work arrived* have to live in the file the
harness loads at the start of every session in this repository: `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md` — whatever yours reads. The profile then points at it, and the two say the same thing.

**Three carriers, one text.** Each covers a case the others cannot:

| Carrier | Reaches | Fails to reach |
|---|---|---|
| Persistent rules file in the repository root | every session in the project, always | nothing — but it is not task-specific |
| The wave/iteration plan | every session that opens the plan, including ones re-cut by hand | a session working without a plan |
| `.parallel-streams.md` profile → the briefs | sessions briefed by this skill | everything else |

When they disagree, the persistent file wins and the others get corrected — one text, three places.

**Why this is written down at all.** Observed 2026-08-31 in a project running this skill: the skill
was current, the profile was current, and both said in as many words that a subagent's report is
never forwarded to the person. Sessions in the wave pasted their subagents' reports anyway — file
paths, tables of dependency files, lines of configuration — to a person who does not read code. The
plan those sessions worked from had been written by hand and carried no rules section, and the
repository had no persistent rules file at all. Nothing was disobeying a rule. The rule was in two
places, and neither was a place those sessions could see.

## The block to add

Paste into the project's persistent rules file and adapt the wording. Keep the parts marked ‼️ —
they are the ones that fail silently when missing, because a session that never learned them
reports success either way.

```markdown
## How sessions work here

- ‼️ **Who is reading.** <Say who the person is and what they do not read. If they do not read
  code: no code blocks, no source file names, no component or setting names, no selectors, no line
  numbers, no paths inside dependency packages. Name things by meaning. Mention a file — give its
  full path so it can be opened.>
- ‼️ **A subagent's report is raw material for the session, not text for the person.** Ask
  subagents for maximum precision — paths, names, line numbers, exact strings; that is what the
  session decides on, and it is not trimmed on the way in. It is trimmed on the way out: no
  subagent report reaches the person whole, summarised, or reworded with the code names left in.
  The person hears the session's own words.
  - **Translating is not hiding.** Specifics a decision needs — how long, what breaks, where the
    risk is, what is still unknown — are given in full, in plain words.
  - **Bad news is stated plainly**: what is not done, what broke, what was never checked, where the
    session is guessing. A smooth retelling that hides a failure breaks this rule rather than
    honouring it.
- ‼️ **The session writes to the person at a few fixed moments, not continuously.** One line per
  task before it starts — no permission asked, no answer awaited. A self-contained question at each
  fork: what is being decided, how the options differ for a human, what follows from each; no "see
  above", because conversations get compacted and whatever the question leaned on is gone. A
  summary at the end, in actions rather than findings — what changed, then either "nothing needed
  from you" or a numbered list of what the person does. No running status.
- **Reading and research go to subagents**, and so does each task's implementation, with a separate
  subagent checking the finished task against what was asked. The session runs the branch, the pull
  request and the conversation. <Add your model tiers.>
- **Isolation and merge.** <One worktree per session, branch off a fresh trunk, never commit to the
  trunk directly, the session opens and merges its own pull request.>
- **Checks before merge.** <Your test, type, build and review commands, and what decides review
  depth.>
```

## Keeping the three in sync

Change the persistent file first; update the profile and the plan template to match in the same
change. A profile that has drifted from the persistent file is worse than a profile with the
section left out, because both are loaded at once and the session has to guess which is current.
