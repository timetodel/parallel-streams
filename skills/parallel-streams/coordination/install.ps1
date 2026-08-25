#Requires -Version 7
<#
Installs the coordination channel between sessions into a project: guard hooks, profile, bridge script.

Why a separate installer. The channel itself lives in the skill's folder, but it only starts working
because of three things OUTSIDE that folder: entries in the project's settings, a section in the
profile, and a short bridge script in `scripts/`. Assembling this by hand in every new project means
reconciling with someone else's settings file and four spots where it is easy to slip — and a mistake
in any one of them looks equally harmless: "the neighbour has nothing to say". Hence one command and
a report that names EVERY action.

What the install does:
  1. settings   `<project>/.claude/settings.json` — two guards: delivering findings and nudging on
     wave-plan edits;
  2. profile    `<project>/.parallel-streams.md`  — the `## Coordination` and `## Plans` sections;
  3. bridge     `<project>/scripts/wave-board.ps1` — so the launch command is equally short in every
     project: `pwsh scripts/wave-board.ps1 ...`.

What the installer NEVER does: touch someone else's entries in the settings, change a single byte of
an already-written profile, or overwrite a foreign file standing where the bridge script belongs.
Everything it chose not to do is named in the report — there are no silent skips here, or the channel
would end up half-connected while looking like silence on the board.

A caveat about the settings file: someone else's entries and their order survive, but the serializer
reassembles the file, and it applies its own layout (indentation, line breaks). A file written in a
different style will show up whole in the diff after the first install. The installer SAYS so — it
checks against the prior text beforehand and prints a warning exactly when it rewrites the style.

Running it again is safe: it does not pile up duplicates, and it picks up a skill folder that has
moved by editing the entries already in place. The reasoning is in the comment on Set-Guards.

Modes: Install (default), Uninstall (remove), Check (show current state).
#>

[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Check')]
    [string]$Mode = 'Install',

    # Project root to install into. Defaults to the repository root the script was run from.
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ‼️ Clear git environment variables BEFORE the first call to git: with an externally set GIT_DIR,
# the project root would resolve to someone else's repository, and the install would silently land
# there. The trap is explained in the file itself.
. (Join-Path $PSScriptRoot 'lib/git-env-clean.ps1')

# The skill's folder, the base every path in the settings and the bridge script is computed from.
$CoordDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path

# The mark of "this is our entry": the command points at OUR guard file, sitting in the channel's
# hooks folder. Neither half of the mark is enough on its own. Going by the folder alone would count
# a foreign hook as ours if it happened to sit in a folder with the same name — and uninstalling would
# carry it away along with our own. Going by the file name alone would fail to recognize OUR prior
# entry after the skill moved, and a second (dead) copy would end up sitting next to the old one.
$OurHookFiles = @('wave-board-deliver.ps1', 'pretooluse-wave-board-nudge.ps1')
$OurMark = 'coordination/hooks/(' +
    (($OurHookFiles | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')'

# The marker string that identifies our bridge script: a foreign file with the same name is left alone.
$BridgeMark = 'moved to the skill folder'

$ProfileName = '.parallel-streams.md'

function Report {
    param([string]$Text)
    # ‼️ Write to the console, not to the output stream: functions that print a report also have a
    # return value and get called from an assignment. Through the output stream, the report text
    # would silently end up captured in a variable instead.
    Write-Host $Text
}

function Deny {
    param([string]$Text)
    # A human reads this refusal: `throw` would wrap the text in an exception frame with a file path
    # and line number.
    [Console]::Error.WriteLine($Text)
    exit 1
}

function Get-SlashPath {
    param([string]$Path)
    return ($Path -replace '\\', '/')
}

function Test-SameText {
    param([string]$Left, [string]$Right)
    # File content comparison is character-by-character: plain -eq in PowerShell is case-insensitive,
    # and a moved path differing only in letter case would pass for "nothing changed".
    return [string]::Equals($Left, $Right, [System.StringComparison]::Ordinal)
}

function Resolve-Root {
    param([string]$Given)
    if ($Given) {
        if (-not (Test-Path -LiteralPath $Given -PathType Container)) {
            Deny "Folder not found: $Given"
        }
        return (Resolve-Path -LiteralPath $Given).Path
    }
    $top = ''
    try { $top = (& git rev-parse --show-toplevel 2>$null) } catch { $top = '' }
    if ($top) { return (Resolve-Path -LiteralPath ([string]$top).Trim()).Path }
    Report "Not a git repository — installing into the current folder: $($PWD.Path)"
    return $PWD.Path
}

function Get-InsidePath {
    param([string]$Root, [string]$Target)
    # Path from the project root, if the target sits INSIDE it, empty otherwise. The relative form
    # exists so the settings survive the project folder moving and work the same way in every
    # worktree.
    $rel = Get-SlashPath ([System.IO.Path]::GetRelativePath($Root, $Target))
    if ([System.IO.Path]::IsPathRooted($rel)) { return '' }
    if ($rel -eq '.' -or $rel -eq '..' -or $rel.StartsWith('../')) { return '' }
    return $rel
}

# ─── Profile markup ──────────────────────────────────────────────────────────────────────────────

function Get-MarkdownSection {
    param([string]$Text, [string]$Name)
    # The whole `## Name` section, without trailing blank lines. Empty means the section is absent.
    #
    # A section boundary is a heading of level two OR level one: a `# Other part` sits higher up in
    # the markup, and stopping only at `##` would drag the rest of the document into the section —
    # along with someone else's backtick-quoted paths, which is exactly where the plans folder is
    # read from later. Subheadings (`### ...`) do not count as a boundary: they belong to the section.
    #
    # The name is compared WITHOUT case sensitivity (that's how PowerShell's string `-eq` already
    # behaves): a human writes the profile, and their `## plans` is the same plans section as
    # `## Plans`. The nudge guard reads it the same way — otherwise a profile with different casing
    # would give a guard that is connected but mute.
    if (-not $Text) { return '' }
    $found = $false
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^(#{1,2})\s+(.+?)\s*$') {
            if ($found) { break }
            if ($Matches[1].Length -eq 2 -and $Matches[2] -eq $Name) { $found = $true; $out.Add($line) }
            continue
        }
        if ($found) { $out.Add($line) }
    }
    if (-not $found) { return '' }
    while ($out.Count -gt 0 -and -not $out[$out.Count - 1].Trim()) { $out.RemoveAt($out.Count - 1) }
    return ($out -join "`n")
}

function Get-PlansFolder {
    param([string]$ProfileText)
    # The profile names the plans folder in the `## Plans` section, backtick-quoted like a path. Take
    # the first match that fits: a human writes the section in prose, and backticks there catch both
    # file names and commands. Empty means the folder is not declared, and the nudge guard does not
    # get connected at all.
    $section = Get-MarkdownSection -Text $ProfileText -Name 'Plans'
    if (-not $section) { return '' }
    foreach ($found in [regex]::Matches($section, '`([^`]+)`')) {
        $value = Get-SlashPath $found.Groups[1].Value.Trim()
        $value = $value -replace '^\./', ''
        if ($value -match '\s') { continue }
        # A placeholder for the folder does not count: in project templates an unfilled value is
        # marked with angle brackets (`<stream number>`), and taking that for a folder name would
        # connect the guard to a folder that does not exist — instead of an honest "folder not named".
        if ($value -match '[<>]') { continue }
        # A file name is never the plans folder: `.parallel-streams.md`, `README.md`, and the like.
        if ($value -notmatch '/$' -and $value -match '\.[A-Za-z0-9]{1,5}$') { continue }
        if (-not $value.EndsWith('/')) { $value += '/' }
        return $value
    }
    return ''
}

# ─── Reading and writing files ───────────────────────────────────────────────────────────────────

function Read-TextFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
}

function Save-TextFile {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function ConvertTo-FileJson {
    param($Data, [string]$Original)
    # ‼️ Reassembling the settings back into text. Three things, each of which silently corrupts
    # someone else's file:
    #   • parsing runs WITHOUT -AsHashtable (see Read-Settings): a dictionary loses key order, and
    #     the file gets reshuffled whole after saving — someone else's entries look rewritten;
    #   • Cyrillic must not end up escaped as \uXXXX: the file carries Russian labels for the guards,
    #     and a human reads them. Unescape everything except what JSON requires to stay escaped;
    #   • line endings and the trailing newline are taken from the prior file: otherwise a harmless
    #     run rewrites EVERY line and drowns in diff noise when merged.
    #
    # ‼️ Unescape by RUNS of backslashes, not by a single `\uXXXX` match. Someone else's value
    # `path C:\u0041pps\bin` is stored in the file as `C:\\u0041pps\\bin`, and a naive search would
    # take the SECOND backslash for the start of an escape sequence: `C:\Apps` would end up in the
    # file — an illegal escape, after which the settings file fails to parse AT ALL, and with it ALL
    # of the project's hooks silently stop working, not just ours. An odd count of backslashes before
    # `u` means the last one opens an escape; an even count means they are all paired, and what we
    # have is plain text that must not be touched.
    $text = $Data | ConvertTo-Json -Depth 20
    $text = [regex]::Replace($text, '(\\+)u([0-9a-fA-F]{4})', {
            param($found)
            $slashes = $found.Groups[1].Value
            if ($slashes.Length % 2 -eq 0) { return $found.Value }
            $code = [Convert]::ToInt32($found.Groups[2].Value, 16)
            if ($code -lt 0x20 -or $code -eq 0x22 -or $code -eq 0x5C) { return $found.Value }
            # Half of a surrogate pair (an emoji) is not a character on its own — leave it as is.
            if ($code -ge 0xD800 -and $code -le 0xDFFF) { return $found.Value }
            return $slashes.Substring(0, $slashes.Length - 1) + [string][char]$code
        })
    $text = $text -replace "`r`n", "`n"
    $useCrlf = $Original -and ($Original -match "`r`n")
    if ($useCrlf) { $text = $text -replace "`n", "`r`n" }
    if (-not $Original -or $Original.EndsWith("`n")) {
        $text += $(if ($useCrlf) { "`r`n" } else { "`n" })
    }
    return $text
}

function Read-Settings {
    param([string]$Path)
    $raw = Read-TextFile -Path $Path
    if (-not $raw -or -not $raw.Trim()) {
        return [pscustomobject]@{ Data = $null; Raw = $raw }
    }
    $data = $null
    try {
        $data = $raw | ConvertFrom-Json
    } catch {
        Deny "Project settings do not parse as JSON: $Path. Fix the file and try again."
    }
    return [pscustomobject]@{ Data = $data; Raw = $raw }
}

function New-Settings {
    return [pscustomobject][ordered]@{
        '$schema' = 'https://json.schemastore.org/claude-code-settings.json'
        hooks     = [pscustomobject]@{}
    }
}

# ─── Working with parsed JSON properties ─────────────────────────────────────────────────────────

function Get-Prop {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if (-not $prop) { return $null }
    return $prop.Value
}

function Set-Prop {
    param($Object, [string]$Name, $Value)
    $prop = $Object.PSObject.Properties[$Name]
    if ($prop) {
        $prop.Value = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

# ─── Recognizing guard entries ───────────────────────────────────────────────────────────────────

function Test-OurHook {
    param($Hook)
    $command = Get-SlashPath ([string](Get-Prop $Hook 'command'))
    return $command -match $OurMark
}

function Get-HookWhat {
    param($Hook)
    # How the entry is named in the report — the same wording both when installing and when checking.
    switch (Get-HookKind $Hook) {
        'deliver' { return 'delivering findings' }
        'nudge' { return 'nudge on wave-plan edits' }
        default { return 'channel entry' }
    }
}

function Get-HookKind {
    param($Hook)
    $command = Get-SlashPath ([string](Get-Prop $Hook 'command'))
    if ($command -match 'wave-board-deliver\.ps1') { return 'deliver' }
    if ($command -match 'pretooluse-wave-board-nudge\.ps1') { return 'nudge' }
    return 'other'
}

function Get-HookMark {
    param($Hook)
    # A distinguisher within the kind: for delivery it is the stage, for the nudge it is the tool from
    # the condition. Running the install again uses it to recognize ITS OWN prior entry and fix it up,
    # instead of placing a second identical one next to it.
    $kind = Get-HookKind $Hook
    if ($kind -eq 'deliver') {
        $stage = [regex]::Match([string](Get-Prop $Hook 'command'), '-Stage\s+(\w+)')
        if ($stage.Success) { return "deliver:$($stage.Groups[1].Value)" }
        return 'deliver:'
    }
    if ($kind -eq 'nudge') {
        $tool = [regex]::Match([string](Get-Prop $Hook 'if'), '^\s*(\w+)\s*\(')
        if ($tool.Success) { return "nudge:$($tool.Groups[1].Value)" }
        return 'nudge:'
    }
    return 'other'
}

function New-HookRecord {
    param($Wanted)
    # Field order matches exactly what is already written by hand in the settings: so the entry reads
    # the same to the eye wherever it sits.
    $record = [ordered]@{ type = 'command'; shell = 'powershell'; command = $Wanted.Command }
    if ($Wanted.If) { $record['if'] = $Wanted.If }
    $record['timeout'] = $Wanted.Timeout
    if ($Wanted.Status) { $record['statusMessage'] = $Wanted.Status }
    return [pscustomobject]$record
}

function Update-HookRecord {
    param($Hook, $Wanted)
    # Fix IN PLACE: existing fields keep their order, only what is missing gets added.
    Set-Prop $Hook 'type' 'command'
    Set-Prop $Hook 'shell' 'powershell'
    Set-Prop $Hook 'command' $Wanted.Command
    if ($Wanted.If) { Set-Prop $Hook 'if' $Wanted.If }
    Set-Prop $Hook 'timeout' $Wanted.Timeout
    if ($Wanted.Status) { Set-Prop $Hook 'statusMessage' $Wanted.Status }
}

function Get-EventEntries {
    param($Settings, [string]$EventName)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return @() }
    $entries = Get-Prop $node $EventName
    if (-not $entries) { return @() }
    return @($entries)
}

function Set-EventEntries {
    param($Settings, [string]$EventName, $Entries)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) {
        $node = [pscustomobject]@{}
        Set-Prop $Settings 'hooks' $node
    }
    $kept = @($Entries)
    if ($kept.Count -eq 0) {
        # Remove an emptied-out event entirely: an empty array reads, to the eye, as a connected guard.
        if ($node.PSObject.Properties[$EventName]) { $node.PSObject.Properties.Remove($EventName) }
        return
    }
    Set-Prop $node $EventName ([object[]]$kept)
}

function Get-Buckets {
    param($Settings, [string]$EventName)
    # The event's entries together with their hook lists — in a mutable form.
    $buckets = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in (Get-EventEntries -Settings $Settings -EventName $EventName)) {
        $list = [System.Collections.Generic.List[object]]::new()
        foreach ($hook in @(Get-Prop $entry 'hooks')) { if ($hook) { $list.Add($hook) } }
        $buckets.Add([pscustomobject]@{ Entry = $entry; Hooks = $list })
    }
    # The comma is required: without it PowerShell unrolls the list into separate values, and the
    # caller ends up with a plain array instead of a mutable list.
    return , $buckets
}

function Save-Buckets {
    param($Settings, [string]$EventName, $Buckets)
    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($bucket in $Buckets) {
        if ($bucket.Hooks.Count -eq 0) { continue }
        Set-Prop $bucket.Entry 'hooks' ([object[]]$bucket.Hooks)
        $entries.Add($bucket.Entry)
    }
    Set-EventEntries -Settings $Settings -EventName $EventName -Entries $entries
}

function Find-Bucket {
    param($Buckets, [string]$Matcher)
    foreach ($bucket in $Buckets) {
        $own = [string](Get-Prop $bucket.Entry 'matcher')
        if (-not $Matcher) {
            if (-not $own) { return $bucket }
            continue
        }
        if (-not $own) { continue }
        # `Write|Edit` and `Edit|Write` are the same selection: compare by membership, not by string.
        $mine = @($Matcher -split '\|' | ForEach-Object { $_.Trim() } | Sort-Object)
        $theirs = @($own -split '\|' | ForEach-Object { $_.Trim() } | Sort-Object)
        if (($mine -join '|') -eq ($theirs -join '|')) { return $bucket }
    }
    return $null
}

function New-Bucket {
    param($Buckets, [string]$Matcher)
    $entry = if ($Matcher) {
        [pscustomobject][ordered]@{ matcher = $Matcher; hooks = @() }
    } else {
        [pscustomobject][ordered]@{ hooks = @() }
    }
    $bucket = [pscustomobject]@{
        Entry = $entry
        Hooks = [System.Collections.Generic.List[object]]::new()
    }
    $Buckets.Add($bucket)
    return $bucket
}

function Get-OurEvents {
    param($Settings)
    # Events that ALREADY carry our entries. Walking only the wanted events is not enough: if the
    # plans folder disappeared or the profile section got renamed, the file-edit event would not make
    # it into the wanted list at all, and the nudge guard's prior entry would stay in the settings
    # pointing at a dead spot. The report would then say "not connected" while Check said "connected" —
    # the report would be lying.
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return @() }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        foreach ($bucket in (Get-Buckets -Settings $Settings -EventName $eventName)) {
            foreach ($hook in $bucket.Hooks) {
                if (Test-OurHook $hook) { $names.Add($eventName); break }
            }
        }
    }
    return @($names | Select-Object -Unique)
}

function Set-Guards {
    param($Settings, $Wanted)
    # Connecting the guards. Idempotency rests on three rules:
    #   • an entry is recognized as "ours" by its command pointing at our guard file — so a moved
    #     skill fixes up ITS OWN prior entry instead of creating a second one;
    #   • within a kind, an entry is recognized by its distinguisher (the stage for delivery, the
    #     tool for the nudge), so a changed condition also gets fixed up in place;
    #   • everything that stays ours and unneeded gets removed — running the install again never
    #     piles up duplicates.
    # Someone else's entries are never read past the command field and never move under any outcome.
    $events = @(@($Wanted | ForEach-Object { $_.Event }) + (Get-OurEvents -Settings $Settings)) |
        Select-Object -Unique
    foreach ($eventName in $events) {
        $here = @($Wanted | Where-Object { $_.Event -eq $eventName })
        $buckets = Get-Buckets -Settings $Settings -EventName $eventName

        $ours = [System.Collections.Generic.List[object]]::new()
        foreach ($bucket in $buckets) {
            foreach ($hook in $bucket.Hooks) {
                if (-not (Test-OurHook $hook)) { continue }
                $ours.Add([pscustomobject]@{
                        Bucket = $bucket
                        Hook   = $hook
                        Kind   = (Get-HookKind $hook)
                        Mark   = (Get-HookMark $hook)
                        Used   = $false
                    })
            }
        }

        foreach ($want in $here) {
            $hit = $ours | Where-Object { -not $_.Used -and $_.Mark -eq $want.Mark } | Select-Object -First 1
            if (-not $hit) {
                # The distinguisher did not match but the kind is the same — this is our entry with a
                # stale condition or path. It needs fixing up, or a second copy would land next to it
                # and the guard would speak twice.
                $hit = $ours | Where-Object { -not $_.Used -and $_.Kind -eq $want.Kind } | Select-Object -First 1
            }
            if ($hit) {
                $hit.Used = $true
                $before = ($hit.Hook | ConvertTo-Json -Depth 5 -Compress)
                Update-HookRecord -Hook $hit.Hook -Wanted $want
                $after = ($hit.Hook | ConvertTo-Json -Depth 5 -Compress)
                if (Test-SameText -Left $before -Right $after) {
                    Report "  already connected: $($want.Title)"
                } else {
                    Report "  entry fixed up (path or condition was stale): $($want.Title)"
                }
                continue
            }
            $bucket = Find-Bucket -Buckets $buckets -Matcher $want.Matcher
            if (-not $bucket) { $bucket = New-Bucket -Buckets $buckets -Matcher $want.Matcher }
            $bucket.Hooks.Add((New-HookRecord -Wanted $want))
            Report "  connected: $($want.Title)"
        }

        # Everything ours that matched none of the wanted entries is surplus, regardless of kind: a
        # nudge-guard entry after the plans folder disappeared is not "a different kind", it is simply
        # no longer needed. Left in place, it would point at a dead spot for as long as the project
        # lives.
        foreach ($extra in $ours) {
            if ($extra.Used) { continue }
            $extra.Bucket.Hooks.Remove($extra.Hook) | Out-Null
            Report "  removed our prior entry, no longer needed: $(Get-HookWhat $extra.Hook) (event $eventName)"
        }

        Save-Buckets -Settings $Settings -EventName $eventName -Buckets $buckets
    }
}

function Remove-Guards {
    param($Settings)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return }
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        $buckets = Get-Buckets -Settings $Settings -EventName $eventName
        $removed = 0
        foreach ($bucket in $buckets) {
            foreach ($hook in @($bucket.Hooks)) {
                if (-not (Test-OurHook $hook)) { continue }
                $bucket.Hooks.Remove($hook) | Out-Null
                $removed++
            }
        }
        if ($removed -eq 0) { continue }
        Save-Buckets -Settings $Settings -EventName $eventName -Buckets $buckets
        Report "  removed entries on event ${eventName}: $removed"
    }
}

# ─── Bridge script ────────────────────────────────────────────────────────────────────────────────

function Get-BridgeText {
    param([string]$Root)
    $target = Join-Path $CoordDir 'wave-board.ps1'
    $inside = Get-InsidePath -Root $Root -Target $CoordDir
    if ($inside) {
        $where = "$inside/"
        $rel = Get-SlashPath ([System.IO.Path]::GetRelativePath((Join-Path $Root 'scripts'), $target))
        $call = "`$target = Join-Path `$PSScriptRoot '$rel'"
    } else {
        # The skill sits outside the project — no relative path to it can be built, write a full one.
        $where = (Get-SlashPath $CoordDir) + '/'
        $call = "`$target = '$(Get-SlashPath $target)'"
    }
    $lines = @(
        '#Requires -Version 7'
        '<#'
        "Bridge script: the tool $BridgeMark (``$where``),"
        'this file is just the call — so the launch command stays equally short in every project with the'
        'skill: `pwsh scripts/wave-board.ps1 ...`. Logic and edits live only in the skill script, not here.'
        '#>'
        ''
        $call
        '& $target @args'
        'exit $LASTEXITCODE'
    )
    return (($lines -join "`n") + "`n")
}

function Get-BridgeTarget {
    param([string]$Text)
    # The call line is the only thing that actually matters in the bridge script. Comparing the whole
    # text works for deciding "rewrite it", but not for the report: a tweaked header in the boilerplate
    # is no reason to scare a human into thinking the bridge script points somewhere wrong.
    if (-not $Text) { return '' }
    $found = [regex]::Match($Text, '(?m)^\$target\s*=.*$')
    if ($found.Success) { return $found.Value.Trim() }
    return ''
}

# ─── Project state ───────────────────────────────────────────────────────────────────────────────

function Get-State {
    param([string]$Root)
    $profilePath = Join-Path $Root $ProfileName
    $bridgePath = Join-Path $Root 'scripts/wave-board.ps1'
    $profileText = Read-TextFile -Path $profilePath
    $bridgeText = Read-TextFile -Path $bridgePath
    $plans = Get-PlansFolder -ProfileText $profileText
    return [pscustomobject]@{
        SettingsPath = (Join-Path $Root '.claude/settings.json')
        ProfilePath  = $profilePath
        BridgePath   = $bridgePath
        ProfileText  = $profileText
        BridgeText   = $bridgeText
        BridgeIsOurs = [bool]($bridgeText -and ($bridgeText -match [regex]::Escape($BridgeMark)))
        Plans        = $plans
        PlansExists  = [bool]($plans -and (Test-Path -LiteralPath (Join-Path $Root $plans) -PathType Container))
    }
}

function Get-Wanted {
    param([string]$Root, [string]$Plans)
    # How the guard's path gets written. Inside the project — relative to the working folder (`$PWD`),
    # exactly the way the project's other hooks are already written: that way the settings survive the
    # folder moving and work the same way in every worktree. Outside the project — a full path, there
    # is no other way.
    $inside = Get-InsidePath -Root $Root -Target $CoordDir
    $base = if ($inside) { '$PWD/' + $inside } else { Get-SlashPath $CoordDir }
    $deliver = "& `"$base/hooks/wave-board-deliver.ps1`""
    $nudge = "& `"$base/hooks/pretooluse-wave-board-nudge.ps1`""

    $wanted = @(
        [pscustomobject]@{
            Event   = 'SessionStart'
            Matcher = ''
            Kind    = 'deliver'
            Mark    = 'deliver:Start'
            Command = "$deliver -Stage Start"
            Timeout = 20
            Status  = 'Checking the wave board'
            If      = ''
            Title   = 'delivering findings at session start'
        }
        [pscustomobject]@{
            Event   = 'UserPromptSubmit'
            Matcher = ''
            Kind    = 'deliver'
            Mark    = 'deliver:Prompt'
            Command = "$deliver -Stage Prompt"
            Timeout = 15
            Status  = ''
            If      = ''
            Title   = 'delivering findings before every human prompt'
        }
    )
    # The nudge guard depends on the plans folder: without knowing it, it cannot tell a wave plan apart
    # from any other file and would stay silent forever. No folder in the profile — do not add these
    # entries at all.
    if (-not $Plans) { return $wanted }
    foreach ($tool in @('Edit', 'Write')) {
        $wanted += [pscustomobject]@{
            Event   = 'PreToolUse'
            Matcher = 'Write|Edit'
            Kind    = 'nudge'
            Mark    = "nudge:$tool"
            Command = $nudge
            Timeout = 20
            Status  = ''
            If      = "$tool($Plans**)"
            Title   = "nudge on wave-plan edits ($tool, folder $Plans)"
        }
    }
    return $wanted
}

# ─── Modes ────────────────────────────────────────────────────────────────────────────────────────

function Update-Profile {
    param([string]$Root, $State, $Told)
    # The profile is parsed FIRST (it is where the plans folder comes from, and without it there is no
    # deciding whether to connect the nudge guard), but printed second — the report follows the order
    # in which one actually thinks about the channel. Hence the report lines are collected into $Told
    # rather than printed on the spot.
    $profileText = $State.ProfileText
    $template = Read-TextFile -Path (Join-Path $CoordDir 'templates/profile.md')
    $extra = Read-TextFile -Path (Join-Path $CoordDir 'templates/profile-coordination.md')
    if (-not $template -or -not $extra) {
        Deny "Profile templates not found — the bundle is incomplete: $CoordDir/templates"
    }
    $blocks = @()
    foreach ($name in @('Coordination', 'Plans')) {
        $block = Get-MarkdownSection -Text $extra -Name $name
        if (-not $block) {
            Deny "The sections template has no ``## $name`` section — the bundle is incomplete: $CoordDir/templates"
        }
        $blocks += [pscustomobject]@{ Name = $name; Text = $block }
    }

    if (-not $profileText) {
        $profileText = $template.TrimEnd() + "`n`n" + (($blocks | ForEach-Object { $_.Text }) -join "`n`n") + "`n"
        Save-TextFile -Path $State.ProfilePath -Text $profileText
        $Told.Add("  created profile $ProfileName, with the ``## Coordination`` and ``## Plans`` sections")
        $Told.Add('  ‼️ edit it for your project: check commands, review, and the wave-plan folder')
    } else {
        $missing = @($blocks | Where-Object { -not (Get-MarkdownSection -Text $profileText -Name $_.Name) })
        if ($missing.Count -eq 0) {
            $Told.Add("  profile $ProfileName already describes the channel — left untouched")
        } else {
            # Append only at the end, and only what is missing: not a single byte of already-written
            # text may change — it holds a human's own words about their project.
            $eol = if ($profileText -match "`r`n") { "`r`n" } else { "`n" }
            $gap = ''
            if (-not $profileText.EndsWith("`n")) { $gap = $eol + $eol }
            elseif (-not $profileText.EndsWith($eol + $eol)) { $gap = $eol }
            $body = (($missing | ForEach-Object { $_.Text }) -join "`n`n") + "`n"
            if ($eol -ne "`n") { $body = $body -replace "`n", $eol }
            $profileText = $profileText + $gap + $body
            Save-TextFile -Path $State.ProfilePath -Text $profileText
            $names = ($missing | ForEach-Object { "``## $($_.Name)``" }) -join ', '
            $Told.Add("  added to the profile: $names")
        }
    }

    $plans = Get-PlansFolder -ProfileText $profileText
    if (-not $plans) {
        $Told.Add('  the wave-plan folder is not named in the `## Plans` section — the nudge guard is not connected')
        $Told.Add('  (write it there, backtick-quoted — a line like "Wave plans: `docs/plans/`" — and run the install again)')
    } elseif (-not (Test-Path -LiteralPath (Join-Path $Root $plans) -PathType Container)) {
        $Told.Add("  the plans folder '$plans', named in the profile, does not exist in the project — the nudge guard is not connected")
        $Told.Add('  (create the folder, or fix the `## Plans` section, and run the install again)')
        $plans = ''
    } else {
        $Told.Add("  wave-plan folder: $plans")
    }
    return $plans
}

function Invoke-Install {
    param([string]$Root)
    $state = Get-State -Root $Root
    $told = [System.Collections.Generic.List[string]]::new()
    $plans = Update-Profile -Root $Root -State $state -Told $told

    Report ''
    Report '1. Guards in the project settings'
    $settings = Read-Settings -Path $state.SettingsPath
    $data = $settings.Data
    $restyle = $false
    if (-not $data) {
        $data = New-Settings
        Report "  no settings file yet — creating $($state.SettingsPath)"
    } else {
        # The serializer reassembles the file back into text and applies its own layout: indentation,
        # line breaks, bracket placement. The content and the order of entries survive, but the file
        # will show up whole in the diff — that needs saying up front, not promising "nothing touched".
        $restyle = -not (Test-SameText `
                -Left (ConvertTo-FileJson -Data $data -Original $settings.Raw) -Right $settings.Raw)
    }
    Set-Guards -Settings $data -Wanted (Get-Wanted -Root $Root -Plans $plans)
    $text = ConvertTo-FileJson -Data $data -Original $settings.Raw
    if (Test-SameText -Left $text -Right $settings.Raw) {
        Report '  settings unchanged'
    } else {
        Save-TextFile -Path $state.SettingsPath -Text $text
        if ($restyle) {
            Report '  settings saved; the other entries and their order survive, but ‼️ the file was rewritten'
            Report '  in a single style — indentation and layout now match how the installer writes it'
        } else {
            Report '  settings saved — the other entries and their order left untouched'
        }
    }

    Report ''
    Report '2. Project profile'
    foreach ($line in $told) { Report $line }

    Report ''
    Report '3. Bridge script scripts/wave-board.ps1'
    $bridge = Get-BridgeText -Root $Root
    if (-not $state.BridgeText) {
        Save-TextFile -Path $state.BridgePath -Text $bridge
        Report "  bridge script placed: $($state.BridgePath)"
    } elseif (-not $state.BridgeIsOurs) {
        Report '  ‼️ a FOREIGN file sits where the bridge script belongs — left untouched'
        Report "  ($($state.BridgePath)); call the tool by its full path, or remove the foreign file yourself"
    } elseif (Test-SameText -Left $state.BridgeText -Right $bridge) {
        Report '  bridge script already in place'
    } else {
        Save-TextFile -Path $state.BridgePath -Text $bridge
        $target = Get-BridgeTarget $bridge
        Report "  bridge script updated — now it calls: $target"
    }

    Report ''
    Report 'Done. What is connected — `-Mode Check`; to remove — `-Mode Uninstall`.'
}

function Invoke-Uninstall {
    param([string]$Root)
    $state = Get-State -Root $Root

    Report ''
    Report '1. Guards in the project settings'
    $settings = Read-Settings -Path $state.SettingsPath
    if (-not $settings.Data) {
        Report '  no project settings — nothing to remove'
    } else {
        # Same caveat as at install: saving rewrites the file with the serializer's layout.
        $restyle = -not (Test-SameText `
                -Left (ConvertTo-FileJson -Data $settings.Data -Original $settings.Raw) -Right $settings.Raw)
        Remove-Guards -Settings $settings.Data
        $text = ConvertTo-FileJson -Data $settings.Data -Original $settings.Raw
        if (Test-SameText -Left $text -Right $settings.Raw) {
            Report '  there were no entries of ours in the settings'
        } elseif ($restyle) {
            Save-TextFile -Path $state.SettingsPath -Text $text
            Report '  settings saved; the other entries survive, but ‼️ the file was rewritten in a single style'
        } else {
            Save-TextFile -Path $state.SettingsPath -Text $text
            Report '  settings saved — the other entries left as they were'
        }
    }

    Report ''
    Report '2. Project profile'
    Report "  $ProfileName left as is — it holds a human's own text about their project"

    Report ''
    Report '3. Bridge script scripts/wave-board.ps1'
    if (-not $state.BridgeText) {
        Report '  there was no bridge script'
    } elseif (-not $state.BridgeIsOurs) {
        Report '  a foreign file sits where the bridge script belongs — left untouched'
    } else {
        Remove-Item -LiteralPath $state.BridgePath -Force
        Report "  removed: $($state.BridgePath)"
    }

    Report ''
    Report 'Channel removed. The skill folder is still there — install it again with this same command.'
}

function Invoke-Check {
    param([string]$Root)
    $state = Get-State -Root $Root

    Report ''
    Report '1. Guards in the project settings'
    $settings = Read-Settings -Path $state.SettingsPath
    if (-not $settings.Data) {
        Report "  no settings at all: $($state.SettingsPath)"
    } else {
        $seen = 0
        foreach ($eventName in @('SessionStart', 'UserPromptSubmit', 'PreToolUse')) {
            foreach ($bucket in (Get-Buckets -Settings $settings.Data -EventName $eventName)) {
                foreach ($hook in $bucket.Hooks) {
                    if (-not (Test-OurHook $hook)) { continue }
                    $seen++
                    $what = Get-HookWhat $hook
                    $cond = [string](Get-Prop $hook 'if')
                    $tail = if ($cond) { ", condition $cond" } else { '' }
                    Report "  ${eventName}: $what$tail"
                    Report "    points at: $(Get-Prop $hook 'command')"
                }
            }
        }
        if ($seen -eq 0) { Report '  not one entry of ours — the channel is not connected' }
    }

    Report ''
    Report '2. Project profile'
    if (-not $state.ProfileText) {
        Report "  no profile: $($state.ProfilePath)"
    } else {
        foreach ($name in @('Coordination', 'Plans')) {
            $has = [bool](Get-MarkdownSection -Text $state.ProfileText -Name $name)
            Report "  section ``## $name``: $(if ($has) { 'present' } else { 'absent' })"
        }
        if ($state.Plans) {
            $where = if ($state.PlansExists) { 'exists in the project' } else { '‼️ not created in the project' }
            Report "  wave-plan folder: $($state.Plans) — $where"
        } else {
            Report '  wave-plan folder not declared — the nudge guard has nothing to work with'
        }
    }

    Report ''
    Report '3. Bridge script scripts/wave-board.ps1'
    if (-not $state.BridgeText) {
        Report "  no bridge script: $($state.BridgePath)"
    } elseif (-not $state.BridgeIsOurs) {
        Report '  a foreign file sits here — call the tool by its full path'
    } elseif (Test-SameText -Left (Get-BridgeTarget $state.BridgeText) -Right (Get-BridgeTarget (Get-BridgeText -Root $Root))) {
        Report '  in place and pointing at the skill folder'
    } else {
        Report '  ‼️ in place, but pointing somewhere other than where the skill currently sits — reinstall needed'
        Report "    currently: $(Get-BridgeTarget $state.BridgeText)"
    }
    Report ''
}

# ─── Flow ────────────────────────────────────────────────────────────────────────────────────────

$root = Resolve-Root -Given $ProjectRoot
$title = switch ($Mode) {
    'Install' { 'install' }
    'Uninstall' { 'uninstall' }
    'Check' { 'status' }
}
Report "Coordination channel between sessions — $title"
Report "  project:      $root"
Report "  skill folder: $CoordDir"

switch ($Mode) {
    'Install' { Invoke-Install -Root $root }
    'Uninstall' { Invoke-Uninstall -Root $root }
    'Check' { Invoke-Check -Root $root }
}
