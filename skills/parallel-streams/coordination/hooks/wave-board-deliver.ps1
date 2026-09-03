#Requires -Version 7
<#
Delivery hook: brings the session what a neighbour put on the wave board FOR IT.

Why. A session reads the wave plan once, at start, from its own worktree. An addition to the plan
never reaches it afterward — not through the file (in its worktree the file stays unchanged), and
not through re-reading (there is none). This hook closes that gap: a neighbour drops a finding on
the shared board, and the session picks it up on its own.

Two events, because there are two gaps:
  • Start  — session start AND recovery after context compaction. Shows EVERYTHING open that is
             addressed to this stream, and resets the shown-log: after compaction the reminder must
             come back, or it gets lost exactly where the work is long.
  • Prompt — a regular turn. Shows only what the session hasn't seen yet this session.

The context cost is budgeted up front and held down by three guards: one record is shown to the
session once (the log), at most five per turn, and nothing at all to another stream. The cap delays,
it doesn't drop: whatever wasn't shown arrives on later turns.

Along the way the hook also marks its own worktree as alive (the beacon
`.claude/.cache/wave-board-alive.txt`): the tool and the wave-plan-edit reminder use it to tell a
working session apart from an abandoned worktree. The mark is set on every turn, before any early
exit — see the comment next to it.

The hook blocks NOTHING, and on any unexpected condition it exits silently with zero: a hook that
misfires must not get in the way of the work.
#>

param(
    # No PowerShell-enforced requiredness or value set here: parameter binding runs BEFORE the
    # script body, and an invalid call would emit a non-zero exit code with someone else's text —
    # exactly against the promise to exit silently with zero. We validate the value ourselves,
    # inside the body.
    [string]$Stage,

    # Tests only: a board off to the side of the real one. Not set in production — the path comes
    # from git.
    [string]$BoardPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Any more than this and it's a wall of text nobody reads; the session can still see the rest via Show.
$MaxRecords = 5

function Get-StateDir {
    # .claude/.cache is already in .gitignore — hook state doesn't litter the repository there.
    #
    # ‼️ The state folder sits at the TREE ROOT, not at the current directory. First, the liveness
    # beacon's folder is created right here, and the beacon is read by root — let the two drift
    # apart and a live session would look abandoned. Second, the shown-log belongs to the session:
    # let the session step into a subdirectory, and everything already shown would arrive all over
    # again.
    $dir = Join-Path (Get-TreeRoot) '.claude/.cache'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    return $dir
}

function Get-OverlapBlock {
    param($Claims, $MyClaim, [string]$StateDir, [string]$SessionId)
    # A neighbour is editing the same files. We say so ONCE per session per neighbour: the warning
    # stays the same, while the session's context gets resent on every turn.
    #
    # Why this exists at all. A session is tempted to pick up a neighbour's task; it offers it to the
    # owner, and the owner — not knowing the task was planned for another stream — confirms it. File
    # overlap is the only sign of this a machine can see, and it's visible BEFORE the merge conflict.
    try {
        $found = @(Get-Overlaps -Claims $Claims -MyClaim $MyClaim)
        if ($found.Count -eq 0) { return $null }
        $said = Join-Path $StateDir "wave-board-overlap-$SessionId.txt"
        $seen = if (Test-Path $said) { @(Get-Content $said -Encoding utf8 | Where-Object { $_.Trim() }) } else { @() }
        $fresh = @($found | Where-Object { "$($_.Claim.Record.wave)/$($_.Claim.Record.stream)" -notin $seen })
        if ($fresh.Count -eq 0) { return $null }
        Add-Content -Path $said -Encoding utf8 -Value (
            @($fresh | ForEach-Object { "$($_.Claim.Record.wave)/$($_.Claim.Record.stream)" }) -join "`n")
        $lines = foreach ($overlap in $fresh) {
            $names = @($overlap.Files | Select-Object -First 3) -join ', '
            $more = if ($overlap.Files.Count -gt 3) { " … and $($overlap.Files.Count - 3) more" } else { '' }
            $who = $overlap.Claim.Record
            "  • stream $($who.wave)/$($who.stream)$(if ($who.name) { ' "' + $who.name + '"' }) — shared files: $names$more"
        }
        return @(
            'A neighbouring stream is editing the same files right now:'
            ($lines -join "`n")
            'Before offering the owner work outside their own tasks — check who owns this task:'
            '  pwsh scripts/wave-board.ps1 -Mode Streams -Task <task number>'
            "The owner doesn't know the task was planned for another stream, and will confirm it to you."
        ) | Where-Object { $_ } | Join-String -Separator "`n"
    } catch {
        return $null
    }
}

function Get-StuckBlock {
    param([string]$Board, $Claims, $MyClaim)
    # A summary of what's stuck — ONLY in the repo's main folder: that's where the owner sits, there
    # is no stream there, and nothing to spam. In a worktree this would be noise about other
    # people's findings.
    #
    # This is the one place where the mechanism admits delivery didn't happen. Without it, a failure
    # looks like silence, and silence looks like "the neighbour has nothing to say."
    try {
        $gitDir = (& git rev-parse --git-dir 2>$null)
        $common = (& git rev-parse --git-common-dir 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $gitDir -or -not $common) { return $null }
        if ($gitDir.Trim() -ne $common.Trim()) { return $null }
        if (-not (Test-Path $Board)) { return $null }
        $records = @(Get-OpenRecords -Path $Board)
        $stuck = @(Get-StuckRecords -Records $records -Claims $Claims -KnownKeys (Get-KnownStreamKeys -AliveOnly))
        if ($stuck.Count -eq 0) { return $null }
        $shown = @($stuck | Select-Object -First $MaxRecords)
        $lines = foreach ($item in $shown) {
            "  • `"$($item.Record.title)`" — to: $($item.Record.to), $($item.Reason)"
        }
        $tail = if ($stuck.Count -gt $shown.Count) { "  … and $($stuck.Count - $shown.Count) more" } else { $null }
        # Where the finding should go — via the same shared check the tool itself uses: in a project
        # with no waves, a plan section is a pointer to nothing, and the finding then goes nowhere at
        # all. It's enough that one stuck finding has an addressee with a plan: the line about the
        # plan section applies to it.
        $withPlan = @($stuck | Where-Object {
                Test-AddresseeHasPlan -Claims $Claims -Raw ([string]$_.Record.to) `
                    -Address (Get-StreamAddress -Raw ([string]$_.Record.to)) -Mine $MyClaim
            })
        $advice = if ($withPlan.Count -gt 0) {
            'Their place is the "Wave Loose Ends" section of the plan, as a separate item with ready-made text for launching a new session.'
        } else {
            'There is no wave plan — name the finding in your reply to the owner.'
        }
        return @(
            "The wave board has $($stuck.Count) stuck record(s) — the addressee won't get them."
            ($lines -join "`n")
            $tail
            $advice
            'Full board: pwsh scripts/wave-board.ps1 -Mode Show'
        ) | Where-Object { $_ } | Join-String -Separator "`n"
    } catch {
        return $null
    }
}

function Send-Context {
    param([string]$Text, [string]$HookEvent)
    @{
        hookSpecificOutput = @{
            hookEventName     = $HookEvent
            additionalContext = $Text
        }
        suppressOutput     = $true
    } | ConvertTo-Json -Depth 5 -Compress
}

try {
    . (Join-Path $PSScriptRoot '../lib/hook-io.ps1')
    $raw = Read-HookInput
    if ($Stage -notin @('Start', 'Prompt')) { exit 0 }
    # Not $input, not $event: both are PowerShell automatic variables.
    $call = if ($raw) { $raw | ConvertFrom-Json } else { $null }
    $sessionId = if ($call -and $call.session_id) { [string]$call.session_id } else { 'nosession' }

    . (Join-Path $PSScriptRoot '../lib/wave-board-lib.ps1')

    $stateDir = Get-StateDir
    $shownFile = Join-Path $stateDir "wave-board-shown-$sessionId.txt"
    $shownName = Split-Path -Leaf $shownFile
    # The log of what's already been said about an overlap is per-session too, and cleanup has to
    # spare it the same way: its write time only advances when there's something to say, so on a
    # session longer than a day it would fall under cleanup and the same warning would come back,
    # even though it's promised "once".
    $overlapName = "wave-board-overlap-$sessionId.txt"

    # The live-session beacon — a neighbour uses it to tell a working session apart from an abandoned
    # worktree. Set on EVERY turn, both stages, before any early exit: otherwise only the session
    # that already got something delivered would count as alive, and every other one would look
    # closed. This costs one write to a file — the hook touches the state folder anyway.
    # ‼️ We write it by the TREE ROOT — where the worktree scan looks for the beacon (it knows paths
    # from git, not from our current folder). The writer used to address it by the current folder,
    # and a session that had stepped into a subdirectory looked abandoned to its neighbours: findings
    # went off into "Wave Loose Ends" past a live person. The fallback to the current folder here is
    # silent — the hook must stay mute.
    Set-Content -Path (Get-AliveBeaconPath -TreePath (Get-TreeRoot)) `
        -Value "$((Get-Date).ToString('s')) $sessionId" -Encoding utf8

    # ‼️ We read the registry BEFORE the marks, not after. Both marks must stay silent on ANY closed
    # record, and a handed-over address is only visible in the registry as a whole: in its own file a
    # handed-over claim still looks open. Read the registry later, and any new session opened in the
    # old folder would, on its very first turn, resurrect a ghost with a fresh mark — along with its
    # address and its mail. The read is forgiving: the hook must stay mute, and a partial snapshot is
    # no reason for it to break off.
    $registry = Get-RegistryDir -BoardOverride $BoardPath
    $claims = @(Get-Claims -Dir $registry)
    # Wave names from the registry, for address parsing: a wave isn't only ever called "wave6" —
    # where there are no waves, the claim supplies one instead (a date or a word). Without this,
    # such a wave's address wouldn't parse, and the finding would never reach the session waiting
    # for it.
    Set-KnownWaves -Keys @($claims | ForEach-Object { $_.WaveKey })

    # The same mark, but in the stream's claim: the beacon speaks about the FOLDER, the claim speaks
    # about the STREAM, and it survives the folder being deleted. No claim (the session never
    # announced) — quietly do nothing.
    Update-ClaimSeen -Dir $registry -TreePath (Get-TreeRoot) -Claims $claims

    # Live-session mark: we bump the log's write time on EVERY turn, not only when there's something
    # to show. Otherwise a long session that went days without a finding would fall under cleanup
    # along with closed ones — and everything already shown would arrive all over again.
    if (Test-Path $shownFile) { (Get-Item $shownFile).LastWriteTime = Get-Date }

    # Cleanup runs BEFORE any early exit: it used to sit below them and never ran at all when the
    # board was empty — which means, most of the time, it never ran.
    Get-ChildItem $stateDir -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -like 'wave-board-shown-*.txt' -or $_.Name -like 'wave-board-overlap-*.txt') -and
            $_.Name -notin @($shownName, $overlapName) -and $_.LastWriteTime -lt (Get-Date).AddDays(-1)
        } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    # The list of files touched in this stream's own claim — neighbours use it to see overlapping
    # work. Recomputed no more than every few minutes, and stays silent on any failure.
    Update-ClaimFiles -Dir $registry -TreePath (Get-TreeRoot) -Claims $claims

    $board = Get-BoardPath -Override $BoardPath
    $claim = Get-CurrentClaim -Dir $registry
    if (-not $claim) {
        # A second route to our own claim — the same one release already uses: by an EXACT match on
        # the worktree folder recorded in the claim. This is how claims filed by an older version
        # from a subdirectory of the tree get found: their file name was derived from that folder, so
        # the current canonical key (the tree root) doesn't find them.
        #
        # ‼️ Without this route, release finds such a claim and delivery doesn't — meaning a finding
        # sent to the address is accepted with a report of "it'll get there on its own", and never
        # reaches the session: the stream's address is taken from ITS OWN claim, and without it the
        # hook doesn't know what the stream is called.
        #
        # ‼️ The hook MARKS the record it finds (liveness and the list of touched files), but never
        # creates, deletes or renames files under any circumstances: it takes no lock, it is already
        # a second writer, and it must not become a second arbiter of names. Marking it is allowed
        # precisely because it was found by an EXACT match on the worktree folder — so it belongs to
        # this very session, and no second writer appears for the file.
        try {
            $found = Find-ClaimByWorktree -Claims $claims -Paths @((Get-TreeRoot), $PWD.Path)
            if ($found) {
                $claim = $found.Record
                # ‼️ Both the liveness mark and the list of touched files go RIGHT HERE, on the file
                # we found. Both marks above go by the canonical key, and this claim's key is a
                # different one — so it worked out that it gets the mail and never gets a mark: after
                # a day it landed in the owner's stuck summary, and neighbours stopped counting the
                # session as alive. No second writer appears here: the record was found by an EXACT
                # match on the worktree folder, so it belongs to this very session. The ban on
                # writing into SOMEONE ELSE'S file still stands.
                Update-ClaimSeen -Path $found.File -Claims $claims
                Update-ClaimFiles -Path $found.File -Claims $claims
            }
        } catch {
            # An ambiguity (two unclosed claims on one folder) is a finding to show, not a reason to
            # wreck the delivery of everything else. The hook must stay mute: we behave as before.
            $claim = $null
        }
    }

    # The blocks the hook puts into context. There are four, and they appear independently: the
    # notice to a session that lost its address, what arrived from the board, an overlap with a
    # neighbour, a summary of what's stuck for the owner. The hook used to exit immediately when the
    # board was empty — the last three would then never be seen at all.
    $blocks = [System.Collections.Generic.List[string]]::new()

    # ‼️ NOTICE TO A SESSION THAT LOST ITS ADDRESS. Its claim on disk still looks open — a handover
    # doesn't touch someone else's file by a single byte — while in the registry the record is
    # cancelled. Without this line the losing side goes SILENT: no findings are brought to it any
    # more, release answers "handed over", an attempt to close a finding is refused — and why, the
    # session doesn't know, and goes on believing it is running the stream. Worse, it will announce
    # to its neighbours and to the owner that it works at an address it doesn't have.
    #
    # Printed on EVERY turn, not once per session like the overlap warning: that one is an event,
    # and saying it once is enough; this is a state the session goes on living in. It costs two
    # lines.
    #
    # The state comes from the REGISTRY: the handover isn't visible in its own file at all. If we
    # trip, we stay silent, just as we were: the hook must stay mute.
    if ($claim -and [string]$claim.state -ne 'released') {
        try {
            $myEntry = Get-ClaimEntry -Claims $claims -Claim $claim
            if ($myEntry -and $myEntry.Superseded) {
                # ‼️ "Taken over into such-and-such folder" is true exactly when that folder IS
                # RUNNING THE SAME address. It could have moved on, released, or taken up the next
                # stream — and then no record leads the address at all, and the session must not be
                # sent there.
                $fate = Get-ClaimAddressFate -Claim $myEntry
                # ‼️ We print the way out according to whoever ACTUALLY holds the address. The
                # take-over switch takes it away from the leading claim of another folder; with no
                # leader, the switch answers "wasn't needed", and the session goes in circles
                # following the one piece of advice printed for it. A printed way out has to work.
                $lines = if ($fate.StillLed) {
                    @(
                        "‼️ Your stream $($claim.wave)/$($claim.stream) was taken over into $($fate.Holder.Record.worktree) — this session is no longer addressable: findings for that address arrive there, and they can't be closed from here."
                        "This is your stream and it was taken over by mistake — take the address back: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $($claim.wave) -Stream $($claim.stream) -TakeOver"
                    )
                } else {
                    @(
                        "‼️ Your stream $($claim.wave)/$($claim.stream) is no longer run from here: $($fate.Text). No record leads the address any more — this session isn't addressable from outside, and intake won't accept a finding for it."
                        'There is nothing to take the address back from — the folder that took it has moved on or finished the stream. For new work, announce yourself under a free number: pwsh scripts/wave-board.ps1 -Mode Claim'
                    )
                }
                $blocks.Add($lines -join "`n")
            }
        } catch {
            # Muteness matters more than the notice: an unparsed registry is no reason to wreck the
            # delivery of everything else.
        }
    }

    $overlapText = Get-OverlapBlock -Claims $claims -MyClaim $claim -StateDir $stateDir -SessionId $sessionId
    if ($overlapText) { $blocks.Add($overlapText) }

    if ($Stage -eq 'Start') {
        $stuckText = Get-StuckBlock -Board $board -Claims $claims -MyClaim $claim
        if ($stuckText) { $blocks.Add($stuckText) }
    }

    if (-not (Test-Path $board)) {
        if ($blocks.Count -gt 0) {
            Send-Context -Text ($blocks -join "`n`n") -HookEvent $(if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' })
        }
        exit 0
    }

    # The session's keys are needed twice: to pick out what's addressed to it, and to hide what it
    # has already dealt with. An "everyone" record has per-recipient closing — there's no shared
    # "closed" for it.
    # ‼️ Stream names come from the shared source, not "whatever is visible right now". The hook
    # used to take only the current ones, and a finding addressed by branch name would NEVER reach
    # the session once the branch was renamed, switched, or detached from — yet intake still
    # accepted it at that very moment and promised the author "a session is running this stream —
    # it'll most likely get there on its own."
    $keys = @(Get-StreamNames -Claim $claim -Claims $claims)
    # ‼️ We don't carry findings to a CLOSED stream — neither a released one nor a handed-over one.
    # A released one has no session left, and if one is still open, it's already been told "findings
    # won't be accepted anymore"; a handed-over one had its address taken by another folder, and the
    # findings for it belong to that folder. Delivering someone else's finding ends in the worst
    # possible way: the delivery text tells the reader outright to close the record if it doesn't
    # apply to their work, and closing a name-addressed record is SHARED — so a closed stream snuffs
    # out a finding meant for a live one.
    #
    # ‼️ The state comes from the REGISTRY, not from our own file: the handover isn't visible in our
    # own file at all, and the losing session would go on receiving the mail of the address's new
    # owner.
    $released = Test-ClaimClosed -Claims $claims -Claim $claim
    $mine = if ($released) {
        @()
    } else {
        Select-ForStream -Records (Get-OpenRecords -Path $board -Viewer $keys) -Keys $keys -Claim $claim
    }
    if ($mine.Count -eq 0) {
        if ($blocks.Count -gt 0) {
            Send-Context -Text ($blocks -join "`n`n") -HookEvent $(if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' })
        }
        exit 0
    }

    if ($Stage -eq 'Start') {
        # Reset deliberately: context compaction raises the same event, and an open finding must
        # come back into context instead of staying marked as shown.
        $fresh = @($mine)
        Set-Content -Path $shownFile -Value '' -Encoding utf8
    } else {
        $seen = if (Test-Path $shownFile) {
            @(Get-Content $shownFile -Encoding utf8 | Where-Object { $_.Trim() })
        } else { @() }
        $fresh = @($mine | Where-Object { [string]$_.id -notin $seen })
    }
    if ($fresh.Count -eq 0) {
        if ($blocks.Count -gt 0) {
            Send-Context -Text ($blocks -join "`n`n") -HookEvent $(if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' })
        }
        exit 0
    }

    # We mark as shown EXACTLY what was shown. Marking the whole remainder would bury the sixth
    # record and beyond: neither the next turn nor a context compaction would bring them — the log
    # would already consider them delivered.
    $shown = @($fresh | Select-Object -First $MaxRecords)
    Add-Content -Path $shownFile -Value (@($shown | ForEach-Object { [string]$_.id }) -join "`n") -Encoding utf8

    # The "your finding was received" notice closes ITSELF as soon as it's shown: it exists to take
    # the question off the author's hands, not to add them work closing records. If this fails, it's
    # not a problem — it will come around again and close on the next try.
    foreach ($record in @($shown | Where-Object { [string]$_.kind -eq 'ack' })) {
        try {
            Add-BoardLine -Path $board -Line (
                [ordered]@{ id = [string]$record.id; at = (Get-Date).ToString('s'); done = $true } |
                    ConvertTo-Json -Depth 2 -Compress)
        } catch {
            # Deliberately silent: the hook must not get in the session's way.
        }
    }

    $lines = foreach ($record in $shown) { Format-BoardRecord -Record $record }
    $tail = if ($fresh.Count -gt $shown.Count) {
        "  … and $($fresh.Count - $shown.Count) more — will arrive on later turns"
    } else { $null }

    $mail = @(
        "The wave board has entries addressed to this stream ($($fresh.Count)):"
        ($lines -join "`n")
        $tail
        'These are additions from neighbouring sessions made AFTER this stream read the plan: it no'
        'longer re-reads the plan, and in its worktree the file stayed unchanged — that''s why the'
        'finding arrives here instead.'
        'Handled it (or decided it doesn''t apply) — close it:'
        '  pwsh scripts/wave-board.ps1 -Mode Done -Id <id>'
    ) | Where-Object { $_ } | Join-String -Separator "`n"
    $blocks.Insert(0, $mail)
    $text = ($blocks -join "`n`n")

    $hookEvent = if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' }
    Send-Context -Text $text -HookEvent $hookEvent
} catch {
    exit 0
}
