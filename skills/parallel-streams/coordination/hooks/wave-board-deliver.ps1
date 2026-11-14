#Requires -Version 7
# TRANSITIONAL: the name the delivery hook carried before 1.19.0 — the hook itself is
# `deliver-wave-board.ps1`, next to this file. Remove no earlier than the next major version: until
# then, settings written before 1.19.0 may still call this name.
#
# Why it exists. Before 1.19.0 the documented update was "copy the new folder over the old one", with
# no installer run, and a copy deletes nothing: settings written by an older installer would either
# find nothing here (the hook silently off) or go on running a stale script against the new library.
# Running the installer again rewrites them to the new name.
#
# It hands the whole call over — arguments, standard input (not read here: the hook reads it),
# output, exit code. The code is passed on only for a failed call: a hook that ends without `exit`
# leaves its last git call's code behind, and passing that on would turn a success into a failure.
& (Join-Path $PSScriptRoot 'deliver-wave-board.ps1') @args
if (-not $?) { exit $LASTEXITCODE }
