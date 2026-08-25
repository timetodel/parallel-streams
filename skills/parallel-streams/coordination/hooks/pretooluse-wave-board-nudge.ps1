#Requires -Version 7
<#
PreToolUse hook: the wave plan is being edited — remind that this alone won't reach a neighbour's
live session.

Why. Adding a finding to the plan feels like enough — the plan is, after all, the wave's law. But it
is law for the NEXT sessions: a live one read the plan once, at start, and works off a copy in its
own worktree. On 2026-08-20 three findings were lost exactly this way. The hook catches this precise
moment — the edit is already being written, and the addressee isn't named yet — and shows which of
the wave's worktrees are alive.

A live worktree is not the same as a live session: the worktree stays around after the session
closes. The hook reads liveness from the beacon that the delivery hook refreshes in its own worktree
on every turn, and it says exactly what it knows: "marked recently" — the session was active in the
last few hours and is most likely alive (it could have closed an hour ago too; the beacon won't
notice, since nobody clears it on close), "no recent mark" — unknown (it could have closed, or it
could be working silently, or it could have started before this hook existed), "no worktree" — the
stream is closed. Passing off "unknown" as "closed" is not allowed: the finding would go to "Wave
Loose Ends" past a live neighbour. The opposite overpromise is just as harmful: "definitely alive"
would take from the finding's author the question of whether to file it in the loose ends instead —
so the hook never says that either.

The list has a cap and leads with the names known for certain. The hook suggests, it doesn't decide:
it narrows the choice down to a handful of names, and the person at the screen takes it from there.

Shown once per session: a stream sees many plan edits, and the reminder is always the same.
The hook blocks nothing and on any unexpected condition exits silently with zero.

The wave-plans folder comes from the project profile (`.parallel-streams.md`, the `## Plans`
section) — via the shared parser in `lib/wave-board-lib.ps1`, the same one the filter for
jointly-edited shared locations also uses to read it. A folder hardcoded for one project would leave
the hook mute in every other one, and an unnamed folder means the project has no plans, and nothing
to remind about.
#>

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# More names than this stops being a hint and becomes a wall of text the session pays for on every turn.
$MaxNames = 8

function Get-StateDir {
    $dir = Join-Path $PWD '.claude/.cache'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    return $dir
}

function Get-WaveStreams {
    param([string]$WaveMarker)
    # The wave's worktrees, sorted by liveness: sessions that have marked themselves go in one pile,
    # silent worktrees in another. The repo's main folder is not a stream, so we don't offer it
    # either.
    $here = ($PWD.Path -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
    $alive = [System.Collections.Generic.List[string]]::new()
    $silent = [System.Collections.Generic.List[string]]::new()
    foreach ($tree in (Get-Worktrees)) {
        if ($tree.path -notmatch '/\.claude/worktrees/[^/]+$') { continue }
        if ($tree.path.ToLowerInvariant() -eq $here) { continue }
        $name = if ($tree.branch) { $tree.branch } else { Split-Path -Leaf $tree.path }
        if ($WaveMarker -and $name -notmatch $WaveMarker -and $tree.path -notmatch $WaveMarker) { continue }
        # We do NOT drop a silent worktree from the list: a session started before this hook existed
        # may simply have no beacon, and calling it closed would send the finding to "Wave Loose
        # Ends" past a live neighbour. We say what we actually know: marked, or unknown.
        if ($tree.live) { $alive.Add($name) } else { $silent.Add($name) }
    }
    return [pscustomobject]@{
        Alive  = @($alive | Sort-Object -Unique)
        Silent = @($silent | Sort-Object -Unique)
    }
}

try {
    . (Join-Path $PSScriptRoot '../lib/hook-io.ps1')
    # The shared library is dot-sourced here, not further down: it supplies both the plans folder,
    # which the hook uses to decide whether it's looking at a plan at all, and the worktree list.
    . (Join-Path $PSScriptRoot '../lib/wave-board-lib.ps1')
    $raw = Read-HookInput
    if (-not $raw) { exit 0 }
    $call = $raw | ConvertFrom-Json
    $filePath = $call.tool_input.file_path
    if (-not $filePath) { exit 0 }
    $normalized = $filePath -replace '\\', '/'
    # The path also arrives relative ("wave-plans/…"), and the pattern below requires a leading
    # slash — on such a call the hook used to exit silently, i.e. it didn't work in exactly the
    # case where it's shorter to type.
    if (-not [System.IO.Path]::IsPathRooted($normalized)) {
        $normalized = (Join-Path $PWD.Path $normalized) -replace '\\', '/'
    }
    # The project profile names the plans folder. Unnamed — the project has no plans, and there's
    # nothing to remind about: stay silent, rather than checking against some one project's folder.
    $plans = Get-ProfilePlansFolder
    if (-not $plans) { exit 0 }
    if ($normalized -notmatch [regex]::Escape("/$plans")) { exit 0 }
    # The archive holds already-closed waves — there's no one there to address.
    if ($normalized -match [regex]::Escape("/${plans}archive/")) { exit 0 }

    $sessionId = if ($call.session_id) { [string]$call.session_id } else { 'nosession' }
    $flag = Join-Path (Get-StateDir) "wave-board-nudge-$sessionId.flag"
    if (Test-Path $flag) { exit 0 }
    Get-ChildItem (Get-StateDir) -Filter 'wave-board-nudge-*.flag' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    # We take the wave marker from the plan's file name (`2026-08-13-server-wave3-corp-tariff.md`)
    # so we don't suggest neighbours from a different wave. Far from every plan has a number in its
    # name — then we show the project's worktrees instead, with a caveat (wave undetermined) and a
    # cap: without the cap the list degenerated into EVERY worktree in the repo, two dozen of them.
    $marker = ''
    $matched = [regex]::Match((Split-Path -Leaf $normalized), 'wave\d+')
    if ($matched.Success) { $marker = $matched.Value }

    $streams = Get-WaveStreams -WaveMarker $marker
    New-Item -ItemType File -Path $flag -Force | Out-Null

    $waveWord = if ($marker) { "the wave ($marker)" } else { 'the project' }
    # Cap shared across both lists: first the ones we know for certain, then the rest.
    $shownAlive = @($streams.Alive | Select-Object -First $MaxNames)
    $shownSilent = @($streams.Silent | Select-Object -First ($MaxNames - $shownAlive.Count))
    $total = $streams.Alive.Count + $streams.Silent.Count
    $rest = $total - $shownAlive.Count - $shownSilent.Count

    $head = [System.Collections.Generic.List[string]]::new()
    if ($total -eq 0) {
        $head.Add("There are no worktrees for ${waveWord} at all — the streams must be closed.")
    } else {
        if (-not $marker) {
            $head.Add('The wave cannot be determined from the plan name — the worktrees below are for the whole project; check them against the stream name.')
        }
        if ($shownAlive.Count -gt 0) {
            $head.Add("Sessions for ${waveWord} marked themselves in the last few hours (most likely alive): $($shownAlive -join ', ')")
        }
        if ($shownSilent.Count -gt 0) {
            $head.Add("Worktrees for ${waveWord} with no recent mark (alive or not — unknown): $($shownSilent -join ', ')")
        }
        if ($rest -gt 0) { $head.Add("… and $rest more — full list: git worktree list") }
    }
    $advice = if ($total -gt 0) {
        @(
            'A finding for one of them goes into BOTH the plan AND the wave board, or it will not arrive:'
            '  pwsh scripts/wave-board.ps1 -Mode Add -To <wave/stream> -Title "<one line>" -Where "<where the full text is>"'
            'The address is the stream NUMBER in the plan table (wave6/3): branch names drift from what was announced by the middle of the wave.'
            'Who runs which stream, and its current state:'
            '  pwsh scripts/wave-board.ps1 -Mode Streams'
            'A released stream — the board will refuse it: the finding belongs in the "Wave Loose Ends" section of the plan,'
            'as a separate item with ready-made text for launching a new session.'
        )
    } else {
        @(
            'Which means the finding cannot be a line in someone else''s task: its place is the "Wave Loose Ends" section of the plan,'
            'as a separate item with ready-made text for launching a new session.'
        )
    }

    $text = @(
        'The wave plan is being edited. An addition to the plan will not reach a neighbour''s LIVE'
        'session on its own: the plan is read once, at start, and in that worktree the file stayed'
        'at the version it was at start.'
        ''
    ) + $head + @('') + $advice | Join-String -Separator "`n"

    @{
        hookSpecificOutput = @{
            hookEventName     = 'PreToolUse'
            additionalContext = $text
        }
        suppressOutput     = $true
    } | ConvertTo-Json -Depth 5 -Compress
} catch {
    exit 0
}
