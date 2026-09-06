#Requires -Version 7
<#
PreToolUse guard: work is about to be committed from a stream that never announced itself — refuse
before it lands.

Why. Announcing is what makes a session reachable at all: findings from neighbours have somewhere to
arrive, the tasks the stream is running show as owned, and a neighbour asking "who owns this task"
gets an answer. Everything else in this kit is an agreement a session keeps of its own accord — and
an unannounced session is precisely the one that isn't keeping it. It cannot be reminded, either: it
is invisible, so there is nothing to send a reminder to.

Which is why the reminder waits for the one moment when an unannounced stream stops being a private
matter: the commit. Up to that point the work is local and reversible; from there it travels into a
branch, a pull request, and someone else's day.

The guard REFUSES rather than nudging, and this is the only place in the kit that refuses a session
anything. The reasoning: a nudge in context is read by the same session that already skipped the
announcement in its brief, whereas a refusal stops the exact command and hands back the line that
fixes it. It catches forgetfulness, not intent — one deliberate line of shell gets around it — and
that is enough, because forgetfulness is what actually happens.

The way out is always one command, and it is printed in full. Nothing has to be worked out: with no
wave and no plan the channel supplies the wave itself and hands out a free number.

Deliberate exception: PARALLEL_STREAMS_ALLOW_UNCLAIMED=1 for the session. The valve exists for the
cases the guard cannot tell apart from a stream — a person committing from the repository's main
folder, a fix to the channel itself, a bulk chore. It is one variable, and it is visible in the
refusal.

On ANY unexpected condition the guard stays out of the way and exits with zero: a broken registry, an
unreadable claim, git not answering. A kit that installs itself into someone else's project may not
turn its own trouble into a project that cannot commit.
#>

param(
    # Tests only: a board off to the side of the real one, exactly as in the delivery hook. In
    # production the path comes from git.
    [string]$BoardPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# The commands after which the work stops being this session's private business. Committing is the
# moment the guard exists for; pushing and opening a pull request are here because a session may have
# committed before the channel was installed, and the next chance to catch it is the one after.
#
# Merging and rebasing are deliberately NOT here: they move work that already exists, and a stream
# that reached a merge has long since had its chance to be caught. Blocking them would only add
# refusals at the end of the work, where they help nobody.
$PublishingCommands = '(?i)(\bgit\b[^\r\n]*\b(commit|push)\b|\bgh\b\s+pr\s+create\b)'

function Send-Denial {
    param([string]$Reason)
    @{
        hookSpecificOutput = @{
            hookEventName            = 'PreToolUse'
            permissionDecision       = 'deny'
            permissionDecisionReason = $Reason
        }
    } | ConvertTo-Json -Depth 5 -Compress
    exit 0
}

try {
    . (Join-Path $PSScriptRoot '../lib/hook-io.ps1')
    $raw = Read-HookInput
    if (-not $raw) { exit 0 }
    $call = $raw | ConvertFrom-Json
    if (-not $call) { exit 0 }

    # The command is examined BEFORE anything else is loaded: this hook runs ahead of every shell
    # call in the session, and on the ones that are none of its business it must cost nothing at all.
    $command = [string]$call.tool_input.command
    if (-not $command) { exit 0 }
    if ($command -notmatch $PublishingCommands) { exit 0 }

    if ($env:PARALLEL_STREAMS_ALLOW_UNCLAIMED -eq '1') { exit 0 }

    # The session's working folder comes from the call, not from wherever this hook happens to be
    # running: the whole identity of a stream is derived from its worktree, and asking about someone
    # else's folder would answer about someone else's stream.
    $cwd = [string]$call.cwd
    if ($cwd -and (Test-Path -LiteralPath $cwd -PathType Container)) {
        Set-Location -LiteralPath $cwd
    }

    . (Join-Path $PSScriptRoot '../lib/wave-board-lib.ps1')

    # ‼️ Asked STRICTLY, and this is the whole difference between a guard and a nuisance. Everywhere
    # else in the kit a tolerant read costs one invisible line; here it costs a refusal. Read
    # tolerantly, an unreadable registry — a dropped drive, a claim file busy for a moment, a folder
    # replaced by a file — comes back EMPTY, and empty is indistinguishable from "this stream never
    # announced itself": the guard would refuse a session that announced perfectly well, and go on
    # refusing it for as long as the trouble lasted. Strict turns every one of those into an
    # exception, and the catch below turns the exception into silence.
    $registry = Get-RegistryDir -BoardOverride $BoardPath

    # ‼️ And the registry's own shape is checked separately, before reading it — because strictness
    # alone does not answer the same way on every system. A FILE standing where the claims folder
    # belongs raises one kind of failure on Windows (strict reading refuses out loud) and another on
    # Linux, where it is indistinguishable from "the folder does not exist yet" — and there the
    # strict read comes back with an empty list and no error at all. On that answer the guard would
    # refuse every commit in the project, and the refusal would be wrong. Asking what actually sits
    # at the path settles it the same way everywhere.
    #
    # "Nothing there" is NOT one of these cases: an empty registry means nobody has announced yet,
    # and that includes the first stream of a wave — the very one the guard exists for.
    $registryState = Get-PathState -Path $registry
    if ($registryState.Kind -eq 'leaf' -or $registryState.Kind -eq 'unknown') { exit 0 }

    $claims = @(Get-Claims -Dir $registry -Strict)
    $claim = Get-CurrentClaim -Dir $registry -Strict
    if (-not $claim) {
        # The second route to one's own claim — the same one delivery and release already use: an
        # EXACT match on the worktree folder recorded in the claim. Claims filed by an older version
        # from a subfolder of the tree sit under that subfolder's key, and the canonical key (the
        # tree root) does not find them. Without this route the guard would refuse a session that
        # announced itself perfectly well.
        $found = Find-ClaimByWorktree -Claims $claims -Paths @((Get-TreeRoot), $PWD.Path)
        if ($found) { $claim = $found.Record }
    }

    # ‼️ Openness is asked through the single closed-ness flag, not by reading the state field: a
    # claim whose address was taken over by another folder still looks open in its own file, and the
    # session holding it is not reachable by anyone.
    if ($claim -and -not (Test-ClaimClosed -Claims $claims -Claim $claim)) { exit 0 }

    $announce = 'pwsh scripts/wave-board.ps1 -Mode Claim -Wave <wave> -Stream <number> -StreamName "<name>" -Tasks "<tasks>"'
    $valve = 'Not a stream (committing from the main folder, fixing the channel itself, a bulk chore) — the deliberate exception is the environment variable PARALLEL_STREAMS_ALLOW_UNCLAIMED=1 for this session.'

    if ($claim) {
        # The stream is closed: released, or its address was taken over into another folder. Both
        # mean the same thing from the outside — nothing leads this address, findings for it are
        # refused at intake, and the tasks show as unowned again.
        $address = "$($claim.wave)/$($claim.stream)"
        Send-Denial (@(
                "Stopped before the commit: stream $address is no longer run from this folder — it was released, or its address moved to another worktree."
                ''
                'From the outside this session does not exist: nothing leads the address, a finding sent to it is refused, and the tasks it was running show as unowned.'
                ''
                'Announce the stream again — take the address back, or take a free number:'
                "  pwsh scripts/wave-board.ps1 -Mode Claim -Wave $($claim.wave) -Stream $($claim.stream) -TakeOver"
                '  pwsh scripts/wave-board.ps1 -Mode Claim'
                ''
                $valve
            ) -join "`n")
    }

    Send-Denial (@(
            'Stopped before the commit: this stream never announced itself on the coordination channel.'
            ''
            'Nothing outside this folder knows the session exists. A neighbour has nowhere to send a finding, the tasks being worked on here show as unowned, and a neighbour asking who owns them is told nobody does — so they are offered to the person, who has no way of knowing they were planned for this stream.'
            ''
            'Announce the stream, then run the command again:'
            "  $announce"
            ''
            'The wave and the number come from the plan table. No wave and no plan in this project — pass nothing at all, and the channel supplies the wave and hands out a free number:'
            '  pwsh scripts/wave-board.ps1 -Mode Claim'
            ''
            $valve
        ) -join "`n")
} catch {
    exit 0
}
