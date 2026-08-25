#Requires -Version 7
<#
Bridge script: the tool moved to the skill folder (`skills/parallel-streams/coordination/`),
this file is just the call — so the launch command stays equally short in every project with the
skill: `pwsh scripts/wave-board.ps1 ...`. Logic and edits live only in the skill script, not here.
#>

$target = Join-Path $PSScriptRoot '../skills/parallel-streams/coordination/wave-board.ps1'
& $target @args
exit $LASTEXITCODE
