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

A subagent commits under the stream of the tab that launched it, from whatever folder the host gave
it: the host names the tab's session in the subagent's every call, and the tab's open claim records
that session. A subagent whose tab runs no open stream gets a refusal of its own — it is sent back to
the tab, never told to announce from its folder, which would open a second stream or move the tab's.

A clone of a contributor's own copy (a fork) is not refused at all, once the project's profile names
its home repository in `## Home`: the guard steps aside wherever `origin` points somewhere else. The
board belongs to the home repository, and a contributor sending pull requests from a fork has no
stream on it to announce. Standing aside is said out loud: the delivery hook says so in one line at
session start, so a section that names the wrong address is noticed in the home repository itself.
The one silent case is a profile that cannot be read at all — the guard stands aside, as for
anything it cannot find out, and there is no section to name.

On ANY unexpected condition the guard stays out of the way and exits with zero: a broken registry, an
unreadable claim, git not answering. A kit that installs itself into someone else's project may not
turn its own trouble into a project that cannot commit. Nor does it refuse anything outside the
project it serves — a session that stepped into someone else's repository commits there freely (see
`Test-ServedFolder`).
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
#
# ‼️ `commit` and `push` count only as the git SUBCOMMAND: `git`, then any global options, then the
# word itself. Matched anywhere after `git` on the line, the word also caught `git merge --no-commit`,
# `git log --grep commit` and `git stash push` — merges, which the paragraph above leaves out on
# purpose. The line needs no splitting into commands: the pattern demands the words to follow one
# another, so `a && git commit`, `cd x; git push` and a newline-separated chain are found as they
# were. A `git` that is only a word inside a wrapper (`pwsh -c "git commit"`, `bash -c '...'`,
# `sudo git push`) is found the same way, and `git commit-tree` stays refused: matching has not
# become any easier to slip past than the old one, only the flags and arguments no longer count.
# Two forms are kept on purpose: `-c alias.x=commit` (an alias made on the spot) and the quoted
# `git` of a full path. The `(?i)` makes `-C <path>` and `-c <key=value>` one alternative.
$GitToken = '(?:"[^"]*"|''[^'']*''|[^\s"''|;&])+'
$GitGlobalOption = '(?:-c\s+' + $GitToken +
    '|--(?:git-dir|work-tree|namespace|super-prefix|config-env|attr-source)(?:=' + $GitToken + '|\s+' + $GitToken + ')' +
    '|-{1,2}[A-Za-z][\w-]*(?:=' + $GitToken + ')?)'
$PublishingCommands = '(?i)(?<!\w)git(?:\.exe)?["'']?(?:\s+' + $GitGlobalOption + ')*\s+(?:commit(?:-tree)?|push)(?![\w-])' +
    '|\balias\.[\w-]+=["'']?\s*(?:commit|push)(?![\w-])' +
    '|\bgh\b\s+pr\s+create\b'

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

    # ‼️ Not our project — stay out of the way. The hook command now finds the guard from any
    # folder, and a session that stepped into someone else's repository would otherwise be refused
    # its commits there "for a stream that never announced itself" — on a board that is not ours.
    # Judged by the folder the call came from, the one everything below asks about. An explicit board
    # (tests only) names the project itself: there is nothing to decide.
    if (-not $BoardPath -and -not (Test-ServedFolder -Path (Get-Location).Path)) { exit 0 }

    # A clone of a contributor's own copy of the project (a fork), not of its home repository. The
    # wave board lives in the home repository's folders: such a clone has none and should have none,
    # and every commit there would be refused for a stream that has nowhere to announce — a refusal
    # its session can only answer with the valve, by hand, every time. Asked only when the profile
    # names its home (`## Home`), and only here, after the command filter and the valve: on calls
    # that are none of the guard's business it still costs nothing. No origin at all, or a folder on
    # disk, counts as home; git not answering, or an origin that does not read, counts as "not home"
    # — the rule for anything unexpected, below, is the same. The session hears about standing aside
    # at its start, from the delivery hook: a wrong section at home must not stay silent.
    if (Test-ForeignClone) { exit 0 }

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

    # ‼️ A subagent, in a folder with no open claim of its own — typically the worktree the host gave
    # it. Its work belongs to the stream of the tab that launched it, and the tie is the session, not
    # the folder: the host names the tab's session in every call a subagent makes (`agent_id` marks
    # the call as a subagent's), and the claim records the session it was announced from. So the
    # commit goes through when an OPEN claim carries that session — exactly that tab's stream, never
    # a live neighbour's, and without a byte of the registry changed.
    #
    # The subagent must not be sent to announce from its folder: an announcement there opens a second
    # stream that nobody runs once the subagent is done, and taking the address over from there moves
    # the stream away from the tab. Hence a refusal of its own, which sends the work back to the tab.
    # A call that is not a subagent's — the tab itself in a folder it never announced — is refused
    # exactly as before.
    if ([string]$call.agent_id) {
        $session = [string]$call.session_id
        if (Test-SessionId $session) {
            $led = @($claims | Where-Object {
                    -not $_.Closed -and [string]$_.Record.session_id -ceq $session
                })
            if ($led.Count -gt 0) { exit 0 }
        }
        Send-Denial (@(
                'Stopped before the commit: this call comes from a subagent, and the tab that launched it runs no open stream on the coordination channel.'
                ''
                'A subagent commits under the stream of the tab that launched it and announces nothing of its own. That tab has no open claim under its current session: it never announced, it announced without its session (by hand from a terminal, or from a client that does not name it), it released its stream, or its session changed since (a context clear in a tab with no name of its own).'
                ''
                'Do not announce from this folder: an announcement here opens a second stream that nobody runs once you are done, and taking the address over from here moves the stream away from the tab that runs it.'
                ''
                'Stop and hand the work back to the tab that launched you, together with this refusal. That tab announces from its own folder — or announces again under the same address, which records its current session — and then the commit goes through here:'
                "  $announce"
                ''
                $valve
            ) -join "`n")
    }

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
