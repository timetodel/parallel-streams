#Requires -Version 7
<#
Shared plumbing for the wave board: where it lives, how it's read, who a finding is addressed to,
and whose session is alive.

A separate file because there are two consumers, and they must understand the board IDENTICALLY:
the tool (`../wave-board.ps1`, which writes and closes entries) and the delivery hook
(`../hooks/wave-board-deliver.ps1`, which brings entries into a session). Let their addressing
rules drift apart and a finding silently fails to arrive — and it looks exactly like "the neighbour
has nothing to say."

The file does nothing by itself: it only declares functions.
#>

# ‼️ Strip git environment variables BEFORE the first call to git. Otherwise, with an externally
# set GIT_DIR, the board ends up in the WRONG repository (git is asked for the common directory
# and answers with the one it was given), the branch name comes from someone else's tree, and the
# whole list of live sessions is someone else's too. All of this silently: the one who posted the
# finding is sure it was delivered, while the neighbour "has nothing to say" — the exact thing the
# board was built to prevent.
# The full breakdown of this trap is in the included file.
. (Join-Path $PSScriptRoot 'git-env-clean.ps1')

function Get-BoardPath {
    param([string]$Override)
    if ($Override) { return $Override }
    # The shared directory, not one per worktree: it's the same for every tree, sits outside
    # branches (so it can't get stuck inside someone else's claim), and survives the worktree
    # being deleted along with the closed session.
    # From the main folder git answers with a relative path; from a worktree, an absolute one.
    $common = (& git rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $common) { throw 'not a git repository — the wave board has nowhere to live' }
    $common = $common.Trim()
    if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $PWD $common }
    return Join-Path (Resolve-Path $common).Path 'wave-board/board.jsonl'
}

function Get-StreamKey {
    param([string]$Raw)
    # A single stream gets called three ways: by branch (`feat/wave3-plan-clock`), by worktree
    # folder (`wave3-plan-clock`), and by folder with the branch's slash swapped for a plus
    # (`feat+wave4-measure-and-accept`). We fold them all to one key, otherwise a finding addressed
    # by branch name won't find a session that knows itself by folder name.
    if (-not $Raw) { return '' }
    $key = ($Raw -replace '\\', '/') -replace '\+', '/'
    $key = $key.Split('/')[-1]
    $key = $key -replace '^worktree-', ''
    return $key.Trim().ToLowerInvariant()
}

function Get-StreamKeys {
    param([string]$Raw)
    # All keys for the named stream. A branch name and a folder name usually fold to the same key,
    # but NOT always: worktree `oddfolder-tab` with branch `feat/oddbranch-tab` gives two different
    # ones. Release writes one of them, while a lookup asks by the other — and it fails to
    # recognize its own stream's release.
    $key = Get-StreamKey -Raw $Raw
    $keys = [System.Collections.Generic.List[string]]::new()
    if ($key) { $keys.Add($key) }
    foreach ($tree in (Get-Worktrees)) {
        $branchKey = Get-StreamKey -Raw $tree.branch
        $folderKey = Get-StreamKey -Raw $tree.path
        if ($key -ne $branchKey -and $key -ne $folderKey) { continue }
        if ($branchKey) { $keys.Add($branchKey) }
        if ($folderKey) { $keys.Add($folderKey) }
    }
    return @($keys | Where-Object { $_ } | Select-Object -Unique)
}

function Get-CurrentKeys {
    # A session has two keys: by branch and by working folder name. Either one matching means the
    # finding is hers.
    #
    # We ask git ONCE per run, same as for the worktree list. The delivery hook gets called on
    # every user message, and it needs the stream's names twice — once to parse the registry and
    # once to select its own entries; a second git call would have been a cost for nothing.
    if ($null -ne $script:WaveBoardCurrentKeys) { return $script:WaveBoardCurrentKeys }
    $keys = [System.Collections.Generic.List[string]]::new()
    try {
        $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $branch) { $keys.Add((Get-StreamKey -Raw $branch.Trim())) }
    } catch {
        # Detached HEAD or a broken git — the second key is enough.
    }
    # We take the folder name from the worktree ROOT, not from the current directory: a session
    # that has stepped into a subfolder otherwise starts calling itself by that subfolder's name and
    # stops answering to its own.
    $keys.Add((Get-StreamKey -Raw ((Get-TreeRoot) -split '/')[-1]))
    $script:WaveBoardCurrentKeys = @($keys | Where-Object { $_ } | Select-Object -Unique)
    return $script:WaveBoardCurrentKeys
}

function Get-FailureReason {
    param($Failure)
    # A human reads the reason, but system messages come in the system's language — here, English.
    # We translate the common case (someone else has the file open) ourselves; everything else we
    # pass through as-is, but flag it as "a system message", so a foreign phrasing isn't mistaken
    # for ours.
    $message = "$($Failure.Exception.Message)".Trim()
    # Both an English and a Russian pattern: the OS may report this in either language depending on locale.
    if ($message -match 'being used by another process' -or $message -match 'используется другим процессом') {
        return 'the file is locked by another process'
    }
    if ($message -match 'Access to the path .* is denied' -or $message -match 'Отказано в доступе') {
        return 'no access to the file'
    }
    return "system message: $message"
}

function Add-BoardLine {
    param([string]$Path, [string]$Line)
    # ‼️ We cut the path with plain string ops, and create the directory inside the try/catch. Shell
    # path parsing asks the shell about the drive, and on a missing drive (or a dropped network
    # share) it dies outright — a raw English system message leaked out instead of our own error.
    # This was fixed for posting; accepting a finding failed the exact same way (reproduced).
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    try {
        if ($dir -and -not (Test-Path -LiteralPath $dir -PathType Container)) {
            New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
        }
    } catch {
        # Stay silent: the write attempt below will name the real reason, and it'll do it in plain terms.
    }
    # Append-only, with retries: several sessions write to the board at once. Rewriting the whole
    # file would lose a neighbour's line, and a neighbour holding the file busy lasts a fraction of a
    # second.
    $reason = 'reason unknown'
    for ($try = 1; $try -le 10; $try++) {
        try {
            # Open for read-write, not append: before writing our line we need to look at how the
            # file currently ends. A truncated write (a session closed mid-word, disk ran out)
            # leaves no trailing newline, and gluing a new record onto it ruins BOTH — neither
            # parses, while the tool still reports success.
            $stream = [System.IO.File]::Open($Path, 'OpenOrCreate', 'ReadWrite', 'Read')
            try {
                $prefix = ''
                if ($stream.Length -gt 0) {
                    $stream.Position = $stream.Length - 1
                    if ($stream.ReadByte() -ne 10) { $prefix = "`n" }
                }
                $stream.Position = $stream.Length
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($prefix + $Line + "`n")
                $stream.Write($bytes, 0, $bytes.Length)
            } finally {
                $stream.Dispose()
            }
            return
        } catch {
            # Keep the real reason: after ten tries, "board is busy" might not be the truth — out
            # of disk space, permissions, a removable drive that dropped — each needs its own fix.
            $reason = Get-FailureReason -Failure $_
            Start-Sleep -Milliseconds 50
        }
    }
    throw "could not append to the board ($Path). Last reason: $reason"
}

function Read-BoardContent {
    param([string]$Path)
    # A board read that CAN REPORT FAILURE. "We couldn't read it" and "the board is empty" are
    # opposite things that look identical: an empty list. Compaction depends on this difference —
    # mistaking a busy file for an empty board would replace it with an empty file and erase every
    # open finding.
    # It's not only neighbouring sessions that hold the board for a fraction of a second — antivirus,
    # the indexing service, and backup software do too, and in that fraction of a second the file
    # size specifically does NOT change.
    # ‼️ We learn "there's no board" FROM A FAILED OPEN, not a separate existence check. An
    # existence check answers "no" both where it's genuinely missing and where it's merely
    # "not visible": a nonexistent drive, a dropped network share, a folder with access closed off.
    # An empty board and an invisible board are opposite things that looked identical; at
    # release time that meant "the inbox is empty, go ahead and release" in a case where the inbox
    # actually hadn't been read at all.
    $reason = ''
    for ($try = 1; $try -le 5; $try++) {
        try {
            # Share the file for read and write: a neighbour might be appending their own line right now.
            $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
            try {
                $length = $stream.Length
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $lines = @(($reader.ReadToEnd() -split "`r?`n") | Where-Object { $_.Trim() })
                return [pscustomobject]@{
                    Ok = $true; Missing = $false; Lines = $lines; Length = $length; Reason = ''
                }
            } finally {
                $stream.Dispose()
            }
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                return [pscustomobject]@{
                    Ok = $true; Missing = $true; Lines = @(); Length = 0; Reason = ''
                }
            }
            # Keep the real reason: "busy" and "no permission" need different fixes.
            $reason = Get-FailureReason -Failure $_
            Start-Sleep -Milliseconds 30
        }
    }
    return [pscustomobject]@{ Ok = $false; Missing = $false; Lines = @(); Length = -1; Reason = $reason }
}

function Read-BoardLines {
    param([string]$Path)
    # The forgiving version, for callers who care more about not choking than about the truth: the
    # delivery hook, when the board is busy, has to stay quiet rather than get in the way. Everyone
    # else should use Read-BoardContent.
    return @((Read-BoardContent -Path $Path).Lines)
}

function Get-BoardEntries {
    param([string[]]$Lines)
    # A pair of "raw line + parsed record". Compaction needs the raw line: it rewrites the board as
    # the TEXT of the surviving lines, not reassembled records — reserializing would quietly change
    # how fields look (the same time of day would come back written differently), and the board
    # would drift from itself.
    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($line in $Lines) {
        $record = $null
        try { $record = $line | ConvertFrom-Json } catch { continue }
        if (-not $record.id) { continue }
        $entries.Add([pscustomobject]@{ Line = $line; Record = $record })
    }
    return @($entries)
}

function Get-BoardClosings {
    param($Entries)
    # Closing comes in two kinds, and they must not be confused.
    #
    # An addressed finding closes GLOBALLY: the one single recipient acted on it — the matter is
    # settled. A broadcast (`-To *`) is addressed to many, and ITS closing is PERSONAL: the line
    # carries the key of the stream that closed it and silences the entry only for that stream. A
    # global close of such a finding would hide it from everyone at once: whoever acted on it first
    # would take it away from the rest, and a stream that never moved or restarted since it was
    # posted would never see it at all.
    #
    # A close is a separate line further down the file, so we collect everything first, then filter.
    $global = [System.Collections.Generic.HashSet[string]]::new()
    $by = @{}
    foreach ($entry in $Entries) {
        if (-not $entry.Record.done) { continue }
        $id = [string]$entry.Record.id
        $who = Get-StreamKey -Raw ([string]$entry.Record.by)
        if (-not $who) { [void]$global.Add($id); continue }
        if (-not $by.ContainsKey($id)) { $by[$id] = [System.Collections.Generic.List[string]]::new() }
        if ($who -notin $by[$id]) { $by[$id].Add($who) }
    }
    return [pscustomobject]@{ Global = $global; By = $by }
}

function Get-BroadcastLifetimeDays {
    # The shelf life of an "everyone" entry. A wave lives for weeks: a finding nobody acted on in
    # two weeks is as stale as the wave itself. Every session in the project pays for it in context
    # meanwhile — including trees set up later that have nothing to do with that wave. This is a
    # fallback for when the global close was forgotten: an addressed entry always has a way out
    # (its close is global), a broadcast one doesn't.
    return 14
}

function Get-BroadcastAgeState {
    param($Record)
    # Age of an "everyone" entry: `live` — still current, `stale` — expired, `broken` — the date
    # can't be parsed.
    #
    # The shelf life applies to "everyone" entries and to "acknowledged" notices: an ordinary
    # addressed finding closes globally, and silently suppressing it would be wrong — it just
    # hasn't been acted on yet. A notice, though, self-closes at display time, and it needs a
    # shelf life for when the author never comes back to their session: otherwise it would sit on
    # the board forever.
    # Both broadcast addresses count, not just the single asterisk: an entry to "the whole
    # project's sessions" has an even wider hole — it reaches EVERY new worktree, including ones
    # set up for other waves, and without a shelf life it would live forever.
    if ((Get-StreamKey -Raw ([string]$Record.to)) -notin @('*', '**') -and [string]$Record.kind -ne 'ack') {
        return 'live'
    }
    $raw = $Record.at
    if ($raw -is [datetime]) {
        return $(if ($raw -lt (Get-Date).AddDays(-(Get-BroadcastLifetimeDays))) { 'stale' } else { 'live' })
    }
    $text = [string]$raw
    $when = [datetime]::MinValue
    # The date can't be parsed (the line was hand-edited, another version wrote it, the field is
    # empty or numeric) — we treat the entry as expired, not as living forever. The previous, softer
    # behaviour reopened, in a narrow case, exactly the hole the shelf life was built to close: such
    # an entry got delivered to every new tree and survived compaction. Erring safe: a finding with
    # a broken date isn't fit to be acted on anyway, and the display will call it out separately —
    # it won't just quietly vanish.
    if (-not $text -or -not [datetime]::TryParse($text, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return 'broken'
    }
    return $(if ($when -lt (Get-Date).AddDays(-(Get-BroadcastLifetimeDays))) { 'stale' } else { 'live' })
}

function Get-BoardStates {
    param($Entries, [string[]]$Viewer)
    # Sorts entries into states: open, closed for this viewer, expired, with a broken date. Display
    # has to tell them apart — otherwise it's unclear why an entry sits in the file yet shows up
    # nowhere, and a human goes off to compact the board or file a duplicate finding.
    $closings = Get-BoardClosings -Entries $Entries
    $mine = @($Viewer | Where-Object { $_ })
    $open = [System.Collections.Generic.List[object]]::new()
    $closedForViewer = [System.Collections.Generic.List[object]]::new()
    $stale = [System.Collections.Generic.List[object]]::new()
    $broken = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $Entries) {
        $record = $entry.Record
        if ($record.done -or -not $record.title) { continue }
        $id = [string]$record.id
        # A global close removes the entry for everyone — both an addressed one and one closed with
        # the -ForAll key.
        if ($closings.Global.Contains($id)) { continue }
        # No switch on purpose: its `continue` moves to the switch's next condition, not to the
        # loop's next entry, and an entry would land in two states at once.
        $age = Get-BroadcastAgeState -Record $record
        if ($age -eq 'stale') { $stale.Add($entry); continue }
        if ($age -eq 'broken') { $broken.Add($entry); continue }
        $seen = $closings.By[$id]
        if ($mine.Count -gt 0 -and $seen -and @($seen | Where-Object { $_ -in $mine }).Count -gt 0) {
            $closedForViewer.Add($entry)
            continue
        }
        $open.Add($entry)
    }
    return [pscustomobject]@{
        Open            = @($open)
        ClosedForViewer = @($closedForViewer)
        Stale           = @($stale)
        Broken          = @($broken)
        Closings        = $closings
    }
}

function Select-OpenEntries {
    param($Entries, [string[]]$Viewer)
    # Without `-Viewer` — everything open for anyone at all (showing the whole board, compaction).
    # With it — what's open FOR THIS SPECIFIC STREAM: its own personal closes are no longer visible to it.
    return @((Get-BoardStates -Entries $Entries -Viewer $Viewer).Open)
}

function Select-KeepEntries {
    param($Entries)
    # What survives compaction: entries open for anyone at all, plus the NAMED closes of those
    # entries (without them, a stream that already acted on an "everyone" finding would get it
    # again). Everything else — entries closed by a global line, expired ones, their closes, and
    # unreadable scraps — is dropped.
    $liveIds = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($entry in (Get-BoardStates -Entries $Entries).Open) {
        [void]$liveIds.Add([string]$entry.Record.id)
    }
    return @($Entries | Where-Object {
            $id = [string]$_.Record.id
            if ($_.Record.done) { $_.Record.by -and $liveIds.Contains($id) } else { $liveIds.Contains($id) }
        })
}

function Get-OpenRecords {
    param([string]$Path, [string[]]$Viewer)
    $entries = Get-BoardEntries -Lines (Read-BoardLines -Path $Path)
    return @(Select-OpenEntries -Entries $entries -Viewer $Viewer | ForEach-Object { $_.Record })
}

function Compress-Board {
    param([string]$Path)
    # Compaction: only open entries stay on the board. A closed entry otherwise sits there as a
    # line forever, and the whole board gets parsed on every single move of every session.
    #
    # This is a dangerous operation — it REWRITES the whole board — so every step second-guesses
    # itself: couldn't read it — leave it alone; read a non-empty file and parsed not a single
    # record — leave it alone. A mistake here costs every open finding at once, while looking like a
    # cheerful success report.
    if (-not (Test-Path $Path)) { return [pscustomobject]@{ Before = 0; After = 0; Unreadable = 0 } }
    $reason = 'reason unknown'
    for ($try = 1; $try -le 10; $try++) {
        $content = Read-BoardContent -Path $Path
        if (-not $content.Ok) {
            throw "couldn't read the board, won't compact it blind ($Path). Last reason: $($content.Reason)"
        }
        $entries = Get-BoardEntries -Lines $content.Lines
        if ($content.Length -gt 0 -and $entries.Count -eq 0) {
            throw "the board has $($content.Length) bytes but not a single record parsed out of it ($Path) — compaction would have wiped its contents; sort the file out by hand"
        }
        # One selection for both compaction and display: otherwise display would promise to remove
        # something other than what actually gets removed.
        $keep = @(Select-KeepEntries -Entries $entries | ForEach-Object { $_.Line })
        # Compaction silently drops unreadable lines — and that's a scrap of somebody's finding. We
        # count them and name them in the report: something erased silently is indistinguishable
        # from something that was never there.
        $unreadable = $content.Lines.Count - $entries.Count
        # A temp file next to the board: replacing it within the same volume is one atomic action,
        # and no half-written file is left in the board's place no matter how the work ends.
        $temp = "$Path.compact-$PID-$(Get-Random).tmp"
        $text = if ($keep.Count -gt 0) { ($keep -join "`n") + "`n" } else { '' }
        [System.IO.File]::WriteAllText($temp, $text, [System.Text.UTF8Encoding]::new($false))
        try {
            # Compare the size against what we read: a neighbour might have appended a line while we
            # were rewriting — then start over, or their line would be lost.
            if ((Get-Item $Path).Length -ne $content.Length) { throw 'the board was appended to while we were rewriting it' }
            [System.IO.File]::Move($temp, $Path, $true)
            return [pscustomobject]@{ Before = $content.Lines.Count; After = $keep.Count; Unreadable = $unreadable }
        } catch {
            # Same craftsmanship for the reason as everywhere else: plain terms for a human, a
            # foreign message flagged as such.
            $reason = Get-FailureReason -Failure $_
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 50
        }
    }
    throw "couldn't compact the board ($Path). Last reason: $reason"
}

function Get-AliveBeaconPath {
    param([string]$TreePath)
    # The beacon of a live session. The delivery hook updates it in ITS OWN worktree on every move,
    # and everyone else reads it from here. The address is declared exactly once: let the writer and
    # the reader drift apart and live sessions would become invisible, which would look like "every
    # stream is closed."
    return (Join-Path $TreePath '.claude/.cache/wave-board-alive.txt')
}

function Test-AliveBeacon {
    param([string]$TreePath)
    # The threshold is deliberately generous: the beacon only updates on a USER move, and a session
    # can go quiet for hours while genuinely working — a long subagent run, waiting on a build, an
    # overnight pause in the conversation. Erring toward "alive" is safe: the finding lands on the
    # board and waits for the session. Erring the other way sends the finding into the "Wave
    # loose ends" pile past a neighbour who's actually alive — that is, past the whole mechanism.
    #
    # ‼️ We take the SHARED threshold, not our own number alongside it. The comment on the shared
    # threshold flatly promises it is one and the same for the tree beacon and for the claim's
    # checked-in mark; while the number sat here as a second copy, editing either of them silently
    # gave two answers to one question — the same tree would count as alive in the display and
    # abandoned in the hint.
    $aliveHours = Get-AliveHours
    $beacon = Get-AliveBeaconPath -TreePath $TreePath
    try {
        if (-not (Test-Path $beacon)) { return $false }
        return ((Get-Date) - (Get-Item $beacon).LastWriteTime).TotalHours -lt $aliveHours
    } catch {
        return $false
    }
}

function Get-Worktrees {
    # Repository worktrees: path, branch, and a LIVE-session flag (a fresh beacon).
    #
    # Liveness isn't the same as the tree existing: a closed session leaves its tree behind, and a
    # finding has nowhere to go from there. But a missing beacon doesn't mean "closed" either — the
    # session might have started before the hook existed. Hence three states — "alive" (fresh
    # beacon), "unknown" (tree exists, no beacon) and "closed" (no tree at all) — and they need to
    # be reported honestly.
    #
    # A worktree lock as a liveness signal was rejected: the environment sets and clears it
    # irregularly — on 2026-08-24 none of the project's 23 trees had one, including the tree of the
    # session that was actively working at that moment. See the decision registry
    # (platform-and-build.md) for the write-up.
    # We ask git ONCE per run. Many things need the list (stream names, address checks, hints); it
    # doesn't change within one short run, and without caching it would rack up a dozen git calls
    # per session move.
    if ($null -ne $script:WaveBoardWorktrees) { return $script:WaveBoardWorktrees }
    $lines = @()
    try {
        $lines = @(& git worktree list --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0) { $script:WaveBoardWorktrees = @(); return @() }
    } catch {
        $script:WaveBoardWorktrees = @()
        return @()
    }
    $trees = [System.Collections.Generic.List[hashtable]]::new()
    $current = $null
    foreach ($line in $lines) {
        if ($line -like 'worktree *') {
            $current = @{
                path   = ($line.Substring(9) -replace '\\', '/').TrimEnd('/')
                branch = ''
                live   = $false
            }
            $trees.Add($current)
            continue
        }
        if (-not $current) { continue }
        if ($line -like 'branch *') { $current.branch = $line.Substring(7) -replace '^refs/heads/', '' }
    }
    foreach ($tree in $trees) { $tree.live = Test-AliveBeacon -TreePath $tree.path }
    $script:WaveBoardWorktrees = @($trees | ForEach-Object { [pscustomobject]$_ })
    return $script:WaveBoardWorktrees
}

function Get-KnownStreamKeys {
    param([switch]$AliveOnly)
    # Keys of the trees that exist — a finding's address gets checked against them. We take both
    # forms of the name (branch and folder): a session knows itself by either. `-AliveOnly` keeps
    # only the ones whose session checked in recently — good for a hint, but NOT for checking an
    # address: posting a finding in advance, for a session that gets picked up in an hour, is
    # perfectly normal.
    $keys = [System.Collections.Generic.List[string]]::new()
    foreach ($tree in (Get-Worktrees)) {
        if ($AliveOnly -and -not $tree.live) { continue }
        if ($tree.branch) { $keys.Add((Get-StreamKey -Raw $tree.branch)) }
        $keys.Add((Get-StreamKey -Raw $tree.path))
    }
    return @($keys | Where-Object { $_ } | Select-Object -Unique)
}

function Select-ForStream {
    param($Records, [string[]]$Keys, $Claim)
    # What on the board is addressed to this session. Four kinds of address, and they must not be
    # confused:
    #   `**`             — every session in the project except the poster;
    #   `*`               — every session of ITS OWN wave (when the wave is known to both sides),
    #                        except the poster: otherwise the entry would reach two dozen trees,
    #                        half of them set up for other waves and paying for it in context for
    #                        nothing;
    #   `wave/stream`     — by claim: this is the main form of address, because that's how the
    #                        stream is named in the plan;
    #   a branch or folder name — the fallback for streams that never made a claim.
    $mine = @($Keys)
    $myWave = if ($Claim) { Get-WaveKey -Raw ([string]$Claim.wave) } else { '' }
    $myStream = if ($Claim) { Get-StreamNumberKey -Raw ([string]$Claim.stream) } else { '' }
    return @($Records | Where-Object {
            $raw = [string]$_.to
            $to = Get-StreamKey -Raw $raw
            $fromMe = (Get-StreamKey -Raw ([string]$_.from)) -in $mine
            $address = Get-StreamAddress -Raw $raw
            if ($to -eq '**') {
                -not $fromMe
            } elseif ($to -eq '*') {
                $recordWave = Get-WaveKey -Raw ([string]$_.wave)
                # Neither side names the wave — behave as before and deliver it: not delivering is
                # worse than delivering something extra.
                (-not $fromMe) -and (-not $recordWave -or -not $myWave -or $recordWave -eq $myWave)
            } elseif ($address) {
                [bool]$myWave -and $address.Wave -eq $myWave -and $address.Stream -eq $myStream
            } else {
                $to -in $mine
            }
        })
}

function Test-TaskInList {
    param([string]$Tasks, [string]$Task)
    # Does a task fall within a stream's task list. The plan writes them however comes naturally:
    # "10-13", "10, 11, 12", "6, 7 and 9", "1b". Answers the question "who owns this task", for
    # when a session is tempted to pick up a neighbouring task and its owner, away from the screen,
    # has no idea it was planned for someone else.
    if (-not $Tasks -or -not $Task) { return $false }
    $wanted = Get-StreamNumberKey -Raw $Task
    if (-not $wanted) { return $false }
    foreach ($chunk in ($Tasks -split '[^\p{L}\p{Nd}\-–]+')) {
        $piece = $chunk.Trim()
        if (-not $piece) { continue }
        $range = [regex]::Match($piece, '^(\d+)\s*[-–]\s*(\d+)$')
        if ($range.Success -and $wanted -match '^\d+$') {
            $from = [int]$range.Groups[1].Value
            $till = [int]$range.Groups[2].Value
            if ([int]$wanted -ge [math]::Min($from, $till) -and [int]$wanted -le [math]::Max($from, $till)) {
                return $true
            }
            continue
        }
        if ((Get-StreamNumberKey -Raw $piece) -eq $wanted) { return $true }
    }
    return $false
}

function Format-BoardRecord {
    param($Record)
    $tail = if ($Record.where) { " — $($Record.where)" } else { '' }
    return "  • `"$($Record.title)`"$tail [id $($Record.id)]"
}

# ─────────────────────────────────────────────────────────────────────────────────────────────
# Stream claim registry: who is running a stream right now.
#
# The board answers "what was handed over"; the registry answers "to whom, and are they alive."
# Without a registry, a finding's address is inferred from a branch or folder name, and names lie:
# in wave 6's plan two streams were assigned the same branch, while the sessions worked on
# different ones, and one session had reused a folder for a different task in the meantime. Such an
# address gets delivered silently, and to the wrong place.
#
# The design is deliberately different from the board's: one file per worktree, written ONLY by its
# own session. One writer per file means no retries, no need to parse the whole log for a single
# field. The board stays a multi-writer log; the registry is a set of small files next to it.
#
# ‼️ A lock is still needed, though — not for the claim file, but for CHOOSING A STREAM NUMBER: the
# number is chosen from a snapshot of the whole registry, and sessions that announce at the same
# moment read the same snapshot. `Enter-RegistryLock` guards this — see the write-up right there.
# ─────────────────────────────────────────────────────────────────────────────────────────────

function Get-AliveHours {
    # The freshness threshold for a checked-in mark — shared between the tree beacon and the stream
    # claim: let them drift apart and the same tree would count as alive in one place and silent in
    # another.
    return 12
}

function Get-SilentDaysBeforeStuck {
    # How long a stream stays silent before a finding addressed to it lands in the "stuck" summary.
    # A full day means not "the session closed" but "time for human eyes": an overnight pause in the
    # conversation doesn't count as this yet, an abandoned stream does.
    return 1
}

function Get-RegistryDir {
    param([string]$BoardOverride)
    # Right next to the board, in the same shared directory: the same three properties (visible to
    # every tree, outside branches, survives a tree being deleted). Tests supply their own board —
    # the registry follows it automatically.
    #
    # ‼️ We strip the file name with plain string ops, not by parsing the path through the shell:
    # shell path parsing asks about the drive and dies outright on a missing one. The tool used to
    # crash right here, on the very first line, leaking a raw English system message before it ever
    # got the chance to give an honest error about the claims directory.
    $board = Get-BoardPath -Override $BoardOverride
    $parent = [System.IO.Path]::GetDirectoryName($board)
    if (-not $parent) { $parent = '.' }
    return [System.IO.Path]::Combine($parent, 'streams')
}

function Get-RepoMarkerState {
    param([string]$StartDir)
    # Is there a repository here AT ALL — a question for the disk, not for git. We need it because
    # git stays silent in exactly the same way in two opposite cases: there is no repository here at
    # all (then there is no tree either, and the current folder IS the session's identity — nothing
    # can drift) and git is out of sorts over a live repository (then falling back to the current
    # folder CHANGES the session's identity). Different decisions follow from those two answers, so
    # we tell them apart.
    #
    # ‼️ There are THREE outcomes, not two: `found` — the marker was found; `none` — it definitely
    # isn't here; `unknown` — we couldn't find out. The third used to be passed off as the second:
    # path parsing on an unreachable path (a dropped drive, a vanished network share) died, the
    # catch quietly answered "no repository", and the caller read that as permission to work off the
    # current folder — that is, the session's identity changed silently in the very place strict
    # mode forbids it. "Not visible" and "not there" must never be conflated anywhere in this
    # toolkit: every refusal out loud rests on that difference.
    #
    # We ask the system the same way everything else in the toolkit does (`Get-PathState`): that
    # tells "this doesn't exist" apart from "couldn't check", and a shell existence check doesn't.
    # The path is cut with plain string ops: shell path parsing on a nonexistent drive dies by
    # itself.
    #
    # In a worktree `.git` is a file, not a folder, so we don't check the kind.
    if (-not $StartDir) { $StartDir = $PWD.Path }
    try {
        $dir = $StartDir
        while ($dir) {
            $state = Get-PathState -Path ([System.IO.Path]::Combine($dir, '.git'))
            if ($state.Kind -eq 'container' -or $state.Kind -eq 'leaf') {
                return [pscustomobject]@{ Kind = 'found'; Reason = '' }
            }
            if ($state.Kind -eq 'unknown') {
                return [pscustomobject]@{ Kind = 'unknown'; Reason = $state.Reason }
            }
            $parent = [System.IO.Path]::GetDirectoryName($dir)
            if (-not $parent -or $parent -eq $dir) { break }
            $dir = $parent
        }
    } catch {
        return [pscustomobject]@{ Kind = 'unknown'; Reason = (Get-FailureReason -Failure $_) }
    }
    # We climbed all the way to the top and found no marker. That is a "no" only where the path is
    # REACHABLE: on a dead drive and on a vanished share every step up answers with the same
    # "nothing here", and quietly concluding "there was never a repository here" would be an
    # invention.
    if (-not (Test-PathReachable -Path ([System.IO.Path]::Combine($StartDir, '.git')))) {
        return [pscustomobject]@{
            Kind   = 'unknown'
            Reason = "the path is unreachable altogether ($StartDir): there isn't a single existing folder above it"
        }
    }
    return [pscustomobject]@{ Kind = 'none'; Reason = '' }
}

# The worktree root, asked for ONCE per run. Three variables instead of one: a failure must not be
# remembered as a success, or a strict reader would get the current folder quietly substituted in —
# the very thing this whole change was made against.
$script:WaveBoardTreeRootAsked = $false
$script:WaveBoardTreeRoot = ''
$script:WaveBoardTreeRootReason = ''

function Get-TreeRoot {
    param([switch]$Strict)
    # ‼️ Who this session is. EVERYTHING that identifies it follows from here: the claim file's name,
    # the liveness beacon, the stream keys, and the "is this my claim" check. So the answer must be
    # one and the same no matter which folder of the tree the session was launched from. Before,
    # each of those places took the current folder — and a session that started in a subfolder of
    # its own tree announced under one key and released under another: release couldn't find its own
    # claim and answered "nothing to release" WITH A SUCCESS CODE. The session closed, while
    # neighbours went on addressing findings to a stream they believed was alive.
    #
    # We ask git once per run, same as for the worktree list: the answer doesn't change within a
    # run, and many callers need it — the delivery hook runs on every user move.
    #
    # `-Strict` is for whoever has a stream's FATE riding on the key (announcing and releasing):
    # there a git failure is said out loud. For tolerant readers (the delivery hook, display) the
    # fallback to the current folder is harmless: there a miss costs one invisible line, while a
    # change of identity costs the stream.
    if (-not $script:WaveBoardTreeRootAsked) {
        $script:WaveBoardTreeRootAsked = $true
        try {
            $top = @(& git rev-parse --show-toplevel 2>$null)
            if ($LASTEXITCODE -eq 0 -and $top.Count -gt 0 -and $top[0]) {
                $script:WaveBoardTreeRoot = ("$($top[0])".Trim() -replace '\\', '/').TrimEnd('/')
            } else {
                $script:WaveBoardTreeRootReason = "git did not name the worktree root (exit code $LASTEXITCODE)"
            }
        } catch {
            $script:WaveBoardTreeRootReason = Get-FailureReason -Failure $_
        }
    }
    if ($script:WaveBoardTreeRoot) { return $script:WaveBoardTreeRoot }
    $here = ($PWD.Path -replace '\\', '/').TrimEnd('/')
    # ‼️ There is no repository here at all — so there is no tree either, and the current folder is
    # this session's only identity: announcing and releasing have nothing to drift between. Refusing
    # here would be wrong for the strict reader and the tolerant one alike — otherwise the toolkit
    # would stop working everywhere the board is supplied explicitly and an ordinary folder stands
    # where a repository would be.
    #
    # ‼️ But "couldn't find out" is a refusal for the strict reader, on a par with "there is a
    # repository". Otherwise we'd get the worst of it: on an unreachable path a session would
    # silently change its own identity, and the blow would land in the most expensive place —
    # release would stop finding its own claim and exit with SUCCESS. For tolerant readers (the
    # delivery hook, display) the fallback is harmless: there a miss costs one invisible line.
    if ($Strict) {
        $marker = Get-RepoMarkerState
        if ($marker.Kind -eq 'found') {
            throw "couldn't work out the worktree root: $($script:WaveBoardTreeRootReason). There IS a repository here, which means the session would identify itself by the current folder ($here) — and under that key neither release nor the neighbours will find its claim, and the stream is quietly lost. Try again once git answers."
        }
        if ($marker.Kind -eq 'unknown') {
            throw "couldn't work out the worktree root: $($script:WaveBoardTreeRootReason). Whether there is a repository here couldn't be found out either ($($marker.Reason)) — and not seeing something is not the same as it not being there: taking one for the other, the session would silently identify itself by the current folder ($here), and neither release nor the neighbours would find its claim. Try again once the path reads again."
        }
    }
    return $here
}

function Get-FolderKey {
    param([string]$Path)
    # ‼️ A worktree folder in ONE form, and this normalization is ONE for the whole toolkit: forward
    # slashes, no trailing one, no difference in letter case. Five places compare the folder recorded
    # in a claim against a session's key — rivals for a number, inheriting former branch names, the
    # second route to one's own claim, the "that's you" mark, and filtering out one's own claim when
    # handing out names — and each of them wrote the normalization by hand. While the normalizations
    # matched letter for letter this held; but the key's source changed from the shell to git, and
    # five identical-looking expressions are five places where they can drift. Let them drift by so
    # much as the case of the drive letter and a session would count its own claim as a rival and
    # lose its memory of former branch names.
    #
    # ‼️ This key is for COMPARISON only. The claim records the worktree folder as it is — a
    # lowercased path must never be shown to a human, who hunts for it with their eyes in a list.
    #
    # We fold the case explicitly instead of trusting the shell to compare strings case-insensitively
    # on its own: the same key is used by ordering (which compares strings BYTE BY BYTE) and by sets,
    # and the shell's convention doesn't hold there. Paths in this toolkit are Windows ones, where
    # case means nothing; on systems where it does, two twin folders differing only in case would
    # count as one — a deliberate price for a session recognizing itself in its own claim.
    return (([string]$Path) -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
}

function Get-PathKey {
    param([string]$TreePath)
    # The claim file's name. The readable part is the tree's folder name; the tail is a fingerprint
    # of the full path — two folders with the same name in different places on disk shouldn't
    # overwrite each other's claims.
    $normalized = Get-FolderKey -Path $TreePath
    $leaf = ($normalized.Split('/')[-1] -replace '[^\p{L}\p{Nd}]+', '-').Trim('-')
    if (-not $leaf) { $leaf = 'tree' }
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalized))
    } finally {
        $sha.Dispose()
    }
    $tail = -join ($bytes[0..3] | ForEach-Object { $_.ToString('x2') })
    return "$leaf-$tail"
}

function Get-ClaimPath {
    param([string]$Dir, [string]$TreePath)
    # ‼️ Joined with plain string ops. Joining paths through the shell asks about the drive and dies
    # outright on a missing one — accepting a finding failed right here, leaking a raw English
    # system message instead of our own error (reproduced).
    return [System.IO.Path]::Combine($Dir, (Get-PathKey -TreePath $TreePath) + '.json')
}

function Get-WaveKey {
    param([string]$Raw)
    # The wave's key. A wave gets called "wave6", "6", and by the plan's file name — we fold them
    # to one form, otherwise a claim filed under one spelling won't be found by another.
    if (-not $Raw) { return '' }
    $text = $Raw.Trim().ToLowerInvariant()
    # The plan's file name: pull the wave marker out of it, if there is one.
    $matched = [regex]::Match($text, 'wave\s*(\d+)')
    if ($matched.Success) { return "wave$($matched.Groups[1].Value)" }
    if ($text -match '^\d+$') { return "wave$text" }
    return ($text -replace '[^\p{L}\p{Nd}]+', '-').Trim('-')
}

function Get-StreamNumberKey {
    param([string]$Raw)
    # The stream number's key. The plan writes it as "3", "S3", "s3", and "3b" — they're all about
    # the same thing.
    # The accepted prefixes are 's' (stream), 'p', '#' and '№' — a plan is written by a human, and
    # the same stream gets called "S3", "p3" or "#3" in different plans. Addresses are parsed here
    # and nowhere else, so this set is the whole contract.
    if (-not $Raw) { return '' }
    $text = $Raw.Trim().ToLowerInvariant() -replace '^[sp#№]\s*', ''
    return ($text -replace '[^\p{L}\p{Nd}]+', '')
}

# Wave names that are ACTUALLY declared in the claim registry. Address parsing needs this: a wave
# isn't only ever called "wave6" — where there are no waves at all, the claim itself supplies one,
# named after a date or a word. Whoever already read the registry (the tool, the delivery hook)
# hands over the list — parsing itself doesn't go read the registry: it's called once per board
# entry, and reading the folder on every call would be a cost for nothing. An empty list means
# parsing behaves as before, understanding a plan wave and a date wave only.
$script:WaveBoardKnownWaves = @()

function Set-KnownWaves {
    param([string[]]$Keys)
    $script:WaveBoardKnownWaves = @($Keys | Where-Object { $_ } | Select-Object -Unique)
}

function Get-KnownWaves {
    return @($script:WaveBoardKnownWaves)
}

function Set-KnownWavesFromRegistry {
    param([string]$Dir)
    # A convenience form for whoever already has the registry at hand. Stays silent on any failure:
    # an unparsed wave list is an address that won't parse, not a breakdown of the mechanism for
    # everyone else.
    try {
        Set-KnownWaves -Keys @((Get-Claims -Dir $Dir) | ForEach-Object { $_.WaveKey })
    } catch {
        Set-KnownWaves -Keys @()
    }
}

function Get-StreamAddress {
    param([string]$Raw)
    # Parses a "wave/stream" address: `wave6/3`, `6/3`, `wave6/S3`, `2026-08-24/2`, `sprint-alpha/1`.
    # Returns $null if it isn't one.
    #
    # It can't be confused with a branch name (`feat/wave6-compute-channel`), and this protection
    # rests on the RIGHT-HAND side: it must hold a stream number, not a word. No branch name fits
    # that, so the left side could be broadened without weakening the check.
    #
    # On the left, a wave gets called three ways, and all three must parse:
    #   `wave6`, `6`      — a wave from the plan;
    #   `2026-08-24`      — a wave substituted by date, for projects with no waves at all (there may
    #                       be no claims for it yet: a finding gets posted even for a stream that
    #                       opens tomorrow);
    #   any other name    — only if that wave is ACTUALLY declared in the registry (`Set-KnownWaves`).
    # Without this, a date-wave address wouldn't parse at all, and a finding just wouldn't reach the
    # neighbour.
    #
    # The wave name is matched WHOLE, not as a folded key: otherwise `wave6-compute/3` (a folder
    # name, not an address) would fold to wave `wave6` and get parsed as someone else's stream.
    #
    # The right-hand side takes the same optional prefix as Get-StreamNumberKey, plus a single
    # trailing letter for numbers like "3b" — plans do split one stream in two that way.
    if (-not $Raw) { return $null }
    $parts = @($Raw.Trim() -split '/')
    if ($parts.Count -ne 2) { return $null }
    $left = $parts[0].Trim()
    $right = $parts[1].Trim()
    if ($right -notmatch '^[sp#№]?\s*\d+[a-z]?$') { return $null }
    $wave = Get-WaveKey -Raw $left
    $isPlanWave = $left -match '^(wave\s*)?\d+$'
    $isDateWave = $left -match '^\d{4}-\d{2}-\d{2}$'
    $isKnownWave = $wave -and $wave -eq $left.ToLowerInvariant() -and $wave -in (Get-KnownWaves)
    if (-not ($isPlanWave -or $isDateWave -or $isKnownWave)) { return $null }
    return [pscustomobject]@{
        Wave   = $wave
        Stream = Get-StreamNumberKey -Raw $right
    }
}

function Get-DateWaveKey {
    # The name a wave gets substituted with by default: today's date. The `YYYY-MM-DD` form was
    # picked for three properties — it reads naturally, sorts like a date, and parses as an address
    # (`2026-08-24/2`).
    return (Get-Date).ToString('yyyy-MM-dd')
}

function Get-SeenTime {
    param($Claim)
    # A claim's checked-in timestamp. An unparsed one counts as the oldest possible: you can't use
    # it to decide what's more recent.
    $raw = $Claim.seen_at
    if ($raw -is [datetime]) { return $raw }
    $when = [datetime]::MinValue
    if ([datetime]::TryParse([string]$raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return $when
    }
    return [datetime]::MinValue
}

function Get-AutoWaveKey {
    param($Claims)
    # Which wave a session that never named one should join: whichever one has work happening RIGHT
    # NOW. Otherwise every session would start its own date-named wave, and neighbours would never
    # see each other.
    #
    # ‼️ Only waves that were substituted THEMSELVES. A named wave (`wave6`) has its stream numbers
    # coming from the plan: joining it would mean taking someone else's number, and half the
    # findings would go to the wrong place.
    $live = @($Claims | Where-Object { $_.Record.wave_auto -and $_.State -eq 'live' })
    if ($live.Count -eq 0) { return '' }
    # There might be more than one thing going on nearby — take the one checked in most recently.
    $freshest = @($live | Sort-Object -Property @{ Expression = { Get-SeenTime -Claim $_.Record } } -Descending)[0]
    return $freshest.WaveKey
}

function Get-NextStreamNumber {
    param($Claims, [string]$WaveKey, [string]$TreePath)
    # The next free stream number within a wave. A number is always needed — it's how the stream is
    # named in an address — but where there's no plan, a human has nowhere to take it from: it was
    # never assigned there.
    $here = Get-FolderKey -Path $TreePath
    $inWave = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })
    # This session's own earlier OPEN claim on this wave isn't a "neighbour": re-announcing the same
    # session must stay the same stream, otherwise an address it already told its neighbours would
    # change on its own.
    #
    # ‼️ A released one of ours doesn't count. While it did, the folder HANDED its old number to the
    # next tenant all by itself, without a single key: the stream was released properly, another one
    # announced from the same folder — and got the same address, and with it the released stream's
    # name memory and its mail. All while the tool promised, at release time, that findings for the
    # released stream would no longer be accepted. The decision says it plainly: after an honest
    # release this is an ordinary new announcement and a FREE number.
    #
    # ‼️ A superseded one of ours doesn't count either — for the same reason a released one doesn't:
    # another folder took its address, and this session is no longer entitled to carry the stream on
    # under it. We ask for the state with the SINGLE closed-ness signal, not with a field of the
    # file: supersession lives only in the parsed registry, and reading the field directly won't see
    # it.
    $mine = @($inWave | Where-Object {
            (Get-FolderKey -Path $_.Record.worktree) -eq $here -and $_.StreamKey -and
            -not $_.Closed
        })
    if ($mine.Count -gt 0) { return $mine[0].StreamKey }
    return (Get-FreeStreamNumber -Claims $Claims -WaveKey $WaveKey -TreePath $TreePath)
}

function Get-FreeStreamNumber {
    param($Claims, [string]$WaveKey, [string]$TreePath)
    # The next free number within a wave, NOT counting our own claim. Split from the function above
    # because, when contending for a number, our own claim is already sitting in the registry — and
    # counting it would make the session "yield" the number to itself and stay stuck in the same
    # spot.
    #
    # We count from the highest number taken, not from how many claims exist: a released stream
    # doesn't free its number (findings are addressed to it by that number), and the plan's numbers
    # don't run in sequence anyway.
    $here = Get-FolderKey -Path $TreePath
    $used = 0
    foreach ($claim in @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })) {
        # ‼️ We skip only our own OPEN claim. Our own RELEASED one still holds its number: it went
        # into the address findings were sent to, and its file can stay in the registry (announcing
        # rewrites a previous version's claim from a subfolder in place, not over the canonical
        # name). Not counting it would put two different streams with one address in the same wave,
        # and the invariant guard would call that handing out a number twice.
        #
        # ‼️ And our own SUPERSEDED one holds its number too: another folder took its address, and
        # there it's alive right now. Not counting it would free up a number a neighbour is running
        # this very moment.
        if ((Get-FolderKey -Path $claim.Record.worktree) -eq $here -and
            -not $claim.Closed) { continue }
        $digits = [regex]::Match([string]$claim.StreamKey, '^\d+')
        if (-not $digits.Success) { continue }
        $number = [int]$digits.Value
        if ($number -gt $used) { $used = $number }
    }
    return [string]($used + 1)
}

function Get-DistinctStreamNumber {
    param($Claims, [string]$WaveKey, [string]$StreamKey)
    # A distinguisher for a taken number: `2` → `2k`. Needed by the harmless way out of the "address
    # taken" refusal — the one printed FIRST. The case is real: `wave9/2` and `wave9/2k` are open in
    # the registry right now, and that distinguisher was invented by hand half a day after the
    # collision.
    #
    # ‼️ The form MUST PARSE as an address (digits and one letter), or the advice would send a session
    # to a stream nobody can reach by the main form of address. So the letter goes onto the number's
    # digits, not onto the whole of it: `3b` would give `3bk`, and such an address doesn't parse at
    # all.
    $digits = [regex]::Match([string]$StreamKey, '^\d+')
    if (-not $digits.Success) { return '' }
    $busy = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey } | ForEach-Object { $_.StreamKey })
    foreach ($letter in @('k', 'm', 'n', 'p', 'r', 's', 't')) {
        $candidate = "$($digits.Value)$letter"
        if ($candidate -notin $busy) { return $candidate }
    }
    return ''
}

function Test-ClaimHasPlan {
    param($Claim)
    # Does the stream have a wave plan. The answer decides where the tool sends a human with a
    # finding or a work summary: to a section of the plan, or a reply to the owner. Suggesting a
    # line in a file that doesn't exist is a dead end: the session can neither carry it out nor
    # figure out what to do instead.
    #
    # There's exactly one signal: was the wave substituted BY ITSELF. Whether the wave was named or
    # taken from the plan's file name, there's a plan — even if the plan file itself isn't recorded
    # in the claim, since the announce command doesn't always record it. An old-format claim carries
    # no such signal — its wave is named, so a plan exists.
    #
    # ‼️ We do NOT check whether the plan file exists. The claim is read from someone else's
    # worktree, while the plan lives in the stream's own tree: its absence "here" says nothing about
    # it, and the old check would have declared plan-less exactly those streams whose plan happens to
    # sit in a neighbouring folder.
    if (-not $Claim) { return $false }
    if (Test-ClaimHasField -Claim $Claim -Name 'wave_auto') { return -not $Claim.wave_auto }
    # A claim from the previous version carries no such signal at all, and "no signal means there's
    # a plan" lied to it precisely where it was made OUTSIDE a wave: such a stream would be told to
    # add a line to a plan section it doesn't have. So, absent the signal, we judge by the wave's
    # name: a plan's wave is called `waveN`, while a date or word is one the session substituted
    # itself.
    return ((Get-WaveKey -Raw ([string]$Claim.wave)) -match '^wave\d+$')
}

function Test-ClaimHasField {
    param($Claim, [string]$Name)
    # Does the claim have this field AT ALL. "No field" and "field omitted" have to be told apart: a
    # claim made by the previous version carries no such signal, and needs judging by a different rule.
    if (-not $Claim) { return $false }
    if ($Claim -is [System.Collections.IDictionary]) { return $Claim.Contains($Name) }
    return [bool]$Claim.PSObject.Properties[$Name]
}

function Get-ClaimOrder {
    param($Claim)
    # The ordering key among claims: announce time, and worktree path as a tiebreaker. Neither
    # field of a claim ever changes, so the order is the same for every session and doesn't shift
    # from run to run. Two decisions rest on it: who keeps the stream number in a dispute over it,
    # and whose claim founded the wave.
    #
    # If the time can't be parsed, we treat the claim as the latest one: the unknown shouldn't
    # displace the known.
    $when = [datetime]::MaxValue
    $raw = $Claim.claimed_at
    if ($raw -is [datetime]) {
        $when = $raw
    } else {
        $parsed = [datetime]::MinValue
        if ([string]$raw -and [datetime]::TryParse([string]$raw, [cultureinfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
            $when = $parsed
        }
    }
    return [pscustomobject]@{
        When = $when
        Path = Get-FolderKey -Path $Claim.worktree
    }
}

function Compare-ClaimOrder {
    param($Left, $Right)
    # The same FULL ordering key, but in the shape of a comparison: needed where records are walked
    # by hand rather than sorted on the way out. ‼️ The key has to be the full one (announce time,
    # tree path on a tie): by time alone, two claims filed in the same second would line up in a
    # different order for different sessions — and different sessions would then supersede different
    # records.
    $a = Get-ClaimOrder -Claim $Left
    $b = Get-ClaimOrder -Claim $Right
    if ($a.When -lt $b.When) { return -1 }
    if ($a.When -gt $b.When) { return 1 }
    return [string]::CompareOrdinal($a.Path, $b.Path)
}

function Get-NumberRivals {
    param($Claims, [string]$WaveKey, [string]$StreamKey, [string]$TreePath)
    # Claims from OTHER trees on the same address — the same wave and the same stream number. Closed
    # ones don't count: a released one has no session left to run the stream, a superseded one has
    # had its address taken away, and neither has anything to dispute. We ask for the state with the
    # SINGLE signal: supersession is visible only in the parsed registry.
    $here = Get-FolderKey -Path $TreePath
    return @($Claims | Where-Object {
            $_.WaveKey -eq $WaveKey -and $_.StreamKey -eq $StreamKey -and -not $_.Closed -and
            (Get-FolderKey -Path $_.Record.worktree) -ne $here
        })
}

function Test-YieldsStreamNumber {
    param($Mine, $Rivals)
    # Which of two sessions announced with the same number gives it up. The one who announced
    # earlier keeps the number; on a tie, the one whose tree path sorts first alphabetically. The
    # order is total and unchanging, so two sessions never shift at the same time and never trade
    # numbers back and forth forever.
    $me = Get-ClaimOrder -Claim $Mine
    foreach ($rival in $Rivals) {
        $other = Get-ClaimOrder -Claim $rival.Record
        if ($other.When -lt $me.When) { return $true }
        if ($other.When -eq $me.When -and [string]::CompareOrdinal($other.Path, $me.Path) -lt 0) {
            return $true
        }
    }
    return $false
}

function Test-WaveIsAuto {
    param($Claims, [string]$WaveKey)
    # Was the wave substituted by itself — meaning it has no plan.
    #
    # Decided by the wave's FIRST claim, not just any of them: whoever announced earliest founds the
    # wave, and the rest join it. The old rule ("plan-less if even one claim is plan-less") made an
    # entire planned wave plan-less the moment a single session announced into it without naming a
    # wave — and the text about plan sections would vanish for all its streams at once. We use the
    # same ordering as when disputing a stream number (announce time, then tree path as a
    # tiebreaker): it doesn't change from run to run, and every session gets the same answer.
    if (-not $WaveKey) { return $false }
    $inWave = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })
    if ($inWave.Count -gt 0) {
        $eldest = @($inWave | Sort-Object -Property @(
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).When } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).Path } }
            ))[0]
        return -not (Test-ClaimHasPlan -Claim $eldest.Record)
    }
    # No claims for that wave exist at all — only its name is known. A date-name only ever belongs to
    # a self-substituted wave.
    return [bool]($WaveKey -match '^\d{4}-\d{2}-\d{2}$')
}

function Test-AddresseeHasPlan {
    param($Claims, [string]$Raw, $Address, $Mine)
    # Does the RECIPIENT of a finding have a wave plan. One answer serves every "put it in the wave
    # loose ends" suggestion — let them drift apart by branch and half the refusals would point to a
    # plan section that doesn't exist.
    #
    # Order: the recipient's own claim (it knows its plan for sure) → the wave from the address →
    # our own wave. Knowing nothing about the recipient — behave as before and assume there's a
    # plan: suggesting a plan section where it exists is more harmless than staying quiet about it
    # where it's needed.
    $found = @(Find-Claims -Claims $Claims -Raw $Raw)
    if ($found.Count -gt 0) {
        return (@($found | Where-Object { Test-ClaimHasPlan -Claim $_.Record }).Count -gt 0)
    }
    $waveKey = if ($Address) {
        [string]$Address.Wave
    } elseif ($Mine) {
        Get-WaveKey -Raw ([string]$Mine.wave)
    } else {
        ''
    }
    if (-not $waveKey) { return $true }
    return -not (Test-WaveIsAuto -Claims $Claims -WaveKey $waveKey)
}

function Get-ClaimReadTimeoutMs {
    param([switch]$Strict)
    # How long we tolerate waiting for a neighbour's claim file to be released.
    #
    # It's not only neighbouring sessions that hold it for a fraction of a second — antivirus,
    # Windows Search, backup software, and a cloud-sync folder do too. `Read-BoardContent` reads the
    # findings board the same way, for the same reason, and it's had retries from the start too.
    #
    # A strict reader (choosing a stream number) gets an order of magnitude more: a missed claim
    # costs it a matching address FOREVER, and it's better to wait two seconds. A tolerant one (the
    # delivery hook, display) can't afford to wait that long: it's called on every session move and
    # has to be fast.
    return $(if ($Strict) { 2500 } else { 150 })
}

function Test-MissingPathFailure {
    param($Failure)
    # Tells "this doesn't exist" apart from "couldn't check". The first is a legitimate answer, the
    # second is trouble that a strict reader must say out loud.
    #
    # ‼️ We learn this FROM AN EXCEPTION, not a separate existence check. An existence check answers
    # "no" both where it's genuinely missing and where it's merely "not visible": a folder with
    # access closed off, a nonexistent drive, a dropped network share. That answer is
    # indistinguishable from an honest "no claims yet" — and that's exactly where every hole of this
    # class was found.
    $inner = $Failure.Exception
    while ($inner.InnerException) { $inner = $inner.InnerException }
    return ($inner -is [System.IO.FileNotFoundException]) -or
        ($inner -is [System.IO.DirectoryNotFoundException])
}

function Read-ClaimRecord {
    param([string]$Path, [switch]$Strict)
    # A neighbour's claim, with HONEST outcomes. Four states, and they must not be confused:
    #   ok         — read and parsed;
    #   missing    — the file doesn't exist at all (the tree never announced, the claim was removed);
    #   unreadable — the file EXISTS but couldn't be read;
    #   broken     — read it, but couldn't parse it.
    #
    # ‼️ Before, "couldn't read it" and "no file" answered the same way — empty, and on one try.
    # Because of this, a neighbour whose file was held by antivirus at that instant became
    # NONEXISTENT: a second session took its number, cheerfully reported success without a word of
    # warning, and the dispute-resolution loop read the same corrupted snapshot and didn't see the
    # rival either. The address collision stuck forever. Reproduced: the claim file was locked for a
    # second and a half, and two streams got the same number.
    #
    # ‼️ We learn "no file" FROM A FAILED OPEN, not a separate existence check: that one answers "no"
    # in cases where it's merely not visible too (see `Test-MissingPathFailure`).
    $deadline = (Get-Date).AddMilliseconds((Get-ClaimReadTimeoutMs -Strict:$Strict))
    $reason = 'reason unknown'
    while ($true) {
        try {
            # Share the file for read and write: a neighbour might be updating its own checked-in mark right now.
            $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
            $text = ''
            try {
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $text = $reader.ReadToEnd()
            } finally {
                $stream.Dispose()
            }
            $record = $null
            try {
                $record = $text | ConvertFrom-Json
            } catch {
                # Parsing failed — this is NOT grounds for a retry: a broken file is broken for good.
                return [pscustomobject]@{
                    State = 'broken'; Record = $null; Reason = (Get-FailureReason -Failure $_)
                }
            }
            if (-not $record.worktree) {
                return [pscustomobject]@{
                    State = 'broken'; Record = $null; Reason = 'the claim names no worktree'
                }
            }
            return [pscustomobject]@{ State = 'ok'; Record = $record; Reason = '' }
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                return [pscustomobject]@{ State = 'missing'; Record = $null; Reason = '' }
            }
            # Keep the real reason: "file busy" and "no permission" need different fixes.
            $reason = Get-FailureReason -Failure $_
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 30
    }
    return [pscustomobject]@{ State = 'unreadable'; Record = $null; Reason = $reason }
}

function Read-ClaimFile {
    param([string]$Path)
    # The forgiving version for callers who care more about not choking than about the truth: our
    # own claim, the "checked in" mark, release. Anyone using the registry to CHOOSE A NUMBER should
    # use `Read-ClaimRecord` in strict mode.
    return (Read-ClaimRecord -Path $Path).Record
}

function Get-ClaimState {
    param($Claim)
    # Four states instead of three — all honest:
    #   live     — claim open, mark fresh;
    #   silent   — claim open, mark stale (the session may have closed without releasing, or it may
    #              have been quietly working for hours: a subagent run, waiting on a build, a pause
    #              in the conversation);
    #   released — the stream was released properly, the session is gone;
    #   no claim — the stream was never announced at all; deciding that is not this function's job,
    #              it's the caller's.
    #
    # ‼️ A fifth state — "superseded" — isn't computed here and cannot be: it lives not in the claim
    # file but in the registry AS A WHOLE (another folder took the address, and it's said so in ITS
    # file). It's set by the loader's second pass — `Set-ClaimSupersessions`. Hence the rule for the
    # whole toolkit: ask a parsed registry record for the state (the `Closed` flag), not the `state`
    # field of your own file, or supersession stays invisible.
    if (-not $Claim) { return 'no claim' }
    if ([string]$Claim.state -eq 'released') { return 'released' }
    $seen = [datetime]::MinValue
    $raw = [string]$Claim.seen_at
    if (-not $raw -or -not [datetime]::TryParse($raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$seen)) {
        return 'silent'
    }
    if (((Get-Date) - $seen).TotalHours -lt (Get-AliveHours)) { return 'live' }
    return 'silent'
}

function Get-ClaimCurrentNames {
    param($Claim)
    # The names a stream carries RIGHT NOW. Three sources: what was recorded in the claim when it
    # announced (branch and working folder), its worktree's current branch, and — if this is our own
    # session — what's visible right this moment, for when git stays completely silent.
    if (-not $Claim) { return @(Get-CurrentKeys) }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($raw in @([string]$Claim.branch, [string]$Claim.worktree)) {
        $names.Add((Get-StreamKey -Raw $raw))
    }
    $here = Get-FolderKey -Path $Claim.worktree
    foreach ($tree in (Get-Worktrees)) {
        if ((Get-FolderKey -Path $tree.path) -ne $here) { continue }
        if ($tree.branch) { $names.Add((Get-StreamKey -Raw $tree.branch)) }
        $names.Add((Get-StreamKey -Raw $tree.path))
    }
    # Whether this is our own claim we check against the tree ROOT: a claim records that, not
    # whatever folder the session happened to be launched from.
    if ($here -eq (Get-FolderKey -Path (Get-TreeRoot))) {
        foreach ($key in (Get-CurrentKeys)) { $names.Add($key) }
    }
    return @($names | Where-Object { $_ } | Select-Object -Unique)
}

function Get-ClaimRememberedNames {
    param($Claim)
    # Names a stream only REMEMBERS: the branch got renamed, and the session announced again, which
    # rewrote the claim wholesale. Without this memory, a finding ALREADY ACCEPTED under the old name
    # wouldn't arrive and wouldn't hold up release — it would just vanish silently.
    #
    # ‼️ Remembering isn't the same as carrying: these names have different rights — see
    # `Resolve-ClaimNames`.
    if (-not $Claim) { return @() }
    $current = @(Get-ClaimCurrentNames -Claim $Claim)
    $names = foreach ($raw in @($Claim.former_branches)) {
        $key = Get-StreamKey -Raw ([string]$raw)
        if ($key -and $key -notin $current) { $key }
    }
    return @($names | Select-Object -Unique)
}

function Get-ClaimTakenFrom {
    param($Claim)
    # The succession field: the worktree folder this claim TOOK the address FROM. Empty means there
    # was no takeover.
    #
    # ‼️ A takeover is recorded in ONE'S OWN claim, not by editing someone else's file. The load-bearing
    # "one writer per file" invariant rests on that: someone else's claim already has a writer — that
    # folder's delivery hook — and it rewrites the document whole on every session move without
    # taking a lock. A mark in someone else's file would be erased by its next liveness update
    # AFTER we'd already reported success. The same choice buys compatibility: another folder's file
    # is untouched, so an older copy of the toolkit in a couple of dozen live worktrees sees exactly
    # what it saw yesterday.
    #
    # ‼️ The field name `taken_from` is a point of agreement with the test suite: it is declared there
    # as a constant, and the registry invariants look for a takeover by the same name. Let them drift
    # and the guard stops seeing takeovers and goes quiet in the very place it was built for.
    #
    # A missing field reads as "there was no takeover", not as corruption: previous-version claims
    # don't carry it at all, and copies of the toolkit of different ages are the norm, not the
    # exception.
    if (-not $Claim) { return '' }
    return (Get-FolderKey -Path ([string]$Claim.taken_from))
}

function Get-ClaimMoment {
    param($Raw)
    # A time out of a claim field: a date — as it is (JSON parsing turns the string into a date), a
    # string — by parsing, anything else — NOTHING. Nothing honestly means "we don't know", and any
    # decision resting on it has to pick the safe side itself rather than be handed an invented
    # value.
    if ($Raw -is [datetime]) { return $Raw }
    $parsed = [datetime]::MinValue
    if ([string]$Raw -and [datetime]::TryParse([string]$Raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Get-ClaimTakenAt {
    param($Claim)
    # The moment this claim TOOK the address — written next to the succession field and inherited
    # along with it. It, and it alone, decides whether a takeover edge applies.
    #
    # No field at all — that's how a claim from an unreleased interim version looks, which wrote only
    # the folder. Then the edge's moment is the moment this same claim was ANNOUNCED (takeover
    # parsing substitutes it itself), and failing that the edge applies unconditionally: not knowing
    # must not resurrect a ghost.
    if (-not $Claim) { return $null }
    return (Get-ClaimMoment -Raw $Claim.taken_at)
}

function Get-ClaimTakeovers {
    param($Claim)
    # EVERY takeover of this record: the current one (fields `taken_from` and `taken_at`) and each
    # past one from the `past_takeovers` list. Each carries ITS OWN address — the one it was taken
    # at — not the claim's current address: a past takeover's was a different one.
    #
    # ‼️ Why the list exists. The memory of a takeover lives in the claim of the TAKING folder, and a
    # folder has ONE claim: the moment that same folder took on the next stream, its file was
    # rewritten, the edge vanished — and the abandoned record of the previous folder became the
    # leader again. Silently: display didn't shout, accepting a finding reported "it'll get there",
    # the delivery hook carried the finding to an abandoned session. The same thing killed an A→B→C
    # chain the moment the MIDDLE folder re-announced. A takeover is an event in an ADDRESS'S
    # history, and reusing a folder doesn't undo it, any more than releasing does.
    #
    # ‼️ The names `past_takeovers` and the fields inside its records are a point of agreement with the
    # test suite: they are declared there as constants, and the registry invariants look for a
    # takeover by the same names. Let them drift and the guard stops seeing takeovers and goes quiet
    # in the very place it was built for.
    #
    # Duplicates of ONE takeover (the same address from the same folder) collapse, keeping the later
    # one by time: an earlier moment silences less than it should — a victim's claim filed between
    # two takeovers would slip out from under the edge. An unknown moment counts as the latest: an
    # edge without a moment applies unconditionally. But takeovers of one address from DIFFERENT
    # folders never collapse: those are different edges, and losing any of them resurrects its own
    # victim.
    if (-not $Claim) { return @() }
    $all = [System.Collections.Generic.List[object]]::new()
    $now = Get-ClaimTakenFrom -Claim $Claim
    if ($now) {
        $all.Add([pscustomobject]@{
                Wave   = (Get-WaveKey -Raw ([string]$Claim.wave))
                Stream = (Get-StreamNumberKey -Raw ([string]$Claim.stream))
                From   = $now
                At     = (Get-ClaimTakenAt -Claim $Claim)
            })
    }
    foreach ($past in @($Claim.past_takeovers)) {
        if (-not $past) { continue }
        $all.Add([pscustomobject]@{
                Wave   = (Get-WaveKey -Raw ([string]$past.wave))
                Stream = (Get-StreamNumberKey -Raw ([string]$past.stream))
                From   = (Get-FolderKey -Path ([string]$past.taken_from))
                At     = (Get-ClaimMoment -Raw $past.taken_at)
            })
    }
    $found = [ordered]@{}
    foreach ($move in $all) {
        # A takeover with no address is not a takeover: there's nothing to silence by it, and two
        # addressless neighbours would meet on an "address" made of two blanks. The test suite skips
        # them the same way.
        if (-not $move.Wave -or -not $move.Stream -or -not $move.From) { continue }
        $key = "$($move.Wave)/$($move.Stream)>$($move.From)"
        $known = $found[$key]
        if ($null -ne $known) {
            $a = if ($null -eq $known.At) { [datetime]::MaxValue } else { $known.At }
            $b = if ($null -eq $move.At) { [datetime]::MaxValue } else { $move.At }
            if ($a -ge $b) { continue }
        }
        $found[$key] = $move
    }
    return @($found.Values)
}

function Get-PastTakeoverLimit {
    # How many past takeovers a claim remembers. Twenty, with room to spare: over the registry's
    # whole live history takeovers happened in single digits, and the list travels from claim to
    # claim and only grows where one folder gets taken for new streams over and over. The limit
    # isn't about space but about making it impossible to inflate a claim file without end; the
    # excess is dropped from the oldest end.
    return 20
}

function Get-ClaimPastTakeovers {
    param($Previous, [string]$Wave, [string]$Stream, [string]$From, [ref]$Dropped)
    # The list of PAST takeovers for this folder's new claim: everything the previous claim
    # remembered, plus its own takeover. That's how the memory of a takeover survives the folder
    # being reused: a folder has one claim, and without this list the next stream would erase the
    # edge — the abandoned record of the previous folder would become the leader again, and silently
    # at that.
    #
    # `-Wave`, `-Stream` and `-From` name the takeover the new claim carries as its CURRENT one: we
    # keep that out of the list, or it would sit there twice. A duplicate does no harm (parsing
    # collapses them), but a human reads the claim file, and a repeat in it is one more question.
    $keep = [System.Collections.Generic.List[object]]::new()
    # ‼️ We normalize the folder in the comparison key. A claim stores it the way its author wrote it
    # (backslashes, its own letter case), while takeover parsing stores it normalized; comparing them
    # as-is, the current takeover wouldn't be recognized in the list and would land there a SECOND
    # time.
    $here = Get-FolderKey -Path $From
    $skip = if ($Wave -and $Stream -and $here) { "$Wave/$Stream>$here" } else { '' }
    foreach ($move in (Get-ClaimTakeovers -Claim $Previous)) {
        if ($skip -and "$($move.Wave)/$($move.Stream)>$($move.From)" -eq $skip) { continue }
        $keep.Add($move)
    }
    if ($keep.Count -eq 0) { return @() }
    # Ordered by the takeover's moment, oldest first; an unknown moment counts as the latest, as
    # everywhere else in takeover parsing. We drop from the top of the list, that is, the oldest: an
    # edge without a moment applies unconditionally, and losing it first would be the worst of the
    # choices.
    $sorted = @($keep | Sort-Object -Property @{ Expression = {
                if ($null -eq $_.At) { [datetime]::MaxValue } else { $_.At }
            }
        }, @{ Expression = { "$($_.Wave)/$($_.Stream)>$($_.From)" } })
    $limit = Get-PastTakeoverLimit
    if ($sorted.Count -gt $limit) {
        # ‼️ What was dropped gets named OUT LOUD — through the same `-Dropped` key that announcing
        # prints it by. There must be no silent losses in this mechanism: with every dropped edge the
        # abandoned record of the previous folder becomes the leader again, and display, accepting a
        # finding and the delivery hook won't say a word about it. The scene is practically
        # unreachable (it takes a twenty-first takeover by one folder), but being unreachable is no
        # reason to stay quiet.
        if ($Dropped) { $Dropped.Value = @($sorted[0..($sorted.Count - $limit - 1)]) }
        $sorted = @($sorted[($sorted.Count - $limit)..($sorted.Count - 1)])
    }
    return @($sorted | ForEach-Object {
            $entry = [ordered]@{
                wave       = $_.Wave
                stream     = $_.Stream
                taken_from = $_.From
            }
            # There may be no moment at all: that's how a takeover inherited from a previous-version
            # claim looks. An empty field would be read differently by different copies — so we don't
            # write it at all.
            if ($null -ne $_.At) { $entry.taken_at = $_.At.ToString('s') }
            $entry
        })
}

function Compare-ClaimTakeover {
    param($LeftAt, [string]$LeftPath, $RightAt, [string]$RightPath)
    # Who took the address LATER. Needed where several applicable edges point at one record: the
    # leader is whoever took it last — the address is with them. An unknown moment counts as the
    # latest, or a previous-version claim would silently yield the name to a current one.
    #
    # ‼️ The moment is taken from THE TAKEOVER ITSELF, not from the claim's current fields: the same
    # record's past takeover happened at another time, and measuring it by the current ones would be
    # measuring the wrong thing.
    $a = if ($null -eq $LeftAt) { [datetime]::MaxValue } else { $LeftAt }
    $b = if ($null -eq $RightAt) { [datetime]::MaxValue } else { $RightAt }
    if ($a -lt $b) { return -1 }
    if ($a -gt $b) { return 1 }
    return [string]::CompareOrdinal($LeftPath, $RightPath)
}

function Set-ClaimSupersessions {
    param($Entries)
    # ‼️ THE SECOND PASS of the registry loader: we silence records whose address another folder took.
    # Without it a takeover would be no more than a mark in one file, while the address would still
    # be run by two records — and who gets a finding would be decided by the order of a directory
    # listing.
    #
    # An edge runs from the claim that TOOK the address to the claim it was taken from: an open claim
    # names someone else's worktree folder, and both share one address. The silenced one loses its
    # names and its right to answer along with its state — handing out names comes next and counts it
    # as closed.
    #
    # ‼️ An edge applies by TIME, not by topology: it fails to apply exactly when it is PROVEN that the
    # victim folder's claim started LATER than the moment of the takeover. The old rule ("a takeover
    # by an already-superseded record doesn't apply") was a crutch against cycles and cost two holes
    # at once: an A↔B circle (the mechanism itself prints the loser a command to take the address
    # back, and both records started pointing at each other) left neither with a zero wait count — so
    # NOBODY was silenced and the address was run by two again; and an A→B→C chain of takeovers
    # resurrected the first record.
    #
    # The time rule also closes a third, hidden case: the address was released properly and the
    # FORMER folder announced on it again — its fresh claim would have been silenced by the old edge,
    # silently, with a success code.
    #
    # ‼️ No takeover moment — we take the moment the taking record was ANNOUNCED. Before, such an edge
    # applied unconditionally, and that locked the address behind the victim forever: however many
    # times it announced again, a momentless edge silenced each fresh claim of its too. There was no
    # way out at all — the printed takeover key answered "not needed", because by then there was
    # nobody left to run the address. Nothing is lost by the substitution: a succession field WITHOUT
    # a moment could only have been written by the unreleased interim version (old copies write
    # neither), and that one wrote both fields at the same instant of announcing.
    #
    # The taking record has no announce moment either — then, as before, unconditionally: not knowing
    # must not resurrect a ghost. The victim's announce time is unknown — the same: we silence until
    # proven otherwise.
    $records = @($Entries)
    $count = $records.Count
    if ($count -lt 2) { return $records }
    # ‼️ We build lists with an ORDINARY loop, not a pipeline: a pipeline unrolls anything enumerable,
    # and an empty list doesn't come out of it at all — the edge set came out empty and the tool
    # crashed on the very first use of it.
    $edges = [System.Collections.Generic.List[object]]::new()
    $drawn = @{}
    # We parse each record's takeovers ONCE: both the edges and the chain walk below read them.
    # ‼️ We lay them out with an ORDINARY loop into pre-sized slots, not with a pipeline: a pipeline
    # unrolls nested lists, and every record's takeovers would fuse into one.
    $moves = [object[]]::new($count)
    for ($i = 0; $i -lt $count; $i++) {
        $when = Get-ClaimMoment -Raw $records[$i].Record.claimed_at
        $mine = [System.Collections.Generic.List[object]]::new()
        foreach ($move in (Get-ClaimTakeovers -Claim $records[$i].Record)) {
            $mine.Add([pscustomobject]@{
                    Wave   = $move.Wave
                    Stream = $move.Stream
                    From   = $move.From
                    # The moment this edge lives by: its own, or failing that the moment the taking
                    # record was announced (see the write-up above).
                    At     = if ($null -eq $move.At) { $when } else { $move.At }
                })
        }
        $moves[$i] = @($mine)
    }
    for ($i = 0; $i -lt $count; $i++) {
        # ‼️ A RELEASED claim still holds its takeover. The temptation to skip it is strong ("the session
        # is gone, so there's nobody to take from"), but that's exactly the costliest consequence of
        # the defect: a stream moved, worked honestly and released — and the abandoned record in the
        # previous folder would become the leader again and keep the address looking alive. A finding
        # for such an address would be accepted with a cheerful success report, the sender would
        # relax and set up no fallback, and it could reach nobody at all. A takeover is an event in
        # an ADDRESS'S history, and releasing doesn't undo it.
        #
        # ‼️ And for the same reason edges are built from EVERY takeover of a record — the current one
        # and each past one. Each edge's address comes from THE TAKEOVER ITSELF: a past one's is
        # different, and the claim's current address has nothing to do with it.
        foreach ($move in $moves[$i]) {
            for ($j = 0; $j -lt $count; $j++) {
                if ($i -eq $j) { continue }
                $loser = $records[$j]
                if ((Get-FolderKey -Path $loser.Record.worktree) -ne $move.From) { continue }
                # The address must match in full: the field names a folder, not a stream, and the
                # NEXT stream may have announced in that same folder since — we have no right to
                # silence that one.
                if ($loser.WaveKey -ne $move.Wave -or $loser.StreamKey -ne $move.Stream) { continue }
                $started = Get-ClaimMoment -Raw $loser.Record.claimed_at
                if ($null -ne $move.At -and $null -ne $started -and $started -gt $move.At) { continue }
                $edges.Add([pscustomobject]@{ Taker = $i; Loser = $j; At = $move.At })
                $drawn["$i>$j"] = $true
            }
        }
    }
    $takenBy = @{}
    foreach ($edge in $edges) {
        if ($drawn.ContainsKey("$($edge.Loser)>$($edge.Taker)")) {
            # ‼️ The edges are MUTUAL: both claims took the address from each other, and neither is
            # proven to have started later than the other's takeover — that's what a take-back
            # circle closed within one second looks like. They must be told apart by the FULL
            # ordering key (announce time, tree path on a tie): by time alone, two claims of the
            # same second would line up differently for different sessions, and different sessions
            # would silence different records. The edge of the ELDER record survives — by the same
            # rule that settles a dispute over a stream number.
            if ((Compare-ClaimOrder -Left $records[$edge.Taker].Record `
                        -Right $records[$edge.Loser].Record) -gt 0) {
                continue
            }
        }
        $known = $takenBy[$edge.Loser]
        # There may be several takers: then the leader is the last one — the address is with them.
        if ($null -ne $known -and (Compare-ClaimTakeover -LeftAt $known.At `
                    -LeftPath (Get-FolderKey -Path $records[$known.Taker].Record.worktree) `
                    -RightAt $edge.At `
                    -RightPath (Get-FolderKey -Path $records[$edge.Taker].Record.worktree)) -ge 0) {
            continue
        }
        $takenBy[$edge.Loser] = $edge
    }
    foreach ($loser in @($takenBy.Keys)) {
        $entry = $records[$loser]
        $entry.Closed = $true
        # A released one we don't rename: it's closed without the takeover anyway, and it matters
        # more to a human that the stream was released properly than that its address was taken
        # afterwards.
        if ($entry.State -ne 'released') {
            $entry.State = 'superseded'
            # ‼️ The "address was taken" flag is ONE for the whole toolkit, like the closed-ness flag.
            # Before, three places (release, the delivery hook, display) compared the state against
            # the word "superseded" directly, and that same word was printed to a human: let someone
            # reword it in the display and release and delivery would go quiet without a hint.
            $entry.Superseded = $true
        }
        $entry.TakenBy = $records[$takenBy[$loser].Taker]
        $entry.TakenAt = $takenBy[$loser].At
    }
    # ‼️ Who runs the address NOW is a separate question from who took it from this record. In a chain
    # of takeovers a record is silenced by the middle folder while the last one runs the address; and
    # the middle one may have taken on another stream since. Telling the victim "the middle folder
    # took your address, and it's running something else now" would be only half true: the address IS
    # being run, just in a third place. So we look for the leader by ADDRESS, not by edge.
    $leaders = @{}
    foreach ($entry in $records) {
        if ($entry.Closed -or -not $entry.WaveKey -or -not $entry.StreamKey) { continue }
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        # Two leaders on one address is a legacy of the defect, and choosing between them for a
        # human is not allowed: we stay quiet, and display is what shouts about the doubling.
        $leaders[$address] = if ($leaders.ContainsKey($address)) { $null } else { $entry }
    }
    # ‼️ Where the address ended up IN THE END is a separate question both from who took it from this
    # record and from who runs it now. There may be no leading record at all (the stream moved and
    # ended there), and then the human is told the last folder of the chain. Before, the chain broke
    # at the FIRST link whose record had changed address: the middle folder of an A→B→C chain, having
    # taken on the next stream, wasn't silenced — and the answer "folder B took the address" sent the
    # human where there is nothing about that address at all. Takeover memory is what lets us go
    # further: it remembers the address left B even when B's own claim is about another stream now.
    #
    # The pointer is built from EVERY takeover in the registry, not from applicable edges: an edge
    # says whether a record is silenced, while the chain says where the ADDRESS went, and the second
    # stays true even where the first isn't there.
    $passedTo = @{}
    for ($i = 0; $i -lt $count; $i++) {
        foreach ($move in $moves[$i]) {
            $key = "$($move.Wave)/$($move.Stream)>$($move.From)"
            $known = $passedTo[$key]
            # Several took from one folder — we take the last: the address is with them.
            if ($null -ne $known -and (Compare-ClaimTakeover -LeftAt $known.At `
                        -LeftPath (Get-FolderKey -Path $records[$known.Index].Record.worktree) `
                        -RightAt $move.At `
                        -RightPath (Get-FolderKey -Path $records[$i].Record.worktree)) -ge 0) {
                continue
            }
            $passedTo[$key] = [pscustomobject]@{ Index = $i; At = $move.At }
        }
    }
    foreach ($loser in @($takenBy.Keys)) {
        $entry = $records[$loser]
        $entry.AddressLedBy = $leaders["$($entry.WaveKey)/$($entry.StreamKey)"]
        # We walk the chain down to the record this address wasn't taken from any further. A circle
        # is broken by the "already seen" clause: the registry will survive this change dirty too.
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        $seen = @{ "$($entry.File)" = $true }
        $at = $records[$takenBy[$loser].Taker]
        while ($null -ne $at -and -not $seen.ContainsKey([string]$at.File)) {
            $seen[[string]$at.File] = $true
            $next = $passedTo["$address>$(Get-FolderKey -Path $at.Record.worktree)"]
            if ($null -eq $next -or $seen.ContainsKey([string]$records[$next.Index].File)) { break }
            $at = $records[$next.Index]
        }
        $entry.AddressChainEnd = $at
    }
    return $records
}

function Resolve-ClaimNames {
    param($Entries)
    # ‼️ Who answers to which name is decided FOR THE WHOLE REGISTRY AT ONCE, not per claim
    # separately. Otherwise one name legitimately points to two streams, and closing a finding with
    # a named address becomes GLOBAL instead of PERSONAL: whoever closed it first snuffs it out for
    # everyone.
    #
    # What happened once name memory shipped without this rule (reproduced on the real repository):
    # branch names get reused in a live tree, and a new session took a name a neighbour merely
    # remembered. The finding went to both, both were told "acted on — go ahead and close it," and
    # the one that only remembered the name closed the other one's finding. The true recipient never
    # saw anything, released green with an empty inbox, and the author got an "acknowledged" — from a
    # stream it never named. Before name memory existed, this never happened at all: a finding always
    # reached exactly the right session.
    #
    # It all follows from a single rule: NO MORE THAN ONE STREAM ANSWERS TO ANY GIVEN NAME.
    #   • whoever carries the name right now — always answers to it;
    #   • whoever only remembers it — only if NO ONE ELSE carries or remembers it either;
    #   • a CLOSED stream doesn't answer at all: there's no session left (released) or its address
    #     was taken away (superseded), and a closed stream's memory shouldn't take a name away from a
    #     live neighbour and snuff out someone else's findings. ‼️ We ask for closed-ness with the
    #     single flag: supersession is visible only in the registry as a whole, and were we to read
    #     the file's field, the losing session would go on answering to names that have already
    #     passed to the address's new owner — that is, it would intercept its mail.
    # A name remembered by two and carried by no one belongs to neither: accepting such a finding
    # refuses out loud (no recipient), and that's more honest than silently delivering it at random.
    $carried = @{}
    $recalled = @{}
    foreach ($entry in $Entries) {
        if ($entry.Closed) { continue }
        foreach ($name in $entry.Current) { $carried[$name] = 1 + [int]$carried[$name] }
        foreach ($name in $entry.Remembered) { $recalled[$name] = 1 + [int]$recalled[$name] }
    }
    foreach ($entry in $Entries) {
        if ($entry.Closed) {
            $entry.Keys = @()
            $entry.Silenced = @($entry.Current + $entry.Remembered | Select-Object -Unique)
            continue
        }
        $keys = [System.Collections.Generic.List[string]]::new()
        $silenced = [System.Collections.Generic.List[string]]::new()
        foreach ($name in $entry.Current) { $keys.Add($name) }
        foreach ($name in $entry.Remembered) {
            if ([int]$carried[$name] -eq 0 -and [int]$recalled[$name] -eq 1) {
                $keys.Add($name)
            } else {
                $silenced.Add($name)
            }
        }
        $entry.Keys = @($keys | Select-Object -Unique)
        $entry.Silenced = @($silenced)
    }
    return $Entries
}

function Get-NameRememberers {
    param($Claims, [string]$Name)
    # Who REMEMBERS this name, carried by no one right now. Needed by exactly one refusal: a name
    # remembered by two and carried by no one deliberately belongs to neither (see
    # `Resolve-ClaimNames`), and the human needs to be told exactly that, not left guessing about
    # worktrees.
    if (-not $Name) { return @() }
    $live = @($Claims | Where-Object { -not $_.Closed })
    if (@($live | Where-Object { $Name -in $_.Current }).Count -gt 0) { return @() }
    return @($live | Where-Object { $Name -in $_.Remembered })
}

function Get-StreamNames {
    param($Claim, $Claims)
    # The names a stream answers to RIGHT NOW, taking the whole registry into account. The single
    # answer to "what is this stream called" — used by all three: accepting a finding, the delivery
    # hook, and releasing a stream.
    #
    # The registry is mandatory: without it there's no way to know whether a live neighbour carries
    # the remembered name — and that's exactly what the "no more than one stream per name" rule
    # rests on.
    if (-not $Claim) { return @(Get-CurrentKeys) }
    $here = Get-FolderKey -Path $Claim.worktree
    foreach ($entry in @($Claims)) {
        if ((Get-FolderKey -Path $entry.Record.worktree) -ne $here) {
            continue
        }
        return @($entry.Keys)
    }
    # No claim found in the registry (it was just filed, the registry was read earlier) — answer at
    # least to the names it carries: those aren't in dispute by construction.
    return @(Get-ClaimCurrentNames -Claim $Claim)
}

function Get-ClaimFiles {
    param([string]$Dir, [switch]$Strict)
    # A listing of the claims directory — the FIRST read of the registry, and it must fail honestly.
    #
    # ‼️ This is where the quietest hole in the whole mechanism used to sit. Listing through the
    # shell with a name pattern, when access to the folder's contents is closed off, RETURNS AN
    # EMPTY LIST and reports no error (verified; three other listing methods in the same experiment
    # fail honestly). There was nothing to catch: since no exception was thrown, neither retries nor
    # a loud refusal ever fired, and the strict guard further up the code simply never got called.
    # In practice it looked like this: a neighbour is running the first stream, a second session
    # announces and gets the SAME number with a success code; "who owns this" answers "nobody took
    # it"; a finding for the live stream gets "this stream never announced."
    #
    # Hence the rule for the whole toolkit: ask the file system only in ways where "couldn't" is
    # distinguishable from "empty". Here that means an exception, not an empty answer.
    #
    # ‼️ "No folder" is a legitimate answer of "no claims yet": the first session to announce is the
    # one that creates the registry. But a DEAD PATH answers with the exact same failure — a dropped
    # drive, a vanished network share — and that's real trouble, and passing it off as an empty
    # registry is wrong: on a dead drive, "who owns this" would answer "nobody took the task," and
    # release would answer "this session has no claim," both with a success code. So we tell them
    # apart: is there AT LEAST ONE existing folder further up the path. There is — the path is alive,
    # the registry just doesn't exist yet. There isn't — we say so out loud.
    #
    # Everything else (no permission, access closed off) is trouble right away, no further questions asked.
    $deadline = (Get-Date).AddMilliseconds((Get-ClaimReadTimeoutMs -Strict:$Strict))
    $reason = 'reason unknown'
    while ($true) {
        try {
            return @([System.IO.Directory]::GetFiles($Dir, '*.json'))
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                if (Test-PathReachable -Path $Dir) { return @() }
                $reason = "the path is unreachable altogether: $(Get-FailureReason -Failure $_)"
                break
            }
            $reason = Get-FailureReason -Failure $_
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 30
    }
    if ($Strict) {
        throw "couldn't list the claim registry ($Dir), and deciding on it blind isn't an option — a stream number would collide with a neighbour's, and someone else's task would look unclaimed. Reason: $reason"
    }
    return @()
}

function Get-Claims {
    param([string]$Dir, [switch]$Strict)
    # The whole registry, with computed state. Ordered by wave and stream number, so display doesn't
    # jump around from run to run.
    #
    # ‼️ `-Strict` is for whoever uses this list to CHOOSE A STREAM NUMBER, look up a task's owner, or
    # decide a finding's fate. It cannot afford to miss a neighbour: a skipped claim gives two streams
    # the same address, an answer of "nobody took this task," and a finding accepted by an already-
    # released stream — all silently and permanently. Tolerant readers (the delivery hook on every
    # move, board display) stay quiet and work with what they got: there, a miss costs one invisible
    # line, not an address.
    #
    # We do NOT check whether the folder exists separately: an existence check answers "no" in cases
    # where it's really "not visible," and that's exactly the substitution every hole of this class
    # rested on. The listing answers instead — it tells "no folder" apart from "couldn't check."
    $claims = [System.Collections.Generic.List[object]]::new()
    foreach ($file in (Get-ClaimFiles -Dir $Dir -Strict:$Strict)) {
        $read = Read-ClaimRecord -Path $file -Strict:$Strict
        # ‼️ For the strict reader, ANY outcome besides "read it" is a refusal out loud. To whoever
        # is choosing a number or looking up a task's owner, "file busy" and "file broken" are the
        # same thing: the file is right there, but the stream vanishes from the list. Before, only
        # the first one refused out loud, while the second passed silently — and an empty file, one
        # truncated midway, or one left by a different version of the toolkit gave TWO STREAMS THE
        # SAME ADDRESS without a single warning (reproduced across all four kinds of corruption).
        #
        # No point retrying on a broken file — it's broken for good; but staying quiet is even less
        # of an option — unlike busy, this won't fix itself.
        if ($Strict -and $read.State -eq 'unreadable') {
            throw "couldn't read a neighbour's claim ($file), and choosing a stream number blind isn't an option — it would collide with a neighbour's. Reason: $($read.Reason). Try again in a few seconds."
        }
        if ($Strict -and $read.State -eq 'broken') {
            throw "a neighbour's claim is broken and won't parse ($file) — while it stays this way its stream is invisible, and its number would get handed out a second time. Reason: $($read.Reason). Remove the file or fix it."
        }
        $record = $read.Record
        if (-not $record) { continue }
        $state = Get-ClaimState -Claim $record
        $claims.Add([pscustomobject]@{
                File         = $file
                Record       = $record
                WaveKey      = Get-WaveKey -Raw ([string]$record.wave)
                StreamKey    = Get-StreamNumberKey -Raw ([string]$record.stream)
                Current      = @(Get-ClaimCurrentNames -Claim $record)
                Remembered   = @(Get-ClaimRememberedNames -Claim $record)
                Keys         = @()
                Silenced     = @()
                State        = $state
                # ‼️ The SINGLE "this record is closed" flag — EVERY deciding place in the toolkit rests
                # on it: rivals for a number, handing out names, the recipient check, a finding's
                # fate, release, closing a finding, both liveness marks and the delivery hook.
                # Before, each of them asked in its own way, and some read the `state` field of
                # THEIR OWN file directly, bypassing the parsed registry. A takeover isn't reflected
                # in the loser's file at all (its file is never touched), so it would go on getting
                # the mail of the address's new owner and snuffing it out for them — the same
                # trouble as a doubled address, only from the other side.
                Closed       = ($state -eq 'released')
                # Separate from closed-ness: "the address was taken" and "the stream was released"
                # are cured differently and told to a human differently. Filled in by the second
                # pass below, as is `TakenBy`.
                Superseded   = $false
                # The record that took this one's address: filled in by the second pass below.
                TakenBy      = $null
                # The moment of THE VERY takeover that silenced this record. The taking record may
                # have several takeovers (its current one and each past one), and its current fields
                # would speak of a different event — while the human is told about this one.
                TakenAt      = $null
                # Who runs this address NOW. Not the same as the taking record: in a chain of
                # takeovers the middle folder silences and the last one runs.
                AddressLedBy = $null
                # The last folder in this address's chain of takeovers — where it ended up in the
                # end. Needed where no leading record is left at all: sending a human to the middle
                # folder of the chain is wrong, there's nothing about that address there any more.
                AddressChainEnd = $null
            })
    }
    # ‼️ Names are handed out in a SECOND pass, once the whole registry is assembled: which stream a
    # name answers to depends on whether anyone else carries or remembers it too. That question
    # can't be settled one claim at a time, and a wrong answer to it hands the same finding to two
    # streams at once.
    #
    # ‼️ The ordering is FULL: wave, number, announce time, tree path. Two records on one address don't
    # happen in a healthy registry, but the change ships onto a dirty one — and there wave-and-number
    # weren't enough, and the order of two such records was settled by the directory listing.
    # Everything else followed from that: display served them up one way one run and another the
    # next, and a human couldn't compare two runs by eye. The key's tail is the same one that settles
    # a dispute over a number (`Get-ClaimOrder`) — otherwise display and dispute resolution would
    # name different records as the elder.
    # ‼️ Address takeovers are parsed BEFORE names are handed out: a silenced record loses its right to
    # answer to names along with its address, and handing out names looks at the closed-ness flag.
    # Swap the passes and the losing session would take the branch name away from the address's new
    # owner.
    return @((Resolve-ClaimNames -Entries (Set-ClaimSupersessions -Entries @($claims))) |
            Sort-Object -Property @(
                @{ Expression = { $_.WaveKey } }
                @{ Expression = { $_.StreamKey } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).When } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).Path } }
            ))
}

function Find-Claims {
    param($Claims, [string]$Raw)
    # Who a finding is addressed to. First try it as "wave/stream" — that's the main form of
    # address, because that's how the stream is named in the plan; failing that, as a branch or
    # folder name.
    $address = Get-StreamAddress -Raw $Raw
    if ($address) {
        return @($Claims | Where-Object {
                $_.WaveKey -eq $address.Wave -and $_.StreamKey -eq $address.Stream
            })
    }
    $key = Get-StreamKey -Raw $Raw
    if (-not $key) { return @() }
    return @($Claims | Where-Object { $key -in $_.Keys })
}

function Get-CurrentClaim {
    param([string]$Dir, [switch]$Strict)
    # ‼️ `-Strict` is for whoever needs "no claim" and "the file happens to be busy right now" to
    # mean different things. Releasing a stream against a busy file would answer "this session has
    # no claim — nothing to release," and the session would close without releasing: neighbours would
    # go on addressing findings to a stream that, as far as they know, is still alive. Same root
    # cause as choosing a number — a single read attempt and emptiness standing in for the truth.
    #
    # The key is the worktree ROOT, not the current folder: otherwise a session that has stepped into
    # a subfolder looks for its claim somewhere other than where it put it, and gets an emptiness
    # indistinguishable from "there is no claim".
    $path = Get-ClaimPath -Dir $Dir -TreePath (Get-TreeRoot)
    $read = Read-ClaimRecord -Path $path -Strict:$Strict
    if ($Strict -and $read.State -eq 'unreadable') {
        throw "couldn't read this session's claim ($($read.Reason)) — releasing the stream blind isn't an option, or it would stay listed as yours. Try again in a few seconds."
    }
    # ‼️ Broken is a refusal too. Otherwise the owner of a broken claim is invisible TO THEMSELVES:
    # release answers "this session has no claim, nothing to release" and succeeds, the session
    # closes, and neighbours keep addressing findings to a stream they believe is alive.
    if ($Strict -and $read.State -eq 'broken') {
        # ‼️ Name the path IN FULL, and give a way out that actually works. Before, the refusal
        # didn't name the file at all and suggested announcing again — and announcing reads the
        # whole registry strictly, hits the same file, and refuses too. A human would read their own
        # refusal and have no way out of it.
        throw "this session's claim is broken and won't parse ($path) — while it stays this way the stream is invisible, both to neighbours and to you. Reason: $($read.Reason). Remove this file, then announce again."
    }
    return $read.Record
}

function Find-ClaimByWorktree {
    param($Claims, [string[]]$Paths)
    # The second route to ONE'S OWN claim — by an exact match on the worktree folder recorded in it.
    #
    # Why. A claim file's name is derived from the path, and claims filed by a PREVIOUS version from
    # a subfolder of the tree sit under that subfolder's key. They aren't found by the canonical key
    # (the tree root), and release would answer "nothing to release" with a success code — that is,
    # it would orphan exactly the streams this change is being made for.
    #
    # ‼️ This is READING, not migration: not a single file is created or deleted, and a record found is
    # edited in place. And the match is EXACT only — not "starts with", not "sits inside": otherwise
    # a session would take over the claim of a neighbouring tree nested in its folder.
    if (-not $Claims) { return $null }
    $wanted = @($Paths | ForEach-Object { Get-FolderKey -Path $_ } |
            Where-Object { $_ } | Select-Object -Unique)
    if ($wanted.Count -eq 0) { return $null }
    $mine = @($Claims | Where-Object {
            (Get-FolderKey -Path $_.Record.worktree) -in $wanted
        })
    # We ask for open-ness with the SINGLE flag: a superseded record looks open in its own file (its
    # file is never touched), and were we to read the field directly, a session whose address was
    # taken would treat it as its own live claim — releasing it and closing other people's findings
    # by it.
    $open = @($mine | Where-Object { -not $_.Closed })
    # Two open claims on one folder don't happen, by the registry's rule. Should it happen anyway —
    # choosing for the human isn't allowed: we'd release one at random while the other went on
    # keeping the address alive.
    if ($open.Count -gt 1) {
        $names = @($open | ForEach-Object { $_.File }) -join ', '
        throw "there are $($open.Count) open claims on this worktree folder in the registry at once ($names) — which of them to release is not the mechanism's call. Remove the spare one and try again."
    }
    if ($open.Count -eq 1) { return $open[0] }
    if ($mine.Count -eq 1) { return $mine[0] }
    return $null
}

function Get-ClaimEntry {
    param($Claims, $Claim, [string]$Path)
    # The PARSED registry record matching this claim. Needed where all we have is the claim file
    # itself while the question is about state: a takeover lives not in the file but in the registry
    # as a whole, and a session will never learn of it from its own file.
    #
    # We look by file first (that's exact even where the claim sits under a non-canonical name), then
    # by the recorded worktree folder.
    if (-not $Claims) { return $null }
    if ($Path) {
        $wanted = Get-FolderKey -Path $Path
        foreach ($entry in @($Claims)) {
            if ((Get-FolderKey -Path $entry.File) -eq $wanted) { return $entry }
        }
    }
    if ($Claim -and $Claim.worktree) {
        $here = Get-FolderKey -Path $Claim.worktree
        # An open one is preferred: in a reused folder a released record sits alongside.
        $mine = @($Claims | Where-Object { (Get-FolderKey -Path $_.Record.worktree) -eq $here })
        $open = @($mine | Where-Object { -not $_.Closed })
        if ($open.Count -eq 1) { return $open[0] }
        if ($mine.Count -eq 1) { return $mine[0] }
    }
    return $null
}

function Test-ClaimClosed {
    param($Claims, $Claim, [string]$Path)
    # Is the record closed — ONE answer for the whole toolkit. Released-ness is visible in the file
    # itself, a takeover only in the parsed registry; so we ask for both, and the registry isn't
    # required: without it we answer exactly as before (released or not).
    if ($Claim -and [string]$Claim.state -eq 'released') { return $true }
    $entry = Get-ClaimEntry -Claims $Claims -Claim $Claim -Path $Path
    if (-not $entry) { return $false }
    return [bool]$entry.Closed
}

function Get-RegistryLockPath {
    param([string]$Dir)
    # The lock lives INSIDE the claim registry itself: it guards exactly that, and travels with it
    # (tests supply their own board — the registry and the lock follow it automatically).
    #
    # ‼️ The name deliberately doesn't end in `.json`: claim listing reads by that pattern, and the
    # lock would show up in it as a ghost stream — no wave, no number, yet still taking a slot in the
    # neighbour list and the wave's stream count.
    #
    # Joined with plain string ops: joining paths through the shell dies outright on a missing drive.
    return [System.IO.Path]::Combine($Dir, '.claim.lock')
}

function Get-RegistryLockWaitSeconds {
    # How long we wait for a neighbour's lock in total before moving on without it (saying so out
    # loud). The critical section is a few folder reads and writing one file — a fraction of a
    # second — so even a dozen sessions announcing at once fit within the limit with room to spare.
    return 30
}

function Get-RegistryLockSpeakAfterSeconds {
    # After this long, a silent wait starts to look like the tool hanging: a human is waiting on a
    # response to their command and, seeing nothing, starts hammering the keyboard.
    return 2
}

function Get-PathState {
    param([string]$Path)
    # What sits at a path: `container` — a folder, `leaf` — a file, `none` — nothing, `unknown` —
    # couldn't tell. Its own function, because the path check itself CAN THROW: on a missing drive
    # it doesn't answer "no", it throws an error, and under strict shell mode that error used to leak
    # the tool a raw English message about a drive that doesn't exist.
    # ‼️ We ask the system ONE question, not two in a row. The world changes between two answers:
    # sessions of a wave create the registry folder at the same moment, and if a neighbour managed to
    # create it between the question "is this a folder?" and the question "does it exist at all?",
    # the result was a confident but FALSE "there's a file here, not a folder." What was dangerous
    # wasn't the wrong answer itself, but the advice built on it: it pointed to deleting the folder
    # where neighbours' claims live. Under load, the lie showed up on every fifth run.
    #
    # A path's properties come from a single system answer: has a folder — it's a folder; exists
    # without the folder flag — it's a file; doesn't exist at all — its own kind of failure says so.
    try {
        $attributes = [System.IO.File]::GetAttributes($Path)
        $kind = if ($attributes -band [System.IO.FileAttributes]::Directory) { 'container' } else { 'leaf' }
        return [pscustomobject]@{ Kind = $kind; Reason = '' }
    } catch {
        if (Test-MissingPathFailure -Failure $_) {
            return [pscustomobject]@{ Kind = 'none'; Reason = '' }
        }
        return [pscustomobject]@{ Kind = 'unknown'; Reason = (Get-FailureReason -Failure $_) }
    }
}

function Test-PathReachable {
    param([string]$Path)
    # Is the path alive at all: is there at least one existing folder somewhere above it. Needed to
    # tell "the registry doesn't exist yet" (normal: the first session to announce creates it) apart
    # from "the path doesn't exist at all" (a dropped drive or network share) — the system answers
    # both cases with the exact same failure.
    #
    # This check only lies toward "no," and that's the safe direction: doubt leads to a refusal out
    # loud, not a quiet "there are no claims."
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($current) {
        if ((Get-PathState -Path $current).Kind -eq 'container') { return $true }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return $false
}

function Get-BlockingAncestor {
    param([string]$Path)
    # The nearest ancestor of the path that exists and is NOT a folder. Needed for exactly one
    # honest refusal: when a file occupies the board's folder, the registry folder can't be created,
    # but the file above it up the path is to blame, not the registry itself — and that's the one
    # that needs naming, or a human has nothing to go look for.
    # The path is cut with plain string ops, not shell parsing: that dies on a missing drive, and
    # here is exactly where we're figuring out what's wrong with the path.
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($current) {
        $state = Get-PathState -Path $current
        if ($state.Kind -eq 'container') { return '' }
        if ($state.Kind -eq 'leaf') { return $current }
        if ($state.Kind -eq 'unknown') { return '' }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return ''
}

function Assert-RegistryDir {
    param([string]$Dir)
    # The registry folder must actually BE a folder, and that needs saying right away — in plain
    # words, with the real reason.
    #
    # ‼️ Three traps live here, and all three have fired for real.
    #
    # First: the "does this path exist" check answers "yes" for a file too, and `New-Item -ItemType
    # Directory -Force` on top of a file quietly does nothing and reports success. From there, the
    # lock would honestly wait half a minute, print a scary "lock wasn't released, the neighbouring
    # session must have crashed" — and still fail one line down, while writing the claim.
    #
    # Second: judge by the OUTCOME, not by two separate probes ("no folder" + "path busy" =
    # "there's a file there"). Probe-then-probe is a race: sessions of a wave create the registry
    # folder at the same moment, and a neighbour can create it between the two probes. The very first
    # session would accuse its neighbour of putting a file where the folder should be, and announcing
    # would fail with a confident but false refusal.
    #
    # Third: a silent success report left the refusal WITHOUT a reason — "reason unknown" — even
    # though the reason is known and nameable: the path is blocked by a file further up the tree.
    # And on a missing drive, things never even got this far: the path check itself threw first.
    $state = Get-PathState -Path $Dir
    if ($state.Kind -eq 'container') { return }
    if ($state.Kind -eq 'leaf') {
        throw "the path meant for the claims folder is occupied: there's a file there, not a folder ($Dir). While it stays there, no session can announce — take a look at what it is and clear the path"
    }
    if ($state.Kind -eq 'unknown') {
        throw "couldn't check the claims folder ($Dir). Reason: $($state.Reason)"
    }
    $reason = ''
    try {
        New-Item -ItemType Directory -Path $Dir -Force -ErrorAction Stop | Out-Null
    } catch {
        # A neighbour might have created the folder at the very same instant — that's not a failure,
        # it's business as usual; we judge below, by the outcome. But we keep the reason: if it
        # didn't work out, the human needs THAT reason, not our guess.
        $reason = Get-FailureReason -Failure $_
    }
    $state = Get-PathState -Path $Dir
    if ($state.Kind -eq 'container') { return }
    if ($state.Kind -eq 'leaf') {
        throw "the path meant for the claims folder is occupied: there's a file there, not a folder ($Dir). While it stays there, no session can announce — take a look at what it is and clear the path"
    }
    if ($state.Kind -eq 'unknown') {
        throw "couldn't check the claims folder ($Dir). Reason: $($state.Reason)"
    }
    # No folder, and creating it didn't report any trouble. So we look further up the path — most
    # often there's a file there.
    $blocking = Get-BlockingAncestor -Path $Dir
    if ($blocking) {
        throw "the path to the claims folder is blocked by a file ($blocking) — remove or rename it, or no session will be able to announce"
    }
    if (-not $reason) { $reason = 'creating the folder reported no trouble, but the folder is not there' }
    throw "couldn't set up the claims folder ($Dir). Reason: $reason"
}

function Enter-RegistryLock {
    param([string]$Dir)
    # A cross-process lock on CHOOSING A STREAM NUMBER. Returns an open file handle for the lock —
    # hand it back to `Exit-RegistryLock` later; $null means "couldn't take the lock."
    #
    # ‼️ What it protects against (without this, it'll get "cleaned up" as dead weight). Choosing a
    # number reads a snapshot of the whole registry, and without a lock that isn't atomic. Six
    # sessions opened at once: all read the still-empty registry and take number 1; in the
    # dispute-resolution loop, two of them count the next free number from the very same snapshot at
    # the same time — and both take 2; whichever re-reads the registry before the other manages to
    # write its own shift never sees its rival and exits the loop with number 2, while the other,
    # comparing itself to the "elder," also stays at 2. Retrying doesn't save this: sessions exit the
    # loop based on a STALE snapshot. Under the lock, the second session reads a registry that
    # already has the first one's claim recorded, and the numbers diverge properly.
    #
    # The lock is a HELD FILE HANDLE, opened with no shared access. Not a named mutex (the toolkit
    # gets dropped into any project, and cross-process mutexes don't work outside Windows) and not
    # "the file exists, so it's taken."
    #
    # ‼️ Why not go by whether the file exists — this is the crux of it. Then a crashed session
    # leaves the file behind, and to keep the board from locking up forever you'd need a staleness
    # threshold and a takeover. And a takeover can't be made safe with off-the-shelf means: the
    # decision "the lock is stale, I'm taking it" is made based on ONE file state, while a DIFFERENT
    # one gets taken away — the holder may have already left, a neighbour may have already set a fresh
    # lock, and that's the one taken away while the neighbour is working inside it. Verified on a rig
    # with eight processes: double entry gets caught. A held handle has none of this problem at all —
    # the system closes it itself when the process dies, and the lock frees up at that very instant
    # (verified by killing the holder: the next one got in immediately, no need to wait out a
    # threshold).
    #
    # ‼️ The lock file is NEVER deleted — not on exit, not during cleanup. The handle holds the lock,
    # not the file — delete it, and a second process would create the file anew and take the lock on
    # the NEW file, while the first one still holds the old one. Both would end up inside at once. An
    # empty file in the registry folder is a trivial price, and it doesn't show up in claim listings
    # (see the name, above).
    #
    # ‼️ How reliable this actually is — honestly, with no promises made. On Windows, mutual
    # exclusion between processes is enforced by the OS itself, and this has been verified in
    # practice here. On other systems, .NET fakes shared access with advisory kernel locks, and
    # those don't work everywhere: some network filesystems don't support them at all, and they can
    # be turned off by a runtime setting. There's no way to test this on the development machine.
    #
    # The mutual-exclusion rig in the test suite catches this class of breakage, but it can't be
    # relied on as a guarantee: it only runs where THIS repository's tests get run — on one machine.
    # A toolkit dropped into someone else's project doesn't run tests and doesn't need Python.
    #
    # Hence the rule for whoever carries this toolkit onward: the claim registry must live on an
    # ordinary local disk. If it ends up on a network share, run the mutual-exclusion rig there
    # before trusting the lock to work.

    # ‼️ The lock does NOT allow re-entry: a second attempt by the same process gets rejected exactly
    # like a foreign one. There's no nested entry today, and none should be added: a session would
    # lock itself out, wait out the limit, and move on without the lock, printing a warning about a
    # stuck neighbour that doesn't exist. If nested entry is ever needed, pass the already-held handle
    # in — don't take the lock a second time.
    Assert-RegistryDir -Dir $Dir
    $path = Get-RegistryLockPath -Dir $Dir
    $started = Get-Date
    $deadline = $started.AddSeconds((Get-RegistryLockWaitSeconds))
    $spoke = $false
    while ($true) {
        try {
            $stream = [System.IO.File]::Open($path, 'OpenOrCreate', 'Write', 'None')
            try {
                # Who's holding it — for a human who peeks inside the file. None of the lock's actual
                # workings depend on this: the handle holds the lock, not what's written inside.
                $stream.SetLength(0)
                $note = [System.Text.Encoding]::UTF8.GetBytes(
                    "process $PID@$([System.Environment]::MachineName), taken $((Get-Date).ToString('s'))")
                $stream.Write($note, 0, $note.Length)
                $stream.Flush()
            } catch {
                # The note didn't get written — no big deal, the lock is already ours. No reason to
                # fail the announcement over it: nothing about how the lock works depends on this note.
            }
            if ($spoke) { [Console]::Out.WriteLine('The claim registry lock is free now — continuing the announcement.') }
            return $stream
        } catch {
            # ‼️ A contested lock and an unfixable obstacle look identical here — a failed file open —
            # but the fix is the opposite in each case: a contest should be waited out, an obstacle is
            # pointless to wait for (half a minute of waiting, a story about a neighbouring session,
            # and it still fails one line down, while writing the claim; before the lock existed, the
            # refusal here was instant and honest).
            #
            # We tell them apart by the kind of error: "file locked by another process" arrives as an
            # ordinary I/O error, while a bad path and missing permissions come as their own kinds
            # (verified). Trying "is the file still there" instead won't work — that check is
            # inherently racy — the holder can leave between the failure and the check, and an
            # ordinary contest gets mistaken for an obstacle.
            $failure = $_.Exception
            while ($failure.InnerException) { $failure = $failure.InnerException }
            #
            # ‼️ The directory check below is NOT the racy "is the file still there" check warned
            # about above, and the difference matters: a lock file may appear and vanish under a
            # normal contest, but a DIRECTORY never sits at the lock's path during normal work —
            # the lock is always a file. So a directory there is an obstacle, not a contest, in any
            # order of events. It is checked explicitly because the systems disagree about the
            # exception: Windows reports opening a directory as an access error (already hopeless
            # by the kind test), while Linux reports it as an ordinary I/O error — indistinguishable
            # from a neighbour holding the lock. Without this, a Linux session waits out the full
            # limit, blames a neighbour that does not exist, and then picks a number with no lock at
            # all. Caught by this repository's own CI, on Linux, where the development machine could
            # never have seen it.
            $hopeless = ($failure -isnot [System.IO.IOException]) -or
                ($failure -is [System.IO.DirectoryNotFoundException]) -or
                (Test-Path -LiteralPath $path -PathType Container)
            if ($hopeless) {
                throw "couldn't set up the claim registry lock ($path). Reason: $(Get-FailureReason -Failure $_)"
            }
        }
        if ((Get-Date) -ge $deadline) { return $null }
        if (-not $spoke -and ((Get-Date) - $started).TotalSeconds -ge (Get-RegistryLockSpeakAfterSeconds)) {
            # A silent wait reads to a human as the tool hanging. Write to the normal output stream,
            # not the error stream — in this tool, only refusals live there. And not through the
            # shell's regular output either — that would come back to the caller instead of the lock
            # handle.
            $spoke = $true
            [Console]::Out.WriteLine("Waiting for the claim registry lock: another session is announcing right now ($path). Will wait up to $(Get-RegistryLockWaitSeconds)s.")
        }
        # Randomized pause: an identical one would sync sessions into lockstep, and they'd keep
        # bumping into each other in unison.
        Start-Sleep -Milliseconds (20 + (Get-Random -Maximum 40))
    }
}

function Exit-RegistryLock {
    param($Handle)
    # Releasing the lock means closing the handle. The file stays put on purpose — see the write-up
    # in `Enter-RegistryLock`: deleting it would let a second process in.
    #
    # Stay silent on any failure: even if the handle fails to close here, the system will close it
    # when the process exits, and there's no reason to fail an announcement that already succeeded
    # over this.
    if (-not $Handle) { return }
    try {
        $Handle.Dispose()
    } catch {
        return
    }
}

function Write-ClaimFile {
    param([string]$Path, $Claim)
    # Write through a temp file next to it: a reader will never see a half-written claim. There's
    # only one writer for the file (this session itself), so no locks or retries are needed.
    # The path is cut with plain string ops, for the same reason as on the board: shell parsing dies
    # outright on a missing drive with a raw English message.
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    if ($dir -and -not (Test-Path -LiteralPath $dir -PathType Container)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    # ‼️ The temp file's name does NOT contain `.json`: the registry listing takes files by that
    # pattern, and a write cut short would otherwise land in the registry as a scrap — and a scrap
    # now stops everything that depends on the address. Clean it up on failure too: we don't need
    # litter in the registry folder.
    $temp = [System.IO.Path]::ChangeExtension($Path, ".tmp-$PID")
    try {
        [System.IO.File]::WriteAllText($temp, ($Claim | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::Move($temp, $Path, $true)
    } catch {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Update-ClaimSeen {
    param([string]$Dir, [string]$TreePath, [string]$Path, $Claims)
    # The "session is active" mark. Called by the delivery hook on every move and has to stay quiet:
    # a failed mark update is no reason to get in the way of work.
    #
    # `-Path` — for when the claim file has already been found and it is NOT the canonical one: that's
    # how claims filed by a previous version from a subfolder of the tree sit. Without this, such a
    # claim got mail but never once got a liveness mark: a day later it landed in the owner's stuck
    # summary, and neighbours stopped counting the session as alive. ‼️ No second writer appears here:
    # it was found by an EXACT match on the worktree folder, so it belongs to this same session. The
    # ban on writing into SOMEONE ELSE'S claim file stands.
    #
    # ‼️ `-Claims` — the parsed registry, if the caller already has it. Stamping a record in ANY closed
    # state is not allowed, not just a released one: a superseded record looks open in its own file,
    # and any new session opened in the old folder would resurrect a ghost on its very first move — a
    # fresh mark gives it back the look of a working stream, along with its address and its mail.
    try {
        if (-not $Path) { $Path = Get-ClaimPath -Dir $Dir -TreePath $TreePath }
        $claim = Read-ClaimFile -Path $Path
        if (-not $claim) { return }
        if (Test-ClaimClosed -Claims $Claims -Claim $claim -Path $Path) { return }
        # Through `Add-Member -Force`, not assignment: a previous-version claim may have no mark
        # field at all, and assignment would fail — that is, such a claim would stay silent forever.
        $claim | Add-Member -NotePropertyName seen_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
        Write-ClaimFile -Path $Path -Claim $claim
    } catch {
        return
    }
}

function Get-ProfilePlansFolder {
    param([string]$StartDir)
    # Where wave plans live in THIS project: the `.parallel-streams.md` profile, `## Plans` section,
    # a path in backticks. Parsed ONCE for the whole toolkit, called by two consumers — the
    # hint hook (deciding what counts as editing the wave plan) and the selection of places streams
    # edit jointly. Let them drift apart and the hook would consider one thing "the plan" while the
    # overlap list means another.
    #
    # The heading is matched CASE-INSENSITIVELY: a human writes the profile by hand, and "## plans"
    # is the same section.
    #
    # ‼️ No folder named — answer empty, no defaults tied to one particular project's path. A
    # hardcoded path would never match in someone else's project: the hook would silently come back
    # with zero, and the wave plan would land in the overlap list — both indistinguishable from
    # working correctly.
    if (-not $StartDir) { $StartDir = $PWD.Path }
    try {
        $dir = $StartDir
        while ($dir) {
            $profilePath = Join-Path $dir '.parallel-streams.md'
            if (Test-Path -LiteralPath $profilePath -PathType Leaf) {
                $text = [System.IO.File]::ReadAllText($profilePath, [System.Text.UTF8Encoding]::new($false))
                $section = [regex]::Match($text, '(?msi)^##\s+Plans\s*$(.*?)(?=^##\s|\z)')
                if (-not $section.Success) { return '' }
                foreach ($found in [regex]::Matches($section.Groups[1].Value, '`([^`]+)`')) {
                    $value = ($found.Groups[1].Value.Trim() -replace '\\', '/') -replace '^\./', ''
                    if ($value -match '\s') { continue }
                    # A file name is never the plans folder: the section also marks up files in backticks.
                    if ($value -notmatch '/$' -and $value -match '\.[A-Za-z0-9]{1,5}$') { continue }
                    if (-not $value.EndsWith('/')) { $value += '/' }
                    return $value
                }
                return ''
            }
            # We don't climb above the repository root: someone else's project with its own profile starts there.
            if (Test-Path -LiteralPath (Join-Path $dir '.git')) { break }
            $dir = Split-Path -Parent $dir
        }
    } catch {
        return ''
    }
    return ''
}

function Get-SharedByDesignPattern {
    # Places streams edit JOINTLY by design, not by oversight: every session touches the wave plan
    # (a status line, adding a finding). Counting that as an overlap would make the hint noisy, and a
    # noisy hint is a useless one.
    #
    # The plans folder comes from the same profile parsing the hint hook uses. Empty means the
    # project has no plans, and therefore no places that are shared by design either: then we filter
    # out NOTHING. An empty pattern would match any name at all and hide every real overlap at once.
    $plans = Get-ProfilePlansFolder
    if (-not $plans) { return '' }
    return "/$plans"
}

function Get-TouchedFiles {
    # Files a stream has already touched: its own branch against the common ancestor with main, plus
    # anything uncommitted. This shows an overlap with a neighbour BEFORE it turns into a merge
    # conflict — and, more importantly, before a session offers the owner someone else's task.
    $files = [System.Collections.Generic.List[string]]::new()
    try {
        $base = (& git merge-base HEAD origin/main 2>$null)
        if ($LASTEXITCODE -eq 0 -and $base) {
            foreach ($name in @(& git diff --name-only $base.Trim() HEAD 2>$null)) { $files.Add($name) }
        }
        # The machine-readable form of the summary names paths from the tree ROOT, not from the
        # launch folder (verified from a subfolder): for a session that stepped into a subfolder the
        # touched-files list lines up with its neighbours', and there's nothing to fix here.
        foreach ($line in @(& git status --porcelain 2>$null)) {
            if ($line.Length -le 3) { continue }
            $name = $line.Substring(3).Trim().Trim('"')
            # A rename comes as "was -> became": we care about what's there now.
            if ($name -match ' -> ') { $name = ($name -split ' -> ')[-1].Trim().Trim('"') }
            $files.Add($name)
        }
    } catch {
        return @()
    }
    $shared = Get-SharedByDesignPattern
    $clean = foreach ($name in $files) {
        if (-not $name) { continue }
        $normalized = ($name -replace '\\', '/').Trim().ToLowerInvariant()
        if (-not $normalized) { continue }
        # An empty pattern means "no plans in this project": then we filter out nothing, otherwise
        # it would match any name at all and the touched-files list would come out empty for everyone.
        if ($shared -and "/$normalized" -like "*$shared*") { continue }
        $normalized
    }
    # A ceiling: this list goes into the claim that every neighbour reads on every move. A sweeping
    # edit across a thousand files would already overlap within the first couple hundred.
    return @($clean | Select-Object -Unique | Select-Object -First 200)
}

function Update-ClaimFiles {
    param([string]$Dir, [string]$TreePath, [string]$Path, $Claims, [int]$MaxAgeMinutes = 5)
    # The touched-files list is recomputed no more than once every few minutes: the hook gets called
    # on EVERY session move, and two git calls per move would be a cost for nothing.
    #
    # `-Path` — the same second route as for the liveness mark: a previous-version claim from a
    # subfolder sits under a non-canonical name, and without this a neighbour would never see its
    # overlaps by file.
    try {
        if (-not $Path) { $Path = Get-ClaimPath -Dir $Dir -TreePath $TreePath }
        $path = $Path
        $claim = Read-ClaimFile -Path $path
        if (-not $claim) { return }
        # ‼️ Closed in ANY sense — don't touch it, by the same single flag and for the same reason as the
        # liveness mark: a fresh touched-files list on a superseded record looks like the work of a
        # live session and brings the ghost back into neighbours' overlaps.
        if (Test-ClaimClosed -Claims $Claims -Claim $claim -Path $path) { return }
        $when = [datetime]::MinValue
        if ($claim.files_at -and [datetime]::TryParse([string]$claim.files_at, [cultureinfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
            if (((Get-Date) - $when).TotalMinutes -lt $MaxAgeMinutes) { return }
        }
        $claim | Add-Member -NotePropertyName files -NotePropertyValue (Get-TouchedFiles) -Force
        $claim | Add-Member -NotePropertyName files_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
        Write-ClaimFile -Path $path -Claim $claim
    } catch {
        return
    }
}

function Get-Overlaps {
    param($Claims, $MyClaim)
    # Neighbors editing the same files. Exactly the case the owner has no way to notice: a session
    # offers them "let's also do this while we're at it," they don't know it's a piece of another
    # stream, and they say yes. An overlap by file is visible mechanically, in advance.
    if (-not $MyClaim -or -not $MyClaim.files) { return @() }
    # Filter out the places shared by design here too, not only when the list was built: the claim
    # might have been written by an older version of the hook, and then the wave plan would count as
    # an overlap all over again.
    $shared = Get-SharedByDesignPattern
    $mine = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($name in @($MyClaim.files)) {
        $text = [string]$name
        if (-not $text) { continue }
        if ($shared -and "/$text" -like "*$shared*") { continue }
        [void]$mine.Add($text)
    }
    if ($mine.Count -eq 0) { return @() }
    $here = Get-FolderKey -Path $MyClaim.worktree
    $found = [System.Collections.Generic.List[object]]::new()
    foreach ($claim in $Claims) {
        if ($claim.State -ne 'live') { continue }
        if ((Get-FolderKey -Path $claim.Record.worktree) -eq $here) { continue }
        $common = @(@($claim.Record.files) | Where-Object { $_ -and $mine.Contains([string]$_) })
        if ($common.Count -eq 0) { continue }
        $found.Add([pscustomobject]@{ Claim = $claim; Files = @($common) })
    }
    return @($found)
}

function Get-StuckRecords {
    param($Records, $Claims, [string[]]$KnownKeys)
    # Entries with nowhere to go. Right now NOBODY sees them: there's no recipient, and the sender
    # already got told it succeeded. The mechanism's silence is indistinguishable from "the neighbour
    # has nothing to say."
    $stuck = [System.Collections.Generic.List[object]]::new()
    $deadline = (Get-Date).AddDays(-(Get-SilentDaysBeforeStuck))
    foreach ($record in $Records) {
        $raw = [string]$record.to
        # An "everyone" entry doesn't land here: it has its own shelf life and its own meaning —
        # it might simply not have been acted on by everyone yet. An "acknowledged" notice, even
        # more so: there's no one to chase it down for, and no reason to.
        if ((Get-StreamKey -Raw $raw) -in @('*', '**')) { continue }
        if ([string]$record.kind -eq 'ack') { continue }
        $addressed = @(Find-Claims -Claims $Claims -Raw $raw)
        $live = @($addressed | Where-Object { $_.State -eq 'live' })
        if ($live.Count -gt 0) { continue }
        $when = [datetime]::MinValue
        $parsed = [datetime]::TryParse([string]$record.at, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)
        $reason = ''
        if (@($addressed | Where-Object { $_.State -eq 'released' }).Count -gt 0) {
            # Released is a case with nothing left to wait for at all: the session is gone and won't be back.
            $reason = 'the stream was released'
        } elseif (@($addressed | Where-Object { $_.Closed }).Count -eq $addressed.Count -and
            $addressed.Count -gt 0) {
            # Every record on the address is superseded, and no leader is left. The chain of
            # takeovers no longer leads here (all but the last are silenced in it), so what we have
            # is a circle: the records took the address from each other, and there is nothing to wait
            # for on it. The reason is not "released", and lying about a release is not allowed — the
            # human would go looking for the outcome of a released stream that nobody ever wrote.
            $reason = 'the address was handed on, and no record leads it any more'
        } elseif ($addressed.Count -gt 0) {
            if ($parsed -and $when -gt $deadline) { continue }
            $reason = "the stream has been silent since $(Format-Stamp -Raw $addressed[0].Record.seen_at)"
        } else {
            if ($parsed -and $when -gt $deadline) { continue }
            # The keys passed in here are ones that CHECKED IN: a tree by itself doesn't make a
            # recipient — the session behind it might have closed a week ago, while a finding
            # addressed by branch name still waits for it.
            if ((Get-StreamKey -Raw $raw) -in $KnownKeys) { continue }
            $reason = 'no claim for this stream, and no fresh check-in from the tree either'
        }
        $stuck.Add([pscustomobject]@{ Record = $record; Reason = $reason })
    }
    return @($stuck)
}

function Format-Stamp {
    param($Raw)
    # A claim's timestamp is stored as a string, but JSON parsing turns it into a date, and then
    # `[string]` prints it in the system locale's format ("08/21/2026 23:34:13"). We show the human
    # the same look regardless of which path the value arrived by.
    if ($Raw -is [datetime]) { return $Raw.ToString('yyyy-MM-dd HH:mm') }
    $text = [string]$Raw
    if (-not $text) { return 'no timestamp' }
    $when = [datetime]::MinValue
    if ([datetime]::TryParse($text, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return $when.ToString('yyyy-MM-dd HH:mm')
    }
    return $text
}

function Get-ClaimFolderMarks {
    param($Claim)
    # Evidence about a record's WORKTREE FOLDER — the very evidence both refusals ("folder taken",
    # "address taken") are explained by. A refusal names someone else's folder to a human, and today
    # there's nowhere for them to see it: display printed the address, the name and the branch, but
    # not the folder. So the evidence and the marks live in one place with the display — having read
    # the refusal, a human finds that same folder in the list and decides in a second.
    $marks = [System.Collections.Generic.List[string]]::new()
    $here = Get-FolderKey -Path $Claim.Record.worktree
    if (-not $here) { return @($marks) }
    # "This is you" comes first, because it's a human's first question to a refusal: "is this about
    # me?" We check against the tree ROOT, not the current folder: a claim records the root, and a
    # session launched from a subfolder would otherwise not recognize its own record in the list.
    if ($here -eq (Get-FolderKey -Path (Get-TreeRoot))) { $marks.Add('this is you') }
    # We say "folder is gone" only when the path is REACHABLE at the same time. A dropped drive and a
    # vanished network share answer with the same failure as a deleted folder — and that's "not
    # visible", not "not there", and passing one off as the other is wrong: the decision of whether
    # someone else's record may be touched rests on that difference. We don't know — we stay quiet.
    $state = Get-PathState -Path $here
    if ($state.Kind -eq 'none' -and (Test-PathReachable -Path $here)) { $marks.Add('folder is gone') }
    # "No check-in for a long time" — the mark is older than the SHARED liveness threshold. An open
    # record's state says the same thing ("silent"), but the mark sits here deliberately: a human
    # reads a refusal's evidence next to the folder, not gathered from different corners of the line.
    if (-not $Claim.Closed) {
        $seen = [datetime]::MinValue
        $raw = [string]$Claim.Record.seen_at
        $known = $raw -and [datetime]::TryParse($raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$seen)
        if (-not $known -or ((Get-Date) - $seen).TotalHours -ge (Get-AliveHours)) {
            $marks.Add('no check-in for a long time')
        }
    }
    return @($marks)
}

function Get-DoubledAddresses {
    param($Claims)
    # Addresses run by several open records at once. This is a legacy of the "a takeover doubles the
    # address" defect: the change ships onto a dirty registry, and such pairs are already lying there.
    #
    # ‼️ This has to be said OUT LOUD and on a line of its own. While the doubling shows up only as two
    # similar-looking lines in a list, the choice of "which of the two is the real one" is made not by
    # a human but by the order of a directory listing — and it is made by accepting a finding,
    # silently.
    #
    # A superseded record doesn't count as a doubling: its address was taken by an explicit key, there
    # is one leader on it, and there's nothing to shout about.
    $seen = [ordered]@{}
    foreach ($entry in @($Claims)) {
        if ($entry.Closed) { continue }
        if (-not $entry.WaveKey -or -not $entry.StreamKey) { continue }
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        if (-not $seen.Contains($address)) { $seen[$address] = [System.Collections.Generic.List[object]]::new() }
        $seen[$address].Add($entry)
    }
    $doubled = [System.Collections.Generic.List[object]]::new()
    foreach ($address in $seen.Keys) {
        if ($seen[$address].Count -lt 2) { continue }
        $doubled.Add([pscustomobject]@{ Address = $address; Claims = @($seen[$address]) })
    }
    return @($doubled)
}

function Get-ClaimAddressHolder {
    param($Claim)
    # Where the address ended up IN THE END: the chain of takeovers is walked by registry parsing
    # (it has every record at once there) and it puts the end of the chain into the record itself. An
    # A→B→C chain is legitimate, and telling the victim A "superseded by B" is a half-truth: that
    # stream isn't in B any more.
    if ($Claim.AddressChainEnd) { return $Claim.AddressChainEnd }
    # ‼️ The fallback — by silencing edges alone, for records assembled without registry parsing. It
    # breaks at the first link whose record changed address, which is why the main route is above.
    $seen = @{}
    $at = $Claim
    while ($at.TakenBy -and -not $seen.ContainsKey([string]$at.File)) {
        $seen[[string]$at.File] = $true
        $at = $at.TakenBy
    }
    return $at
}

function Get-ClaimAddressFate {
    param($Claim)
    # The fate of a silenced record's address — ONE answer for the whole toolkit: display, release,
    # the delivery hook and accepting a finding speak about it in the same words and by the same
    # signal.
    #
    # ‼️ "Handed on to folder X" holds exactly when X RUNS THAT SAME ADDRESS. The taking folder may
    # have released the stream since, or taken on the next one — then "handed on to X" lies twice:
    # the human will go and send a finding to X, and nobody there runs that address. Such an address
    # has no leading record left at all, and that is the RIGHT outcome (the stream moved and ended),
    # but it must be visible rather than look like a handover to a live session.
    # We look for the leader BY ADDRESS, not by edge: in a chain of takeovers this record is silenced
    # by the middle folder while the address is run by the last — and that's the one the human needs.
    if ($Claim.AddressLedBy) {
        return [pscustomobject]@{
            Holder   = $Claim.AddressLedBy
            StillLed = $true
            Text     = "handed on to $([string]$Claim.AddressLedBy.Record.worktree)"
        }
    }
    $holder = Get-ClaimAddressHolder -Claim $Claim
    if (-not $holder -or $holder -eq $Claim) {
        return [pscustomobject]@{
            Holder = $null; StillLed = $false; Text = 'the address was taken by another worktree folder'
        }
    }
    $folder = [string]$holder.Record.worktree
    $text = if ($holder.State -eq 'released') {
        "folder $folder took the address, and that stream has since been released"
    } elseif ($holder.WaveKey -and $holder.StreamKey) {
        "folder $folder took the address, but it is running a different stream now ($($holder.WaveKey)/$($holder.StreamKey))"
    } else {
        "folder $folder took the address"
    }
    return [pscustomobject]@{ Holder = $holder; StillLed = $false; Text = $text }
}

function Get-ClaimTakenAwayText {
    param($Claim)
    # One line about the address's fate — for the places where the fork "is it still being run or
    # not" doesn't change the rest of the text.
    return (Get-ClaimAddressFate -Claim $Claim).Text
}

function Get-LeaderlessAddresses {
    param($Claims)
    # Addresses that have records open IN THEIR OWN FILES but not a single leader. That's what a
    # session that doesn't exist from the outside looks like: its claim file is open, it believes it
    # is running the stream, yet accepting a finding for that address will refuse and the delivery
    # hook will bring nothing.
    #
    # ‼️ In itself this isn't corruption but a legitimate end to an address's history: the stream moved
    # and ended. But staying quiet about it is not allowed, for exactly the reason we don't stay quiet
    # about a doubled address: a human can't make the "is this session alive" call until they've been
    # told about it.
    $records = @($Claims | Where-Object { $_.WaveKey -and $_.StreamKey })
    $byAddress = [ordered]@{}
    foreach ($entry in $records) {
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        if (-not $byAddress.Contains($address)) {
            $byAddress[$address] = [System.Collections.Generic.List[object]]::new()
        }
        $byAddress[$address].Add($entry)
    }
    $found = [System.Collections.Generic.List[object]]::new()
    foreach ($address in $byAddress.Keys) {
        $group = @($byAddress[$address])
        if (@($group | Where-Object { -not $_.Closed }).Count -gt 0) { continue }
        # A released record raises no questions: the stream is finished, and there's nobody to ask
        # about it. We're after the one whose file is open — a session that believes it is leading
        # stands behind it.
        $orphans = @($group | Where-Object { $_.State -ne 'released' })
        if ($orphans.Count -eq 0) { continue }
        $found.Add([pscustomobject]@{ Address = $address; Claims = $orphans })
    }
    return @($found)
}

function Format-ClaimLine {
    param($Claim)
    $record = $Claim.Record
    $name = if ($record.name) { " `"$($record.name)`"" } else { '' }
    $tasks = if ($record.tasks) { ", tasks $($record.tasks)" } else { '' }
    # ‼️ We show remembered names. A stream answers to more than just the name it carries now — and
    # until these names were visible anywhere, a human couldn't notice either that a finding would go
    # to the old address, or that a name had been taken away from the stream. Names taken away get
    # marked separately: a finding will NOT arrive at those, because someone else carries or
    # remembers that name.
    $kept = @($Claim.Remembered | Where-Object { $_ -in $Claim.Keys })
    $lost = @($Claim.Silenced)
    $memory = ''
    if ($kept.Count -gt 0) { $memory += ", remembers names: $($kept -join ', ')" }
    if ($lost.Count -gt 0) { $memory += ", names taken away: $($lost -join ', ')" }
    # The worktree folder goes next to the branch, not at the end of the line: both refusals name
    # exactly it to a human, and they should find it by eye in the same place they looked for the
    # branch.
    $folder = if ($record.worktree) { ", folder $($record.worktree)" } else { '' }
    foreach ($mark in (Get-ClaimFolderMarks -Claim $Claim)) { $folder += ", $mark" }
    # ‼️ For a superseded record we name WHERE the address went. Without that, "superseded" is a dead
    # end: a human sees the record is silenced but doesn't know which folder to look for the stream
    # in or whom to send a finding to. The taking folder may have moved on further, released, or
    # taken on the next stream — then we say so plainly rather than pass it off as a handover to a
    # live session on the same address.
    $state = if ($Claim.TakenBy -and $Claim.Superseded) {
        Get-ClaimTakenAwayText -Claim $Claim
    } else {
        $Claim.State
    }
    return "  $($record.wave)/$($record.stream)$name$tasks — $state (checked in $(Format-Stamp -Raw $record.seen_at), branch $($record.branch)$folder$memory)"
}
