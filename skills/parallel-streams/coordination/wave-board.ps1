#Requires -Version 7
<#
Wave board: a finding addressed to a neighbouring session is placed here and reaches that session on
its own.

Why this exists. A session reads the wave plan ONCE, at the start, and never rereads it; on top of
that, the plan sits inside that session's own worktree, frozen at whatever version it was at when
the session started. So an addition written into the plan later never reaches a live neighbour —
neither through the plan, nor through a merge. On 2026-08-20 three findings were lost exactly this
way: they were addressed to a stream whose session, at that moment, was still working from a
week-old plan.

The board delivers the addition, but it doesn't replace the plan: a finding is still written into
the plan too (later sessions read it there), and it's placed on the board only WHILE the neighbour's
session is LIVE.

A stream's address is its number in the wave plan (`wave6/3`), not its branch name — names lie. In
wave plan 6, two streams were assigned the same branch name in the plan while their sessions
actually worked on different branches; one folder had already been repurposed by a session for other
work. A branch or folder name is still accepted — as a fallback path.

Modes:
  Claim   — announce at start: [-Wave <wave>] [-Stream <number>] [-StreamName …] [-Tasks …]
            [-Plan …]. Wave not named — taken from the plan's file name, from work already under
            way nearby, or from today's date; stream number not named — the next free one in the
            wave is issued.
  Release — release the stream before closing the session (refused while the inbox still has
            anything open)
  Streams — who is running which stream: [-Wave …] [-Task <task number>]
  Add     — place a finding:  -To <wave/stream, branch, or folder> -Title <one line> [-Where …]
  Show    — show open entries
  Done    — close a handled finding: -Id <id>; one addressed to everyone can only be closed for
            your own stream, and -ForAll clears it for everyone at once
  Compact — compact the board: keep only the open entries
  Path    — print the board's path

How the board and the claim registry work — in `lib/wave-board-lib.ps1`.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Add', 'Show', 'Done', 'Compact', 'Path', 'Claim', 'Release', 'Streams')]
    [string]$Mode,

    # To whom: the stream's number in the wave (`wave6/3`), the stream's branch name
    # (`feat/wave3-plan-clock`), its worktree folder name, `*` for every stream in your own wave, or
    # `**` for every session in the project.
    [string]$To,

    # Place a finding by a branch or folder name that doesn't exist on this machine at all. An
    # address of the form "wave/stream" needs no such key: a stream that hasn't announced itself yet
    # is normal — the entry will wait for it.
    [switch]$AllowUnknownStream,

    [string]$Title,

    # Where the full text lives: a plan section, a task number, a claim number. The title on the
    # board is only a hook.
    [string]$Where,

    [string]$Wave,

    # Stream number from the wave-plan table: `3`, `P3`, `3b`.
    [string]$Stream,

    # Human-readable stream name from the plan — so neighbours can recognize it by more than a
    # number.
    #
    # ‼️ It's `-StreamName`, not `-Name`: under the short name, the parameter value arrived at the
    # build box swapped out — instead of the name that was actually passed, the claim ended up
    # holding the name of an environment variable (`GIT_ALTERNATE_OBJECT_DIRECTORIES`), and the same
    # thing happened for every value, Russian or Latin alike. On the development machine the same
    # call worked correctly. Neighboring parameters (`-Wave`, `-Stream`, `-Tasks`) were unaffected —
    # so the cause is the name `-Name` itself. Do not rename it back.
    [string]$StreamName,

    # Plan task numbers the stream is running: `10-13` or `10, 11, 12`. They show whose work a task
    # belongs to when a session is tempted to pick up a neighbouring one.
    [string]$Tasks,

    # Wave-plan file: its name supplies the wave when one wasn't named separately.
    [string]$Plan,

    # Task number for the "who owns this" question in Streams mode.
    [string]$Task,

    [string]$Id,

    # Clear a "to everyone" entry for EVERY addressee at once: the topic is closed, nobody needs it
    # anymore. Without this switch, closing such an entry is personal — it only clears it for the
    # stream that closed it.
    [switch]$ForAll,

    # Release the stream without clearing the inbox: what's left in it gets named in the report and
    # moved by hand into "Wave Loose Ends". Release is refused without this switch.
    [switch]$Force,

    # Tests only: a board separate from the real one. Not set in normal use — the path comes from
    # git.
    [string]$BoardPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'lib/wave-board-lib.ps1')

# A title longer than this stops being a hook and becomes a retelling: the full text belongs in the
# plan or the task.
$MaxTitleLength = 200

# This many lines on the board is a reason to compact: every session parses it whole on every turn.
$CrowdedLines = 200

# More names in a refusal isn't a hint anymore, it's a wall — and the closest match is already
# listed first.
$MaxHints = 8

function Deny-Call {
    param([string]$Message)
    # Write the refusal straight to the error stream and exit with a code: `throw` would wrap the
    # text in an exception frame — with a path, a line number, line breaks, and color. A human reads
    # this, not the runtime, so NOT ONE refusal goes through the frame, not even a one-liner.
    [Console]::Error.WriteLine($Message)
    exit 1
}

function Get-TailsAdvice {
    param($Claims, [string]$Raw, $Address, [switch]$Closed)
    # Where to send a finding that has nowhere to arrive. There is exactly one branching point for
    # all such advice, and it lives here: scatter it across call sites, and half the refusals would
    # send a session to a plan section that doesn't exist in the project at all, stalling the work on
    # a dead end.
    #
    # ‼️ Check the RECIPIENT's plan, not your own: your own plan says nothing about where to put a
    # finding meant for a neighbour.
    $mine = Get-CurrentClaim -Dir $registry -Strict
    if (Test-AddresseeHasPlan -Claims $Claims -Raw $Raw -Address $Address -Mine $mine) {
        if ($Closed) {
            return 'There is no worktree at all (the stream is released) — the finding belongs in the plan''s "Wave Loose Ends" section, as a separate item with ready-made text for starting a new session, not on the board.'
        }
        return 'The finding belongs in the plan''s "Wave Loose Ends" section — as a separate item with ready-made text for starting a new session.'
    }
    return 'There is no wave plan — name the finding in your reply to the owner.'
}

function Assert-Addressee {
    param([string]$Raw, $Address, $Claims)
    # Check the addressee. Order matters: first the claim registry (it knows whether the stream is
    # LIVE and whether it's been released), and only if the stream never announced itself, the old
    # check against the list of worktrees.
    if ($Raw -in @('*', '**')) { return }
    $found = @(Find-Claims -Claims $Claims -Raw $Raw)
    $live = @($found | Where-Object { $_.State -ne 'released' })
    if ($live.Count -gt 0) { return }
    if ($found.Count -gt 0) {
        # ‼️ The main new thing this guard catches: the stream is RELEASED. This entry used to be
        # accepted (the worktree was still there, after all) and stayed on the board forever — there
        # was no one left to receive it.
        $released = $found[0].Record
        $when = if ($released.released_at) { Format-Stamp -Raw $released.released_at } else { 'unknown when' }
        $advice = Get-TailsAdvice -Claims $Claims -Raw $Raw -Address $Address
        Deny-Call @"
stream "$Raw" was RELEASED ($when) — the session that ran it is gone, and the entry would have stayed on the board forever.
$advice
Who's running what now: pwsh scripts/wave-board.ps1 -Mode Streams
"@
    }
    if ($Address) {
        # No claim at all: the stream just hasn't been opened yet. That's normal, not an error — the
        # entry will lie there and wait for the claim. No bypass switch is needed here: a
        # "wave/stream" address isn't guessed from names.
        return
    }
    # ‼️ A name REMEMBERED by two streams and currently worn by neither deliberately goes to
    # neither: otherwise the finding would go to both, and closing a name-based address is shared —
    # either side could clear it for the other. The refusal here must name this exact reason. It
    # used to say the addressee wasn't among the worktrees and offer three explanations, none of
    # them correct — the truth was visible only in the stream listing, which the refusal didn't even
    # point to.
    $rememberers = @(Get-NameRememberers -Claims $Claims -Name (Get-StreamKey -Raw $Raw))
    if ($rememberers.Count -gt 1) {
        $who = @($rememberers | ForEach-Object { "$($_.Record.wave)/$($_.Record.stream)" }) -join ', '
        Deny-Call @"
the name "$Raw" is remembered by two streams ($who), and currently worn by neither — so it went to neither of them.
A finding sent under that name would reach both, and closing a name-based address is SHARED: either side could clear it for the other.
Name the address by wave and stream number instead — that can't be confused with anything.
Who's called what now: pwsh scripts/wave-board.ps1 -Mode Streams
"@
    }
    Assert-KnownStream -Wanted (Get-StreamKey -Raw $Raw) -Raw $Raw `
        -Advice (Get-TailsAdvice -Claims $Claims -Raw $Raw -Address $Address -Closed)
}

function Assert-KnownStream {
    param([string]$Wanted, [string]$Raw, [string]$Advice)
    # Fallback path: the addressee was named by branch or folder, and there's no claim for such a
    # stream. So we check it against worktrees at least — otherwise the entry would land in the void
    # with a cheerful report.
    if ($Wanted -eq '*') { return }
    if (-not $Wanted) {
        Deny-Call "addressee `"$Raw`" reduces to an empty key (stray slash at the end?) — no one would receive this entry"
    }
    $known = Get-KnownStreamKeys
    if ($known.Count -eq 0) {
        if ($AllowUnknownStream) { return }
        Deny-Call 'git named no worktrees at all — there is nothing to check the addressee against; place it deliberately with -AllowUnknownStream'
    }
    if ($Wanted -in $known) { return }
    if ($AllowUnknownStream) { return }
    # Look for similar names by chunks of the name: the usual miss is a typo or a different spelling
    # of the same name, and the right one is nearby. Nothing matched at all (the addressee was named
    # in words) — suggest whoever checked in recently: out of two dozen worktrees, those are
    # definitely alive.
    $tokens = @(($Wanted -split '[^\p{L}\p{Nd}]+') | Where-Object { $_.Length -ge 3 })
    $similar = @($known | Where-Object { $name = $_; @($tokens | Where-Object { $name -like "*$_*" }).Count -gt 0 })
    $what = 'Similar names'
    if ($similar.Count -eq 0) {
        $what = 'Sessions that checked in recently'
        $similar = @(Get-KnownStreamKeys -AliveOnly | Sort-Object)
    }
    if ($similar.Count -eq 0) {
        $what = 'Worktrees that exist'
        $similar = @($known | Sort-Object)
    }
    $hint = @($similar | Select-Object -First $MaxHints) -join ', '
    if ($similar.Count -gt $MaxHints) { $hint += ", … and $($similar.Count - $MaxHints) more" }
    Deny-Call @"
addressee "$Raw" is not among the worktrees — the entry would land in the void, with a cheerful report.
${what}: $hint
$Advice
Worktree not created yet — retry with -AllowUnknownStream.
"@
}

# Anything that fails deeper than this (antivirus is holding the board, disk is full, no
# permissions) surfaces to the outside as plain text with the real reason, not an exception frame
# with a path and line number: a human reads this, and decides what to do next from the reason.
function Select-Asked {
    param($Records)
    # The same filter for the list of open entries and for the count of the rest. Split them apart,
    # and the "overdue — N" line would count other waves while the list next to it showed only its
    # own.
    $result = @($Records)
    if ($To) {
        $wanted = Get-StreamKey -Raw $To
        # Compare a "wave/stream" address parsed, not by key: by key, `wave6/3` and `wave7/3`
        # collapse to the same triple, and the display would pass off someone else's wave as your
        # own.
        $wantedAddress = Get-StreamAddress -Raw $To
        # "To everyone" entries pass the filter by stream: delivery will bring them to this same
        # session anyway.
        $result = @($result | Where-Object {
                $to = Get-StreamKey -Raw ([string]$_.to)
                if ($to -in @('*', '**')) { return $true }
                $address = Get-StreamAddress -Raw ([string]$_.to)
                if ($wantedAddress -and $address) {
                    return ($address.Wave -eq $wantedAddress.Wave -and $address.Stream -eq $wantedAddress.Stream)
                }
                if ($wantedAddress -or $address) { return $false }
                return ($to -eq $wanted)
            })
    }
    if ($Wave) { $result = @($result | Where-Object { [string]$_.wave -eq $Wave }) }
    return @($result)
}

trap { Deny-Call $_.Exception.Message }

$board = Get-BoardPath -Override $BoardPath
$registry = Get-RegistryDir -BoardOverride $BoardPath
# Wave names from the registry — for address parsing. A wave isn't only ever called "wave6": where
# there are no waves at all, the claim supplies one itself, named by date or by word. Miss these,
# and an address like `2026-08-24/3` wouldn't parse, and the finding would never reach its neighbour.
Set-KnownWavesFromRegistry -Dir $registry

switch ($Mode) {
    'Path' {
        $board
    }

    'Claim' {
        # Announcing a stream. The first thing a session does once its worktree is set up: without
        # it, a stream is indistinguishable from the outside from "never opened", and findings get
        # addressed to it by branch name — which, by then, has already changed.
        $branch = ''
        try {
            $head = (& git rev-parse --abbrev-ref HEAD 2>$null)
            if ($LASTEXITCODE -eq 0 -and $head) { $branch = $head.Trim() }
        } catch {
            # Detached HEAD: the claim will do with just the worktree path.
        }
        $tree = ($PWD.Path -replace '\\', '/').TrimEnd('/')
        # ‼️ From here to the end of dispute resolution — under the claim registry's lock. Number
        # selection works off a snapshot of the registry, and without the lock, sessions opened at
        # the same moment read the SAME snapshot: everyone picks the same number, and the dispute
        # circle below doesn't catch everything — it can exit on a snapshot made stale by a rival's
        # move in the meantime, leaving two streams silently sharing one address, each reporting
        # cheerful success. The full scenario is worked out at `Enter-RegistryLock`.
        #
        # The lock is released on failure too: PowerShell's `finally` runs both on `exit`
        # (`Deny-Call`) and on an exception caught by the trap above. And if the session is killed
        # before even that runs, the system releases the lock: it's held by an open handle, not by a
        # file.
        $lockHandle = Enter-RegistryLock -Dir $registry
        try {
            # ‼️ Strict, deliberately: this snapshot PICKS THE STREAM NUMBER. Missing a neighbour here
            # means handing out their number a second time, silently and for good; better to refuse
            # out loud and ask for a retry.
            $claims = @(Get-Claims -Dir $registry -Strict)
            # Fill in the wave ourselves if it wasn't named: the channel is set up in projects with
            # no waves at all too, and there a session has nowhere to get either a wave number or a
            # stream number. Refusing here was a dead end — you can't announce yourself, and without
            # announcing, a stream doesn't exist from the outside.
            #
            # Steps top to bottom: named wave → wave from the plan's file name → work already under
            # way nearby → a new wave keyed to today's date.
            $waveAuto = $false
            $waveSource = 'named'
            if ($Wave) {
                $waveKey = Get-WaveKey -Raw $Wave
            } elseif ($Plan) {
                $waveKey = Get-WaveKey -Raw $Plan
                $waveSource = 'plan'
            } else {
                $joined = Get-AutoWaveKey -Claims $claims
                if ($joined) {
                    $waveKey = $joined
                    $waveSource = 'joined'
                } else {
                    $waveKey = Get-DateWaveKey
                    $waveSource = 'date'
                }
                # ‼️ This flag matters exactly here: joining is allowed ONLY into a wave that was
                # self-supplied. A named wave gets its stream numbers from the plan, and taking a
                # number there that belongs to someone else would split the addressing in two.
                $waveAuto = $true
            }
            if (-not $waveKey) {
                # We land here only if a wave (or plan) was named but the name reduced to empty:
                # without a wave name a stream has no address, and no finding will ever reach it.
                $named = if ($Wave) { $Wave } else { $Plan }
                Deny-Call "wave `"$named`" reduces to an empty name — name it with a word or a number"
            }
            # The stream number is optional too: a project with no plan has nowhere to get one from,
            # yet the address always needs one. A named number still works as before — it comes from
            # the plan's table.
            $streamAuto = $false
            if ($Stream) {
                $streamKey = Get-StreamNumberKey -Raw $Stream
                if (-not $streamKey) { Deny-Call "stream number `"$Stream`" reduces to empty — name it the way the plan does" }
            } else {
                $streamKey = Get-NextStreamNumber -Claims $claims -WaveKey $waveKey -TreePath $tree
                $streamAuto = $true
            }
            # ‼️ Carry over the branch's earlier names into the new claim. A session can announce
            # itself a second time, and announcing silently overwrites the claim: rename the branch
            # and announce again, and the old name would vanish from everywhere — a finding ALREADY
            # ACCEPTED under it would stop arriving, and it wouldn't hold up release either. Keep it
            # only for a while: the name is needed as long as the wave lives, not forever.
            $former = [System.Collections.Generic.List[string]]::new()
            $before = @($claims | Where-Object {
                    (([string]$_.Record.worktree) -replace '\\', '/').TrimEnd('/') -eq $tree
                })
            if ($before.Count -gt 0) {
                $earlier = @([string]$before[0].Record.branch) + @($before[0].Record.former_branches)
                foreach ($name in $earlier) {
                    # ‼️ Nothing gets displaced. A displaced name would mean a finding ALREADY
                    # ACCEPTED under it that stops being delivered and stops holding up release — the
                    # very silent loss this memory was built to prevent. The list only grows from
                    # actual branch renames, and there are only a handful of those per wave.
                    if ($name -and $name -ne $branch -and $name -notin $former) { $former.Add($name) }
                }
            }
            $claim = [ordered]@{
                wave            = $waveKey
                stream          = $streamKey
                # ‼️ The "wave was self-supplied" flag. This, and only this, is what a neighbouring
                # session uses to decide whether it can join this wave. Old-style claims don't carry
                # it — correctly so: they were made with a named wave, where numbers come from the
                # plan.
                wave_auto       = $waveAuto
                name            = $StreamName
                tasks           = $Tasks
                plan            = $Plan
                branch          = $branch
                # Names the stream was known by earlier: a finding sent under one of them must still
                # arrive.
                former_branches = @($former)
                worktree        = $tree
                claimed_at      = (Get-Date).ToString('s')
                seen_at         = (Get-Date).ToString('s')
                state           = 'open'
            }
            Write-ClaimFile -Path (Get-ClaimPath -Dir $registry -TreePath $tree) -Claim $claim
            # The circle that resolves a number dispute. Usually there's no rival under this lock:
            # the registry was read with neighbours' claims already written, and the chosen number is
            # free. The circle stays for cases with no lock at all:
            #   • the lock wasn't handed over within the time allotted (which the session says out
            #     loud below) — then this is the only defense against two streams sharing one
            #     address;
            #   • a neighbour's claim appeared AFTER ours — say, it was restored from a backup, or
            #     brought in by a session running a version without the lock.
            #
            # The dispute is resolved by shifting YOUR OWN number: the claim file belongs only to
            # this session, so overwriting it is safe. Whoever announced later yields (at equal time,
            # whoever's worktree path sorts earlier alphabetically wins): the order is total and
            # fixed, so two sessions never shift at the same time and never trade numbers back and
            # forth forever.
            $movedFrom = ''
            # ‼️ Everything AFTER writing your own claim is covered the same way: a strict read, and
            # a failure isn't a refusal but a loud warning. Refusing here is no longer an option —
            # the claim is written, the stream is announced, and "announcing failed" would be a lie,
            # on top of an open claim sitting in the registry. Before, only the last snapshot was
            # covered this way, and the dispute-resolution circle wasn't — a hiccup starting right
            # after the write produced exactly the lie this covering exists to avoid.
            $snapshotFailed = ''
            try {
                # Several passes: shifting can land on a number a third neighbour claims that same
                # second. Everyone resolves disputes the same way, so the passes converge instead of
                # looping.
                for ($round = 1; $round -le 5; $round++) {
                    # Strict here too: the dispute circle reads the same registry and answers the
                    # same question.
                    $claims = @(Get-Claims -Dir $registry -Strict)
                    $rivals = @(Get-NumberRivals -Claims $claims -WaveKey $waveKey -StreamKey $streamKey -TreePath $tree)
                    if ($rivals.Count -eq 0) { break }
                    # A named number comes from the plan — it IS the stream's name, findings are
                    # addressed by it, and it must not be moved. A human resolves this kind of
                    # dispute; the tool says so below.
                    if (-not $streamAuto) { break }
                    if (-not (Test-YieldsStreamNumber -Mine $claim -Rivals $rivals)) { break }
                    if (-not $movedFrom) { $movedFrom = $streamKey }
                    $streamKey = Get-FreeStreamNumber -Claims $claims -WaveKey $waveKey -TreePath $tree
                    $claim.stream = $streamKey
                    Write-ClaimFile -Path (Get-ClaimPath -Dir $registry -TreePath $tree) -Claim $claim
                }
                # ‼️ Take the snapshot for the report LAST and UNDER THE LOCK. Last, because while
                # the number dispute was going on a neighbour could have shifted too, and warning
                # about them would be a lie. Under the lock, because this snapshot prints the only
                # defense against two sessions sharing ONE NAMED number: such a number comes from
                # the plan, must not be moved, and all that's left is to tell the human "another
                # worktree has a claim on this same stream". It also prints the neighbour map: an
                # empty snapshot means a session confident it's alone.
                $claims = @(Get-Claims -Dir $registry -Strict)
                # Wave names for address parsing — from THIS snapshot: the lookup below uses the
                # same snapshot to find entries left waiting for the stream, and their address may
                # name the wave by a word.
                Set-KnownWaves -Keys @($claims | ForEach-Object { $_.WaveKey })
            } catch {
                $snapshotFailed = $_.Exception.Message
                $claims = @()
            }
        } finally {
            # Always release the lock: otherwise a session that crashed would hold up its neighbours'
            # announcements for the rest of its life.
            Exit-RegistryLock -Handle $lockHandle
        }
        $rivals = @(Get-NumberRivals -Claims $claims -WaveKey $waveKey -StreamKey $streamKey -TreePath $tree)
        "Stream $waveKey/$streamKey announced for this session (branch $branch)."
        if ($snapshotFailed) {
            "‼️ Could not read the claim registry to check against neighbours: $snapshotFailed"
            '   Stream announced, but the number-collision warning and the neighbour map were not built — check by hand: -Mode Streams'
        }
        if (-not $lockHandle) {
            # This can't be left unsaid: without the lock, the number was picked off a snapshot a
            # neighbour could outrun — exactly the case where two streams get one address and neither
            # side finds out.
            "‼️ The claim registry lock wasn't handed over within $(Get-RegistryLockWaitSeconds)s — the stream number was picked without it and may collide with a neighbour's."
            '   Check the addresses: -Mode Streams. A neighbouring session may be stuck mid-announcement.'
        }
        # Say out loud where the wave came from. A silently self-supplied wave would get reported to
        # the owner as the one they named, and neighbours would hear an address neither side expected.
        switch ($waveSource) {
            'date' {
                "Wave not named — taken from today's date. Address for neighbours: $waveKey/$streamKey."
            }
            'joined' {
                # Count streams in the wave AFTER joining, including this one: the session is asking
                # "how many of us", not "how many before me".
                $inWave = @(@($claims | Where-Object { $_.WaveKey -eq $waveKey } |
                            ForEach-Object { $_.StreamKey }) + $streamKey |
                        Where-Object { $_ } | Select-Object -Unique)
                "Wave not named — session joined work already under way, $waveKey, streams in it: $($inWave.Count)."
            }
            'plan' {
                'Wave taken from the plan file name.'
            }
        }
        if ($streamAuto) {
            "Stream number not named — issued the next free one: $streamKey."
        }
        if ($movedFrom) {
            "‼️ A neighbouring session announced number $waveKey/$movedFrom at the very same moment — your stream was shifted to the next free one: $waveKey/$streamKey."
            '   Tell neighbours the new address: the old one now delivers to a different stream.'
        }
        # A number that can't be used to address later. Address parsing requires a NUMBER on the
        # right; announcing accepts any word in that field — such a stream can only be addressed the
        # fallback way (by branch name), and names lie by mid-wave. Don't refuse — warn right away,
        # before the session tells neighbours an address that doesn't exist.
        if ($Stream -and -not (Get-StreamAddress -Raw "1/$Stream")) {
            "‼️ Number `"$Stream`" can't be used to address the stream: only a number (3, P3, 3b) parses on the right of the slash."
            '   Name the stream number from the plan — otherwise neighbours can''t send a finding the main way.'
        }
        # Entries left waiting for the stream before it was ever opened. The delivery guard will
        # bring them in on the next turn, but it's worth mentioning here too: the claim is the exact
        # moment the session learns it was already being waited for.
        $waiting = @(Select-ForStream -Records (Get-OpenRecords -Path $board -Viewer (Get-CurrentKeys)) `
                -Keys (Get-CurrentKeys) -Claim $claim)
        if ($waiting.Count -gt 0) {
            "Entries already waiting for you on the board: $($waiting.Count) — will arrive on the next turn."
        }
        # A rival's live claim on the same stream that survived dispute resolution (the number was
        # named, not issued — it must not be moved). Not a refusal: taking over a stream on purpose
        # does happen, the first session may have closed without releasing. But silence isn't right
        # either — two streams sharing one number split the addressing, and half the findings go to
        # the wrong place.
        foreach ($rival in $rivals) {
            "‼️ Another worktree has an open claim on this same stream: $($rival.Record.worktree) — $($rival.State)."
            '   Two streams sharing one number split the addressing: work out which session is actually running it.'
        }
        # The neighbour map prints right away, not on request: a session learns the boundaries of its
        # own work in the very first minute. Without it, it proposes work outside its own tasks to
        # the owner, and the owner has no way of knowing the task was planned for another stream, so
        # they approve it.
        $neighbours = @($claims | Where-Object {
                $_.WaveKey -eq $waveKey -and ($_.Record.worktree -replace '\\', '/').TrimEnd('/') -ne $tree
            })
        if ($neighbours.Count -gt 0) {
            ''
            "Neighboring streams in wave ${waveKey}:"
            foreach ($neighbour in $neighbours) { Format-ClaimLine -Claim $neighbour }
            'Working outside your own tasks — check who owns it first: -Mode Streams -Task <number>.'
        }
    }

    'Release' {
        # Releasing the stream. This is the one place that asks "is everything that arrived actually
        # handled" — without it, an entry placed ten minutes before the session closes reaches no
        # one, ever, while the sender has already been told it succeeded.
        # Strict, deliberately: "no claim" and "file is busy" lead in opposite directions, yet look
        # identical — empty. Read your own claim FIRST: if it's the one that's corrupted, the owner
        # should get a refusal about THEIR OWN file and its path, not a generic refusal about a
        # neighbour's claim.
        $claim = Get-CurrentClaim -Dir $registry -Strict
        if (-not $claim) {
            # ‼️ "No claim" is only true when the registry CAN BE READ. On a dead path (the drive
            # dropped, the share disappeared), the absence of your own file looks exactly the same,
            # and the session used to be told "nothing to release" with a success code. So ask the
            # registry strictly first: it's the one that tells "no claims" apart from "couldn't even
            # look".
            $null = Get-Claims -Dir $registry -Strict
            'No claim on this session — nothing to release. Announce with: -Mode Claim (wave and stream number can be left unnamed).'
            return
        }
        if ([string]$claim.state -eq 'released') {
            "Stream $($claim.wave)/$($claim.stream) already released ($(Format-Stamp -Raw $claim.released_at))."
            return
        }
        $content = Read-BoardContent -Path $board
        if (-not $content.Ok) {
            Deny-Call "couldn't read the board ($board), and releasing a stream blind isn't safe. Last reason: $($content.Reason)"
        }
        # Read the registry ONCE, and take both things from it.
        #
        # ‼️ Wave names are needed for parsing a "wave/stream" address when the wave is named BY A
        # WORD: at the tool's start the list is built tolerantly, and one busy claim used to strip a
        # word of its right to be a wave name — then a finding stopped counting as this stream's
        # finding, the inbox looked empty, and release SUCCEEDED, leaving the entry on the board
        # forever.
        #
        # ‼️ Stream names — by the same single set that decides who a finding is addressed to
        # elsewhere, and that the delivery guard uses to tell "is this my entry". Split them apart,
        # and a finding accepted under one name wouldn't be found under the other.
        $registryNow = @(Get-Claims -Dir $registry -Strict)
        Set-KnownWaves -Keys @($registryNow | ForEach-Object { $_.WaveKey })
        $keys = @(Get-StreamNames -Claim $claim -Claims $registryNow)
        $records = @(Select-OpenEntries -Entries (Get-BoardEntries -Lines $content.Lines) -Viewer $keys |
                ForEach-Object { $_.Record })
        # Exclude "handled" acknowledgments from the inbox: they clear themselves on display, and
        # requiring them closed before release would mean refusing release over an entry that carries
        # no work.
        $left = @(Select-ForStream -Records $records -Keys $keys -Claim $claim |
                Where-Object { [string]$_.kind -ne 'ack' })
        # Does the stream have a wave plan. Without a plan, there's nowhere to write the summary or
        # the leftover findings, and pointing to a section of a file that doesn't exist is a dead
        # end: the session can neither follow the advice nor tell what to do instead.
        $hasPlan = Test-ClaimHasPlan -Claim $claim
        if ($left.Count -gt 0 -and -not $Force) {
            $list = @($left | ForEach-Object { Format-BoardRecord -Record $_ }) -join "`n"
            $notMine = if ($hasPlan) {
                'Not your work — move it into the plan''s "Wave Loose Ends" section as an item, close it, then release: -Mode Release -Force'
            } else {
                'Not your work — name it in your reply to the owner and release with -Force.'
            }
            Deny-Call @"
stream $($claim.wave)/$($claim.stream)'s inbox still has $($left.Count) open — can't release, or no one would ever get them.
$list
Handled one — close it: -Mode Done -Id <id>.
$notMine
"@
        }
        $claim.state = 'released'
        $claim | Add-Member -NotePropertyName released_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
        Write-ClaimFile -Path (Get-ClaimPath -Dir $registry -TreePath $PWD.Path) -Claim $claim
        if ($hasPlan) {
            "Stream $($claim.wave)/$($claim.stream) released. Findings will no longer be accepted for it — their place is now the Wave Loose Ends."
        } else {
            "Stream $($claim.wave)/$($claim.stream) released. Findings will no longer be accepted for it."
        }
        if ($left.Count -gt 0) {
            if ($hasPlan) {
                "‼️ Released with a non-empty inbox: $($left.Count) entries left — no one will get them, move them into the Wave Loose Ends."
            } else {
                "‼️ Released with a non-empty inbox: $($left.Count) entries left — no one will get them, name them in your reply to the owner."
            }
            foreach ($record in $left) { Format-BoardRecord -Record $record }
        }
        if ($hasPlan) {
            'Last step — a line for your stream in the wave plan''s "Stream status" section.'
        } else {
            'No wave plan — nowhere to write a stream line; the summary goes in your reply to the owner.'
        }
    }

    'Streams' {
        # Who is running which stream. Also answers "who owns this work": -Task <number>.
        #
        # ‼️ Strict. This is exactly the question the rules require asking BEFORE proposing work
        # outside your own tasks to the owner. Miss a neighbour's claim, and the answer comes back
        # "no one's taken this task" — a direct green light to take someone else's piece; the owner
        # will approve it, because they have no way to know otherwise. The cost: two sessions do the
        # same work and collide in a merge conflict. This command is rare and run at a person's
        # direct request, so waiting a second for it isn't a cost worth avoiding (reproduced: with a
        # neighbour's claim file busy, the answer was exactly "no one's taken this").
        $claims = @(Get-Claims -Dir $registry -Strict)
        if ($Wave) {
            $waveKey = Get-WaveKey -Raw $Wave
            $claims = @($claims | Where-Object { $_.WaveKey -eq $waveKey })
        }
        if ($Task) {
            $claims = @($claims | Where-Object { Test-TaskInList -Tasks ([string]$_.Record.tasks) -Task $Task })
        }
        if ($claims.Count -eq 0) {
            if ($Task) {
                "Task $Task isn't claimed by any stream — either no one's taken it, or the stream that owns it didn't list its tasks when announcing."
                return
            }
            "No stream claims ($registry)."
            return
        }
        "Stream claims: $($claims.Count)"
        foreach ($claim in $claims) { Format-ClaimLine -Claim $claim }
        ''
        '"live" — the session checked in within the last few hours; "silent" — it may have closed without releasing, or it may just be working quietly.'
    }

    'Add' {
        if (-not $To) {
            Deny-Call 'need -To: the stream''s number in the wave (wave6/3), the stream''s branch or worktree folder, * for your own wave, ** for the whole project'
        }
        $wanted = Get-StreamKey -Raw $To
        # ‼️ Strict, and BEFORE address parsing. A missed claim here costs a lost finding: a stream
        # that's actually RELEASED looks "never announced", the entry gets accepted with a report
        # saying "will wait for its claim" — and no claim will ever come. After a report like that,
        # the author relaxes and doesn't set up a fallback item in the plan either (reproduced).
        $claims = @(Get-Claims -Dir $registry -Strict)
        # Give wave names for address parsing from THIS snapshot, not from a separate tolerant read:
        # otherwise a date-wave address might fail to parse because of a claim that read missed.
        Set-KnownWaves -Keys @($claims | ForEach-Object { $_.WaveKey })
        $address = Get-StreamAddress -Raw $To
        Assert-Addressee -Raw $To -Address $address -Claims $claims
        $clean = ($Title -replace '\s+', ' ').Trim()
        # An empty title and a title made of nothing but spaces are the same thing: no one would
        # ever see such an entry (the listing hides it, delivery skips it), and the report would be
        # a lie.
        if (-not $clean) { Deny-Call 'need -Title: a one-line title, and it can''t be nothing but spaces' }
        if ($clean.Length -gt $MaxTitleLength) {
            $clean = $clean.Substring(0, $MaxTitleLength - 1) + '…'
            Write-Warning "title trimmed to $MaxTitleLength characters — the board keeps a hook, the full text belongs in the plan or the task"
        }
        # Always fill in the author: it's how a session's own broadcast entry gets filtered back
        # out. A blank field would return such a finding straight back to its own author — and the
        # worktree folder name is known even without git.
        $from = Split-Path -Leaf $PWD
        try {
            $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
            if ($LASTEXITCODE -eq 0 -and $branch) { $from = $branch.Trim() }
        } catch {
            # Couldn't get the branch — the folder name is enough for filtering.
        }
        # Wave not named — take it from your own claim. That way a "to everyone" entry naturally
        # ends up addressed to YOUR OWN wave, not to all two dozen worktrees in the project.
        # Strict, deliberately: the entry's wave comes from your own claim, and it decides who a "to
        # everyone" finding reaches — your own wave, or all two dozen worktrees in the project.
        $mine = Get-CurrentClaim -Dir $registry -Strict
        $waveOfRecord = $Wave
        if (-not $waveOfRecord -and $mine) { $waveOfRecord = [string]$mine.wave }
        $record = [ordered]@{
            id    = [guid]::NewGuid().ToString('N').Substring(0, 8)
            at    = (Get-Date).ToString('s')
            wave  = $waveOfRecord
            to    = $To
            title = $clean
            where = $Where
            from  = $from
        }
        Add-BoardLine -Path $board -Line ($record | ConvertTo-Json -Depth 3 -Compress)
        # The report says exactly what it knows, in both directions. A fresh check-in means "the
        # session worked recently", not "it's working right now": it could just as well have closed
        # an hour ago, and nothing clears the mark when it does. Delivery can't be promised firmly
        # either — the author would relax on that promise and never set up a task for it in the Wave
        # Loose Ends. No mark at all doesn't mean "closed" either, just "unknown" — the finding lies
        # there and waits regardless.
        $addressed = @(Find-Claims -Claims $claims -Raw $To | Where-Object { $_.State -ne 'released' })
        $fate = if ($wanted -eq '**') {
            'Will reach every session in the project except this one — including ones set up for other waves.'
        } elseif ($wanted -eq '*') {
            if ($waveOfRecord) {
                "Will reach sessions of wave $waveOfRecord that checked in recently, except this one."
            } else {
                'Wave not named and you have no claim — will reach every session in the project except this one.'
            }
        } elseif ($addressed.Count -gt 0 -and $addressed[0].State -eq 'live') {
            "The stream's session is live (checked in $(Format-Stamp -Raw $addressed[0].Record.seen_at)) — it will most likely get there on its own."
        } elseif ($addressed.Count -gt 0) {
            # Where to duplicate it — through the same shared check as every other bit of advice: the
            # recipient's claim is already found, and "Wave Loose Ends" in a project with no plan
            # would be pointing into nothing, leaving the finding with nowhere to land at all.
            $spare = if (Test-AddresseeHasPlan -Claims $claims -Raw $To -Address $address -Mine $mine) {
                'Duplicate the finding as an item in the Wave Loose Ends.'
            } else {
                'Name the finding in your reply to the owner.'
            }
            "Stream is claimed, but its session hasn't checked in for a while — whether it's alive is unknown. $spare"
        } elseif ($address) {
            # A "wave/stream" address only ever arrives through a claim: there's no branch name in
            # it to match against until the stream announces itself. This isn't a loss — the entry
            # lies there and waits.
            'Stream hasn''t announced itself yet — the entry will wait for its claim and arrive in its first minute of work.'
        } elseif ($wanted -in (Get-KnownStreamKeys -AliveOnly)) {
            # A branch- or folder-name address arrives even without a claim — by worktree key. So say
            # exactly what was said before the registry existed: a check-in means "worked recently".
            'Neighboring session checked in recently — will most likely get there on its own.'
        } else {
            'Neighboring session has no recent check-in — whether it''s alive right now is unknown; the finding will wait for it.'
        }
        "Placed on the wave board for `"$To`" (id $($record.id)). $fate"
        # Compact it ourselves when the board has grown: every session parses it whole on every
        # turn, and "please compact by hand" is a request aimed at whoever came here for something
        # else. The work is safe — it refuses on any doubt — and its refusal must not sink adding
        # the finding: the entry is already down, and that's what matters.
        try {
            $lines = @((Read-BoardContent -Path $board).Lines)
            if ($lines.Count -gt $CrowdedLines) {
                $squeezed = Compress-Board -Path $board
                if ($squeezed.Before -gt $squeezed.After) {
                    "Board compacted along the way: was $($squeezed.Before) lines, now $($squeezed.After)."
                }
            }
        } catch {
            "Board has grown, but compacting it failed: $($_.Exception.Message)"
        }
    }

    'Done' {
        if (-not $Id) { Deny-Call 'need -Id: the entry to close (visible in the board listing)' }
        $content = Read-BoardContent -Path $board
        if (-not $content.Ok) {
            # A busy board isn't "no such entry" — a read failure used to look like "maybe it's
            # already closed", and a person walked away reassured, having closed nothing.
            Deny-Call "couldn't read the board ($board). Last reason: $($content.Reason)"
        }
        $entries = Get-BoardEntries -Lines $content.Lines
        $mineKeys = @(Get-CurrentKeys)
        # Look for the entry through your own stream's eyes — except for "clear for everyone": the
        # one who handled the finding personally is the first to know the topic is closed, and their
        # own closing would hide it from themselves. Without this exception, the only way out of the
        # board would be locked in front of them.
        $lookupKeys = if ($ForAll) { @() } else { $mineKeys }
        $record = @(
            Select-OpenEntries -Entries $entries -Viewer $lookupKeys |
                Where-Object { [string]$_.Record.id -eq $Id } |
                ForEach-Object { $_.Record }
        )[0]
        if (-not $record) {
            # Answer with what's actually true. A generic "closed for everyone, or expired" used to
            # lie most often to exactly the person who'd closed it for themselves a minute ago:
            # neither is true, and the actual way out (clear for everyone) went unnamed.
            $states = Get-BoardStates -Entries $entries -Viewer $mineKeys
            $isMine = @($states.ClosedForViewer | Where-Object { [string]$_.Record.id -eq $Id }).Count -gt 0
            $isStale = @($states.Stale | Where-Object { [string]$_.Record.id -eq $Id }).Count -gt 0
            $isBroken = @($states.Broken | Where-Object { [string]$_.Record.id -eq $Id }).Count -gt 0
            if ($isMine) {
                "You already closed entry `"$Id`" for yourself — neighbours can still see it."
                "Topic's closed and they don't need it — clear it for everyone: pwsh scripts/wave-board.ps1 -Mode Done -Id $Id -ForAll"
            } elseif ($isStale) {
                "Entry `"$Id`" is expired (older than $(Get-BroadcastLifetimeDays) days) — it already dropped off the board."
            } elseif ($isBroken) {
                "Entry `"$Id`" has a corrupted timestamp — it's been pulled from delivery; fix the date in the board file."
            } else {
                "No open entry `"$Id`" — either it's closed for everyone, or that id was never on the board."
            }
            return
        }
        # ‼️ Only the addressee can close a finding with a NAMED address. Closing one is SHARED —
        # whoever closes it first clears it for everyone — so a stranger's hand here costs the whole
        # finding: the real addressee will never see it, in delivery or on the board, releases green
        # with an empty inbox, and the author gets an "acknowledged" from a stream they never named.
        #
        # Today delivery hands such a finding to exactly one stream, so there's nowhere to learn a
        # stranger's id from. But the check still stands as a second door: find out, and you'd hear
        # whose entry it is instead of closing it. The deliberate way out remains the -ForAll switch:
        # topic's closed, the addressee doesn't need it.
        $addressedToMe = $ForAll -or (Get-StreamKey -Raw ([string]$record.to)) -in @('*', '**')
        if (-not $addressedToMe) {
            $registryNow = @(Get-Claims -Dir $registry -Strict)
            Set-KnownWaves -Keys @($registryNow | ForEach-Object { $_.WaveKey })
            $myClaim = Get-CurrentClaim -Dir $registry -Strict
            $myNames = @(Get-StreamNames -Claim $myClaim -Claims $registryNow)
            $addressedToMe = @(Select-ForStream -Records @($record) -Keys $myNames -Claim $myClaim).Count -gt 0
        }
        if (-not $addressedToMe) {
            Deny-Call @"
entry "$Id" is addressed to stream "$($record.to)", not you — closing it is SHARED, and you'd be clearing it for them.
They would then never see it, not in delivery, not on the board, and their author would get an "acknowledged" from you.
Topic's closed and the addressee doesn't need it — clear it for everyone deliberately: pwsh scripts/wave-board.ps1 -Mode Done -Id $Id -ForAll
"@
        }
        $closing = [ordered]@{ id = $Id; at = (Get-Date).ToString('s'); done = $true }
        # There are TWO broadcast addresses: "everyone in your own wave" and "every session in the
        # project". Forget the second one, and the first stream to acknowledge it clears it for
        # everyone at once.
        $broadcast = (Get-StreamKey -Raw ([string]$record.to)) -in @('*', '**')
        # A "to everyone" finding gets handled by each addressee for themselves, so closing it
        # carries the closer's key and clears it only for them. A shared way out is still needed too
        # — without it there'd be no way to remove the entry from the board at all: it would keep
        # arriving at every NEW worktree, even one outside this wave.
        if ($broadcast -and -not $ForAll) {
            if ($mineKeys.Count -eq 0) {
                Deny-Call 'could not tell which stream this is, and closing a "to everyone" entry is personal; clear it for everyone at once instead: -ForAll'
            }
            $closing.by = $mineKeys[0]
        }
        Add-BoardLine -Path $board -Line ($closing | ConvertTo-Json -Depth 2 -Compress)
        # Tell the finding's author it's been handled. Without this they never learn its fate, and
        # it decides whether they set up a task for it in the Wave Loose Ends. The notice is addressed
        # to the author, marked with a distinct kind, and clears itself when displayed — it creates
        # no new work.
        # "To everyone" entries aren't acknowledged: they have many addressees, and the author would
        # get as many notices about the same thing. Acknowledgment only makes sense where there's one
        # addressee and one definite outcome for the finding.
        $author = Get-StreamKey -Raw ([string]$record.from)
        if ($author -and -not $broadcast -and $author -notin $mineKeys -and [string]$record.kind -ne 'ack') {
            $ack = [ordered]@{
                id    = [guid]::NewGuid().ToString('N').Substring(0, 8)
                at    = (Get-Date).ToString('s')
                wave  = [string]$record.wave
                to    = [string]$record.from
                title = "acknowledged: `"$($record.title)`""
                where = "closed by stream $(if ($mineKeys.Count -gt 0) { $mineKeys[0] } else { 'unknown' })"
                from  = if ($mineKeys.Count -gt 0) { $mineKeys[0] } else { '' }
                kind  = 'ack'
            }
            Add-BoardLine -Path $board -Line ($ack | ConvertTo-Json -Depth 3 -Compress)
        }
        if ($broadcast -and $ForAll) {
            "Entry `"$Id`" closed for every addressee — topic's closed, neighbours won't see it again."
        } elseif ($broadcast) {
            "Entry `"$Id`" closed for stream `"$($mineKeys[0])`" — still visible to the rest of the addressees."
            "Topic's closed and neighbours don't need it — clear it for everyone: pwsh scripts/wave-board.ps1 -Mode Done -Id $Id -ForAll"
        } else {
            "Entry `"$Id`" closed — no longer shown to any session."
        }
    }

    'Compact' {
        $result = Compress-Board -Path $board
        if ($result.Before -eq 0) {
            "Nothing to compact — the board has no lines at all ($board)."
            return
        }
        "Board compacted: was $($result.Before) lines, now $($result.After) (open entries only)."
        if ($result.Unreadable -gt 0) {
            # An entry fragment is someone's finding that can't be parsed. Something erased silently
            # is indistinguishable from something that never existed, so say what got dropped out
            # loud.
            "Lines not parsed and dropped: $($result.Unreadable) — these are broken entry fragments."
        }
    }

    'Show' {
        $content = Read-BoardContent -Path $board
        if (-not $content.Ok) {
            Deny-Call "couldn't read the board ($board). Last reason: $($content.Reason)"
        }
        $entries = Get-BoardEntries -Lines $content.Lines
        # Look through the named stream's eyes: it may have its own closings. A stream has two
        # keys — by branch and by folder — and they can collapse to DIFFERENT ones; take both, or
        # the display won't recognize this stream's own closing and will show a handled finding as
        # open.
        $viewer = if ($To) { @(Get-StreamKeys -Raw $To) } else { @() }
        $states = Get-BoardStates -Entries $entries -Viewer $viewer
        $open = Select-Asked -Records @($states.Open | ForEach-Object { $_.Record })
        $closedHere = Select-Asked -Records @($states.ClosedForViewer | ForEach-Object { $_.Record })
        $stale = Select-Asked -Records @($states.Stale | ForEach-Object { $_.Record })
        $broken = Select-Asked -Records @($states.Broken | ForEach-Object { $_.Record })
        $total = $content.Lines.Count
        # Count how many lines compacting would remove using the SAME filter it actually runs on:
        # recommending a compaction that removes nothing is wasted work and false hope.
        $droppable = $total - @(Select-KeepEntries -Entries $entries).Count
        if ($total -gt $CrowdedLines) {
            "Board has grown: $total lines, $($open.Count) of them open."
            if ($droppable -gt 0) {
                "Compacting would remove $droppable lines — pwsh scripts/wave-board.ps1 -Mode Compact"
            } else {
                'Compacting would remove nothing — the board only has needed lines.'
            }
            ''
        }
        # Say the other states out loud: staying silent about them looks like a lost entry.
        $aside = @()
        if ($closedHere.Count -gt 0) { $aside += "closed for you — $($closedHere.Count)" }
        if ($stale.Count -gt 0) {
            $aside += "expired (older than $(Get-BroadcastLifetimeDays) days) — $($stale.Count)"
        }
        $asideLine = if ($aside.Count -gt 0) { "Also on the board: $($aside -join ', ')." } else { $null }
        # A corrupted date gets its own line: this isn't "it just expired", it's a broken file, and
        # the person needs to understand why the finding disappeared and what to do about it.
        $brokenLine = if ($broken.Count -gt 0) {
            "Entries with a corrupted timestamp: $($broken.Count) — they aren't delivered; fix the date in the board file or close them."
        } else { $null }
        if ($open.Count -eq 0) {
            "No open entries on the wave board ($board)."
            if ($asideLine) { $asideLine }
            if ($brokenLine) { $brokenLine }
            return
        }
        "Open entries on the wave board: $($open.Count) ($board)"
        $anyBroadcast = $false
        foreach ($record in $open) {
            $tail = if ($record.where) { " — $($record.where)" } else { '' }
            $wave = if ($record.wave) { "wave $($record.wave), " } else { '' }
            # Who's already handled it — only for "to everyone" entries: closing is personal for
            # them, and staying silent about it would pass "no one's handled this yet" off as true.
            $seen = $states.Closings.By[[string]$record.id]
            $mark = if ($seen -and $seen.Count -gt 0) { " (handled by: $($seen -join ', '))" } else { '' }
            if ((Get-StreamKey -Raw ([string]$record.to)) -eq '*') { $anyBroadcast = $true }
            "  [$($record.id)] ${wave}to: $($record.to) — `"$($record.title)`"$tail$mark"
        }
        if ($asideLine) { $asideLine }
        if ($brokenLine) { $brokenLine }
        ''
        'Handled a finding — close it: pwsh scripts/wave-board.ps1 -Mode Done -Id <id>'
        if ($anyBroadcast) {
            'A "to everyone" entry only clears for you; topic''s closed and neighbours don''t need it — add -ForAll.'
        }
    }
}
