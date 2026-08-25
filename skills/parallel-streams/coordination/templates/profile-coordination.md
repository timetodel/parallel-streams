## Coordination

Coordination channel between sessions. Five commands, folded into every task brief.

- **Announce — the first action after the worktree is created, before any edit:**
  `pwsh scripts/wave-board.ps1 -Mode Claim -Stream <stream number> -StreamName "<name from the plan>" -Tasks "<task numbers>" [-Wave <wave>] [-Plan <plan file>]`.
  Without a claim, the stream is indistinguishable from outside as "not opened yet", and findings
  get addressed to it by branch name — which is already different by the middle of the wave. The wave
  and stream number can be left unnamed: the wave is taken from the plan's file name, or, lacking one,
  from work already running nearby or from today's date; the number handed out is the next free one.
  The claim prints back a map of neighbours: whose streams are nearby and which tasks belong to whom.
- **A finding for a live neighbour:**
  `pwsh scripts/wave-board.ps1 -Mode Add -To <wave/stream> -Title "<one line>" -Where "<where the full text is>"`.
  The address is the stream number in the plan (`wave6/3`), not the branch name. `*` — every stream in
  the same wave, `**` — every session in the project.
- **Incoming — handle it, then close:** `pwsh scripts/wave-board.ps1 -Mode Done -Id <id>`, otherwise
  the entry keeps coming back after every context compaction. Decided it is out of scope — close it
  anyway.
- **Release the stream — before saying "done":** `pwsh scripts/wave-board.ps1 -Mode Release`. It
  refuses while the inbox still has something open: it lists exactly what.
- **Whose piece of work this is:** `pwsh scripts/wave-board.ps1 -Mode Streams [-Task <task number>]`.
  Asking is MANDATORY before proposing work outside your own tasks to its owner: they do not know the
  task was planned for another stream, and will say yes.

The board and the claims registry live in the repository's shared service directory: visible to every
worktree at once, require no merging, and never land inside someone else's claim.

## Plans

The folder where wave plans live: <path from the project root, backtick-quoted; no plans — remove this line>

This is how the guard recognizes that a wave plan is being edited, and nudges toward the board. No
folder named — the nudge guard does not get connected at all, and work proceeds without plans: the
wave is taken from work already running nearby, or from today's date.
