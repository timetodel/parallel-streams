#Requires -Version 7
<#
Tab name: which session runs a stream, and what the person called its tab.

Why. The owner keeps dozens of tabs open and names them by hand ("O1-3-1"). The board knows a
stream by folder and branch, and had no answer to "which tab is running stream wave9/3" — people
went looking for it through every session transcript, for minutes. Everything that ties a stream
to a tab lives here:

  • reading the name from the session transcript — fast, reading only the new tail;
  • the tab registry — a small record per session next to the claim registry;
  • one shared way of saying "tab O1-3-1" for every listing.

‼️ EXACTLY two service records are read from a session transcript — `custom-title` (the name a
person gave the tab) and `ai-title` (one made up automatically). Neither the conversation nor
anything else is read or stored: the transcript is the person's whole conversation, and a kit
installed into someone else's project has no right to take more out of it than the name.

‼️ The editor's internals (its databases, workspace storage) are not read at all: that approach was
already rejected — it produced false alarms. The one source is the session transcript.

‼️ The hot path is written with as few calls as possible. The delivery hook writes the tab record on
EVERY human message, in a fresh shell process, and there the first call into each function costs
milliseconds. Measured 2026-09-13 (process CPU time, machine under full load): the same work through
a chain of helper functions — about 150 ms; as one function — about 55 ms. So the constants here are
variables, and the file writes on the hot path are done in place, with no helpers in between.

The file does nothing on its own and depends on nothing else in the kit: it only declares
functions. Any read failure is a quiet "not found": a missed name is no reason to crash a command
or the hook.
#>

# The session id goes into a file name, so only a safe shape is accepted: letters, digits, hyphen and
# underscore. A slash, a dot or an asterisk would mean escaping our folder or a search pattern
# instead of a name — and the id comes from outside (an environment variable, the hook's input).
$script:TabSessionIdPattern = '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'

# One backward-read chunk. A megabyte is a dozen or two milliseconds to read and scan, and in live
# transcripts the name record usually sits 1–30 KB from the end: one chunk is usually enough.
$script:TabTitleChunkBytes = 1MB

# The ceiling on backward reading the first time a transcript is read.
#
# The host repeats the name record as the conversation goes on (paired with its "last prompt" service
# record), so from any point in a transcript the last name record is no further back than the largest
# gap between repeats. Measured 2026-09-13 on the twelve largest live transcripts (up to 270 MB): the
# largest gap is 30.4 MB (a 207 MB transcript), then 25.9 and 19.1 MB. The first estimate the same day,
# on three transcripts up to 105 MB, gave 9.3 MB — transcripts grow, and the gaps grow with them. 64 MB
# is twice the largest measured gap.
#
# The cost is bounded: 64 one-megabyte chunks, and only on the FIRST read of a session's transcript —
# after that only the new tail is read. A miss isn't permanent either: no human-given name found —
# the automatic one is returned if it turned up, otherwise "not found", and the next repeat of the
# record in the new tail brings the name back by itself. Silence past the ceiling is more honest than
# reading two hundred megabytes.
$script:TabTitleCeilingBytes = 64MB

# The first line of the name cache: it says the file is our cache of this shape. Change the shape —
# change the mark, and old files simply get read again.
$script:TabTitleCacheMark = 'parallel-streams tab title cache 1'

function Test-SessionId {
    param([string]$Id)
    return [bool]($Id -and $Id -cmatch $script:TabSessionIdPattern)
}

function Get-ClaudeConfigDir {
    # The host's config directory: the `CLAUDE_CONFIG_DIR` variable if set, otherwise `.claude` in the
    # user's folder. Tests point it at their own temporary folder — they never touch the real one.
    if ($env:CLAUDE_CONFIG_DIR) { return $env:CLAUDE_CONFIG_DIR }
    $profileDir = [Environment]::GetFolderPath('UserProfile')
    if (-not $profileDir) { $profileDir = $HOME }
    return [System.IO.Path]::Combine($profileDir, '.claude')
}

function Find-SessionTranscript {
    param([string]$SessionId)
    # A session transcript is `<config dir>/projects/<project folder>/<id>.jsonl`.
    #
    # ‼️ The project folder is NOT derived from a path: the encoding formula is declared nowhere,
    # folder junctions on some machines stand one folder in for another, and a session that moved
    # into a worktree keeps writing to the transcript of the folder it STARTED in. So we look for the
    # id in every project folder. Several found — take the largest: that's the one being written.
    if (-not (Test-SessionId $SessionId)) { return '' }
    $projects = [System.IO.Path]::Combine((Get-ClaudeConfigDir), 'projects')
    $best = ''
    $bestSize = [long]-1
    foreach ($dir in [System.IO.Directory]::EnumerateDirectories($projects)) {
        $info = [System.IO.FileInfo]::new([System.IO.Path]::Combine($dir, "$SessionId.jsonl"))
        if ($info.Exists -and $info.Length -gt $bestSize) {
            $best = $info.FullName
            $bestSize = $info.Length
        }
    }
    return $best
}

function Get-TitleFromLine {
    param([string]$Line, [string]$Type, [string]$Field)
    # A name record is recognized ONLY as a parsed top-level object with the right `type` field.
    #
    # ‼️ Text search is not trusted here: the same words appear inside the conversation too — search
    # output, reading a transcript, discussing the format. Inside a string the quotes are escaped, but
    # an object with such a `type` can also be nested (a tool result parsed into a structure). Search
    # only PICKS candidate lines; parsing decides.
    #
    # Parsed with .NET, not the shell: the shell turns a string that looks like a date into a date, and
    # a tab named "2026-09-04" (waves here are named by date) would lose its name.
    $doc = $null
    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($Line)
        $root = $doc.RootElement
        if ($root.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { return $null }
        $kind = [System.Text.Json.JsonElement]::new()
        if (-not $root.TryGetProperty('type', [ref]$kind)) { return $null }
        if ($kind.ValueKind -ne [System.Text.Json.JsonValueKind]::String -or $kind.GetString() -ne $Type) {
            return $null
        }
        $value = [System.Text.Json.JsonElement]::new()
        if (-not $root.TryGetProperty($Field, [ref]$value)) { return $null }
        if ($value.ValueKind -ne [System.Text.Json.JsonValueKind]::String) { return $null }
        return $value.GetString()
    } catch {
        return $null
    } finally {
        if ($doc) { $doc.Dispose() }
    }
}

function Find-LastTitleInText {
    param([string]$Text, [string]$Type, [string]$Field)
    # The last record of the given kind in a piece of text made of WHOLE lines. We go from the end:
    # it's the last one we need — a rename appends a new record at the end, and that one wins.
    $needle = '"' + $Type + '"'
    $at = $Text.LastIndexOf($needle, [System.StringComparison]::Ordinal)
    while ($at -ge 0) {
        $lineStart = if ($at -gt 0) { $Text.LastIndexOf([char]10, $at - 1) + 1 } else { 0 }
        $lineEnd = $Text.IndexOf([char]10, $at)
        if ($lineEnd -lt 0) { $lineEnd = $Text.Length }
        $value = Get-TitleFromLine -Line $Text.Substring($lineStart, $lineEnd - $lineStart) -Type $Type -Field $Field
        if ($null -ne $value) { return $value }
        if ($lineStart -le 0) { break }
        $at = $Text.LastIndexOf($needle, $lineStart - 1, [System.StringComparison]::Ordinal)
    }
    return $null
}

function Read-TitleRecords {
    param([string]$Path, [long]$From, [long]$To)
    # Backward read of the window [From, To) in chunks: the last human-given name record, the last
    # automatic one, and the offset right after the window's last WHOLE line (the next tail read
    # starts there).
    #
    # ‼️ A line on a chunk border is neither lost nor parsed in half. There is one rule: only lines
    # whose start AND trailing newline are both visible count. A chunk's tail with no newline is the
    # start of a line the next chunk will finish; a chunk's head before its first newline is the end
    # of a line that began earlier, and it goes into the next chunk whole. An incomplete last line of
    # the file (being written right now) doesn't count, and the offset doesn't move past it: the next
    # call finishes it.
    #
    # A line longer than a chunk can't be a name record (that's a hundred bytes), so such a line is
    # skipped whole rather than read by widening the window.
    $chunk = [long]$script:TabTitleChunkBytes
    $custom = $null
    $auto = $null
    $offset = $null
    $stream = [System.IO.FileStream]::new($Path, 'Open', 'Read', 'ReadWrite, Delete')
    try {
        # Does the window start at a line start: a tail-read window starts at the remembered offset, a
        # first-read window starts at the ceiling, which is usually mid-line.
        $fromIsLineStart = $true
        if ($From -gt 0) {
            $stream.Position = $From - 1
            $fromIsLineStart = ($stream.ReadByte() -eq 10)
        }
        $bufferSize = $To - $From
        if ($bufferSize -gt $chunk) { $bufferSize = $chunk }
        if ($bufferSize -lt 1) { $bufferSize = 1 }
        $buffer = [byte[]]::new([int]$bufferSize)
        $end = $To
        while ($end -gt $From) {
            $pos = $end - $chunk
            if ($pos -lt $From) { $pos = $From }
            $length = [int]($end - $pos)
            $stream.Position = $pos
            $read = 0
            while ($read -lt $length) {
                $got = $stream.Read($buffer, $read, $length - $read)
                if ($got -le 0) { break }
                $read += $got
            }
            # The file got shorter right under the read — nothing more to read, the next call decides.
            if ($read -lt $length) { break }
            # A chunk usually ends with a newline (transcript lines are written whole) — then there's
            # nothing to search for. A byte search is needed only when the tail is cut mid-line.
            $lastBreak = if ($buffer[$length - 1] -eq 10) {
                $length - 1
            } else {
                [Array]::LastIndexOf($buffer, [byte]10, $length - 1, $length)
            }
            if ($lastBreak -lt 0) {
                # Not a single newline in the whole chunk: this is the middle of a line longer than a chunk.
                $end = $pos
                continue
            }
            if ($null -eq $offset) { $offset = $pos + $lastBreak + 1 }
            $atWindowStart = ($pos -eq $From)
            $startsAtLine = $atWindowStart -and $fromIsLineStart
            # The start of the first whole line is searched for only when the chunk starts mid-line.
            $firstBreak = if ($startsAtLine) { -1 } else { [Array]::IndexOf($buffer, [byte]10, 0, $length) }
            $regionStart = $firstBreak + 1
            if ($regionStart -le $lastBreak) {
                # Chunk borders sit on newlines, and a multi-byte character never contains a newline
                # byte — so Cyrillic isn't torn at the border.
                $text = [System.Text.Encoding]::UTF8.GetString($buffer, $regionStart, $lastBreak + 1 - $regionStart)
                if ($null -eq $custom) {
                    $custom = Find-LastTitleInText -Text $text -Type 'custom-title' -Field 'customTitle'
                }
                if ($null -eq $auto) {
                    $auto = Find-LastTitleInText -Text $text -Type 'ai-title' -Field 'aiTitle'
                }
            }
            # A human-given name was found — no reason to read further back: older records lose to it.
            # The exception is a name cleared to an empty string: then the automatic one counts, and we
            # keep looking for it if it hasn't turned up yet.
            if ($null -ne $custom -and ($custom -ne '' -or $null -ne $auto)) { break }
            if ($atWindowStart) { break }
            $next = $pos + $firstBreak + 1
            # The only newline sits at the very end of the chunk: the line began before the chunk and is
            # longer than it — skip it whole, or the read would stand still.
            $end = if ($next -ge $end) { $pos } else { $next }
        }
    } finally {
        $stream.Dispose()
    }
    return @{ Custom = $custom; Auto = $auto; Offset = $offset }
}

function Get-CleanTitle {
    param([string]$Raw)
    # A name is written by a person or a model, and it's printed into a listing line. Control
    # characters and text-direction switches are removed (they would break the line and could
    # reorder its words), whitespace is collapsed, length is capped: a listing needs a recognizable
    # name, not a paragraph.
    if (-not $Raw) { return '' }
    $text = $Raw -replace '[\p{Cc}\u200E\u200F\u202A-\u202E\u2066-\u2069]', ' '
    $text = ($text -replace '\s+', ' ').Trim()
    if ($text.Length -gt 80) { $text = $text.Substring(0, 79) + '…' }
    return $text
}

function Get-SessionTitle {
    param([string]$SessionId, [string]$CacheDir)
    # A tab's current name by session id: `Title` and its kind `Kind` — `custom` (given by a person),
    # `auto` (made up automatically), or empty (not found).
    #
    # The current name is the LAST human-given name record; if there is none at all — the last
    # automatic one.
    #
    # ‼️ The delivery hook calls this on EVERY human message, and transcripts reach two hundred
    # megabytes. Hence a per-session cache: the transcript path, the offset read up to, and the names
    # found. The next call reads only the NEW tail from the offset. The file got shorter than the
    # offset — it was rewritten, read again. The tail grew past the ceiling — read back from the end up
    # to the ceiling, and if nothing turns up there, keep what we had.
    #
    # The cache is five lines of plain text: shape mark, transcript path, offset, human-given name,
    # automatic name (both already cleaned). Not JSON on purpose: parsing JSON in the hook's fresh
    # process costs tens of milliseconds on every turn, while five lines are read in one disk call.
    # Nothing from the transcript goes into the cache except those two names.
    #
    # Any failure — "not found", no exception out.
    if (-not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return @{ Title = ''; Kind = '' } }
    try {
        $cacheFile = if ($CacheDir) { [System.IO.Path]::Combine($CacheDir, "$SessionId.txt") } else { '' }
        $cached = $null
        if ($cacheFile) {
            try { $cached = [System.IO.File]::ReadAllLines($cacheFile) } catch { $cached = $null }
        }
        $known = if ($cached -and $cached.Count -eq 5 -and $cached[0] -eq $script:TabTitleCacheMark) { $cached[2] -as [long] } else { $null }
        $usable = $null -ne $known -and $known -ge 0
        $info = if ($usable) { [System.IO.FileInfo]::new($cached[1]) } else { $null }
        if (-not $info -or -not $info.Exists) {
            $usable = $false
            $path = Find-SessionTranscript -SessionId $SessionId
            if (-not $path) { return @{ Title = ''; Kind = '' } }
            $info = [System.IO.FileInfo]::new($path)
        }
        $size = $info.Length
        if (-not $usable) { $known = [long]0 }
        # The file got shorter than what was read — it was rewritten: the old names and offset mean nothing.
        if ($known -gt $size) { $usable = $false; $known = [long]0 }
        $custom = if ($usable) { $cached[3] } else { '' }
        $auto = if ($usable) { $cached[4] } else { '' }
        if ($size -gt $known -or -not $usable) {
            $offset = $null
            # ‼️ THE SHORT PATH for the common case: there is a cache, the tail is shorter than a chunk,
            # and it holds no name records (the host doesn't append them on every turn). Then it's enough
            # to read the tail in one piece and make sure the record-kind word isn't in it at all — no
            # general backward read, which costs several times more in a fresh process.
            #
            # We read from the byte BEFORE the offset: it must be a newline (otherwise the offset isn't on
            # a line border), and so must the last byte (otherwise the last line is still being written).
            # Anything doesn't match — take the general path, it handles all such cases.
            if ($usable -and $known -gt 0 -and ($size - $known) -le $script:TabTitleChunkBytes) {
                $length = [int]($size - $known + 1)
                $tail = [byte[]]::new($length)
                $got = 0
                $stream = [System.IO.FileStream]::new($info.FullName, 'Open', 'Read', 'ReadWrite, Delete')
                try {
                    $stream.Position = $known - 1
                    while ($got -lt $length) {
                        $step = $stream.Read($tail, $got, $length - $got)
                        if ($step -le 0) { break }
                        $got += $step
                    }
                } finally {
                    $stream.Dispose()
                }
                if ($got -eq $length -and $tail[0] -eq 10 -and $tail[$length - 1] -eq 10 -and
                    [System.Text.Encoding]::UTF8.GetString($tail, 1, $length - 1).IndexOf('-title"', [System.StringComparison]::Ordinal) -lt 0) {
                    $offset = $size
                }
            }
            if ($null -eq $offset) {
                # The general path. The window is from what was read to the end, but no more than the ceiling.
                $from = $size - [long]$script:TabTitleCeilingBytes
                if ($from -lt $known) { $from = $known }
                $found = @{ Custom = $null; Auto = $null; Offset = $null }
                if ($size -gt $from) { $found = Read-TitleRecords -Path $info.FullName -From $from -To $size }
                if ($null -ne $found.Custom) { $custom = Get-CleanTitle -Raw $found.Custom }
                if ($null -ne $found.Auto) { $auto = Get-CleanTitle -Raw $found.Auto }
                $offset = if ($null -ne $found.Offset) { [long]$found.Offset } elseif ($usable) { $known } else { $from }
            }
            if ($cacheFile) {
                # Written in place, no helper: through a temporary file next to it, so a reader never sees
                # a half-written file. A newline after EVERY line, the last included: otherwise empty names
                # at the end wouldn't count as lines, and the cache would never be recognized.
                $temp = "$cacheFile.tmp-$PID"
                try {
                    $null = [System.IO.Directory]::CreateDirectory($CacheDir)
                    [System.IO.File]::WriteAllText($temp, "$($script:TabTitleCacheMark)`n$($info.FullName)`n$offset`n$custom`n$auto`n")
                    [System.IO.File]::Move($temp, $cacheFile, $true)
                } catch {
                    # The cache didn't get written — no harm: the next call reads again.
                    try { [System.IO.File]::Delete($temp) } catch { }
                }
            }
        }
        if ($custom) { return @{ Title = $custom; Kind = 'custom' } }
        if ($auto) { return @{ Title = $auto; Kind = 'auto' } }
        return @{ Title = ''; Kind = '' }
    } catch {
        return @{ Title = ''; Kind = '' }
    }
}

# ─────────────────────────────────────────────────────────────────────────────────────────────
# Tab registry: a small record per session next to the claim registry.
#
# Only the session itself writes it — the delivery hook at session start and on every human message,
# and a stream announcement. Every listing reads it. It lives in the repository's shared internal
# directory, like the board: visible to every worktree, and it survives a worktree being deleted.
# ─────────────────────────────────────────────────────────────────────────────────────────────

# Tab records already read in this run: a listing asks about the same tab once per line.
$script:WaveBoardTabRecords = @{}

# How long a tab may stay silent before the tab listing moves it to the "long silent" tail. A day,
# not the claim's liveness threshold: a night's pause in the conversation doesn't push a tab out.
$script:TabSilentHours = 24

# How long a tab record lives without a single turn: after that the hook removes it at session start.
# A month covers a wave with room to spare; nobody looks up a tab by name after a month of silence.
$script:TabKeepDays = 30

function Get-TabsDir {
    param([string]$RegistryDir)
    # The directory next to the claim registry. Derived from the registry path rather than asking git
    # again: the delivery hook runs on every turn, and one more git call would be a cost for nothing.
    if (-not $RegistryDir) { return '' }
    $parent = [System.IO.Path]::GetDirectoryName($RegistryDir.TrimEnd('/', '\'))
    if (-not $parent) { return '' }
    return [System.IO.Path]::Combine($parent, 'tabs')
}

function Read-TabRecordFile {
    param([string]$Path)
    # A tab record is flat JSON made of strings. Unreadable or unparsable — nothing, not a crash.
    #
    # Parsed with .NET, not the shell, for the same reason as the transcript: the shell would turn a
    # name that looks like a date into a date, and tab "2026-09-04" would be shown in the system
    # locale's format.
    $text = $null
    try {
        $text = [System.IO.File]::ReadAllText($Path)
    } catch {
        return $null
    }
    $doc = $null
    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($text)
        if ($doc.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { return $null }
        $fields = [ordered]@{}
        foreach ($property in $doc.RootElement.EnumerateObject()) {
            if ($property.Value.ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
                $fields[$property.Name] = $property.Value.GetString()
            }
        }
        return $fields
    } catch {
        return $null
    } finally {
        if ($doc) { $doc.Dispose() }
    }
}

function Get-TabRecord {
    param([string]$Dir, [string]$SessionId)
    if (-not $Dir -or -not (Test-SessionId $SessionId)) { return $null }
    $key = "$Dir|$SessionId"
    if ($script:WaveBoardTabRecords.ContainsKey($key)) { return $script:WaveBoardTabRecords[$key] }
    $record = Read-TabRecordFile -Path ([System.IO.Path]::Combine($Dir, "$SessionId.json"))
    $script:WaveBoardTabRecords[$key] = $record
    return $record
}

function Get-TabRecords {
    param([string]$Dir)
    # The whole tab registry. Unreadable records are skipped silently: this is a listing for the eye,
    # and one busy record is no reason not to show the rest.
    $records = [System.Collections.Generic.List[object]]::new()
    if (-not $Dir) { return @() }
    $files = @()
    try {
        $files = @([System.IO.Directory]::GetFiles($Dir, '*.json'))
    } catch {
        return @()
    }
    foreach ($file in $files) {
        $record = Read-TabRecordFile -Path $file
        if ($record -and (Test-SessionId ([string]$record.session_id))) { $records.Add($record) }
    }
    return @($records)
}

function Update-TabRecord {
    param([string]$Dir, [string]$SessionId, [string]$Cwd, [string]$Tree, [switch]$Prompt)
    # This session's tab record: session id, folder, tree root, the name and its kind, the time of the
    # last human message. `-Prompt` is a human turn: only it moves the message time. A session start
    # and a stream announcement leave it alone — they aren't messages.
    #
    # Mute on any failure: a missed tab record is no reason to get in the way of work.
    #
    # ‼️ The previous record is read only when it can't be avoided: at session start and in an
    # announcement — for the last message time; on a human turn — only if the name wasn't found.
    # Building and writing happen in place, with no helpers: this is the kit's most frequent call (see
    # the file header).
    if (-not $Dir -or -not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return }
    $file = [System.IO.Path]::Combine($Dir, "$SessionId.json")
    $temp = "$file.tmp-$PID"
    try {
        $title = Get-SessionTitle -SessionId $SessionId -CacheDir ([System.IO.Path]::Combine($Dir, 'cache'))
        $old = if (-not $Prompt -or -not $title.Kind) { Read-TabRecordFile -Path $file } else { $null }
        # The transcript didn't read for a moment — the previous name is better than nothing.
        if (-not $title.Kind -and $old -and $old.title) {
            $title = @{ Title = [string]$old.title; Kind = [string]$old.title_kind }
        }
        $now = (Get-Date).ToString('s')
        $promptAt = if ($Prompt) { $now } elseif ($old) { [string]$old.prompt_at } else { '' }
        $record = [ordered]@{
            session_id = $SessionId
            cwd        = ([string]$Cwd -replace '\\', '/').TrimEnd('/')
            tree       = ([string]$Tree -replace '\\', '/').TrimEnd('/')
            title      = [string]$title.Title
            title_kind = [string]$title.Kind
            prompt_at  = $promptAt
            seen_at    = $now
        }
        # A flat record of strings — turned into JSON by hand, not by the shell's general converter: that
        # one costs tens of milliseconds in a fresh process. Backslash and quote are escaped; the fields
        # hold no control characters (the name is cleaned, paths don't contain them), and just in case
        # they're replaced with a space.
        $pairs = foreach ($key in $record.Keys) {
            "  `"$key`": `"$((($record[$key] -replace '\\', '\\') -replace '"', '\"') -replace '[\x00-\x1f]', ' ')`""
        }
        $null = [System.IO.Directory]::CreateDirectory($Dir)
        [System.IO.File]::WriteAllText($temp, "{`n$($pairs -join ",`n")`n}`n")
        [System.IO.File]::Move($temp, $file, $true)
        $script:WaveBoardTabRecords["$Dir|$SessionId"] = $record
    } catch {
        try { [System.IO.File]::Delete($temp) } catch { }
        return
    }
}

function Remove-StaleTabRecords {
    param([string]$Dir)
    # Cleanup of tab records silent longer than the limit. By file write time: every turn of a tab
    # updates it, so a live tab never gets here. Mute on any failure.
    if (-not $Dir) { return }
    $deadline = (Get-Date).AddDays(-$script:TabKeepDays)
    # Tab records are `.json` in the directory itself; the name cache is `.txt` in a subdirectory.
    $places = @{ $Dir = '*.json'; ([System.IO.Path]::Combine($Dir, 'cache')) = '*.txt' }
    foreach ($folder in @($places.Keys)) {
        try {
            foreach ($file in [System.IO.Directory]::GetFiles($folder, $places[$folder])) {
                try {
                    # Delete only our own: the file name is a session id. A foreign file that ended up in
                    # the directory is never touched by cleanup, however old.
                    if (-not (Test-SessionId ([System.IO.Path]::GetFileNameWithoutExtension($file)))) { continue }
                    if ([System.IO.File]::GetLastWriteTime($file) -lt $deadline) { [System.IO.File]::Delete($file) }
                } catch {
                    continue
                }
            }
        } catch {
            continue
        }
    }
}

function Format-TabTitle {
    param($Tab, [switch]$Past)
    # One form for every listing: "tab O1-3-1"; for an automatically made-up one — "unnamed tab
    # (auto: …)"; for a stream no longer being run — "was run by tab …". Unknown — empty: a listing
    # line isn't cluttered with "unknown" on every stream.
    if (-not $Tab -or -not [string]$Tab.title) { return '' }
    $lead = if ($Past) { 'was run by ' } else { '' }
    if ([string]$Tab.title_kind -eq 'custom') { return "${lead}tab $($Tab.title)" }
    return "${lead}unnamed tab (auto: $($Tab.title))"
}

function Get-ClaimTabText {
    param($Claim)
    # The name of the tab running (or that ran) the stream of a parsed registry record. The tabs
    # directory is derived from the claim file's path: it's always a sibling of the claims directory.
    # A closed stream — "was run by".
    try {
        if (-not $Claim -or -not $Claim.File -or -not $Claim.Record) { return '' }
        $sessionId = [string]$Claim.Record.session_id
        if (-not (Test-SessionId $sessionId)) { return '' }
        $dir = Get-TabsDir -RegistryDir ([System.IO.Path]::GetDirectoryName([string]$Claim.File))
        return (Format-TabTitle -Tab (Get-TabRecord -Dir $dir -SessionId $sessionId) -Past:([bool]$Claim.Closed))
    } catch {
        return ''
    }
}

function Test-LinkedWorktree {
    param([string]$TreePath)
    # A separate worktree, not the repository's main folder. We ask the disk, not git: the delivery
    # hook runs on every message, and one more git call would cost tens of milliseconds. A separate
    # worktree's `.git` is a file pointing into `.../worktrees/<name>`; the main folder's is a
    # directory. A submodule also carries a `.git` file, but it points into `.../modules/...` — we
    # don't count that as a worktree.
    if (-not $TreePath) { return $false }
    try {
        $marker = [System.IO.Path]::Combine($TreePath, '.git')
        if (-not [System.IO.File]::Exists($marker)) { return $false }
        $text = [System.IO.File]::ReadAllText($marker) -replace '\\', '/'
        return [bool]($text -match '(?im)^gitdir:\s*\S.*/worktrees/[^/]+/?\s*$')
    } catch {
        return $false
    }
}
