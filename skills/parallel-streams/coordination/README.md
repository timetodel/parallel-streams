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

1. wires three hooks into the project settings (`.claude/settings.json`) — without duplicating them
   if they're already wired. The wave-plan-edit nudge goes in only when the profile names a plans
   folder and it exists in the project: not knowing the folder, it would stay silent forever
   anyway. The other two — delivery and the commit guard — always: a stream announces itself in a
   project with no waves and no plans at all. The installer removes its own entry once that entry
   is no longer needed, and reports that too. It leaves other entries and their order alone, but
   reassembles the file in its own layout — a file written in a different style will show up whole
   in the diff after the first install; the report calls this out on its own line;
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
| `hooks/pretooluse-claim-before-publish.ps1` | The one refusal in the kit: a commit from a stream that never announced itself is stopped, and handed the line that fixes it |
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

## An unannounced stream is stopped at the commit

Everything else in this kit is an agreement a session keeps of its own accord. The announcement is
the one thing the rest rests on — a session that skipped it is invisible: a neighbour has nowhere to
send a finding, its tasks show as unowned, and a neighbour asking who owns them is told nobody does.
It can't even be reminded, because there's nothing to send a reminder to.

So the channel refuses exactly once, at the one moment an unannounced stream stops being that
session's private business: `git commit` (and, failing that, `git push` or opening a pull request).
Local work is never touched. The refusal hands back the whole command that fixes it, and nothing has
to be worked out — with no wave and no plan, `-Mode Claim` on its own supplies the wave and hands out
a free number.

It catches forgetfulness, not intent: one deliberate line of shell gets around it, and that's fine —
forgetfulness is what actually happens. Where there is genuinely no stream to announce — a commit
from the repository's main folder, a fix to the channel itself, a bulk chore — the deliberate
exception is the environment variable `PARALLEL_STREAMS_ALLOW_UNCLAIMED=1` for that session, and the
refusal names it.

The guard asks the registry STRICTLY: an unreadable registry, a dropped drive, a claim file busy for
a moment all mean "couldn't find out", never "the stream didn't announce". In every one of those it
stays silent and lets the commit through — a kit installed into someone else's project has no
business turning its own trouble into a project that can't commit.

## One claim per folder, one leading record per address

A session's key is the root of its worktree, and the unit of accounting is the stream. Three rules
follow from that:

- announcing under a DIFFERENT address from a folder that already holds an unclosed claim is
  refused before it gets recorded: otherwise the previous stream would vanish silently, taking its
  inbox and its tasks with it;
- announcing under an address already held by another folder's claim is refused the same way: two
  leading records for one address would mean a directory listing's order decides who gets the
  finding;
- relocating is legitimate, and done with the takeover key (`-TakeOver`): the session announces from
  the new folder under the same address, the address and the inbox travel with it, and the
  abandoned record in the old folder goes dark — from the outside it no longer answers and receives
  no findings.

Both refusals print ready-made commands, whole lines: run them without reading the code.

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
