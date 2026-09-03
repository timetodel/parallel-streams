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
            [-Plan …] [-TakeOver]. Wave not named — taken from the plan's file name, from work
            already under way nearby, or from today's date; stream number not named — the next free
            one in the wave is issued. `-TakeOver` takes a named address away from another worktree
            folder (a move into your own tree, or picking up an abandoned session).
  Release — release the stream before closing the session (refused while the inbox still has
            anything open). With `-Wave <wave> -Stream <number>` it releases an ORPHAN by address —
            an entry whose worktree folder is no longer on disk: folder still there (even if it has
            been silent for five days) — refused.
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

    # ‼️ In Release, the `-Wave` and `-Stream` pair means something else: release an ORPHAN by
    # address — an entry whose worktree folder is no longer on disk. Your own stream is released from
    # your own folder and needs no switches.
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
    #
    # ‼️ This switch has EXACTLY ONE meaning, and it must never be given a second one. In a
    # neighbouring copy the same switch is used to overwrite someone else's claim while announcing —
    # and since the tool itself recommends it for releasing, people hit it without looking, and the
    # stream in an occupied folder vanishes silently. Announcing has no force switch at all: releasing
    # the previous stream happens in that very same folder, loses nothing, and gives the same outcome.
    [switch]$Force,

    # Take a stream's address away from another worktree folder: the session moved into its own tree
    # (or is picking up an abandoned one) and announces itself under the same address from there.
    #
    # ‼️ The switch writes the succession field into YOUR OWN claim. Not one byte of the other file is
    # touched: it has a second writer — that folder's delivery guard — and any mark left in it would
    # be wiped by its next liveness check-in, right after we had reported success. The address is
    # superseded by the registry loader's second pass, not by editing someone else's claim.
    #
    # ‼️ The switch has exactly one meaning — moving the address. Announcing has no overwriting of
    # someone else's claim at all, and it must never be given one: releasing the previous stream
    # happens in that very same folder, loses nothing, and gives the same outcome.
    [switch]$TakeOver,

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
    # ‼️ Liveness is asked through the SINGLE closed-ness flag, not through "not released". A moved
    # entry looks open in its own file, and were we to read the field directly, a finding sent to an
    # honestly released (and then moved) address would be accepted with a cheerful report of success —
    # the costliest consequence of the defect: the sender relaxed, and the finding reached no one.
    $live = @($found | Where-Object { -not $_.Closed })
    if ($live.Count -gt 0) { return }
    if ($found.Count -gt 0) {
        # ‼️ The main new thing this guard catches: the stream is CLOSED. This entry used to be
        # accepted (the worktree was still there, after all) and stayed on the board forever — there
        # was no one left to receive it.
        #
        # Released is preferred over moved: "released" is the final answer, and it matters more to a
        # human.
        $closed = @($found | Where-Object { $_.State -eq 'released' })
        $advice = Get-TailsAdvice -Claims $Claims -Raw $Raw -Address $Address
        if ($closed.Count -gt 0) {
            $released = $closed[0].Record
            $when = if ($released.released_at) { Format-Stamp -Raw $released.released_at } else { 'unknown when' }
            Deny-Call @"
stream "$Raw" was RELEASED ($when) — the session that ran it is gone, and the entry would have stayed on the board forever.
$advice
Who's running what now: pwsh scripts/wave-board.ps1 -Mode Streams
"@
        }
        $moved = $found[0]
        # Where the address went — worded the same way the listing and release word it: the folder
        # that took it may have released itself since, or picked up the next stream, and "moved to it"
        # would send the sender somewhere nobody runs that address.
        $where = Get-ClaimTakenAwayText -Claim $moved
        Deny-Call @"
address "$Raw" has no leading entry: $where — the entry would lie on the board forever.
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
        #
        # ‼️ The tree root is asked for FIRST and strictly: the claim will be filed under this key,
        # and release will look for it under the same one. A silent fallback to the current folder
        # would change the session's identity — it would announce under one key and release under
        # another, and release would answer "nothing to release" with a success code. Better to refuse
        # out loud before writing than to file a claim no one will ever find.
        $tree = Get-TreeRoot -Strict
        $branch = ''
        try {
            $head = (& git rev-parse --abbrev-ref HEAD 2>$null)
            if ($LASTEXITCODE -eq 0 -and $head) { $branch = $head.Trim() }
        } catch {
            # Detached HEAD: the claim will do with just the worktree path.
        }
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
            # Where the claim will be filed, and what lay in this folder before it. Both values are
            # the same for the whole block, and they are asked for FIRST: the stream's address in a
            # short announcement is taken from them, and they also decide whether announcing would
            # wipe out someone else's stream.
            #
            # ‼️ The canonical key is the worktree root, but claims filed by an EARLIER version from
            # a subfolder of the tree lie under that subfolder's key. Not finding our own file under
            # the canonical name, we look for our entry by a second route — the same one release and
            # the delivery guard already use: by an EXACT match on the worktree folder recorded in the
            # claim. We then write back into the file we FOUND: not one file is created, deleted, or
            # renamed.
            #
            # ‼️ Without that route, announcing under the same address created a SECOND open entry for
            # the same stream: the tool printed a warning to the session that it was disputing with
            # itself, and wrote the second entry anyway, with a success code. Two entries then ran the
            # address, and which one got a finding was decided by the directory listing order.
            #
            # The match is EXACT only — not "starts with", not "lies inside": otherwise a session
            # would take over the claim of a neighbouring tree nested in its own folder.
            #
            # ‼️ ONE choice serves all three decisions: the folder rule, inheritance, and writing the
            # file must all look at the same entry. There used to be two — the folder rule took the
            # FIRST of this folder's entries in listing order, while the file write went to the one
            # found by the canonical key — and on a folder where a released entry lies next to an open
            # one they diverged: the rule looked at the released one (which doesn't count and raises
            # no refusal), while we wrote over the LIVE one. A short announcement of a different stream
            # passed with a success code and erased a working one.
            #
            # ‼️ An unclosed entry always outranks a released one. Two unclosed ones on a single folder
            # don't happen by the registry's own rule; if it happened anyway — choosing for the human
            # isn't allowed, and announcing refuses out loud exactly the way release does.
            $claimPath = Get-ClaimPath -Dir $registry -TreePath $tree
            $hereKeys = @(@($tree, $PWD.Path) | ForEach-Object { Get-FolderKey -Path $_ } |
                    Where-Object { $_ } | Select-Object -Unique)
            $canonKey = Get-FolderKey -Path $claimPath
            $mineHere = @($claims | Where-Object {
                    (Get-FolderKey -Path $_.Record.worktree) -in $hereKeys -or
                    (Get-FolderKey -Path $_.File) -eq $canonKey
                })
            # Closed-ness through the SINGLE flag: a moved entry looks open in its own file, and were
            # we to read the field directly, a session whose address had been taken away would count
            # it as its own live claim and go on running a stream it no longer has.
            $openHere = @($mineHere | Where-Object { -not $_.Closed })
            if ($openHere.Count -gt 1) {
                $names = @($openHere | ForEach-Object { $_.File }) -join ', '
                Deny-Call "this worktree folder has $($openHere.Count) unclosed claims in the registry at once ($names) — which of them this session is continuing, the tool has no right to decide. Remove the extra one and retry."
            }
            $previous = if ($openHere.Count -eq 1) {
                $openHere[0]
            } elseif ($mineHere.Count -eq 1) {
                $mineHere[0]
            } else {
                $null
            }
            # ‼️ We write into the file of the entry we CHOSE, not into the canonical one: otherwise
            # announcing would create a second entry for the same stream alongside the one found, and
            # which of them got a finding would be decided by the directory listing order. Not one
            # file is created, deleted, or renamed in the process.
            if ($previous) { $claimPath = $previous.File }

            # ‼️ This folder's previous UNCLOSED claim is THE SAME stream, not an obstacle. That is
            # why it rose above the wave-substitution ladder: below it, the wave, the number, and
            # everything else the session didn't name in a short announcement are taken from it. A
            # released one doesn't count — the session that ran that stream is gone, and the next
            # stream in this folder is a new one.
            $previousOpen = if ($previous -and -not $previous.Closed) {
                $previous
            } else {
                $null
            }
            # ‼️ Your own MOVED entry is your stream too, just one left without an address. Its name,
            # tasks, and plan are its own, and a session that took its address back with the command
            # the tool printed for it must get them back: otherwise the return hands back the address
            # but loses the stream itself — the session announces itself nameless, and neighbours see
            # an empty line instead of work.
            #
            # A released one does NOT land here: the session that ran that stream is gone, and the
            # next stream in this folder is a different one. Inheriting from it would mean answering
            # to its name and intercepting findings addressed to it.
            $previousSelf = if ($previousOpen) {
                $previousOpen
            } elseif ($previous -and $previous.Superseded) {
                $previous
            } else {
                $null
            }
            # Fill in the wave ourselves if it wasn't named: the channel is set up in projects with
            # no waves at all too, and there a session has nowhere to get either a wave number or a
            # stream number. Refusing here was a dead end — you can't announce yourself, and without
            # announcing, a stream doesn't exist from the outside.
            #
            # Steps top to bottom: named wave → wave from the plan's file name → YOUR OWN PREVIOUS
            # CLAIM → work already under way nearby → a new wave keyed to today's date.
            $waveAuto = $false
            $waveSource = 'named'
            # What the session will take from its own previous entry. These are set up here, before
            # the ladder, because the ladder picks only the WAVE, while the rest of the fields are
            # inherited below — once the address has been settled whole. Two flags sit next to the
            # fields: whether the number was issued now or inherited (those are said out loud
            # differently), and whether the previous entry carried the "wave was self-supplied" flag
            # at all — its absence is inherited too.
            $streamInherited = $false
            $waveAutoKnown = $true
            $claimedAt = (Get-Date).ToString('s')
            $claimName = $StreamName
            $claimTasks = $Tasks
            $claimPlan = $Plan
            if ($Wave) {
                $waveKey = Get-WaveKey -Raw $Wave
            } elseif ($Plan) {
                $waveKey = Get-WaveKey -Raw $Plan
                $waveSource = 'plan'
            } elseif ($previousOpen -and $previousOpen.WaveKey) {
                # ‼️ The session announces itself again from its own folder and doesn't name a wave —
                # this is a CONTINUATION of the same stream, not a new one. Without this step the
                # stream drifted off into a wave keyed to today's date and lost, along with its
                # address, its name, tasks, plan, and seniority: neighbours went on addressing
                # findings to the old address, which was no longer in the registry.
                $waveKey = $previousOpen.WaveKey
                $waveSource = 'own'
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
                # number there that belongs to someone else would split the addressing in two. On the
                # "your own previous claim" step it isn't set but INHERITED: the wave there could have
                # been a named one.
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
            } elseif ($previousOpen -and $previousOpen.WaveKey -eq $waveKey -and $previousOpen.StreamKey) {
                # ‼️ A number taken from your own previous entry stays ISSUED, not named: the yielding
                # circle must work exactly as it does today. Count it as named, and a session
                # announcing itself a second time would stop yielding to a senior neighbour and would
                # get a refusal whose only way out is destructive.
                #
                # ‼️ The condition here is a WAVE match, not "the wave wasn't named". A session is
                # entitled to name its own wave by word and not name the number; demand silence about
                # the wave, and the number would go into the general pick, carrying the name, the
                # tasks, and seniority away with it.
                $streamKey = $previousOpen.StreamKey
                $streamAuto = $true
                $streamInherited = $true
            } else {
                $streamKey = Get-NextStreamNumber -Claims $claims -WaveKey $waveKey -TreePath $tree
                $streamAuto = $true
            }
            # ‼️ The inheritance step: unnamed fields are taken from YOUR OWN UNCLOSED claim AT THE
            # SAME ADDRESS. Each field — only if it wasn't named in the call: named means the session
            # knows better, and inheritance would overwrite what was named on the spot.
            #
            # ‼️ The condition is an address match, NOT "the wave wasn't named". While this step hung
            # on silence about the wave, a partial re-announcement ("named the wave, didn't name the
            # number", or "named the plan path") kept the address but erased the name, the tasks, and
            # the plan path, reset the announcement time, and printed an untruth about a freshly
            # issued number on top of that. The costliest loss was the announcement time: seniority in
            # a number dispute goes with it, and the yielding circle hands the address to a neighbour
            # who announced later — the stream silently drifts onto another number. Continuing a
            # stream is never partial: it continues whole.
            $sameStream = (
                $previousSelf -and $previousSelf.WaveKey -eq $waveKey -and
                $previousSelf.StreamKey -eq $streamKey
            )
            if ($sameStream) {
                # The announcement time is seniority in a number dispute. Take the current one, and
                # the session would yield its number to anyone who announced between its two
                # announcements.
                #
                # ‼️ But a MOVED entry's seniority is not inherited: it was lost along with the address
                # itself, and there is nothing left to inherit. It isn't only about honesty — a session
                # that took its address back would look as if it had announced EARLIER than the address
                # was taken from it: the rival's edge would start working again, both entries would
                # supersede each other, and the address would be left with no leading entry at all.
                if (-not $previousSelf.Superseded) {
                    $inheritedAt = if ($previousSelf.Record.claimed_at -is [datetime]) {
                        # Reading JSON turns the time into a date — give it back the same shape it had
                        # when it went into the claim, or the file would end up holding a rendering in
                        # the system locale.
                        $previousSelf.Record.claimed_at.ToString('s')
                    } else {
                        [string]$previousSelf.Record.claimed_at
                    }
                    if ($inheritedAt) { $claimedAt = $inheritedAt }
                }
                if (-not $claimName) { $claimName = [string]$previousSelf.Record.name }
                if (-not $claimTasks) { $claimTasks = [string]$previousSelf.Record.tasks }
                if (-not $claimPlan) { $claimPlan = [string]$previousSelf.Record.plan }
                if ($waveSource -eq 'own') {
                    # ‼️ The "wave was self-supplied" flag is taken AS IT LAY — together with its
                    # absence — and only when the wave wasn't named. The whole "Wave Loose Ends or a
                    # reply to the owner" fork hangs on it, and on top of that a neighbour's right to
                    # join this wave. An earlier version's claim has no such field at all, and every
                    # reader judges such a claim by the wave's name; invent a value for it, and a wave
                    # named by word would suddenly become "self-supplied", and a neighbour would take
                    # a number in it that the plan had already announced. So the field wasn't there —
                    # we don't create it either (below, when the record is assembled).
                    #
                    # The wave WAS named — the flag honestly becomes "named": that's an answer about
                    # the CALL, not a property of the stream, and inheriting it against what was said
                    # would be a lie.
                    $waveAutoKnown = Test-ClaimHasField -Claim $previousSelf.Record -Name 'wave_auto'
                    $waveAuto = [bool]$previousSelf.Record.wave_auto
                }
            }
            # ‼️ A DIFFERENT stream from the same folder — refused BEFORE writing. There is ONE claim
            # per folder, so a second announcement used to erase the first one silently, and the whole
            # stream with it: its tasks looked untaken, no one addressed findings to it, and its own
            # release at the end closed someone else's entry. On 2026-08-31 stream 9 of wave 5 vanished
            # without a trace this way in a neighbouring project, and neither side noticed.
            #
            # ‼️ The refusal stands BEFORE the write, not as a warning after: a warning after is
            # exactly what doesn't work today, because by then there is nothing left to erase.
            #
            # We compare the WHOLE address — wave and number: that's what the plan calls the stream by
            # and what findings are addressed by. Matched — this is the same stream announcing itself
            # again (the branch was renamed, the session restarted), and it goes through as it does
            # today.
            #
            # A released stream doesn't count: the session that ran it is gone, and the next stream in
            # the same folder is the normal course of work. An addressless claim (no wave or no
            # number) doesn't either: there is nothing to address a finding to such a stream with
            # anyway, so there is nothing to lose in it.
            #
            # ‼️ There will be no false refusal where the tool issues the number itself either: for
            # THIS folder's unclosed claim the number is taken by the inheritance step above — that is,
            # ITS OWN, as long as the wave is the same — and the shift by the yielding circle happens
            # AFTER this check. The refusal is no dead end: name the previous address explicitly, and
            # announcing goes through as that same stream.
            #
            # ‼️ There is NO force-overwrite switch here AT ALL, and there must never be one. It is
            # destructive without need: releasing the previous stream happens in that very same folder,
            # loses nothing, and gives the same outcome. And overloading an existing switch with it (in
            # a neighbouring copy the same one means "release with a non-empty inbox", and the tool
            # itself recommends it) would breed the habit of hitting it without looking. The whole
            # class is cut out, not the one case.
            if ($previous) {
                $prevWave = [string]$previous.Record.wave
                $prevStream = [string]$previous.Record.stream
                $otherStream = (
                    -not $previous.Closed -and $prevWave -and $prevStream -and
                    ($previous.WaveKey -ne $waveKey -or $previous.StreamKey -ne $streamKey)
                )
                if ($otherStream) {
                    $prevName = if ($previous.Record.name) { " `"$($previous.Record.name)`"" } else { '' }
                    $prevTasks = if ($previous.Record.tasks) { ", tasks $($previous.Record.tasks)" } else { '' }
                    # The claim doesn't always know the branch (announced from a folder with a detached
                    # HEAD) — then that is what we say, instead of passing an empty spot off as a name.
                    $prevBranch = if ($previous.Record.branch) {
                        ", branch $($previous.Record.branch)"
                    } else {
                        ', branch not named in the claim'
                    }
                    Deny-Call @"
this worktree folder already holds a different stream: $prevWave/$prevStream$prevName$prevTasks — $($previous.State)$prevBranch, claimed $(Format-Stamp -Raw $previous.Record.claimed_at).
There is ONE claim per folder: announcing stream $waveKey/$streamKey would erase it silently — the previous stream's tasks would look untaken, no one would address findings to it, and its release at the end would close someone else's entry.
The previous stream is finished — release it right here: pwsh scripts/wave-board.ps1 -Mode Release
This is that same stream and you're announcing it again — name its address: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $prevWave -Stream $prevStream
The work is new — set up a separate worktree and announce from there.
"@
                }
            }
            # ‼️ THE ADDRESS RULE: an unclosed LEADING claim on the same address from a DIFFERENT
            # worktree folder — refused BEFORE writing. Two streams on one address are the root of
            # every consequence of the "a move splits the address in two" defect: a number taken, a
            # finding accepted for a dead address, and an arbitrary choice of which of the two entries
            # counts as the real one. The refusal stands where the split hasn't happened yet; a warning
            # AFTER the write is exactly what doesn't work today.
            #
            # ‼️ The rule applies only to a NAMED number. Where the tool issued the number itself
            # (including one inherited from your own previous entry — it stays issued), the number can
            # be MOVED, and the yielding circle below settles the dispute: it settles it correctly and
            # without a human. Dragging a refusal in there would break something that works.
            #
            # Closed entries are no rivals: a released one has no session left, a moved one has already
            # had its address taken. Your own entry is none either: we recognize it both by worktree
            # folder and by file, because an earlier version's claim from a subfolder lies under a
            # non-canonical name.
            $addressRivals = @($claims | Where-Object {
                    $_.WaveKey -eq $waveKey -and $_.StreamKey -eq $streamKey -and -not $_.Closed -and
                    (Get-FolderKey -Path $_.Record.worktree) -notin $hereKeys -and
                    (Get-FolderKey -Path $_.File) -ne (Get-FolderKey -Path $claimPath)
                })
            # The succession field and the moment of the takeover are inherited on the same footing as
            # everything else: without that, the new owner's very first short re-announcement would
            # erase the mark, and the superseded entry would come back to life along with its address,
            # its names, and its inbox.
            #
            # ‼️ They are inherited ONLY when the final address matches, and below, in the yielding
            # circle, they are dropped along with the number shift. The field names the folder THIS
            # VERY address was taken from; at a different address it turns into a false edge — the
            # claim asserts it took an address away from a folder that never ran it, the real edge
            # disappears, and the superseded entry comes back to life.
            $takenFrom = ''
            $takenAt = ''
            if ($sameStream) {
                $takenFrom = [string]$previousSelf.Record.taken_from
                $takenAt = if ($previousSelf.Record.taken_at -is [datetime]) {
                    $previousSelf.Record.taken_at.ToString('s')
                } else {
                    [string]$previousSelf.Record.taken_at
                }
            }
            $takeOverNotes = [System.Collections.Generic.List[string]]::new()
            # The entry whose address was taken: branch names are inherited from it too — a finding
            # sent under the stream's former name must reach the new folder along with the stream
            # itself.
            $takenRival = $null
            if ($addressRivals.Count -gt 0 -and -not $streamAuto) {
                # We take the senior rival by the same FULL key the number dispute is settled by: the
                # refusal and the takeover must be aimed at one and the same addressee.
                $rival = @($addressRivals | Sort-Object -Property @(
                        @{ Expression = { (Get-ClaimOrder -Claim $_.Record).When } }
                        @{ Expression = { (Get-ClaimOrder -Claim $_.Record).Path } }
                    ))[0]
                $rivalFolder = [string]$rival.Record.worktree
                $rivalKey = Get-FolderKey -Path $rivalFolder
                # Whether that folder is on disk — the clue by which a human tells a move from a
                # dispute with a working neighbour in a second. ‼️ "Can't see it" and "it's gone" are
                # not mixed: a dropped drive and a vanished network share answer with the same refusal
                # as a deleted folder.
                $onDisk = switch ((Get-PathState -Path $rivalKey).Kind) {
                    'container' { 'folder still there' }
                    'none' {
                        if (Test-PathReachable -Path $rivalKey) {
                            'folder is no longer on disk'
                        } else {
                            'the path to the folder is unreachable entirely — whether it is alive is unknown'
                        }
                    }
                    default { 'could not look at whether the folder is there' }
                }
                if (-not $TakeOver) {
                    # ‼️ Order of the ways out: the harmless one FIRST, the destructive one LAST. The
                    # session uses the first one printed, and the case is live — wave9/2 and wave9/2k
                    # are open in the registry right now, and the distinguishing suffix there was
                    # invented by hand half a day later.
                    $distinct = Get-DistinctStreamNumber -Claims $claims -WaveKey $waveKey -StreamKey $streamKey
                    $another = if ($distinct) {
                        "A different split of the same wave — announce under your own number: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $waveKey -Stream $distinct"
                    } else {
                        "A different split of the same wave — announce under your own number: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $waveKey -Stream <free number>"
                    }
                    Deny-Call @"
address $waveKey/$streamKey is already run by an unclosed claim of a DIFFERENT worktree folder: $rivalFolder — $($rival.State), checked in $(Format-Stamp -Raw $rival.Record.seen_at), $onDisk.
There is ONE leading entry per address: announce as a second one, and which of you got a finding would be decided by the directory listing order — half of what is addressed would vanish with a cheerful report of success.
$another
That stream is finished — release it, standing in exactly its folder $($rivalFolder): pwsh scripts/wave-board.ps1 -Mode Release
This is your stream and you moved here (or are picking up an abandoned session) — take the address for yourself, folder $rivalFolder will lose it: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $waveKey -Stream $streamKey -TakeOver
"@
                }
                # The switch was named — the address moves. ‼️ We write ONLY into our own claim: not
                # one byte of the other file is touched, and an older copy of the kit in a neighbouring
                # tree sees it exactly as it saw it yesterday.
                $takenFrom = $rivalFolder
                # ‼️ The moment of the takeover is NOW, even if the succession field was inherited from
                # an earlier takeover: it decides which claims this edge supersedes. Take the earlier
                # moment, and a session that took its address back would supersede entries that began
                # after the address had been taken from it — a neighbour's fresh, lawful claim included.
                $takenAt = (Get-Date).ToString('s')
                $takenRival = $rival
                $takeOverNotes.Add("Address $waveKey/$streamKey taken from folder $rivalFolder ($($rival.State), checked in $(Format-Stamp -Raw $rival.Record.seen_at), $onDisk).")
                if ($rival.State -eq 'live') {
                    # Loudly: taking over from a working neighbour is lawful, but it must be named, and
                    # it is reversible (their file wasn't touched; with the same switch they take the
                    # address back).
                    $takeOverNotes.Add('‼️ That session checked in just now — it looks like it is working. The address was taken from a working neighbour: make sure this is your move and not a dispute between two live sessions.')
                    $takeOverNotes.Add('   Made a mistake — the neighbour takes the address back with the same switch: their claim wasn''t touched.')
                }
                if ($addressRivals.Count -gt 1) {
                    # The succession field names ONE folder, and there turned out to be more entries:
                    # the rest stay leading, and staying silent about that isn't allowed — the address
                    # would simply remain split in two.
                    $rest = @($addressRivals | Where-Object { $_ -ne $rival } |
                            ForEach-Object { [string]$_.Record.worktree }) -join ', '
                    $takeOverNotes.Add("‼️ This address is also run by entries of other folders: $rest — they stayed leading.")
                    $takeOverNotes.Add('   Release each of them, standing in exactly its folder, or the address stays split in two.')
                }
            } elseif ($TakeOver) {
                # The switch was named, and there is nothing to move. Staying silent isn't allowed: the
                # session thinks it took the address and will tell its neighbours so. There are exactly
                # two reasons, and they must not be confused.
                if ($addressRivals.Count -gt 0) {
                    $takeOverNotes.Add("‼️ The takeover switch did nothing: number $streamKey was issued by the tool, not named by you.")
                    $takeOverNotes.Add('   A move doesn''t apply to an issued number — there the number can be shifted, and the yielding circle settles the dispute on its own. Name the stream number explicitly if the address really is yours.')
                } else {
                    $takeOverNotes.Add('The takeover switch wasn''t needed: no other folder''s claim runs this address.')
                }
            }
            # ‼️ Carry over the branch's earlier names into the new claim. A session can announce
            # itself a second time, and announcing silently overwrites the claim: rename the branch
            # and announce again, and the old name would vanish from everywhere — a finding ALREADY
            # ACCEPTED under it would stop arriving, and it wouldn't hold up release either. Keep it
            # only for a while: the name is needed as long as the wave lives, not forever.
            #
            # ‼️ Names are inherited by whoever matched the ADDRESS, not by whoever sat down in THE
            # SAME FOLDER. A branch name is a way of addressing a finding to a STREAM, so it belongs
            # to the stream, not to the worktree folder: a folder gets reused, and the stream doesn't
            # move in with it. Hence two symmetrical troubles from carrying names over unconditionally:
            # a stream was released, the next one announced itself from the same folder — and it
            # started answering to the released one's name and intercepting its findings; while a
            # session that moved, the other way round, lost its name memory, because in the new folder
            # there was no one to inherit from. Wave AND number matched — this is the same stream
            # announcing itself again, and the names are its own; they diverged — this is a different
            # stream, and someone else's names aren't its due.
            #
            # ‼️ And the claim must be UNCLOSED as well (the `$sameStream` flag is computed above, from
            # your own previous unclosed one). A released one at the same address gives up no names:
            # the session that ran that stream is gone, and the next stream in this folder is a
            # different one. Ignore the state, and the folder's new tenant would answer to the released
            # neighbour's name and receive findings addressed to it — exactly the trouble
            # address-based inheritance exists to prevent, only coming in from the other side. On top
            # of that the tool promises at release that findings will no longer be accepted for a
            # released stream — a promise that has to be kept.
            #
            # ‼️ When an address is moved, names are inherited from the entry the address was taken
            # FROM. The inbox is work, not bookkeeping: moving the address without moving the names
            # would be the same loss, only from the other side — a finding ALREADY SENT under the
            # stream's former branch name wouldn't reach the folder the stream moved to. Both entries
            # have one and the same address, so the stream is one: the rule "names belong to the
            # stream, not to the folder" isn't broken here.
            $inheritNamesFrom = [System.Collections.Generic.List[object]]::new()
            # ‼️ Your own entry — the same one the name and the tasks were taken from: an open one and
            # a moved one alike. A session that came back has its OWN name memory, and losing it on the
            # way back isn't allowed — a finding already sent under its former branch name would
            # otherwise reach no one.
            if ($sameStream) { $inheritNamesFrom.Add($previousSelf.Record) }
            if ($takenRival) { $inheritNamesFrom.Add($takenRival.Record) }
            $former = [System.Collections.Generic.List[string]]::new()
            foreach ($source in $inheritNamesFrom) {
                $earlier = @([string]$source.branch) + @($source.former_branches)
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
                name            = $claimName
                tasks           = $claimTasks
                plan            = $claimPlan
                branch          = $branch
                # Names the stream was known by earlier: a finding sent under one of them must still
                # arrive.
                former_branches = @($former)
                worktree        = $tree
                claimed_at      = $claimedAt
                seen_at         = (Get-Date).ToString('s')
                state           = 'open'
            }
            if ($takenFrom) {
                # ‼️ We put the field in ONLY when a takeover actually happened. An empty field on every
                # claim is noise that copies of the kit of different ages would read differently; the
                # absence of the field is read the same way by all of them: "there was no move".
                $claim.taken_from = $takenFrom
                # The moment sits next to the folder: it decides which claims this edge supersedes. It
                # is empty in exactly one case — the field was inherited from an earlier version's
                # claim, which wrote the folder alone; then the edge works unconditionally, as it did
                # before this rule.
                if ($takenAt) { $claim.taken_at = $takenAt }
            }
            # ‼️ THIS FOLDER's previous claim's PAST moves move into the new claim. There is one claim
            # per folder, and without this list announcing the next stream would erase the edge along
            # with the file: the previous folder's abandoned entry would become leading again —
            # silently, with an "it will get there on its own" report at the accepting end and a
            # finding carried to an abandoned session. The same thing also killed an A→B→C chain the
            # moment the MIDDLE folder re-announced itself.
            #
            # The list is inherited from ANY previous claim of this folder — a released one, a moved
            # one, and your own unclosed one alike: a takeover is an event in the ADDRESS's history, and
            # the list keeps each takeover's address separately from the claim's current address.
            #
            # ‼️ Compatibility with old copies is incomplete, and lying about that isn't allowed. They
            # READ unfamiliar fields tolerantly: an old copy's delivery guard rewrites the claim file
            # on every turn and preserves the takeover memory while doing so — that is verified by the
            # suite. But a FULL announcement by an old copy in this same folder rebuilds the claim from
            # its own fixed set of fields and erases both the list of past takeovers and the current
            # succession field: the previous folder's ghost silently becomes leading again. The only
            # cure is rolling the fix out to every copy of the kit. Other claim files still aren't
            # touched by a single byte in the process — an old copy won't touch them either.
            $droppedTakeovers = @()
            $pastTakeovers = @(Get-ClaimPastTakeovers `
                    -Previous $(if ($previous) { $previous.Record } else { $null }) `
                    -Wave $waveKey -Stream $streamKey -From $takenFrom -Dropped ([ref]$droppedTakeovers))
            if ($pastTakeovers.Count -gt 0) { $claim.past_takeovers = @($pastTakeovers) }
            foreach ($gone in $droppedTakeovers) {
                # What was dropped on hitting the memory limit — out loud, in the same command: losing
                # an edge silently brings the previous folder's abandoned entry back to life, and
                # nobody will ever mention it again.
                $takeOverNotes.Add("‼️ The takeover memory is full ($(Get-PastTakeoverLimit)) — the oldest takeover was forgotten: address $($gone.Wave)/$($gone.Stream), taken from folder $($gone.From).")
                $takeOverNotes.Add('   If that folder''s entry is still in the registry, it will become leading on that address again — release it, standing in its folder.')
            }
            if ($waveSource -eq 'own' -and -not $waveAutoKnown) {
                # An earlier version's claim doesn't carry the flag, and a re-announcement has no right
                # to decide for it: create the field now, and the claim would change kind, and with it
                # the answers to "is there a plan" and "can I join this wave".
                $claim.Remove('wave_auto')
            }
            Write-ClaimFile -Path $claimPath -Claim $claim
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
                    # ‼️ The address has diverged from the one it was taken from — so the succession
                    # field is no longer about the CURRENT address. Leave it as it is, and the claim
                    # would assert it took an address away from a folder that never ran it: the real
                    # takeover edge disappears, and the entry it superseded comes back to life along with
                    # its inbox. The takeover switch never reaches here (it only goes with a NAMED number,
                    # and the yielding circle doesn't shift those), so what gets shifted here is always
                    # an inherited field.
                    #
                    # ‼️ But it can't simply be thrown away either: the takeover DID happen, and the
                    # previous folder's entry was superseded by it. Throw the memory away, and the
                    # ghost would come back to life just the same, only from the other side. So the
                    # takeover goes into the list of past ones TOGETHER WITH ITS ADDRESS — the one the
                    # stream ran before the shift; the address the stream left will have no leading
                    # entry at all, and that is right: the stream moved away and ended there.
                    if ($claim.Contains('taken_from') -and $claim.taken_from) {
                        $movedTakeover = [ordered]@{
                            wave       = $waveKey
                            stream     = $movedFrom
                            # The folder goes in normalized — in the same shape the past-moves list is
                            # assembled in: two shapes of one folder couldn't collapse together, and
                            # the takeover would lie in the list twice.
                            taken_from = (Get-FolderKey -Path ([string]$claim.taken_from))
                        }
                        if ($claim.Contains('taken_at') -and $claim.taken_at) {
                            $movedTakeover.taken_at = [string]$claim.taken_at
                        }
                        $claim.past_takeovers = @(
                            @(@($claim.past_takeovers) | Where-Object { $_ }) + @($movedTakeover)
                        )
                    }
                    $claim.Remove('taken_from')
                    $claim.Remove('taken_at')
                    Write-ClaimFile -Path $claimPath -Claim $claim
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
        # Moving the address comes first, right after the announcement itself: it is the only thing the
        # tool did FOR the session that touches a neighbour, and everything it does by itself it must
        # say out loud in the very command where it happened.
        foreach ($note in $takeOverNotes) { $note }
        # ‼️ ANNOUNCING REREADS THE REGISTRY AND TELLS THE TRUTH. The claim went into its own file —
        # but whether it is leading or already superseded is decided by the registry AS A WHOLE, not by
        # the file: someone else's takeover edge could have superseded it at that very instant. That
        # happens on an equal second (the uncertainty was resolved in favour of the edge working — and
        # that is right) and in a take-back circle that fits within a second: the session ran the
        # command the tool itself printed for it and would have got a cheerful report of success, even
        # though from the outside it doesn't exist.
        #
        # The rule here is general, not a patch over two scenes: we report silent success NOWHERE the
        # tool decided for the human. Any future way of superseding your own entry while announcing
        # will name itself out loud on its own.
        # Your own entry turned out superseded — announcing ends in a REFUSAL, not a silent zero. It is
        # set here and printed at the very end: first the session reads everything the tool tells it.
        $quenchedAtWrite = $false
        if (-not $snapshotFailed) {
            $mineNow = Get-ClaimEntry -Claims $claims -Claim $claim -Path $claimPath
            if ($mineNow -and $mineNow.Superseded) {
                $quenchedAtWrite = $true
                $fate = Get-ClaimAddressFate -Claim $mineNow
                # The moment of THAT VERY move the entry was superseded by, not of the taking claim's
                # current fields: it may have several moves, and they are about something else.
                $whenTaken = if ($mineNow.TakenAt) {
                    "takeover $(Format-Stamp -Raw $mineNow.TakenAt)"
                } else {
                    'that claim doesn''t name the moment of the takeover — it is of an earlier version'
                }
                "‼️ Your entry was SUPERSEDED at once by someone else's takeover: $($fate.Text) ($whenTaken)."
                '   Outside, this session doesn''t exist: findings for the address won''t be brought to it, and its release will say there is nothing to release.'
                # ‼️ We recommend the takeover switch ONLY where somebody really does run the address: it
                # takes the address away from another folder's leading claim, and there may be none
                # here at all — then the switch will honestly answer "wasn't needed", and the session
                # will go round in circles running the one way out printed for it. A printed way out
                # has to work.
                if ($fate.StillLed) {
                    "   The address really is yours — take it back: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $waveKey -Stream $streamKey -TakeOver"
                } else {
                    '   There is nothing to take the address back from: it has no leading entry left — the folder that took it has moved on or finished the stream.'
                }
                "   There is nothing to dispute — announce under a free number: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $waveKey -Stream <free number>"
            }
            # The same thing about the address as a whole: it may have no leading entry left at all —
            # that is what an address looks like when its stream moved away and ended there. The
            # outcome is lawful, but it must not be invisible: a finding for such an address won't be
            # accepted.
            $leading = @($claims | Where-Object {
                    $_.WaveKey -eq $waveKey -and $_.StreamKey -eq $streamKey -and -not $_.Closed
                })
            if ($leading.Count -eq 0) {
                "‼️ Address $waveKey/$streamKey has no leading entry left — a finding for it won't be accepted, and neighbours can't address it."
                '   Work out from the listing where the stream went: pwsh scripts/wave-board.ps1 -Mode Streams'
            }
        }
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
        # The line about continuing your own stream. Assembled HERE, because it is printed from two
        # places: the wave wasn't named — it goes as a tail to the fourth wave source; it was named —
        # on its own, below.
        #
        # ‼️ The address in it is the one the stream RAN, not the one that came out of the yielding
        # circle. The circle could have shifted the number after the stream was recognized, and
        # printing the current one as the continued one would name the session an address it never
        # had: "you're continuing stream wave/2, claimed yesterday" — while yesterday it was wave/1.
        # The tool speaks about the shift itself on the next line, but the first line is believed
        # before the second one is read.
        $ownName = if ($claim.name) { " `"$($claim.name)`"" } else { '' }
        $ownAddress = if ($movedFrom) { "$waveKey/$movedFrom" } else { "$waveKey/$streamKey" }
        $ownMoved = if ($movedFrom) { ' — the number was yielded to a neighbour, the new address is named below' } else { '' }
        $continued = "You're continuing stream $ownAddress$ownName, claimed $(Format-Stamp -Raw $claim.claimed_at)$ownMoved."
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
            'own' {
                # The fourth wave source. Staying silent about it isn't allowed for exactly the same
                # reason as about the other three: the session announced itself briefly and got an
                # address, a name, and tasks it never named in the call — and it will pass them on to
                # neighbours as its own.
                "Wave not named — inherited from your previous entry. $continued"
                '   The work is different — release the previous stream right here (-Mode Release) and announce again.'
            }
        }
        if ($sameStream -and $waveSource -ne 'own') {
            # The wave (or the plan path) was named, and everything else the session took from its own
            # previous entry. We say so exactly the way we do about the fourth wave source: silence
            # here would cost the same — the session got a name, tasks, and seniority it never named
            # in the call.
            $continued
            '   The work is different — release the previous stream right here (-Mode Release) and announce again.'
        }
        # We mention an issued number only when it really was issued now. One inherited from your own
        # entry was issued last time, not now, and calling it "the next free one" would be lying to the
        # session about its own address. One shifted by the yielding circle is issued again — about
        # that we speak as before.
        if ($streamAuto -and (-not $streamInherited -or $movedFrom)) {
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
                $_.WaveKey -eq $waveKey -and (Get-FolderKey -Path $_.Record.worktree) -ne (Get-FolderKey -Path $tree)
            })
        if ($neighbours.Count -gt 0) {
            ''
            "Neighboring streams in wave ${waveKey}:"
            foreach ($neighbour in $neighbours) { Format-ClaimLine -Claim $neighbour }
            'Working outside your own tasks — check who owns it first: -Mode Streams -Task <number>.'
        }
        if ($quenchedAtWrite) {
            # ‼️ A REFUSAL, not a success: from the outside this session doesn't exist, and a zero code
            # would read as "announced, working". The claim file HAS been written — that is said above,
            # and the command doesn't need repeating: it would give exactly the same outcome. Not one
            # of the kit's guards, the installer, or the task template reads the announcement's exit
            # code (verified), so the price of a refusal here is only the truth, told in a way that
            # can't be missed.
            #
            # Printed LAST and to the error stream, like every refusal the tool makes.
            [Console]::Error.WriteLine("announcing failed: the entry for stream $waveKey/$streamKey was superseded by someone else's takeover at the very same instant. The claim file is written, but from the outside the session doesn't exist — the ways out are printed above.")
            exit 1
        }
    }

    'Release' {
        # Releasing the stream. This is the one place that asks "is everything that arrived actually
        # handled" — without it, an entry placed ten minutes before the session closes reaches no
        # one, ever, while the sender has already been told it succeeded.
        # ‼️ The tree root — strictly and first, just as in announcing: your own claim is looked up by
        # it. A silent fallback to the current folder hits the costliest spot here — release doesn't
        # find the claim and exits with SUCCESS, the session closes, and the stream goes on counting
        # as live.
        $tree = Get-TreeRoot -Strict
        # ‼️ RELEASING AN ORPHAN BY ADDRESS. This is the ONLY operation in the kit that writes into
        # SOMEONE ELSE'S claim file, and it is allowed in exactly one situation: the entry's folder is
        # NOT on disk, while the path itself is reachable.
        #
        # The condition was chosen by the ABSENCE OF A WRITER, not by silence. A claim file has a
        # second writer — that folder's delivery guard: it reads the whole document and writes it
        # whole, on every turn of the session, takes no lock and cannot take one. In a folder that
        # doesn't exist it can't start at all, so there is no writer and no race. A silent check-in
        # mark is NEVER such a proof: it proves exactly one thing — the session made no turns — and a
        # subagent run, waiting for a build, and an overnight pause all look the same.
        #
        # ‼️ "I can't see it" and "it's gone" are not the same thing here, and mixing them is
        # forbidden: a dropped drive and a vanished network share answer with the same refusal as a
        # deleted folder. We tell them apart the same way the address rule does — by the path's
        # reachability.
        #
        # Your own stream is released from your own folder and needs no switches at all; here they
        # mean "release the entry left behind by a session that is gone".
        if ($Wave -or $Stream) {
            if (-not $Wave -or -not $Stream) {
                Deny-Call 'releasing by address needs both the wave and the stream number: -Mode Release -Wave <wave> -Stream <number>. Your own stream is released from your own worktree folder, with no switches at all.'
            }
            $wantWave = Get-WaveKey -Raw $Wave
            $wantStream = Get-StreamNumberKey -Raw $Stream
            $address = "$wantWave/$wantStream"
            # Under the claim registry's lock: at this very instant a neighbouring session may be
            # taking that same entry with the takeover switch. The lock wasn't handed over — we say so out
            # loud and carry on, the way announcing does: the decision is made from the snapshot we
            # read, and it is useful even without the lock.
            $lockHandle = Enter-RegistryLock -Dir $registry
            try {
                # ‼️ Strict: this snapshot decides SOMEONE ELSE'S entry's fate. Missing a neighbour
                # here means declaring gone the one whose file is merely busy at that instant.
                $all = @(Get-Claims -Dir $registry -Strict)
                Set-KnownWaves -Keys @($all | ForEach-Object { $_.WaveKey })
                $orphans = @($all | Where-Object {
                        $_.WaveKey -eq $wantWave -and $_.StreamKey -eq $wantStream -and -not $_.Closed
                    })
                if ($orphans.Count -eq 0) {
                    Deny-Call @"
there is no unclosed entry with address $address in the registry — nothing to release at this address.
Who's running which stream: pwsh scripts/wave-board.ps1 -Mode Streams
"@
                }
                if ($orphans.Count -gt 1) {
                    # Choosing for the human isn't allowed: we would release one at random, and the
                    # other would stay holding the address alive — that is, accepting findings that
                    # reach no one. We name each one along with its file: by the file the owner will
                    # find it by hand, even if the folder is already gone.
                    $listed = @($orphans | ForEach-Object {
                            "$(Format-ClaimLine -Claim $_)`n    claim file: $($_.File)"
                        }) -join "`n"
                    Deny-Call @"
address $address is run by several unclosed entries at once ($($orphans.Count)) — which of them to release, the tool has no right to decide.
$listed
Release each one, standing in exactly its worktree folder: pwsh scripts/wave-board.ps1 -Mode Release
The entry's folder is already gone from disk — remove its claim file by hand, the path is named above.
"@
                }
                $orphan = $orphans[0]
                $orphanFolder = [string]$orphan.Record.worktree
                $orphanKey = Get-FolderKey -Path $orphanFolder
                $folderState = Get-PathState -Path $orphanKey
                switch ($folderState.Kind) {
                    'none' {
                        if (-not (Test-PathReachable -Path $orphanKey)) {
                            Deny-Call @"
the path to stream $address's worktree folder is unreachable entirely: $orphanFolder — whether it is alive is unknown.
A dropped drive and a vanished network share answer with the same refusal as a deleted folder, and writing into someone else's claim is allowed exactly where a writer is KNOWN not to exist.
Bring the path back (attach the drive or the network share) and retry.
"@
                        }
                    }
                    'container' {
                        Deny-Call @"
stream $address's worktree folder is still there: $orphanFolder — go into it and release the stream from there: pwsh scripts/wave-board.ps1 -Mode Release
There is no bypass switch here and there never will be: a claim in a live folder has a second writer — that session's delivery guard — and the release mark would be wiped by its next liveness check-in, AFTER we had reported success.
The entry is silent ($($orphan.State), checked in $(Format-Stamp -Raw $orphan.Record.seen_at)) — that is no proof: the session could have been running subagents, waiting for a build, or standing idle overnight.
"@
                    }
                    default {
                        $why = if ($folderState.Reason) { $folderState.Reason } else { 'reason unknown' }
                        Deny-Call @"
could not look at whether stream $address's worktree folder is there ($orphanFolder): $why.
Releasing someone else's entry blind isn't allowed: writing into it is allowed exactly where the folder is known to be gone and no writer exists.
"@
                    }
                }
                # The folder is gone and the path is reachable — no writer exists, and the entry can be
                # closed.
                if (-not $lockHandle) {
                    # The lock wasn't handed over. That doesn't touch writing into SOMEONE ELSE'S file
                    # — there is no writer there at all — but a neighbouring session could have taken
                    # the address with the takeover switch during those same seconds, and then the decision
                    # was made from a snapshot made stale by its claim. Staying silent isn't allowed:
                    # everything the tool decided by itself it says out loud in the same command.
                    "‼️ The claim registry lock wasn't handed over within $(Get-RegistryLockWaitSeconds)s — the entry was released from a snapshot taken without it."
                }
                $orphanRecord = $orphan.Record
                # Through `Add-Member -Force`, not assignment: the orphan's claim could have been filed
                # by an earlier version that has no state field at all — assignment would fail, and the
                # orphan would stay holding the address alive.
                $orphanRecord | Add-Member -NotePropertyName state -NotePropertyValue 'released' -Force
                $orphanRecord | Add-Member -NotePropertyName released_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
                # ‼️ A trace of "who released it". Your own release doesn't write one — there the
                # releaser and the owner are the same session. Here the entry was closed by an
                # OUTSIDER, and without a trace it would look like an honest release by the session
                # itself: the owner could never tell one from the other.
                $orphanRecord | Add-Member -NotePropertyName released_from -NotePropertyValue $tree -Force
                Write-ClaimFile -Path $orphan.File -Claim $orphanRecord
                "Entry $address released by address: worktree folder $orphanFolder is not on disk, and its claim has no writer."
                "Trace in the entry: released $(Format-Stamp -Raw $orphanRecord.released_at) from folder $tree."
                'Findings will no longer be accepted for this stream.'
                # What was left in the orphan's inbox — out loud, right here. The session that would
                # have handled it is gone, so there is no point refusing and calling for a switch:
                # whoever releases it will have to handle it by hand. Stay silent, and the findings
                # would vanish along with the address, while their authors had already been told it
                # succeeded.
                $content = Read-BoardContent -Path $board
                if ($content.Ok) {
                    $orphanKeys = @(Get-StreamNames -Claim $orphanRecord -Claims $all)
                    $onBoard = @(Select-OpenEntries -Entries (Get-BoardEntries -Lines $content.Lines) -Viewer $orphanKeys |
                            ForEach-Object { $_.Record })
                    # "Handled" acknowledgments don't count: they clear themselves on display and carry
                    # no work.
                    $waiting = @(Select-ForStream -Records $onBoard -Keys $orphanKeys -Claim $orphanRecord |
                            Where-Object { [string]$_.kind -ne 'ack' })
                    if ($waiting.Count -gt 0) {
                        "‼️ The released entry's inbox still has $($waiting.Count) open — they now have nowhere to arrive, handle them by hand."
                        foreach ($record in $waiting) { Format-BoardRecord -Record $record }
                    }
                } else {
                    "‼️ Could not read the board ($($content.Reason)) — what is left in this entry's inbox isn't said here."
                }
            } finally {
                # Always release the lock, on a refusal too: otherwise a session that crashed would
                # hold up its neighbours' announcements for the rest of its life.
                Exit-RegistryLock -Handle $lockHandle
            }
            return
        }
        $claimPath = Get-ClaimPath -Dir $registry -TreePath $tree
        # Strict, deliberately: "no claim" and "file is busy" lead in opposite directions, yet look
        # identical — empty. Read your own claim FIRST: if it's the one that's corrupted, the owner
        # should get a refusal about THEIR OWN file and its path, not a generic refusal about a
        # neighbour's claim.
        $mine = Get-CurrentClaim -Dir $registry -Strict
        # ‼️ "No claim" is only true when the registry CAN BE READ. On a dead path (the drive
        # dropped, the share disappeared), the absence of your own file looks exactly the same,
        # and the session used to be told "nothing to release" with a success code. So ask the
        # registry strictly: it's the one that tells "no claims" apart from "couldn't even look". We
        # read it HERE, because this folder's entry is looked up through it as well.
        $all = @(Get-Claims -Dir $registry -Strict)
        $claim = $null
        # ‼️ FIRST comes the entry whose recorded worktree folder EXACTLY equals the one being
        # released from, and only then the canonical key (the tree root). The order is exactly this
        # because the listing prints the folder FROM THE ENTRY, and the one printed way out of a split
        # address reads "release the extra one from its folder". While the canonical key came first,
        # that advice was not merely unfollowable but HARMFUL: a person stood in the ghost's folder,
        # release resolved it to the tree root and closed the LIVE entry, leaving the ghost open — and
        # reported success. (Reproduced by a reviewer on a live scene.)
        #
        # The match is EXACT only — not "starts with", not "lies inside": otherwise a session would
        # take the claim of a neighbouring tree nested in its folder. It breaks nothing where the
        # session stands at the root either: its own entry's folder IS the root, and both routes lead
        # to the same entry.
        $exact = Find-ClaimByWorktree -Claims $all -Paths @($PWD.Path)
        if ($exact) {
            $claim = $exact.Record
            $claimPath = $exact.File
        } elseif ($mine) {
            $claim = $mine
        } else {
            # The third route — by an exact match on the tree ROOT. That is how claims filed by an
            # earlier version from a subfolder are found: their file name is derived from that folder,
            # and the canonical key doesn't find them. We then write back into the file we FOUND: the
            # claim stays where it lay, and not one file is created or deleted.
            $found = Find-ClaimByWorktree -Claims $all -Paths @($tree)
            if ($found) {
                $claim = $found.Record
                $claimPath = $found.File
            }
        }
        if (-not $claim) {
            'No claim on this session — nothing to release. Announce with: -Mode Claim (wave and stream number can be left unnamed).'
            return
        }
        if ([string]$claim.state -eq 'released') {
            # We name the folder here too: otherwise a person takes an "already released" answer about
            # SOMEONE ELSE'S entry for their own.
            "Stream $($claim.wave)/$($claim.stream) already released ($(Format-Stamp -Raw $claim.released_at)), worktree folder $($claim.worktree)."
            return
        }
        # ‼️ This session's address was TAKEN AWAY. That state lives only in the parsed registry — in
        # its own file the entry looks open — and had we not asked the registry, release would have
        # closed an entry nobody addresses any more and reported "stream released, findings will no
        # longer be accepted for it" about a stream another folder is running at that very moment. We
        # tell the truth: it was moved.
        $myEntry = Get-ClaimEntry -Claims $all -Claim $claim -Path $claimPath
        if ($myEntry -and $myEntry.Superseded) {
            # ‼️ "That session runs the address" is true only when the folder that took it really does
            # run THE SAME address. It may have released itself since, or picked up the next stream —
            # then the address has no leading entry left at all, and sending a person there isn't
            # allowed.
            $fate = Get-ClaimAddressFate -Claim $myEntry
            if ($fate.StillLed) {
                "Stream $($claim.wave)/$($claim.stream) $($fate.Text) — there is nothing to release here: that session runs the address."
                'This session is no longer addressable: findings for the address arrive there, and they can''t be closed from here.'
                # ‼️ We recommend the takeover switch only where it has someone to take the address from:
                # without a leading entry it will answer "wasn't needed", and the printed way out turns
                # out empty.
                "This is your stream and it was moved by mistake — take the address back: pwsh scripts/wave-board.ps1 -Mode Claim -Wave $($claim.wave) -Stream $($claim.stream) -TakeOver"
            } else {
                "Stream $($claim.wave)/$($claim.stream) isn't run here any more: $($fate.Text) — nothing to release."
                'The address has no leading entry left: a finding for it won''t be accepted, and this session isn''t addressable from the outside.'
                'There is nothing to take the address back from — the folder that took it has moved on or finished the stream. New work — announce under a free number: pwsh scripts/wave-board.ps1 -Mode Claim'
            }
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
        Write-ClaimFile -Path $claimPath -Claim $claim
        # ‼️ We say WHAT exactly was closed: the entry's address, name, and worktree folder. Release
        # has three routes to its own claim, and a human isn't obliged to remember which one fired;
        # while only the address was printed, a substituted entry was invisible to them altogether —
        # and that is exactly how a reviewer got a live entry released instead of a ghost, with a
        # cheerful report of success.
        $closedName = if ($claim.name) { "`"$($claim.name)`"" } else { 'unnamed' }
        $closedFolder = if ($claim.worktree) { [string]$claim.worktree } else { 'not named in the claim' }
        "Closed claim $($claim.wave)/$($claim.stream) $closedName, worktree folder $closedFolder."
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
        # We look for a split address BEFORE filtering by task, but after filtering by wave: filtering
        # by task leaves one line out of the two, and the split would go invisible in exactly the
        # question it has to be known for — "whose piece of work is this".
        $doubled = @(Get-DoubledAddresses -Claims $claims)
        # ‼️ And the reverse trouble of the same kind: an address has entries that are unclosed in
        # their own files, and not one leading. That is what a session that doesn't exist from the
        # outside looks like: its claim file is open, it believes it is running the stream, and a
        # finding for that address won't be accepted and won't be brought by the delivery guard. In
        # itself this is a lawful end to an address's story (the stream moved away and ended there),
        # but a human can't make the "is this session alive" choice while nobody says anything.
        $leaderless = @(Get-LeaderlessAddresses -Claims $claims)
        if ($Task) {
            $claims = @($claims | Where-Object { Test-TaskInList -Tasks ([string]$_.Record.tasks) -Task $Task })
        }
        # ‼️ A split address gets its own loud line, not two similar-looking lines in the list. While
        # nobody says anything about it, the choice of "which of the two entries counts as the real
        # one" is made by the directory listing order, not by a human: a finding goes to one of them
        # silently, and half of what is addressed vanishes with a cheerful report of success.
        #
        # ‼️ Printed BEFORE any early return. The line used to be computed before the task filter too,
        # but printed after — and it vanished in exactly the most dangerous answer: "no one's taken
        # this task" reads as permission to take the piece, and at that moment it may be run by two
        # entries at once.
        foreach ($pair in $doubled) {
            $folders = @($pair.Claims | ForEach-Object { [string]$_.Record.worktree }) -join ' and '
            "‼️ Address $($pair.Address) is run by several unclosed entries at once ($($pair.Claims.Count)): folders $folders."
            '   Which of them gets a finding is decided by the directory listing order — work out whose session is actually running the stream, and release the extra one, standing in exactly its folder.'
        }
        foreach ($pair in $leaderless) {
            $folders = @($pair.Claims | ForEach-Object { [string]$_.Record.worktree }) -join ' and '
            "‼️ Address $($pair.Address) has no leading entry left, and unclosed claims exist: folders $folders."
            '   A finding for this address won''t be accepted: the stream moved away and ended there. The sessions in those folders aren''t addressable from the outside — they need to announce themselves again or take the address back with the takeover switch.'
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
        # The folder name comes from the tree ROOT: from a subfolder the session would name itself by
        # the subfolder's name, wouldn't recognize itself in it, and would get its own "to everyone"
        # entry back.
        $from = ((Get-TreeRoot) -split '/')[-1]
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
        $addressed = @(Find-Claims -Claims $claims -Raw $To | Where-Object { -not $_.Closed })
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
        # ‼️ A split address gets a loud line in the accepting step too, not only in the listing. What
        # reassures the finding's author is exactly the accepting step: after a cheerful "it will most
        # likely get there on its own" they set up no fallback item in the Wave Loose Ends, while the
        # finding may go to the wrong one — and which one is decided by the directory listing order.
        # Staying silent costs most right here.
        if ($addressed.Count -gt 1 -and $wanted -notin @('*', '**')) {
            $rivalFolders = @($addressed | ForEach-Object { [string]$_.Record.worktree }) -join ' and '
            "‼️ Address `"$To`" is run by $($addressed.Count) unclosed entries at once: folders $rivalFolders — the finding may go to the wrong one."
            '   Work out whose session is actually running the stream, and release the extra one, standing in exactly its folder: -Mode Streams'
        }
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
            $myClaimPath = ''
            if (-not $myClaim) {
                # ‼️ The second route to your own claim — the same one release and the delivery guard
                # already use: by an EXACT match on the worktree folder recorded in the claim. Claims
                # filed by an earlier version from a subfolder lie under that subfolder's key and
                # aren't found by the canonical key at all.
                #
                # Without it the closed-ness check didn't fire at all, and a session whose address had
                # been TAKEN AWAY would clear the new owner's finding: closing a name-based address is
                # SHARED, so they wouldn't see it in delivery or on the board, and its author would
                # get an "acknowledged".
                #
                # This is a READ: not one file is created, deleted, or renamed.
                $found = Find-ClaimByWorktree -Claims $registryNow -Paths @((Get-TreeRoot), $PWD.Path)
                if ($found) {
                    $myClaim = $found.Record
                    $myClaimPath = $found.File
                }
            }
            $myNames = @(Get-StreamNames -Claim $myClaim -Claims $registryNow)
            # ‼️ A CLOSED entry closes no findings at all — neither a released one nor a moved one. We
            # ask the registry about our own state, not our own file: a takeover isn't reflected in the
            # file, and a session whose address had been taken away would clear the new owner's
            # findings — closing a name-based address is SHARED, so they wouldn't see them in delivery
            # or on the board, and the author would get an "acknowledged" from a stream they never
            # named.
            #
            # ‼️ The condition starts with "there IS a claim". A session that never announced itself
            # closes what's addressed to it by branch or folder name — that's the fallback path, and
            # it worked before the registry existed; zero out the names for it too, and the only way
            # out of the board would be locked in front of every unannounced session.
            if ($myClaim -and (Test-ClaimClosed -Claims $registryNow -Claim $myClaim -Path $myClaimPath)) {
                $myClaim = $null
                $myNames = @()
            }
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
