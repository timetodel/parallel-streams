# Installation instructions for an AI agent

**If you are an AI agent and your user asked you to install this skill, follow this file exactly.
It is written for you, not for them.** Do not ask the user to run the steps by hand — do them, then
report the one line they need to type.

The skill is one directory: `skills/parallel-streams/`. It contains `SKILL.md`, a `references/`
folder, a `scripts/` folder, and a `coordination/` folder. Copy the whole directory — the skill
loads the references by relative path, runs the script from `scripts/`, and points at
`coordination/` for the channel commands it prints.

## Step 1 — pick the destination

**Default to the project.** This skill earns its keep in a repository big enough to have plans with
parts; installing it into every project the user ever opens is noise. Choose personal scope only if
the user says they want it everywhere.

| Scope | Destination | Use when |
|---|---|---|
| Project (default) | `<repo-root>/.claude/skills/parallel-streams/` | the usual case — it lives with the repo and the team gets it |
| Personal | `~/.claude/skills/parallel-streams/` | the user explicitly wants it in every project |

On Windows the personal path is `%USERPROFILE%\.claude\skills\parallel-streams\`.

## Step 1a — Russian projects

If the sessions in this project talk to their owner in Russian, copy `localization/ru/parallel-streams`
instead of `skills/parallel-streams` in the next step — same version, same layout, translated. Profile
section headings stay English in both. Everything else in these instructions is unchanged.

## Step 2 — copy the skill directory

If git is available (project scope — run from the repository root):

```bash
git clone --depth 1 https://github.com/timetodel/parallel-streams /tmp/parallel-streams
mkdir -p .claude/skills
cp -r /tmp/parallel-streams/skills/parallel-streams .claude/skills/
rm -rf /tmp/parallel-streams
```

If git is not available, download and extract the archive instead:

```bash
curl -L https://github.com/timetodel/parallel-streams/archive/refs/heads/main.tar.gz -o /tmp/ps.tar.gz
tar -xzf /tmp/ps.tar.gz -C /tmp
mkdir -p .claude/skills
cp -r /tmp/parallel-streams-main/skills/parallel-streams .claude/skills/
rm -rf /tmp/ps.tar.gz /tmp/parallel-streams-main
```

For personal scope, swap `.claude/skills` for `~/.claude/skills` in either block.

If the environment has no shell access but can write files, recreate the directory yourself by
fetching each file from `https://raw.githubusercontent.com/timetodel/parallel-streams/main/skills/parallel-streams/...`:

```
SKILL.md
references/dependency-analysis.md
references/diagram-rules.md
references/brief-template.md
references/configuration.md
scripts/render_map.py
coordination/README.md
coordination/install.ps1
coordination/wave-board.ps1
coordination/lib/wave-board-lib.ps1
coordination/lib/git-env-clean.ps1
coordination/lib/hook-io.ps1
coordination/hooks/wave-board-deliver.ps1
coordination/hooks/pretooluse-wave-board-nudge.ps1
coordination/templates/profile.md
coordination/templates/profile-coordination.md
```

The `coordination/` files are only needed if the user wants the channel (step 3a). The skill itself
works without them.

## Step 3 — verify before reporting success

Both checks must pass. Do not claim the install worked without running them.

```bash
test -f .claude/skills/parallel-streams/SKILL.md && echo "skill file: ok"
printf '1:\n2: 1\n3: 1\n4: 2, 3\n' | python3 .claude/skills/parallel-streams/scripts/render_map.py --check
```

The second command must print a diagram and `render_map: check passed`. If Python is missing, the
skill still works — say so, and note that diagrams will have to be hand-drawn under the rules in
`references/diagram-rules.md`.

## Step 3a — the coordination channel, only if asked for

The skill splits the plan; the channel is what lets the running sessions reach each other
afterwards — send a finding to a live neighbour, close what arrives, ask who owns a task, release
the stream at the end. It is optional and off until installed. Install it when the user wants
sessions to coordinate, or when they say several people or sessions work in this repository at
once:

```
pwsh .claude/skills/parallel-streams/coordination/install.ps1
```

It needs PowerShell 7 and a git repository. It reports every change it makes: two hooks in the
project's settings, the coordination sections in `.parallel-streams.md`, and a short bridge script
at `scripts/wave-board.ps1`. Check with `-Mode Check`, remove with `-Mode Uninstall`. If PowerShell
7 is missing, say so plainly — the skill still works, only without the channel.

## Step 3b — the persistent rules file

Ask, once, whether this repository has a file every session loads on start — `CLAUDE.md`,
`AGENTS.md`, or whatever this harness reads. Check for it yourself first; it usually sits in the
repository root.

**If it exists**, offer to add the block from
`.claude/skills/parallel-streams/references/persistent-rules.md` — who the person is and what they
do not read, that a subagent's report is never forwarded to them, the few moments a session writes
at. Add it only if the user says yes, and adapt the placeholders to this project instead of pasting
them raw.

**If it does not exist**, say so in one line and offer to create it with that block. Do not create
it silently.

Why this is a step and not a footnote: a brief reaches one session, once. A task typed by hand into
a chat, a plan someone wrote themselves, a session opened tomorrow for one small fix — none of
those ever load the profile or this skill, and a rule they never saw fails quietly, with a
confident report either way. The full account is in that reference file.

## Step 4 — tell the user what to do next

Report exactly this, adapted to where you installed it:

> Installed `parallel-streams` to `<path>`. Restart the session (or start a new one) so the skill is
> picked up, then point it at a plan:
>
> `split docs/plans/<your-plan>.md into parallel streams`
>
> Optional, and worth it: drop a `.parallel-streams.md` profile in the repository root so the
> briefs use this project's test command, review gate, and merge policy. Template:
> https://github.com/timetodel/parallel-streams/blob/main/examples/profiles/TEMPLATE.md
>
> Also worth doing once: put the rules that must hold for *any* session — not only briefed ones —
> into the file your harness loads on start (`CLAUDE.md`, `AGENTS.md`). Ready block:
> `.claude/skills/parallel-streams/references/persistent-rules.md`.

## Updating later

Repeat step 2 over the existing directory, then re-run the checks in step 3. There is no package
manager involved and nothing to unregister — the skill is a directory, and a newer copy of that
directory is the whole update.

## Uninstall

If the coordination channel was installed, remove it first — that puts the project's settings and
bridge script back the way they were:

```
pwsh .claude/skills/parallel-streams/coordination/install.ps1 -Mode Uninstall
```

```bash
rm -rf .claude/skills/parallel-streams
```
