#Requires -Version 7
<#
Tab name: which session runs a stream, and what the person called its tab.

Why. The owner keeps dozens of tabs open and names them by hand ("O1-3-1"). The board knows a
stream by folder and branch, and had no answer to "which tab is running stream wave9/3" — people
went looking for it through every session transcript, for minutes. Everything that ties a stream
to a tab lives here:

  • reading the name from the session transcript — fast, reading only the new tail;
  • the tab registry — a small record per session next to the claim registry;
  • which session runs a claim when the announcement didn't name one (adoption and hand-over);
  • one shared way of saying "tab O1-3-1" for every listing.

‼️ EXACTLY two service records are read from a session transcript — `custom-title` (the name a
person gave the tab) and `ai-title` (one made up automatically). Neither the conversation nor
anything else is read or stored: the transcript is the person's whole conversation, and a kit
installed into someone else's project has no right to take more out of it than the name.

‼️ The editor's internals (its databases, workspace storage) are not read at all: that approach was
already rejected — it produced false alarms. The one source is the session transcript.

‼️ Everything in the repository's internal directory (tab registry, cache, claims) is trusted exactly
as far as the directory itself is: any process of the same person can write there. So whatever is
PRINTED from it is cleaned at print time, the transcript path from the cache is checked against the
transcripts directory, and cleanup deletes only files it recognizes by their content.

‼️ The hot path is written with as few calls as possible. The delivery hook writes the tab record on
EVERY human message, in a fresh shell process, and there the first call into each function costs
milliseconds. Measured 2026-09-13 (process CPU time, machine under full load): the same work through
a chain of helper functions — about 150 ms; as one function — about 55 ms. So the constants here are
variables, and the file writes on the hot path are done in place, with no helpers in between. On a
human message the name is re-read from the transcript at most every two minutes, and the message
time is the modification time of a small marker, not a rewrite of the tab record.

The file does nothing on its own and depends on nothing else in the kit: it only declares
functions. Any read failure is a quiet "not found": a missed name is no reason to crash a command
or the hook.
#>

# The session id goes into a file name, so only a safe shape is accepted: letters, digits, hyphen and
# underscore. A slash, a dot or an asterisk would mean escaping our folder or a search pattern
# instead of a name — and the id comes from outside (an environment variable, the hook's input).
#
# ‼️ The end anchor is `\z`, not `$`: in these patterns `$` also matches before a trailing newline, and
# the id "abc" followed by a newline passed the check.
$script:TabSessionIdPattern = '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\z'

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

# The parse budget per call. Candidate lines are picked by text, and EVERY one gets parsed — while the
# record-kind word can also be a string value inside a nested object (a tool result, a third-party
# server's reply). A review experiment on 2026-09-13: a megabyte of such lines cost four seconds on one
# human message, before any findings were delivered. So:
#   • a line longer than 8 KB is never parsed — a name record takes a few hundred bytes;
#   • after 200 parses that yield no record the read stops: the offset and the previous names go into
#     the cache, and the next repeat of the record in the new tail brings the name back.
$script:TabTitleLineMaxChars = 8192
$script:TabTitleParseLimit = 200
$script:TabTitleParsesLeft = 200

# The first line of the name cache: it says the file is our cache of this shape. Change the shape —
# change the mark, and old files simply get read again.
$script:TabTitleCacheMark = 'parallel-streams tab title cache 1'

# The first line of a session marker — cleanup recognizes its own file by it.
$script:TabStateMark = 'parallel-streams tab state 1'

# On a human message the name is re-read from the transcript at most once in this many seconds (by the
# cache file's modification time). A rename reaches the listings within two minutes; a session start
# and a stream announcement always read the transcript.
$script:TabTitleRereadSeconds = 120

# No transcript found — look for it again at most once in this many seconds. The search walks every
# project folder, and some of them are junctions onto network shares: without this memory an
# unreachable host would cost a timeout on every message. The same period as the name re-read, not a
# longer one: a new session's transcript appears after the session starts, and a longer memory of the
# miss would delay both the name and the claim hand-over after a context clear by as much.
$script:TabTitleMissRecheckSeconds = 120

# An encoding that doesn't throw on an invalid character. The default file encoding throws on a lone
# half of a surrogate pair — and the cache and the tab record were silently never written.
$script:TabUtf8 = [System.Text.UTF8Encoding]::new($false)

# Names read from the transcript in this run: the claim hand-over check and the tab record ask the
# same thing, and the transcript isn't read twice.
$script:TabTitleNow = @{}

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
    # No transcripts directory at all (another client) is a "not found" the cache will remember, not an
    # exception after which the search would repeat on every turn.
    $dirs = @()
    try { $dirs = [System.IO.Directory]::GetDirectories($projects) } catch { return '' }
    foreach ($dir in $dirs) {
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
    #
    # The parse budget is shared by the whole name read (see `$script:TabTitleParseLimit`): once it's
    # spent we stop, leaving the rest unparsed.
    $needle = '"' + $Type + '"'
    $at = $Text.LastIndexOf($needle, [System.StringComparison]::Ordinal)
    while ($at -ge 0 -and $script:TabTitleParsesLeft -gt 0) {
        $lineStart = if ($at -gt 0) { $Text.LastIndexOf([char]10, $at - 1) + 1 } else { 0 }
        $lineEnd = $Text.IndexOf([char]10, $at)
        if ($lineEnd -lt 0) { $lineEnd = $Text.Length }
        if ($lineEnd - $lineStart -le $script:TabTitleLineMaxChars) {
            $value = Get-TitleFromLine -Line $Text.Substring($lineStart, $lineEnd - $lineStart) -Type $Type -Field $Field
            if ($null -ne $value) { return $value }
            $script:TabTitleParsesLeft--
        }
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
    #
    # The parse budget ran out — the read stops: the offset already sits at the window's end, and
    # whatever wasn't found stays as the caller had it.
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
            if ($atWindowStart -or $script:TabTitleParsesLeft -le 0) { break }
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
    param([string]$Raw, [int]$Max = 80)
    # A name is written by a person or a model, and it's printed into a listing line and into the
    # context of neighbouring tabs. So:
    #   • tag characters U+E0000–E007F are removed whole — text a person can't see but a model reads (a
    #     known way to hide instructions); in a string they are surrogate pairs `\uDB40[\uDC00-\uDC7F]`,
    #     and the format-character category, matched per code unit, doesn't catch them;
    #   • lone halves of surrogate pairs are removed — they can't be written to a file;
    #   • control and format characters (zero-width, soft hyphen, text-direction switches) are replaced
    #     with a space — they would break the line, reorder its words, and make two different names look
    #     the same;
    #   • whitespace is collapsed, length is capped: a listing needs a recognizable name, not a paragraph.
    #
    # `-Max 0` — no cap: that's how paths in listing lines are cleaned.
    if (-not $Raw) { return '' }
    $text = $Raw -replace '\uDB40[\uDC00-\uDC7F]', ''
    $text = $text -replace '[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]', ''
    $text = $text -replace '[\p{Cc}\p{Cf}]', ' '
    $text = ($text -replace '\s+', ' ').Trim()
    if ($Max -gt 0 -and $text.Length -gt $Max) {
        # ‼️ Don't leave the first half of a pair (an emoji, a rare ideograph) at the cut: a lone
        # surrogate broke the write of both the cache and the tab record.
        $cut = $Max - 1
        if ([char]::IsHighSurrogate($text[$cut - 1])) { $cut-- }
        $text = $text.Substring(0, $cut) + '…'
    }
    return $text
}

function Get-SessionTitle {
    param([string]$SessionId, [string]$CacheDir, [switch]$Fresh)
    # A tab's current name by session id: `Title` and its kind `Kind` — `custom` (given by a person),
    # `auto` (made up automatically), or empty (not found).
    #
    # The current name is the LAST human-given name record; if there is none at all — the last
    # automatic one.
    #
    # ‼️ The delivery hook calls this on human messages, and transcripts reach two hundred megabytes.
    # Hence a per-session cache: the transcript path, the offset read up to, and the names found. The
    # next call reads only the NEW tail from the offset. The file got shorter than the offset — it was
    # rewritten, read again. The tail grew past the ceiling — read back from the end up to the ceiling,
    # and if nothing turns up there, keep what we had.
    #
    # The cache is five lines of plain text: shape mark, transcript path, offset, human-given name,
    # automatic name (both already cleaned). Not JSON on purpose: parsing JSON in the hook's fresh
    # process costs tens of milliseconds on every turn, while five lines are read in one disk call.
    # Nothing from the transcript goes into the cache except those two names. An empty path means "no
    # transcript found": it's looked for again at most once in `$script:TabTitleMissRecheckSeconds`.
    #
    # `-Fresh` — a session start and an announcement: the transcript is looked for again rather than
    # taken from the cache (the session may have been resumed from another folder, and a transcript of
    # the same id grew in another project folder), and the memory of a miss doesn't apply. If the
    # transcript found is the one remembered, the cache stays valid.
    #
    # Any failure — "not found", no exception out.
    if (-not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return @{ Title = ''; Kind = '' } }
    $script:TabTitleParsesLeft = $script:TabTitleParseLimit
    try {
        $cacheFile = if ($CacheDir) { [System.IO.Path]::Combine($CacheDir, "$SessionId.txt") } else { '' }
        $cached = $null
        if ($cacheFile) {
            try { $cached = [System.IO.File]::ReadAllLines($cacheFile) } catch { $cached = $null }
        }
        $known = if ($cached -and $cached.Count -eq 5 -and $cached[0] -eq $script:TabTitleCacheMark) { $cached[2] -as [long] } else { $null }
        $usable = $null -ne $known -and $known -ge 0
        $info = $null
        if ($usable -and -not $cached[1]) {
            # Memory of a miss: there was no transcript very recently — don't walk the project folders again.
            if (-not $Fresh -and ([datetime]::Now - [System.IO.File]::GetLastWriteTime($cacheFile)).TotalSeconds -lt $script:TabTitleMissRecheckSeconds) {
                return @{ Title = ''; Kind = '' }
            }
            $usable = $false
        }
        if ($usable) {
            # ‼️ The transcript path from the cache is accepted only if it's THIS session's transcript in
            # the transcripts directory: the name is `<id>.jsonl`, and the directory two levels up is
            # `<config dir>/projects`. Compared as strings, without touching the disk. Otherwise a cache
            # edited by anyone would name the tab after someone else or send the hook to a network path
            # on every turn.
            $info = [System.IO.FileInfo]::new($cached[1])
            $projects = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine((Get-ClaudeConfigDir), 'projects')).TrimEnd('\', '/')
            $journalRoot = if ($info.Directory -and $info.Directory.Parent) { $info.Directory.Parent.FullName.TrimEnd('\', '/') } else { '' }
            if ($info.Name -cne "$SessionId.jsonl" -or -not [string]::Equals($journalRoot, $projects, [System.StringComparison]::OrdinalIgnoreCase)) {
                $usable = $false
                $info = $null
            } elseif (-not $Fresh -and -not $info.Exists) {
                $info = $null
            }
        }
        if ($Fresh -or -not $info) {
            $path = Find-SessionTranscript -SessionId $SessionId
            if ($path -and $info -and [string]::Equals($path, $info.FullName, [System.StringComparison]::OrdinalIgnoreCase)) {
                # The transcript found is the one in the cache — the cache stays valid.
            } elseif ($path) {
                $usable = $false
                $info = [System.IO.FileInfo]::new($path)
            } else {
                $usable = $false
                $info = $null
            }
        }
        $custom = if ($usable) { $cached[3] } else { '' }
        $auto = if ($usable) { $cached[4] } else { '' }
        $pathText = ''
        $offset = [long]0
        $write = $true
        if ($info) {
            $pathText = $info.FullName
            $size = $info.Length
            if (-not $usable) { $known = [long]0 }
            # The file got shorter than what was read — it was rewritten: the old names and offset mean nothing.
            if ($known -gt $size) { $usable = $false; $known = [long]0; $custom = ''; $auto = '' }
            $write = ($size -gt $known -or -not $usable)
            if ($write) {
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
            }
        }
        if ($write -and $cacheFile) {
            # Written in place, no helper: through a temporary file next to it, so a reader never sees
            # a half-written file. A newline after EVERY line, the last included: otherwise empty names
            # at the end wouldn't count as lines, and the cache would never be recognized.
            $temp = "$cacheFile.tmp-$PID"
            try {
                $null = [System.IO.Directory]::CreateDirectory($CacheDir)
                [System.IO.File]::WriteAllText($temp, "$($script:TabTitleCacheMark)`n$pathText`n$offset`n$custom`n$auto`n", $script:TabUtf8)
                [System.IO.File]::Move($temp, $cacheFile, $true)
            } catch {
                # The cache didn't get written — no harm: the next call reads again.
                try { [System.IO.File]::Delete($temp) } catch { }
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
# Only the session itself writes it — the delivery hook at session start and on human messages, and a
# stream announcement. Every listing reads it. It lives in the repository's shared internal directory,
# like the board: visible to every worktree, and it survives a worktree being deleted.
#
# Up to three files per session:
#   • `<id>.json` — the record itself (id, folder, tree, starting tree, name, kind); rewritten only
#     when one of those changed;
#   • `<id>.prompt` — the marker: its modification time IS the time of the last human message, and
#     inside it are the same folder, tree and name, so a human message can compare them without
#     parsing JSON;
#   • `cache/<id>.txt` — the name cache (see `Get-SessionTitle`).
# ─────────────────────────────────────────────────────────────────────────────────────────────

# Tab records already read in this run: a listing asks about the same tab once per line.
$script:WaveBoardTabRecords = @{}

# How long a tab may stay silent before the tab listing moves it to the "long silent" tail. A day,
# not the claim's liveness threshold: a night's pause in the conversation doesn't push a tab out.
$script:TabSilentHours = 24

# A claim without a session isn't adopted if a person wrote in ANOTHER tab of the same worktree within
# this many hours: two tabs in a worktree are an ambiguity, and choosing for the person isn't allowed.
$script:TabAdoptRivalHours = 24

# How long a tab record lives without a single turn: after that the hook removes it at session start.
# A month covers a wave with room to spare; nobody looks up a tab by name after a month of silence.
$script:TabKeepDays = 30

# How long a leftover temporary file lives (the process was killed between the write and the move).
$script:TabTempKeepHours = 24

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
    # The time of the last human message comes from the session marker (its modification time) when
    # there is one: the tab record isn't rewritten on every turn. No marker — the record's own field.
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
        $mark = [System.IO.Path]::ChangeExtension($Path, '.prompt')
        if ([System.IO.File]::Exists($mark)) {
            $fields['prompt_at'] = [System.IO.File]::GetLastWriteTime($mark).ToString('s')
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
    param(
        [string]$Dir,
        [string]$SessionId,
        [string]$Cwd,
        [string]$Tree,
        # `Prompt` — a human turn (moves the message time), `Start` — a session start (writes the
        # starting tree), `Claim` — a stream announcement. A session start and an announcement always
        # read the transcript.
        [string]$Stage,
        # A session start after a context compaction: the starting tree stays as it was — the tab may
        # have moved into someone else's worktree, and a compaction doesn't make that its start.
        [switch]$KeepStartTree,
        # Rewrite the record even if nothing in it changed (the hook just changed the claim's session).
        [switch]$Force
    )
    # This session's tab record: session id, folder, tree root, starting tree, the name and its kind.
    #
    # ‼️ The kit's most frequent call is a human turn. There, within two minutes of the last transcript
    # read, the work is one read of a small marker and one touch of its time: the name comes from the
    # marker itself, and the tab record (JSON) is neither parsed nor rewritten unless the folder, tree or
    # name changed. Building and writing happen in place, with no helpers (see the file header).
    #
    # Mute on any failure: a missed tab record is no reason to get in the way of work.
    if (-not $Dir -or -not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return }
    $file = [System.IO.Path]::Combine($Dir, "$SessionId.json")
    $temp = "$file.tmp-$PID"
    try {
        $cacheDir = [System.IO.Path]::Combine($Dir, 'cache')
        $markFile = [System.IO.Path]::Combine($Dir, "$SessionId.prompt")
        $state = $null
        try { $state = [System.IO.File]::ReadAllLines($markFile) } catch { $state = $null }
        $stateOk = $state -and $state.Count -eq 5 -and $state[0] -eq $script:TabStateMark
        $cwdText = (([string]$Cwd -replace '\\', '/').TrimEnd('/')) -replace '[\x00-\x1f]', ' '
        $treeText = (([string]$Tree -replace '\\', '/').TrimEnd('/')) -replace '[\x00-\x1f]', ' '
        $title = $script:TabTitleNow[$SessionId]
        if (-not $title) {
            if ($Stage -eq 'Prompt' -and $stateOk -and
                ([datetime]::Now - [System.IO.File]::GetLastWriteTime([System.IO.Path]::Combine($cacheDir, "$SessionId.txt"))).TotalSeconds -lt $script:TabTitleRereadSeconds) {
                $title = @{ Title = $state[4]; Kind = $state[3] }
            } else {
                $title = Get-SessionTitle -SessionId $SessionId -CacheDir $cacheDir -Fresh:($Stage -ne 'Prompt')
            }
        }
        # The transcript didn't read for a moment — the previous name is better than nothing.
        if (-not $title.Kind -and $stateOk -and $state[3]) { $title = @{ Title = $state[4]; Kind = $state[3] } }
        if ($Stage -eq 'Prompt' -and -not $Force -and $stateOk -and $state[1] -ceq $cwdText -and $state[2] -ceq $treeText -and
            $state[3] -ceq [string]$title.Kind -and $state[4] -ceq [string]$title.Title) {
            # Nothing changed: the message time is a touch of the marker.
            [System.IO.File]::SetLastWriteTime($markFile, [datetime]::Now)
            return
        }
        $old = Read-TabRecordFile -Path $file
        if (-not $title.Kind -and $old -and $old.title) {
            $title = @{ Title = [string]$old.title; Kind = [string]$old.title_kind }
        }
        $now = (Get-Date).ToString('s')
        $startTree = if ($Stage -eq 'Start' -and -not ($KeepStartTree -and $old -and $old.start_tree)) {
            $treeText
        } elseif ($old) {
            [string]$old.start_tree
        } else {
            ''
        }
        # The message time inside the record is as of its rewrite; the exact one is the marker's.
        $promptAt = if ($Stage -eq 'Prompt') { $now } elseif ($old) { [string]$old.prompt_at } else { '' }
        $record = [ordered]@{
            session_id = $SessionId
            cwd        = $cwdText
            tree       = $treeText
            start_tree = $startTree
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
        [System.IO.File]::WriteAllText($temp, "{`n$($pairs -join ",`n")`n}`n", $script:TabUtf8)
        [System.IO.File]::Move($temp, $file, $true)
        $script:WaveBoardTabRecords["$Dir|$SessionId"] = $record
        # The marker goes AFTER the record: if the record write fails, the marker stays as it was, and
        # the next turn sees the mismatch and rewrites the record again.
        $stateText = "$($script:TabStateMark)`n$cwdText`n$treeText`n$(([string]$title.Kind) -replace '[\x00-\x1f]', ' ')`n$(([string]$title.Title) -replace '[\x00-\x1f]', ' ')`n"
        if ($Stage -eq 'Prompt') {
            [System.IO.File]::WriteAllText($markFile, $stateText, $script:TabUtf8)
        } elseif ([System.IO.File]::Exists($markFile)) {
            # A session start and an announcement aren't human messages: the marker's content is updated,
            # and its modification time is put back.
            $when = [System.IO.File]::GetLastWriteTimeUtc($markFile)
            [System.IO.File]::WriteAllText($markFile, $stateText, $script:TabUtf8)
            [System.IO.File]::SetLastWriteTimeUtc($markFile, $when)
        }
    } catch {
        try { [System.IO.File]::Delete($temp) } catch { }
        return
    }
}

function Remove-StaleTabRecords {
    param([string]$Dir)
    # Cleanup of the files of tabs silent longer than the limit. A session's life is the freshest
    # modification of any of its files (record, marker, cache): a live tab touches its marker on every
    # human message. Mute on any failure.
    #
    # ‼️ Deletion is irreversible, so only what's RECOGNIZED as ours is deleted:
    #   • the tabs or cache directory turned out to be a link or a junction — no cleanup at all: anything
    #     of someone else's may lie behind it;
    #   • a `.json` record — only if it parses and its session id equals the file name;
    #   • a `.txt` cache and a `.prompt` marker — only if the first line is our mark;
    #   • a leftover temporary file — only older than a day and named `<id>.<our extension>.tmp-<number>`.
    # A stranger's file with a plain name (`package.json`, `notes.txt`) survives cleanup at any age.
    if (-not $Dir) { return }
    try {
        $cacheDir = [System.IO.Path]::Combine($Dir, 'cache')
        foreach ($folder in @($Dir, $cacheDir)) {
            $info = [System.IO.DirectoryInfo]::new($folder)
            if ($info.Exists -and (($info.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $info.LinkTarget)) { return }
        }
        $now = Get-Date
        $deadline = $now.AddDays(-$script:TabKeepDays)
        $tempDeadline = $now.AddHours(-$script:TabTempKeepHours)
        $latest = @{}
        $candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($place in @(@{ Folder = $Dir; Kinds = @('.json', '.prompt') }, @{ Folder = $cacheDir; Kinds = @('.txt') })) {
            $names = @()
            try { $names = @([System.IO.Directory]::GetFiles($place.Folder)) } catch { continue }
            foreach ($path in $names) {
                try {
                    $name = [System.IO.Path]::GetFileName($path)
                    $at = $name.IndexOf('.tmp-', [System.StringComparison]::Ordinal)
                    if ($at -gt 0) {
                        $base = $name.Substring(0, $at)
                        if ($name.Substring($at + 5) -cmatch '^[0-9]+\z' -and
                            [System.IO.Path]::GetExtension($base) -cin $place.Kinds -and
                            (Test-SessionId ([System.IO.Path]::GetFileNameWithoutExtension($base))) -and
                            [System.IO.File]::GetLastWriteTime($path) -lt $tempDeadline) {
                            [System.IO.File]::Delete($path)
                        }
                        continue
                    }
                    $kind = [System.IO.Path]::GetExtension($name)
                    if ($kind -cnotin $place.Kinds) { continue }
                    $session = [System.IO.Path]::GetFileNameWithoutExtension($name)
                    if (-not (Test-SessionId $session)) { continue }
                    $when = [System.IO.File]::GetLastWriteTime($path)
                    if (-not $latest.ContainsKey($session) -or $when -gt $latest[$session]) { $latest[$session] = $when }
                    $candidates.Add(@{ Path = $path; Session = $session; Kind = $kind })
                } catch {
                    continue
                }
            }
        }
        foreach ($candidate in $candidates) {
            if ($latest[$candidate.Session] -ge $deadline) { continue }
            try {
                $ours = $false
                if ($candidate.Kind -eq '.json') {
                    $record = Read-TabRecordFile -Path $candidate.Path
                    $ours = [bool]($record -and [string]$record.session_id -ceq $candidate.Session)
                } else {
                    $reader = [System.IO.StreamReader]::new($candidate.Path)
                    try { $first = $reader.ReadLine() } finally { $reader.Dispose() }
                    $expected = if ($candidate.Kind -eq '.txt') { $script:TabTitleCacheMark } else { $script:TabStateMark }
                    $ours = ($first -ceq $expected)
                }
                if ($ours) { [System.IO.File]::Delete($candidate.Path) }
            } catch {
                continue
            }
        }
    } catch {
        return
    }
}

function Format-TabTitle {
    param($Tab, [switch]$Past)
    # One form for every listing: "tab O1-3-1"; for an automatically made-up one — "unnamed tab
    # (auto: …)"; for a stream no longer being run — "was run by tab …". Unknown — empty: a listing
    # line isn't cluttered with "unknown" on every stream.
    #
    # ‼️ The name is cleaned HERE, at print time, not only when the transcript is read: the tab record
    # lives in a shared directory, and a record made by hand or by another program would carry a
    # newline and terminal control sequences to the screen and into neighbouring tabs' context.
    if (-not $Tab) { return '' }
    $name = Get-CleanTitle -Raw ([string]$Tab.title)
    if (-not $name) { return '' }
    $lead = if ($Past) { 'was run by ' } else { '' }
    if ([string]$Tab.title_kind -eq 'custom') { return "${lead}tab $name" }
    return "${lead}unnamed tab (auto: $name)"
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
    # A linked git worktree, not the repository's main folder. We ask the disk, not git: one more git
    # call would cost tens of milliseconds.
    #
    # The sign is a `commondir` file in the directory the `gitdir:` line of the `.git` file points to.
    # Only linked worktrees have it. The previous sign (a path ending in `/worktrees/<name>`) fired
    # falsely on a submodule living at `worktrees/<name>` and on a main folder with its git directory
    # moved out — and many tabs live there, so the first one to write would sign itself up for someone
    # else's stream.
    if (-not $TreePath) { return $false }
    try {
        # In the main folder `.git` is a directory: reading it as a file fails, and that's a "no".
        $text = [System.IO.File]::ReadAllText([System.IO.Path]::Combine($TreePath, '.git'))
        $found = [regex]::Match($text, '(?im)^gitdir:[ \t]*(\S[^\r\n]*?)[ \t\r]*$')
        if (-not $found.Success) { return $false }
        $gitDir = $found.Groups[1].Value
        if (-not [System.IO.Path]::IsPathRooted($gitDir)) { $gitDir = [System.IO.Path]::Combine($TreePath, $gitDir) }
        return [System.IO.File]::Exists([System.IO.Path]::Combine($gitDir, 'commondir'))
    } catch {
        return $false
    }
}

function Get-TabFolderKey {
    param([string]$Path)
    # A folder in one form for comparison — the same approach as the claims' folder key: forward
    # slashes, no trailing one, case-insensitive. A copy of its own, because this file depends on
    # nothing else in the kit.
    return (([string]$Path -replace '\\', '/').TrimEnd('/')).ToLowerInvariant()
}

function Get-TabOwnTitle {
    param([string]$Dir, [string]$SessionId, [string]$Stage)
    # This tab's own name for the claim hand-over decision. On a human message — from the session marker
    # (at most two minutes stale); at a session start — from the transcript. What's read from the
    # transcript is remembered: the tab record at the end of the turn doesn't read it again.
    if ($script:TabTitleNow.ContainsKey($SessionId)) { return $script:TabTitleNow[$SessionId] }
    if ($Stage -eq 'Prompt') {
        try {
            $state = [System.IO.File]::ReadAllLines([System.IO.Path]::Combine($Dir, "$SessionId.prompt"))
            if ($state.Count -eq 5 -and $state[0] -eq $script:TabStateMark -and $state[3]) {
                return @{ Title = $state[4]; Kind = $state[3] }
            }
        } catch {
            # No marker yet — the session's first turn: read the transcript.
        }
    }
    $title = Get-SessionTitle -SessionId $SessionId -CacheDir ([System.IO.Path]::Combine($Dir, 'cache')) -Fresh:($Stage -ne 'Prompt')
    $script:TabTitleNow[$SessionId] = $title
    return $title
}

function Test-TabMayAdoptClaim {
    param([string]$Dir, [string]$SessionId, [string]$Tree)
    # May this session write itself into a worktree's claim that was filed without a session.
    #
    #   • The session's starting tree is known — it must be THIS worktree: a tab started in the main
    #     folder that wandered into someone else's worktree to look doesn't take their stream.
    #   • There is no OTHER tab of this worktree where a person wrote within the last day: two tabs in
    #     a worktree are an ambiguity, and the first to write would name itself the runner for good.
    # No starting tree (the session began before the update) — only the second condition decides.
    $treeKey = Get-TabFolderKey -Path $Tree
    if (-not $treeKey) { return $false }
    $own = Get-TabRecord -Dir $Dir -SessionId $SessionId
    $startTree = if ($own) { [string]$own.start_tree } else { '' }
    if ($startTree -and (Get-TabFolderKey -Path $startTree) -ne $treeKey) { return $false }
    $since = (Get-Date).AddHours(-$script:TabAdoptRivalHours)
    foreach ($tab in (Get-TabRecords -Dir $Dir)) {
        if ([string]$tab.session_id -ceq $SessionId) { continue }
        if ((Get-TabFolderKey -Path ([string]$tab.tree)) -ne $treeKey) { continue }
        $moment = [datetime]::MinValue
        if ([string]$tab.prompt_at -and [datetime]::TryParse([string]$tab.prompt_at, [ref]$moment) -and $moment -ge $since) {
            return $false
        }
    }
    return $true
}

function Test-TabIsSameNamedTab {
    param([string]$Dir, [string]$SessionId, [string]$HeldSession, [string]$Tree, [string]$Stage)
    # Is this the same tab the claim names, only with a new session id.
    #
    # Clearing the context and resuming with a fork give the tab a new session id, while the name a
    # person gave it carries over. There's one sign: the recorded session has the same worktree, and
    # both carry a human-given name, non-empty and equal ignoring case. ‼️ Unnamed tabs and tabs named
    # differently NEVER match: otherwise two tabs in one worktree would pull the claim back and forth on
    # every message.
    $held = Get-TabRecord -Dir $Dir -SessionId $HeldSession
    if (-not $held -or [string]$held.title_kind -ne 'custom') { return $false }
    if ((Get-TabFolderKey -Path ([string]$held.tree)) -ne (Get-TabFolderKey -Path $Tree)) { return $false }
    $heldName = (Get-CleanTitle -Raw ([string]$held.title)).ToLowerInvariant()
    if (-not $heldName) { return $false }
    $mine = Get-TabOwnTitle -Dir $Dir -SessionId $SessionId -Stage $Stage
    if ([string]$mine.Kind -ne 'custom') { return $false }
    return ((Get-CleanTitle -Raw ([string]$mine.Title)).ToLowerInvariant() -ceq $heldName)
}

function Update-ClaimSessionFromTab {
    param($Claim, [string]$Session, [string]$Stage, [string]$TabsDir, [string]$Tree)
    # Which session runs a claim when the announcement didn't name one, or named the tab's previous id.
    # Edits the parsed claim in place and says whether it changed it; the caller writes it (the liveness
    # stamp). The caller checks whether the claim is closed — only open, not moved claims come here.
    #
    #   • Adoption: the claim has no session. Only on a human message, only in a linked worktree, and
    #     only if the session is entitled to it (`Test-TabMayAdoptClaim`). Marked `session_adopted`: it's
    #     the hook's guess, not an announcement, and the listing says so; an announcement clears the mark.
    #   • Hand-over: the claim names ANOTHER session of the same tab (`Test-TabIsSameNamedTab`) — at a
    #     session start and on a human message, in a linked worktree. Also marked as done by the hook.
    #
    # ‼️ The tab name is a hint telling a person where to go, not proof: the session id changes
    # addressing and delivery nowhere.
    if (-not $Claim -or -not $TabsDir -or -not (Test-SessionId $Session)) { return $false }
    if ($Stage -ne 'Start' -and $Stage -ne 'Prompt') { return $false }
    $held = [string]$Claim.session_id
    if ($held -ceq $Session) { return $false }
    if (-not $held) {
        if ($Stage -ne 'Prompt') { return $false }
        if (-not (Test-LinkedWorktree -TreePath $Tree)) { return $false }
        if (-not (Test-TabMayAdoptClaim -Dir $TabsDir -SessionId $Session -Tree $Tree)) { return $false }
    } else {
        if (-not (Test-SessionId $held)) { return $false }
        if (-not (Test-LinkedWorktree -TreePath $Tree)) { return $false }
        if (-not (Test-TabIsSameNamedTab -Dir $TabsDir -SessionId $Session -HeldSession $held -Tree $Tree -Stage $Stage)) { return $false }
    }
    $Claim | Add-Member -NotePropertyName session_id -NotePropertyValue $Session -Force
    $Claim | Add-Member -NotePropertyName session_adopted -NotePropertyValue $true -Force
    return $true
}
