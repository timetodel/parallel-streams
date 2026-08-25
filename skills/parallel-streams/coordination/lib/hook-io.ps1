#Requires -Version 7
<#
A copy kept for the skill's self-containedness: the project original lives at
`scripts/hooks/hook-io.ps1` and is edited there separately — do not hand-edit this copy, only port
changes over from the original.

Shared reading of the call the environment feeds a hook on standard input.

It is always fed as UTF-8, but [Console]::In decodes the stream using the CONSOLE code page of
whichever shell the command was launched from. In the assistant's bash shell that's cp866: Cyrillic
in the path and in the command itself turns into garbage. From there the hook judges by the corrupted
data — the calling folder isn't found, the replacement pattern isn't recognized — and stays silent
about it, because every hook swallows any surprise and exits with zero. The mismatch then looks like
a difference between shells: the very same hook on the very same worktree decides differently
depending on where it's run from.

So we read the stream directly and decode UTF-8 ourselves. We leave the console's code page alone:
the console is shared with the parent shell, and changing the page from a hook would change it there
too.
#>

function Read-HookInput {
    $reader = [System.IO.StreamReader]::new(
        [Console]::OpenStandardInput(),
        [System.Text.UTF8Encoding]::new($false)
    )
    return $reader.ReadToEnd()
}
