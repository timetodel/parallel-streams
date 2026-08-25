#Requires -Version 7
<#
A copy kept for the skill's self-containedness: the project original lives at
`scripts/git-env-clean.ps1` and is edited there separately — do not hand-edit this copy, only port
changes over from the original.

Strips git environment variables that override folder selection from the CURRENT process.

Dot-source it BEFORE the first git call — from a hook, from the tool's shared code, from anywhere.
It prints nothing, returns nothing, and never throws.

Why this is needed at all. The -C flag picks a folder, but it does NOT override GIT_DIR: set without
GIT_WORK_TREE, it points git at someone else's .git directory while declaring the current folder the
worktree. That produces two silent reversals at once:

- "not a repository" stops ever happening: any unrelated folder answers with success and is called
  the root — and root-lookup ("where the build manifest lives", "where the board lives") ends up in
  someone else's tree or the first subfolder it finds;
- the project's own working copy becomes permanently "dirty": the whole contents of the other repo
  are listed as deleted in it, while the branch name and the worktree list come entirely from that
  other repository.

Where the contamination comes from. These variables get set while working with worktrees, on the
assumption that they'll survive a move to another folder, and hooks and scripts inherit the
long-lived session process's environment. In a clean process — in tests, on the build box — this is
NEVER visible, which is why the bug looks like "green everywhere, breaks only in real use."

There's nothing to restore: both a hook and a one-off script run live for a single command and die
with it. The file breaks nothing for a long-lived shell either — the variables are cleared in its own
process, and they'd only be needed there for exactly what we're guarding against.

‼️ We erase them FOR REAL, not blank them out. A variable left set but EMPTY is worse than the
original bug: git treats an empty .git directory as explicitly given and answers "not a git
repository: ''" in absolutely any folder, so the state of the tree can't be determined anywhere.
Measured on shell 7.6.5: the variable stays empty both from assigning an empty string and from
calling the .NET method with $null — in a string parameter, $null itself turns into an empty string,
so "clear it via the method" lands in exactly this trap. Assigning $null through $env: is what
PowerShell 7 actually treats as removal, and that would work too, but it differs from the trap by one
character. Remove-Item was chosen because its intent is spelled out in a word, and there's nothing to
mix it up with.
#>

foreach ($name in @(
        'GIT_DIR',
        'GIT_WORK_TREE',
        'GIT_CEILING_DIRECTORIES',
        'GIT_INDEX_FILE',
        'GIT_COMMON_DIR',
        'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES')) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
