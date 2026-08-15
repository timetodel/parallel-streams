# Installation instructions for an AI agent

**If you are an AI agent and your user asked you to install this skill, follow this file exactly.
It is written for you, not for them.** Do not ask the user to run the steps by hand — do them, then
report the one line they need to type.

The skill is one directory: `skills/parallel-streams/`. It contains `SKILL.md`, a `references/`
folder, and a `scripts/` folder. All three must be copied together — the skill loads the references
by relative path and runs the script from `scripts/`.

## Step 1 — pick the destination

Ask the user only if it is genuinely unclear; otherwise default to personal scope.

| Scope | Destination | Use when |
|---|---|---|
| Personal (default) | `~/.claude/skills/parallel-streams/` | the user wants it available in every project |
| Project | `<repo-root>/.claude/skills/parallel-streams/` | the user wants it committed for their team |

On Windows the personal path is `%USERPROFILE%\.claude\skills\parallel-streams\`.

## Step 2 — copy the skill directory

If git is available:

```bash
git clone --depth 1 https://github.com/timetodel/parallel-streams /tmp/parallel-streams
mkdir -p ~/.claude/skills
cp -r /tmp/parallel-streams/skills/parallel-streams ~/.claude/skills/
rm -rf /tmp/parallel-streams
```

If git is not available, download and extract the archive instead:

```bash
curl -L https://github.com/timetodel/parallel-streams/archive/refs/heads/main.tar.gz -o /tmp/ps.tar.gz
tar -xzf /tmp/ps.tar.gz -C /tmp
mkdir -p ~/.claude/skills
cp -r /tmp/parallel-streams-main/skills/parallel-streams ~/.claude/skills/
rm -rf /tmp/ps.tar.gz /tmp/parallel-streams-main
```

If the environment has no shell access but can write files, recreate the directory yourself by
fetching each file from `https://raw.githubusercontent.com/timetodel/parallel-streams/main/skills/parallel-streams/...`:

```
SKILL.md
references/dependency-analysis.md
references/diagram-rules.md
references/brief-template.md
references/configuration.md
scripts/render_map.py
```

## Step 3 — verify before reporting success

Both checks must pass. Do not claim the install worked without running them.

```bash
test -f ~/.claude/skills/parallel-streams/SKILL.md && echo "skill file: ok"
printf '1:\n2: 1\n3: 1\n4: 2, 3\n' | python3 ~/.claude/skills/parallel-streams/scripts/render_map.py --check
```

The second command must print a diagram and `render_map: check passed`. If Python is missing, the
skill still works — say so, and note that diagrams will have to be hand-drawn under the rules in
`references/diagram-rules.md`.

## Step 4 — tell the user what to do next

Report exactly this, adapted to where you installed it:

> Installed `parallel-streams` to `<path>`. Restart the session (or start a new one) so the skill is
> picked up, then point it at a plan:
>
> `split docs/plans/<your-plan>.md into parallel streams`
>
> Optional: drop a `.parallel-streams.md` profile in the repository root so the briefs use this
> project's test command, review gate, and merge policy. Template:
> https://github.com/timetodel/parallel-streams/blob/main/examples/profiles/TEMPLATE.md

## Alternative: plugin install

If the user is on Claude Code and prefers the plugin manager, they run these two commands
themselves — you cannot run slash commands on their behalf:

```
/plugin marketplace add timetodel/parallel-streams
/plugin install parallel-streams@parallel-streams
```

## Uninstall

```bash
rm -rf ~/.claude/skills/parallel-streams
```

Or `/plugin uninstall parallel-streams@parallel-streams` for the plugin install.
