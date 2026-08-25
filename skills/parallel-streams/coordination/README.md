# Coordination channel between sessions

The `parallel-streams` skill splits a plan into streams and writes the task briefs. That's not
enough: the sessions working through those tasks can't see each other's context, and a plan edited
after they started never reaches them — each one read it once, on its own. What's needed is a
channel that delivers a finding to a live neighbour and answers the question "who owns this task."

The channel itself lives here: the board, the claim registry, two hooks, and an installer. The
skill doesn't require it — without a profile carrying a coordination section, it runs as it always
did and stays silent about the channel.

## Installing it into a project

From the project root:

```
pwsh .claude/skills/parallel-streams/coordination/install.ps1
```

The installer does exactly three things and reports what it did:

1. wires two hooks into the project settings (`.claude/settings.json`) — without duplicating them
   if they're already wired; the second one (the wave-plan-edit nudge) only when the profile names
   a plans folder and it exists in the project: not knowing the folder, it would stay silent
   forever anyway. It removes its own entry once that entry is no longer needed, and reports that
   too. It leaves other entries and their order alone, but reassembles the file in its own layout —
   a file written in a different style will show up whole in the diff after the first install; the
   report calls this out on its own line;
2. drops a profile scaffold, `.parallel-streams.md`, or, if a profile already exists, adds only the
   missing coordination sections, changing nothing in what's already there;
3. drops a short bridge script, `scripts/wave-board.ps1`, so the launch command is equally short in
   every project.

To remove it: `pwsh .claude/skills/parallel-streams/coordination/install.ps1 -Mode Uninstall`. To
see what's wired in: `-Mode Check`.

Updating the channel means replacing the skill's whole folder: both the settings and the bridge
script point at the folder, not at its contents.

## What lives where

| File | What it holds |
|---|---|
| `wave-board.ps1` | The tool: announce, post a finding, close what arrived, release a stream, ask who owns this task |
| `lib/wave-board-lib.ps1` | How the board and the claim registry are built: where they live, how they're read, who's alive, who's silent |
| `lib/git-env-clean.ps1` | Strips git environment variables — otherwise the board would end up in someone else's repository |
| `lib/hook-io.ps1` | Reads the data that reaches a hook |
| `hooks/wave-board-deliver.ps1` | Delivery: brings a session the records addressed to it at session start and before every human turn |
| `hooks/pretooluse-wave-board-nudge.ps1` | A nudge when the wave plan is edited: an addition to the plan doesn't catch up with a live neighbour |
| `install.ps1` | Install, uninstall, and check wiring |
| `templates/profile.md` | Profile scaffold for a new project |
| `templates/profile-coordination.md` | Coordination sections — the ones the installer adds to a profile that already exists |
| `tests/` | Mechanics checks; run together with the project's own checks |

## Where the records themselves live

The board and the claim registry sit in the repository's shared internal directory
(`.git/wave-board/`). That's why every worktree can see them at once, they never land in any
branch, and they need no merge.

## Working without a wave

Naming a wave is optional. It's taken from the name of the document that got split; if there isn't
one either, the session joins whatever work is already running nearby, or, if nobody's nearby, a
wave is opened under today's date. The stream number is optional too — the next free one is handed
out.

## Known limitation: two sessions can carry the same branch name

A stream is called by three names: its number in the wave (`wave6/3`), its branch name, and its
worktree folder name. The branch name comes from the claim filed at announce time, and from
whatever version control says about the worktree right now. If a session renamed its branch and
**did not announce again**, its claim keeps the old name — and that old name still counts as
carried, on equal footing with the real one.

If a neighbour then takes that old name (branch names do get reused in a live repository), two
sessions end up carrying the same name. When that happens:

- a finding sent to that name reaches both sessions;
- closing a finding addressed by name is shared: whichever session closes it first extinguishes it
  for the other one too;
- the true addressee never sees it, releases their stream green with an empty inbox, and the
  finding's author gets an "acknowledged" from a stream they never named.

**What already guards against this.** Only the session the finding is actually addressed to can
close a name-addressed finding, so an unrelated session can't extinguish it. Two claims sharing a
branch name are visible to the eye in the stream listing (`-Mode Streams`). And the
"carries vs. remembers" conflict is resolved: the name goes to whoever carries it now, and whoever
only remembers it no longer answers to it.

**What's still missing.** The "carries vs. carries" conflict isn't resolved: a worktree's real,
current branch name should win over an old name left in a claim that was never re-filed. That
belongs in the same place everything else gets resolved — a second pass over the claim registry
(`Resolve-ClaimNames` in `lib/wave-board-lib.ps1`).

**How to work around it right now.** Address the finding by wave and stream number (`wave6/3`) —
that address can't be confused with anything. And announce again after renaming a branch: that
moves the old name into the claim's memory and it stops counting as carried.
