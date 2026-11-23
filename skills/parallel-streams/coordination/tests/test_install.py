"""Channel installer: it unpacks the channel into a foreign project without breaking anything that
belongs to that project.

The installer edits files it does not own: the project settings, where someone else's guard hooks
already sit; the profile, holding a human's own words; and the `scripts/` folder, where a file with
the same name might belong to anyone. Every one of these edits can go wrong silently — a corrupted
file still looks whole, and the channel still looks connected afterward. Hence behavioural checks
against stand-in projects, not a check that files merely exist.

The properties everything rests on, checked by name:
  • a fresh install gives a WORKING channel — the bridge script runs and answers;
  • running it again changes not one byte: otherwise it piles up duplicates, and a guard would speak
    twice;
  • a skill folder that has moved gets ITS OWN prior entry fixed up, not a second one placed beside it;
  • a foreign settings file survives the run: the content and order of foreign entries stay intact, a
    foreign value containing backslashes is not mangled, and Cyrillic labels do not turn into \\uXXXX;
  • the report matches what was actually done: rewrote the file in one style — said so;
  • OUR entry that became unneeded gets removed, rather than left pointing at a dead spot;
  • a foreign hook from a folder with the same name is never counted as ours — neither at install nor
    at uninstall;
  • the guards the installer wires are found from ANY folder the tab stands in — a subfolder, a plain
    folder, someone else's repository — the copy of the tree the tab stands in first; outside the
    project they serve they say nothing and leave nothing behind;
  • an entry written in an older form is rewritten in place, and uninstall takes away every form;
  • the guard files were renamed in 1.19.0, so the command never starts a copy from before that —
    and settings written before it reach the renamed guards through the files under the old names;
  • only what is missing gets appended to the profile, not one prior byte moves;
  • without a plans folder the nudge guard does not get connected at all: connected, it would stay
    silent forever, and that would look exactly like "nothing to remind about";
  • the plans folder and the guard itself come from the profile — so the channel works in more than
    just this one project;
  • uninstalling returns the settings to their prior shape and leaves the profile to the human;
  • a foreign file standing where the bridge script belongs is never overwritten, neither at install
    nor at uninstall.

Every stand-in project is its own, and its plans folder is DELIBERATELY unlike this repository's: the
bundle promises to be portable, and a folder hard-wired to one particular project would prove exactly
the opposite. This repository's own settings are only ever taken as a copy, and only where the check
genuinely needs to compare against the live file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

# Every shape the guard's own tests require it to refuse: the bash check in front of the guard must
# hand each of them to it.
from test_wave_board import PUBLISHING_SHAPES

# The bundle does not live only here: it also exists in five other copies, and each has its own
# prefix to the coordination folder's path (for example, in the public repository —
# localization/ru/parallel-streams/coordination and skills/parallel-streams/coordination, with no
# .claude/skills/ prefix of this project at all). The old count of levels upward (parents[4]) was a
# prefix hard-wired to this one project: in a foreign copy it landed in the wrong folder, the
# installer was never found, and the whole suite failed red. The tests folder sits INSIDE
# coordination always and everywhere, so the bundle folder itself is now taken from the test file's
# own location — one level up, with no prefix counting.
COORDINATION_DIR = Path(__file__).resolve().parent.parent


def _find_repo_root(start: Path) -> Path:
    """The repository root: the first folder walking upward where a `.git` is set up.

    Needed as the working folder for the installer and to compare against this project's real files
    (REAL_SETTINGS, REAL_BRIDGE below). Counting levels upward would again be a prefix hard-wired to
    one project — different copies of the bundle sit at different depths from the root; look for the
    `.git` marker instead of counting folders. Not found — fall back to this file's own old level
    count.
    """
    for candidate in start.parents:
        if (candidate / ".git").exists():
            return candidate
    return start.parents[4]


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
INSTALLER = COORDINATION_DIR / "install.ps1"
NUDGE = COORDINATION_DIR / "hooks" / "nudge-wave-plan-edit.ps1"
REAL_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
REAL_BRIDGE = REPO_ROOT / "scripts" / "wave-board.ps1"

# Where a CONSUMER project keeps the installed skill, once the installer has placed it there. This
# repository is the skill's own source and keeps it directly under `skills/`, without a `.claude/`
# prefix — so COORDINATION_DIR and REPO_ROOT / SKILL_INSIDE are two different paths here.
SKILL_INSIDE = Path(".claude") / "skills" / "parallel-streams" / "coordination"


def wired(script: str) -> str:
    """The command the installer writes for a guard of a skill INSIDE the project, up to its arguments.

    The copy is looked for twice: in the folder the tab stands in, otherwise in the project the
    session started in (`CLAUDE_PROJECT_DIR`, which the host sets for every hook). The old form was a
    bare `$PWD/...`: a hook runs in the tab's CURRENT folder, and from a subfolder it found nothing.
    """
    rel = f"{SKILL_INSIDE.as_posix()}/hooks/{script}"
    return (
        f'& "$(if (Test-Path -LiteralPath "$PWD/{rel}") {{ "$PWD" }} '
        f'else {{ $env:CLAUDE_PROJECT_DIR }})/{rel}"'
    )


# The stand-in project's plans folder — DELIBERATELY unlike this repository's own: the channel must
# take it from the project's profile, not from however it happens to be set up here.
PLANS = "wave-plans/"

# The channel's guard files, the names the installer writes.
HOOK_FILES = (
    "deliver-wave-board.ps1",
    "nudge-wave-plan-edit.ps1",
    "guard-claim-before-publish.ps1",
)

# The names the same files carried before 1.19.0, by the current name. Renamed so that the command,
# which falls back to the project the session started in, can never start an OLD copy there — one
# that does not know where its project ends. Entries under the old names are still ours: the
# installer rewrites them to the new names and uninstall takes them away.
FORMER_NAME = {
    "deliver-wave-board.ps1": "wave-board-deliver.ps1",
    "nudge-wave-plan-edit.ps1": "pretooluse-wave-board-nudge.ps1",
    "guard-claim-before-publish.ps1": "pretooluse-claim-before-publish.ps1",
}

# An "our" entry is recognized by any of these — both in the installer and here.
OUR_HOOK_FILES = HOOK_FILES + tuple(FORMER_NAME.values())

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh not found — nothing to run the scripts with")


def _host_bash() -> str:
    """The bash a host runs a `"shell": "bash"` hook with, looked for the way the host looks for it.

    On Windows that is Git Bash, in the host's own order: CLAUDE_CODE_GIT_BASH_PATH, the two default
    Git folders, then two levels above `git` from PATH. `bash` from PATH would not do there: it can be
    the launcher of another Linux subsystem, which is not what the host runs.
    """
    if os.name != "nt":
        return shutil.which("bash") or ""
    named = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")
    candidates = []
    if named and Path(named).name.lower() in ("bash.exe", "sh.exe", "bash", "sh"):
        candidates.append(Path(named))
    candidates += [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    ]
    git_exe = shutil.which("git")
    if git_exe:
        candidates.append(Path(git_exe).parent.parent / "bin" / "bash.exe")
    return next((str(path) for path in candidates if path.exists()), "")


bash = _host_bash()
needs_bash = pytest.mark.skipif(not bash, reason="no bash the host would run hooks with")

# Both forms of the commit guard's entry, for the checks that must hold in each. Named to the
# installer explicitly: left to itself, it writes the bash form only where the settings already
# carry it.
CLAIM_SHELLS = ["PowerShell", pytest.param("Bash", marks=needs_bash)]


def git(repo: Path, *args: str) -> None:
    done = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr


def new_repo(root: Path, name: str) -> Path:
    """A stand-in project: an empty repository with nothing in it yet."""
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    git(project, "init", "-b", "main")
    return project


def stand_in_worktree(project: Path, name: str) -> Path:
    """A linked worktree of a stand-in project, inside its main folder, where the kit puts one.

    Everything in the project is committed first: a worktree sees only what is committed, the copy of
    the kit and the profile included. The commit guard steps aside in the repository's main folder —
    it is not a stream — so what the guard refuses is checked from a worktree.
    """
    git(project, "add", "-A")
    git(
        project,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=probe",
        "commit",
        "-m",
        "stand-in",
    )
    tree = project / ".claude" / "worktrees" / name
    git(project, "worktree", "add", "-b", f"feat/{name}", str(tree))
    return tree


def run_installer(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert pwsh
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(INSTALLER), *args],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def install(project: Path, *args: str) -> str:
    done = run_installer(project, *args)
    assert done.returncode == 0, done.stderr
    return done.stdout


def install_in(project: Path, *args: str) -> str:
    """Run the installer that sits INSIDE the stand-in project: paths then get written in short form."""
    assert pwsh
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(installer_in(project)), *args],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def settings_path(project: Path) -> Path:
    return project / ".claude" / "settings.json"


def settings_of(project: Path) -> dict:
    return json.loads(settings_path(project).read_text(encoding="utf-8"))


def hooks_for(data: dict, event: str) -> list[dict]:
    return [
        hook for entry in data.get("hooks", {}).get(event, []) for hook in entry.get("hooks", [])
    ]


def is_ours(hook: dict) -> bool:
    """A channel entry: its command points at OUR guard file inside the channel's hooks folder.

    The folder alone is not enough: a foreign hook that happens to be dropped into a folder with the
    same name is not ours — and uninstalling has no right to carry it away too.
    """
    command = hook.get("command", "").replace("\\", "/")
    return any(f"coordination/hooks/{name}" in command for name in OUR_HOOK_FILES)


def ours(data: dict, event: str) -> list[dict]:
    return [hook for hook in hooks_for(data, event) if is_ours(hook)]


def foreign_records(data: dict) -> list[tuple[str, str, str]]:
    """Foreign entries in order: event, matcher, the entry itself with all its fields and their order."""
    return [
        (event, entry.get("matcher", ""), json.dumps(hook, ensure_ascii=False))
        for event, entries in data.get("hooks", {}).items()
        for entry in entries
        for hook in entry.get("hooks", [])
        if not is_ours(hook)
    ]


def nudge_hooks(data: dict) -> list[dict]:
    return [
        hook
        for hook in ours(data, "PreToolUse")
        if "nudge-wave-plan-edit.ps1" in hook.get("command", "")
    ]


def claim_hooks(data: dict) -> list[dict]:
    return [
        hook
        for hook in ours(data, "PreToolUse")
        if "guard-claim-before-publish.ps1" in hook.get("command", "")
    ]


def snapshot(project: Path) -> dict[str, bytes]:
    """The bytes of all three files the installer touches."""
    files = {
        "settings": settings_path(project),
        "profile": project / ".parallel-streams.md",
        "bridge": project / "scripts" / "wave-board.ps1",
    }
    return {name: path.read_bytes() if path.exists() else b"" for name, path in files.items()}


def write_settings(project: Path, data: dict, indent: int = 2) -> None:
    """Foreign settings. Indent 2 is the same style the installer writes; anything else is a foreign style."""
    settings_path(project).parent.mkdir(parents=True, exist_ok=True)
    settings_path(project).write_text(
        json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8"
    )


def write_profile(project: Path, plans: str = PLANS) -> None:
    """The project profile: a coordination section and a plans section. An empty folder means no path."""
    where = (
        f"Wave plans: `{plans}`.\n" if plans else "The wave-plan folder has not been picked yet.\n"
    )
    (project / ".parallel-streams.md").write_text(
        f"# Profile\n\n## Coordination\n\nChannel commands.\n\n## Plans\n\n{where}",
        encoding="utf-8",
    )


def with_plans(project: Path, plans: str = PLANS) -> None:
    """The profile names the plans folder, and the folder exists — the nudge guard will connect."""
    write_profile(project, plans)
    (project / plans).mkdir(parents=True, exist_ok=True)


FOREIGN_HOOK = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/foreign-guard.ps1"',
                        "timeout": 10,
                        "statusMessage": "Foreign signature, must not be broken",
                    }
                ],
            }
        ]
    }
}

# A foreign settings file, the way another project would write it: several events, its own matchers
# and conditions, and one Cyrillic label (deliberately — see the comment on the encoding check
# below). The check here is that the run moves neither the content nor the order.
FOREIGN_SETTINGS = {
    "$schema": "https://json.schemastore.org/claude-code-settings.json",
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/session-start.ps1"',
                        "timeout": 15,
                        "statusMessage": "Готовлю рабочее место",
                    }
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Write|Edit",
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/contract-edit.ps1"',
                        "if": "Edit(contracts/**)",
                        "timeout": 20,
                    },
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/contract-edit.ps1"',
                        "if": "Write(contracts/**)",
                        "timeout": 20,
                    },
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/after-command.ps1"',
                        "timeout": 10,
                        "statusMessage": "Checking how the command finished",
                    }
                ],
            }
        ],
    },
}

# A foreign hook sitting in a folder with the same name. It is not ours: the folder name alone is not
# the mark.
FOREIGN_LOOKALIKE = {
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/tools/coordination/hooks/task-summary.ps1"',
                        "timeout": 10,
                        "statusMessage": "Foreign guard from a folder with the same name",
                    }
                ]
            }
        ]
    }
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return new_repo(tmp_path, "project")


@pytest.fixture
def project_with_skill(tmp_path: Path) -> Path:
    """A project with the skill folder INSIDE it: then paths get written in short form from `$PWD`."""
    made = new_repo(tmp_path, "project-with-skill")
    shutil.copytree(
        COORDINATION_DIR,
        made / SKILL_INSIDE,
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    with_plans(made)
    return made


def installer_in(project: Path) -> Path:
    return project / SKILL_INSIDE / "install.ps1"


def hook_env(session_project: Path, scratch: Path) -> dict[str, str]:
    """The environment the host gives a hook — and nothing of whoever runs the suite.

    `CLAUDE_PROJECT_DIR` names the folder the session started in. The host's own session id and
    config folder, and the commit guard's valve, are taken out: any of them left in place would make
    a check pass or fail for a reason that is not in it.
    """
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(session_project)
    env["CLAUDE_CONFIG_DIR"] = str(scratch / "claude-config")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("PARALLEL_STREAMS_ALLOW_UNCLAIMED", None)
    return env


def run_wired(
    hook: dict[str, object], cwd: Path, call: dict[str, object] | str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """An entry from the settings, run the way the host runs it — by the entry's own shell.

    In the tab's CURRENT folder, the call as JSON on stdin. `shell: powershell` — PowerShell 7 with
    the command. `shell: bash` — `bash -c` with the command, as the host spawns it (on Windows the
    host's Git Bash, with its folder first on PATH and `CLAUDE_PROJECT_DIR` in forward slashes, the
    way the host hands them over). A script the command fails to find shows up as a non-zero exit and
    an error on stderr — the failure that used to pass for "the guard had nothing to say".
    """
    command = str(hook["command"])
    if hook.get("shell") == "bash":
        assert bash
        env = dict(env)
        if os.name == "nt":
            env["PATH"] = str(Path(bash).parent) + os.pathsep + env.get("PATH", "")
            env["CLAUDE_PROJECT_DIR"] = env.get("CLAUDE_PROJECT_DIR", "").replace("\\", "/")
        argv = [bash, "-c", command]
    else:
        assert pwsh
        argv = [pwsh, "-NoProfile", "-Command", command]
    return subprocess.run(
        argv,
        input=call if isinstance(call, str) else json.dumps(call),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def wired_commands(project: Path) -> dict[str, dict[str, object]]:
    """The installed guard entries, by what each one does."""
    data = settings_of(project)
    found: dict[str, dict[str, object]] = {}
    for event, what in (("SessionStart", "deliver Start"), ("UserPromptSubmit", "deliver Prompt")):
        hooks = ours(data, event)
        assert len(hooks) == 1, f"{event} does not carry exactly one delivery entry: {hooks}"
        found[what] = hooks[0]
    found["claim"] = claim_hooks(data)[0]
    found["nudge"] = nudge_hooks(data)[0]
    return found


def pwsh_stand_in(folder: Path, env: dict[str, str]) -> tuple[dict[str, str], Path]:
    """A `pwsh` that records its own start: first on PATH, it writes down what came on its stdin.

    The bash check in front of the commit guard exists so that PowerShell does NOT start on calls
    that publish nothing — so whether it started is exactly what has to be seen. Returns the
    environment to run the entry in and the file that appears only when PowerShell was started.
    """
    shim = folder / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    started = folder / "pwsh-started.txt"
    stand_in = shim / "pwsh"
    # Bytes, not text: a script with `\r\n` line ends does not run in sh, and `write_text` takes no
    # `newline` before Python 3.10.
    stand_in.write_bytes(f"#!/bin/sh\ncat > '{started.as_posix()}'\n".encode())
    stand_in.chmod(0o755)
    env = dict(env)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    return env, started


def front_call(command: str) -> str:
    """A shell call as the host writes it for a hook: compact JSON, the machine's own paths around it.

    The paths are Windows paths on purpose — with a lowercase segment after a backslash
    (`C:\\Users\\user`), the case the check must not take for an escaped letter.
    """
    return json.dumps(
        {
            "session_id": "s-front",
            "transcript_path": "C:\\Users\\user\\.claude\\projects\\p\\s-front.jsonl",
            "cwd": "D:\\work\\project",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "description": "a call"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def every_entry(root: Path) -> dict[str, bytes | None]:
    """Everything under a folder, its git directory included: a file with its bytes, a folder as None."""
    return {
        path.relative_to(root).as_posix(): (path.read_bytes() if path.is_file() else None)
        for path in sorted(root.rglob("*"))
    }


@needs_pwsh
def test_fresh_install_gives_a_working_channel(project: Path) -> None:
    """A fresh project — one command, and the channel works.

    What is checked is not that files exist, but that they work: the bridge script must actually RUN
    from the project root. The path to the skill folder is computed by the installer, and a mistake
    in it would leave a file that sits in place and looks correct, yet fails when run.

    The profile here comes from the template, and it does not have to name a plans folder: delivering
    findings gets connected either way, and the nudge guard is covered by separate checks below.
    """
    out = install(project)

    data = settings_of(project)
    for event, stage in (("SessionStart", "-Stage Start"), ("UserPromptSubmit", "-Stage Prompt")):
        wired = [hook["command"] for hook in ours(data, event)]
        assert len(wired) == 1, (
            f"{event} does not have exactly one delivery entry, but {len(wired)}"
        )
        assert stage in wired[0], f"on {event} the delivery guard is not run with {stage}"

    profile = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert "## Coordination" in profile, (
        "the channel commands did not make it into the profile — sessions will never learn about it"
    )
    assert "## Plans" in profile

    bridge = project / "scripts" / "wave-board.ps1"
    assert bridge.exists(), (
        "the bridge script was not placed — the launch command in this project would be long"
    )
    assert pwsh
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", "scripts/wave-board.ps1", "-Mode", "Show"],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, f"the bridge script did not run: {done.stderr}"
    assert "board" in done.stdout.lower(), (
        f"the tool did not answer about the board: {done.stdout!r}"
    )

    for word in ("connected", "profile", "bridge script"):
        assert word in out.lower(), (
            f'the report says nothing about "{word}" — the work was done silently'
        )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_second_run_changes_nothing(project: Path, shell: str) -> None:
    """Running it again must be a no-op — byte for byte.

    The install is called again after every skill update. Were it to place a second, identical entry,
    a guard would speak twice, and a finding would arrive at the session as a duplicate.
    """
    with_plans(project)
    install(project, "-HookShell", shell)
    before = snapshot(project)

    out = install(project, "-HookShell", shell)
    assert snapshot(project) == before, (
        "installing again changed the files, though there was nothing to change"
    )

    data = settings_of(project)
    assert len(ours(data, "SessionStart")) == 1
    assert len(ours(data, "UserPromptSubmit")) == 1
    assert len(nudge_hooks(data)) == 2, "the nudge guard's entries multiplied"
    assert len(claim_hooks(data)) == 1, "the commit guard's entries multiplied"
    assert "already connected" in out, (
        "running it again said nothing about everything already being in place"
    )


@needs_pwsh
def test_moved_skill_repairs_its_own_record(project: Path) -> None:
    """The skill has moved — the prior entry gets fixed up, not duplicated.

    This is exactly the case "our entry" is recognized by the hooks folder rather than the full path
    for: otherwise a new entry would land beside the old (dead) one, and both would remain in the
    file.
    """
    write_settings(
        project,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "shell": "powershell",
                                "command": '& "$PWD/foreign/task.ps1"',
                                "timeout": 5,
                            },
                            {
                                "type": "command",
                                "shell": "powershell",
                                "command": '& "$PWD/old/location/coordination/hooks/wave-board-deliver.ps1" -Stage Start',
                                "timeout": 20,
                                "statusMessage": "Checking the wave board",
                            },
                        ]
                    }
                ]
            }
        },
    )
    out = install(project)

    data = settings_of(project)
    wired = ours(data, "SessionStart")
    assert len(wired) == 1, (
        f"after the move there are {len(wired)} entries — the old one stayed next to it"
    )
    assert "old/location" not in wired[0]["command"], (
        "the entry still points at the skill's prior location"
    )
    assert str(COORDINATION_DIR.name) in wired[0]["command"].replace("\\", "/")
    assert any("task.ps1" in hook.get("command", "") for hook in hooks_for(data, "SessionStart")), (
        "the foreign entry disappeared — the installer touched something it does not own"
    )
    assert "fixed up" in out, "fixing up the foreign path happened silently"


@needs_pwsh
@pytest.mark.parametrize("source", ["a sample foreign file", "this project's real settings"])
def test_foreign_settings_survive_the_run(project_with_skill: Path, source: str) -> None:
    """A foreign settings file survives the run: content, order, and Cyrillic labels stay intact.

    Everything comes together here: foreign entries and their order, a Cyrillic guard label (escaped
    into \\uXXXX, it would become unreadable), and recognizing entries already connected — after the
    first run, a second one must be a no-op, or every install would drown in diff noise when merged.

    The sample runs first and does not depend on this repository at all: the bundle is installed into
    foreign projects, and the check must hold there too. This project's real file is taken second,
    and only as a copy.
    """
    if source == "this project's real settings":
        if not REAL_SETTINGS.exists():
            pytest.skip("this project has no settings file of its own — nothing to compare against")
        text = REAL_SETTINGS.read_text(encoding="utf-8")
    else:
        text = json.dumps(FOREIGN_SETTINGS, ensure_ascii=False, indent=2) + "\n"
    settings_path(project_with_skill).parent.mkdir(parents=True, exist_ok=True)
    settings_path(project_with_skill).write_text(text, encoding="utf-8")
    before = foreign_records(json.loads(text))

    install_in(project_with_skill)

    assert foreign_records(settings_of(project_with_skill)) == before, (
        "foreign entries or their order changed — the installer touched something it does not own"
    )
    after = settings_path(project_with_skill).read_text(encoding="utf-8")
    # The one deliberately non-English check in this file: a Cyrillic guard label must survive as
    # Cyrillic, not turn into a \uXXXX escape — this checks byte encoding, not language, and only a
    # genuinely non-ASCII phrase can catch the escaping bug. It lives only in the synthetic sample;
    # this project's own real settings currently carry no such label to check.
    if source != "this project's real settings":
        assert "Готовлю рабочее место" in text, (
            "the sample lost its Cyrillic label — then this case proves nothing about escaping"
        )
    if "Готовлю рабочее место" in text:
        assert "Готовлю рабочее место" in after, (
            "a Cyrillic guard label is no longer readable — it got escaped into \\uXXXX"
        )

    stable = settings_path(project_with_skill).read_bytes()
    install_in(project_with_skill)
    assert settings_path(project_with_skill).read_bytes() == stable, (
        "the second run rewrote the settings, even though everything in them was already connected — "
        "the whole file would show up in the diff, and foreign entries would look touched"
    )


@needs_pwsh
def test_a_foreign_path_with_backslashes_is_not_mangled(project: Path) -> None:
    """A foreign value with backslashes must survive the run byte for byte.

    In the file the path `C:\\u0041pps\\bin` is written with doubled backslashes, and a search for
    escape sequences across the whole text mistakes the SECOND backslash for the start of `\\u0041`.
    `C:\\Apps` ends up in the file — an illegal escape sequence, after which the settings file fails
    to parse AT ALL, and along with it EVERY hook in the project silently stops working, not just
    ours.
    """
    tricky = "C:\\u0041pps\\bin"
    write_settings(
        project,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "shell": "powershell",
                                "command": f'& "{tricky}/foreign-guard.ps1"',
                                "timeout": 10,
                                "statusMessage": f"path {tricky}",
                            }
                        ],
                    }
                ]
            }
        },
    )
    with_plans(project)
    install(project)

    raw = settings_path(project).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"the settings file stopped parsing — EVERY hook in the project is disconnected: {exc}"
        )
    foreign = hooks_for(data, "PostToolUse")[0]
    assert foreign["statusMessage"] == f"path {tricky}", "the foreign value changed"
    assert tricky in foreign["command"], "the foreign path in the command changed"


@needs_pwsh
def test_the_report_admits_rewriting_the_file_in_one_style(project: Path) -> None:
    """A file written in a different style gets rewritten whole — and the report must say so.

    The content and order of foreign entries genuinely stay intact, but the layout (indentation, line
    breaks) is set by the serializer, and the whole file shows up in the diff. A human reads the
    promise "foreign entries and their order are untouched" as "the diff will be small" — and gets
    the opposite.
    """
    write_settings(project, FOREIGN_SETTINGS, indent=4)
    with_plans(project)
    before = foreign_records(FOREIGN_SETTINGS)

    out = install(project)

    assert "rewritten" in out, (
        "the file was rewritten in one style, and the report said nothing about it"
    )
    assert "the other entries and their order left untouched" not in out, (
        "the report promises more than what was actually done"
    )
    assert foreign_records(settings_of(project)) == before, (
        "a warning alone is not enough — the content and order of foreign entries must stay the same"
    )


@needs_pwsh
def test_a_record_that_is_no_longer_needed_is_taken_away(project: Path) -> None:
    """OUR entry, once it becomes unneeded, gets removed — and the report says it removed it.

    The plans folder disappeared (or the profile section got renamed) — there is nothing left for the
    nudge guard to connect to. Left in place, the prior entry would point at a dead spot for as long
    as the project lives, and that is exactly the state where the install report says "not connected"
    while the status view immediately shows it connected.
    """
    with_plans(project)
    install(project)
    assert len(nudge_hooks(settings_of(project))) == 2, "the nudge guard did not connect"

    shutil.rmtree(project / PLANS)
    out = install(project)

    assert nudge_hooks(settings_of(project)) == [], (
        "the nudge guard's prior entry stayed in the settings and points at a dead spot"
    )
    assert "removed our prior entry" in out, (
        "the entry was removed silently — the report does not match what was done"
    )
    assert "the other entries and their order left untouched" in out, (
        "the settings file was already in our style — there is nothing to warn about restyling"
    )

    check = install(project, "-Mode", "Check")
    assert "nudge on wave-plan edits" not in check, (
        "the status view shows the guard connected, even though the install said the opposite"
    )


@needs_pwsh
def test_a_foreign_hook_in_a_folder_with_the_same_name_is_never_touched(project: Path) -> None:
    """A foreign hook from a folder with the same name isn't ours: uninstalling has no right to take it.

    The "our entry" mark based on the folder name alone would be too broad — it would catch anyone's
    hook that happens to sit in a folder called `coordination/hooks/`. A missing foreign guard would
    not be noticed right away: a silent hook is indistinguishable from a working one.
    """
    write_settings(project, FOREIGN_LOOKALIKE)
    with_plans(project)

    install(project)
    survived = [
        hook
        for hook in hooks_for(settings_of(project), "SessionStart")
        if "task-summary" in hook.get("command", "")
    ]
    assert survived, "the foreign hook from the lookalike folder was already removed at install"

    install(project, "-Mode", "Uninstall")
    survived = [
        hook
        for hook in hooks_for(settings_of(project), "SessionStart")
        if "task-summary" in hook.get("command", "")
    ]
    assert survived, (
        "uninstalling the channel carried away the foreign hook from a folder with the same name"
    )


@needs_pwsh
def test_profile_keeps_every_byte_and_gets_only_what_is_missing(project: Path) -> None:
    """The profile holds a human's own words: only what is missing may be appended, only at the end."""
    written = "# My profile\n\n## Tests\n\nOur own test command.\n"
    (project / ".parallel-streams.md").write_text(written, encoding="utf-8")

    install(project)
    grown = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert grown.startswith(written), (
        "the prior profile text shifted — and it holds a human's own words"
    )
    assert "## Coordination" in grown and "## Plans" in grown, (
        "the missing sections were not appended"
    )
    assert grown.count("## Tests") == 1, "the human's own section got duplicated"

    # A second pass over the same profile: both sections already present, nothing to append.
    before = (project / ".parallel-streams.md").read_bytes()
    out = install(project)
    assert (project / ".parallel-streams.md").read_bytes() == before
    assert "already describes the channel" in out


@needs_pwsh
def test_only_the_missing_section_is_added(project: Path) -> None:
    """The channel section is already written in the human's own words — it may not be touched or duplicated."""
    written = "# My profile\n\n## Coordination\n\nHere the channel is called differently, with our own commands.\n"
    (project / ".parallel-streams.md").write_text(written, encoding="utf-8")

    out = install(project)
    grown = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert grown.startswith(written), "the existing channel section was rewritten"
    assert grown.count("## Coordination") == 1, (
        "a second section about the same thing landed beside it"
    )
    assert grown.count("## Plans") == 1, (
        "the missing section was not appended, or was appended twice"
    )
    assert "added to the profile: `## Plans`" in out, (
        f"the report names something other than what was actually appended: {out!r}"
    )


@needs_pwsh
def test_a_first_level_heading_ends_the_section(project: Path) -> None:
    """A level-one heading ends the section — it does not get pulled inside it.

    Otherwise the plans section swallows the whole rest of the document, and the plans folder becomes
    the first path found in an UNRELATED part of the profile. The guard then does get connected — to
    the wrong folder — and stays silent exactly where it is needed.
    """
    (project / "drafts").mkdir()
    (project / ".parallel-streams.md").write_text(
        "# Profile\n\n## Coordination\n\nChannel commands.\n\n"
        "## Plans\n\nThe wave-plan folder has not been picked yet.\n\n"
        "# Drafts\n\nScratch dump: `drafts/`.\n",
        encoding="utf-8",
    )

    out = install(project)

    assert nudge_hooks(settings_of(project)) == [], (
        "the nudge guard is connected to a folder from an unrelated section of the profile"
    )
    assert "drafts/" not in out, (
        "a folder from an unrelated section of the profile was named as the plans folder"
    )
    assert "not named" in out, "the report does not say the plans folder is not named"


@needs_pwsh
def test_the_section_heading_is_read_regardless_of_case(project: Path) -> None:
    """The section heading is read case-insensitively — a human writes the profile.

    Had the installer read it differently from the guard, the result would be a guard that is
    connected yet mute: the condition sits in the settings, but the guard itself does not recognize
    the plans folder in the profile.
    """
    (project / PLANS).mkdir(parents=True)
    (project / ".parallel-streams.md").write_text(
        "# Profile\n\n## coordination\n\nChannel commands.\n\n"
        f"## plans\n\nWave plans: `{PLANS}`.\n",
        encoding="utf-8",
    )

    out = install(project)

    filters = sorted(hook.get("if", "") for hook in nudge_hooks(settings_of(project)))
    assert filters == [f"Edit(//**/{PLANS}**)", f"Write(//**/{PLANS}**)"], (
        f"a plans section written in a different case was not read: {filters}"
    )
    assert "already describes the channel" in out, (
        "sections written in a different case were counted as absent — a second copy would land beside them"
    )


@needs_pwsh
@pytest.mark.parametrize("plans", ["", "<plans-folder>/"])
def test_without_a_plans_folder_the_nudge_stays_out(project: Path, plans: str) -> None:
    """No plans folder — the nudge guard is never connected at all, and we say what to fill in.

    Connected, it would not be able to tell a wave plan apart from any other file and would stay
    silent forever. A silent guard is indistinguishable from "nothing to remind about", so the
    unfinished state would look like a working one. The profile template does not name a plans
    folder — meaning that in a new project this is the normal outcome, and a human needs one line
    telling them exactly what to fill in.

    A placeholder in angle brackets counts the same as "not named": mistaking it for a real folder
    name, the installer would report on a folder that does not exist instead of asking for a real
    one.
    """
    write_profile(project, plans=plans)

    out = install(project)

    data = settings_of(project)
    assert nudge_hooks(data) == [], (
        "the nudge guard is connected even though the project has no plans folder set up — it will "
        "stay silent forever, and that would pass for working correctly"
    )
    assert ours(data, "SessionStart"), "delivering findings must be connected even without plans"
    assert len(claim_hooks(data)) == 1, (
        "the commit guard is not connected without a plans folder — yet it needs none: a stream "
        "announces itself in a project with no waves and no plans, and there the channel supplies "
        "the wave itself"
    )
    assert "nudge guard is not connected" in out, "the omission passed silently"
    assert "backtick-quoted" in out and "## Plans" in out, (
        f"the report does not say what to fill into the profile for the guard to connect: {out!r}"
    )


@needs_pwsh
def test_the_plans_folder_comes_from_the_profile(project: Path) -> None:
    """The profile names the plans folder — otherwise the channel works in exactly one project.

    Both sides are checked: the condition in the settings and the guard itself. Should they drift
    apart, the guard would end up connected to a folder it does not itself consider the plans folder,
    and stay silent.

    The condition is anchored at the root of the file system and matches the folder at any depth:
    without a leading slash the host matches it against the tab's CURRENT folder, and from a
    subfolder it never matched at all.
    """
    (project / "waves").mkdir()
    (project / ".parallel-streams.md").write_text(
        "# Profile\n\n## Coordination\n\nChannel commands.\n\n## Plans\n\nWave plans: `waves/`.\n",
        encoding="utf-8",
    )
    install(project)

    filters = sorted(hook.get("if", "") for hook in nudge_hooks(settings_of(project)))
    assert filters == ["Edit(//**/waves/**)", "Write(//**/waves/**)"], (
        f"the nudge guard's conditions are not about the folder from the profile: {filters}"
    )

    assert pwsh
    call = json.dumps(
        {"session_id": "s-plans", "tool_input": {"file_path": "waves/wave7-draft.md"}}
    )
    # The guard run here lies outside the stand-in project — a kit installed outside, which knows its
    # project from the folder the session started in.
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(NUDGE)],
        input=call,
        cwd=str(project),
        env=hook_env(project, project.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), (
        "the guard stayed silent while editing a plan in the folder the profile declares — meaning "
        "the folder is hard-wired into it, and it would be mute in a foreign project"
    )


@needs_pwsh
def test_the_commit_guard_watches_the_shell_with_no_condition(project: Path) -> None:
    """The commit guard sits on the shell tools and carries no condition of its own.

    A condition in the settings would have to spell out every shape a publishing command comes in
    (`git -C … commit`, a chain of commands, `gh pr create`) — and every shape missed is a guard that
    reads as connected while staying silent in exactly the case it exists for. The command is
    examined by the guard itself instead, and it must therefore SEE every shell call.
    """
    with_plans(project)
    install(project)

    wired = claim_hooks(settings_of(project))
    assert len(wired) == 1, f"there must be exactly one commit-guard entry, not {len(wired)}"
    assert not wired[0].get("if"), (
        f"the commit guard was given a condition — it will not see the calls it filters out: {wired[0]}"
    )
    matchers = [
        entry.get("matcher", "")
        for entry in settings_of(project)["hooks"]["PreToolUse"]
        for hook in entry.get("hooks", [])
        if "guard-claim-before-publish.ps1" in hook.get("command", "")
    ]
    assert matchers and "Bash" in matchers[0], (
        f"the commit guard is not wired to the shell tool: {matchers}"
    )


def _plain_sh() -> str:
    """A POSIX sh that is not bash — what a host may run a bash-form entry with outside Windows."""
    for name in ("dash", "sh"):
        found = shutil.which(name)
        if not found:
            continue
        probe = subprocess.run(
            [found, "-c", 'printf %s "$BASH_VERSION"'], capture_output=True, text=True
        )
        if probe.returncode == 0 and not probe.stdout:
            return found
    return ""


plain_sh = _plain_sh()


@needs_pwsh
def test_left_to_itself_the_installer_wires_the_commit_guard_in_powershell(project: Path) -> None:
    """Without `-HookShell`, a project with no entry yet gets the PowerShell form — on any machine.

    The bash form runs only where the host finds Git Bash, and the settings are shared: whether every
    Windows teammate has it is for the team to say, not for the machine the installer happens to run
    on. So the installer never picks the bash form by itself, Git Bash found here or not. The entry
    tells the host that a failure of the guard is not a refusal.
    """
    out = install(project)

    wired = claim_hooks(settings_of(project))
    assert len(wired) == 1, wired
    assert wired[0]["shell"] == "powershell", (
        f"the installer chose {wired[0]['shell']} by itself: {out}"
    )
    assert wired[0].get("onFailure") == "continue", (
        f"a failure of the guard would count as a refusal on a machine without Git Bash: {wired[0]}"
    )
    assert "the commit guard runs through PowerShell" in out, out


BASH_FORM_REQUIREMENT = "every Windows teammate has Git Bash that Claude Code can find"


@needs_pwsh
def test_a_named_form_is_kept_by_a_run_without_the_parameter(project: Path) -> None:
    """The form a team named stays in the project's settings: a run without `-HookShell` keeps it.

    The installer is run again after every update, by whoever updates, on whatever machine. Were it
    to choose the form anew, the shared file would flip with the machine: a team that chose the bash
    form would lose it, or a teammate without Git Bash would lose the guard. The entry itself is the
    record — a run without the parameter leaves the file exactly as it was, in either form, and each
    run that writes the bash form says what that form requires.
    """
    write_settings(project, FOREIGN_SETTINGS)
    out = install(project, "-HookShell", "Bash")
    assert BASH_FORM_REQUIREMENT in out, f"the report hides what the bash form requires: {out}"
    named_bash = settings_path(project).read_bytes()

    out = install(project)
    assert settings_path(project).read_bytes() == named_bash, (
        "a run without -HookShell did not keep the bash form the team named"
    )
    assert "the commit guard runs through Git Bash" in out, out
    assert BASH_FORM_REQUIREMENT in out, f"the report hides what the bash form requires: {out}"

    install(project, "-HookShell", "PowerShell")
    assert claim_hooks(settings_of(project))[0]["shell"] == "powershell"
    named_powershell = settings_path(project).read_bytes()
    out = install(project)
    assert settings_path(project).read_bytes() == named_powershell, (
        "a run without -HookShell did not keep the PowerShell form named to switch back"
    )
    assert "the commit guard runs through PowerShell" in out, out
    assert BASH_FORM_REQUIREMENT not in out, out


def hand_over_misses(
    claim: dict[str, object],
    project: Path,
    calls: dict[str, str],
    env: dict[str, str],
    started: Path,
) -> list[str]:
    """Each call through the bash form, with a stand-in PowerShell: the ones not handed over whole.

    Every call runs twice: in the environment of the run, and in the C locale, where bash folds no
    letter beyond ASCII — a hook's environment need not name a UTF-8 locale.
    """
    missed = []
    for locale, run_env in (("as run", env), ("C", {**env, "LC_ALL": "C"})):
        for what, call in calls.items():
            if started.exists():
                started.unlink()
            done = run_wired(claim, project, call, run_env)
            handed = started.read_text(encoding="utf-8") if started.exists() else None
            if done.returncode != 0 or handed != call + "\n":
                missed.append(
                    f"{what!r} (locale {locale}): code {done.returncode}, handed {handed!r}, "
                    f"{done.stderr!r}"
                )
    return missed


@needs_pwsh
@needs_bash
def test_the_bash_front_hands_every_publishing_command_to_the_guard(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Every command here that the guard refuses as publishing reaches the guard, byte for byte.

    The bash check in front of the guard is built to match a SUPERSET of the guard's pattern on the
    raw call; this is the evidence for the shapes it names. Every shape the guard's own tests require
    it to refuse goes through the installed entry, with a stand-in PowerShell that records its start
    and what it was handed. So do an upper-case command, a command whose letters are written as JSON
    escapes (`\\u0067it`), invisible to the check by themselves, and commands spelled with the dotted
    capital I (`İ`, U+0130), which the guard's match takes for `i`.
    """
    install_in(project_with_skill, "-HookShell", "Bash")
    claim = claim_hooks(settings_of(project_with_skill))[0]
    env, started = pwsh_stand_in(tmp_path, hook_env(project_with_skill, tmp_path))

    calls = {command: front_call(command) for command in PUBLISHING_SHAPES}
    calls["GIT COMMIT -m x"] = front_call("GIT COMMIT -m x")
    calls["escaped: \\u0067it push"] = front_call("git push").replace("git push", "\\u0067it push")
    calls["dotted capital I: git commİt -m x"] = front_call("git commİt -m x")
    calls["dotted capital I: gİt push"] = front_call("gİt push")
    missed = hand_over_misses(claim, project_with_skill, calls, env, started)
    assert not missed, "the bash check kept from the guard:\n" + "\n".join(missed)

    # A raw zero byte stops `read` short of the end. The host never writes one — JSON escapes it,
    # and an escape is handed over whole above — so all this shows is that the check does not end
    # such a call itself: PowerShell starts, with the part before the byte.
    if started.exists():
        started.unlink()
    cut = run_wired(claim, project_with_skill, front_call("echo hi") + "\0rest", env)
    assert cut.returncode == 0 and started.exists(), (
        f"a call stopped by a zero byte ended in the check, the guard never started: {cut.stderr!r}"
    )
    assert started.read_text(encoding="utf-8") == front_call("echo hi") + "\n"


# Every character beyond ASCII that .NET's case-insensitive match equates with a letter of the
# guard's words (`git`, `commit`, `push`, `alias`, `gh`, `pr`, `create`), over the whole Basic
# Multilingual Plane: in the culture of this run and in the Turkish and Azerbaijani ones, whose `i`
# differs. One "<code> <letter>" line each.
FOLDED_LETTERS_PROBE = r"""
$letters = 'acegilmoprstuh'
$found = [System.Collections.Generic.SortedSet[string]]::new()
foreach ($name in @([cultureinfo]::CurrentCulture.Name, 'en-US', 'tr-TR', 'az-Latn-AZ')) {
    try { [cultureinfo]::CurrentCulture = [cultureinfo]::new($name) } catch { continue }
    $any = [regex]::new('(?i)^[' + $letters + ']$')
    for ($code = 0x80; $code -le 0xFFFF; $code++) {
        if ($code -ge 0xD800 -and $code -le 0xDFFF) { continue }
        $char = [string][char]$code
        if (-not $any.IsMatch($char)) { continue }
        foreach ($letter in $letters.ToCharArray()) {
            if ([regex]::new('(?i)^' + $letter + '$').IsMatch($char)) { [void]$found.Add("$code $letter") }
        }
    }
}
$found
"""


@needs_pwsh
@needs_bash
def test_every_letter_the_guard_folds_into_its_words_reaches_the_guard(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """A publishing command spelled with a character the guard reads as its letter reaches it.

    The guard matches without regard to case, the way .NET does it: some characters beyond ASCII
    count as an ASCII letter, and which ones depends on the runtime and the culture; bash in the C
    locale folds none. So the characters are not listed here — PowerShell itself is asked, and each
    one it names is spelled into publishing commands in place of the letter it stands for.
    """
    assert pwsh
    probe = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", FOLDED_LETTERS_PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    pairs = [line.split() for line in probe.stdout.splitlines() if line.strip()]
    folded = [(chr(int(code)), letter) for code, letter in pairs]
    if not folded:
        pytest.skip(
            "this runtime equates no character beyond ASCII with a letter of the guard's words"
        )

    install_in(project_with_skill, "-HookShell", "Bash")
    claim = claim_hooks(settings_of(project_with_skill))[0]
    env, started = pwsh_stand_in(tmp_path, hook_env(project_with_skill, tmp_path))
    calls = {}
    for char, letter in folded:
        for command in ("git commit -m x", "git push", "git -c alias.x=push x", "gh pr create"):
            for at, own in enumerate(command):
                if own == letter:
                    spelled = command[:at] + char + command[at + 1 :]
                    calls[f"U+{ord(char):04X} for {letter!r}: {spelled}"] = front_call(spelled)
    missed = hand_over_misses(claim, project_with_skill, calls, env, started)
    assert not missed, "the bash check kept from the guard:\n" + "\n".join(missed)


@needs_pwsh
@needs_bash
def test_the_bash_front_starts_no_powershell_for_other_calls(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """A call that publishes nothing ends in bash: PowerShell never starts, and nothing is said.

    That is what the bash form is for — PowerShell's start is the whole cost of the guard on the
    calls it has no business with. The paths around the command are a Windows machine's own,
    `C:\\Users\\user` among them: a backslash and a lowercase `u` that are not a JSON escape.
    """
    install_in(project_with_skill, "-HookShell", "Bash")
    claim = claim_hooks(settings_of(project_with_skill))[0]
    env, started = pwsh_stand_in(tmp_path, hook_env(project_with_skill, tmp_path))

    for command in (
        "echo hi",
        "git status",
        "git log --oneline -5",
        "ls C:\\Users\\user\\work",
        "gh pr view 3",
        "pwsh -NoProfile -File build.ps1",
    ):
        done = run_wired(claim, project_with_skill, front_call(command), env)
        assert not started.exists(), f"PowerShell started for {command!r}"
        assert done.returncode == 0, f"{command!r}: {done.stderr!r}"
        assert done.stdout == "" and done.stderr == "", (
            f"{command!r}: {done.stdout!r} {done.stderr!r}"
        )


@needs_pwsh
@pytest.mark.skipif(not plain_sh, reason="no POSIX sh other than bash to run the entry with")
def test_outside_bash_the_front_hands_every_call_to_the_guard(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Run by a shell that is not bash, the bash form hands EVERY call to the guard, untouched.

    The project's settings are shared, and outside Windows the host may run the entry with sh. The
    check needs bash; without it, the entry falls back to what the PowerShell form always did —
    the guard sees every call — and reads nothing first, so the call reaches it whole.
    """
    install_in(project_with_skill, "-HookShell", "Bash")
    claim = claim_hooks(settings_of(project_with_skill))[0]
    env, started = pwsh_stand_in(tmp_path, hook_env(project_with_skill, tmp_path))

    call = front_call("echo hi")
    done = subprocess.run(
        [plain_sh, "-c", str(claim["command"])],
        input=call,
        cwd=str(project_with_skill),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert started.exists() and started.read_text(encoding="utf-8") == call, (
        "outside bash the call did not reach the guard whole"
    )


@needs_pwsh
@needs_bash
def test_switching_the_form_rewrites_the_one_entry(project: Path) -> None:
    """Run with the other form, the installer rewrites the commit guard's entry where it stands.

    The form is named by the team, and the settings are shared: the same file goes from one form to
    the other and back. Each time it must come out exactly as a fresh install of that
    form writes it — one entry, nothing left of the other form, every foreign byte in place.
    """
    second = new_repo(project.parent, "second")
    for folder in (project, second):
        write_settings(folder, FOREIGN_SETTINGS)
    install(second, "-HookShell", "Bash")
    fresh_bash = settings_path(second).read_bytes()
    install(project, "-HookShell", "PowerShell")
    fresh_powershell = settings_path(project).read_bytes()
    assert fresh_bash != fresh_powershell

    out = install(project, "-HookShell", "Bash")
    assert settings_path(project).read_bytes() == fresh_bash, (
        "going over to the bash form left a different file than a fresh install of it"
    )
    assert "fixed up" in out, "rewriting the entry in place happened silently"
    install(project, "-HookShell", "PowerShell")
    assert settings_path(project).read_bytes() == fresh_powershell, (
        "going back to the PowerShell form left a different file than a fresh install of it"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_inside_project_paths_are_written_relative(project_with_skill: Path, shell: str) -> None:
    """The skill sits inside the project — the path is written from the project, not in full.

    A full path would tie the settings to one machine and one location of the project folder: in a
    neighbouring session's worktree, such a guard would point at someone else's copy of the skill.
    And the copy is looked for in the tab's folder first, then in the project the session started in
    — a bare `$PWD/...` found nothing once the tab stepped into a subfolder. The commit guard's bash
    form looks in the same two places.
    """
    install_in(project_with_skill, "-HookShell", shell)

    data = settings_of(project_with_skill)
    entries = (
        ours(data, "SessionStart")
        + ours(data, "UserPromptSubmit")
        + nudge_hooks(data)
        + claim_hooks(data)
    )
    claim = claim_hooks(data)[0]
    commands = sorted(hook["command"] for hook in entries if hook is not claim)
    expected = sorted(
        [
            wired("deliver-wave-board.ps1") + " -Stage Start",
            wired("deliver-wave-board.ps1") + " -Stage Prompt",
            wired("nudge-wave-plan-edit.ps1"),
            wired("nudge-wave-plan-edit.ps1"),
        ]
    )
    assert commands == expected, (
        f"the guards are not written in the form that finds them from any folder: {commands}"
    )
    rel = f"{SKILL_INSIDE.as_posix()}/hooks/guard-claim-before-publish.ps1"
    if shell == "PowerShell":
        assert claim["command"] == wired("guard-claim-before-publish.ps1"), claim
    else:
        assert claim["command"].startswith(f'if [ -f "{rel}" ]; then g="{rel}"; '), claim
        assert f'elif [ -f "$CLAUDE_PROJECT_DIR/{rel}" ]' in claim["command"], claim
    machine = (str(project_with_skill), project_with_skill.as_posix())
    assert not any(path in hook["command"] for path in machine for hook in entries), (
        "a path specific to this one machine leaked into the settings"
    )

    bridge = (project_with_skill / "scripts" / "wave-board.ps1").read_text(encoding="utf-8")
    assert "$PSScriptRoot" in bridge, (
        "the bridge script inside the project must also count its path from itself"
    )
    assert str(project_with_skill) not in bridge, (
        "a path specific to this one machine leaked into the bridge script"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_the_wired_guards_work_from_a_subfolder_of_the_project(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """A session that stepped into a subfolder keeps every guard.

    The command used to be written from the bare working folder, and a hook runs in the tab's
    CURRENT folder: from a subfolder the script was not found, the hook failed with a non-blocking
    error, and the commit it was there to stop went through — with nothing to tell that apart from
    "nothing to say". So each guard is checked by what it does from there, not by having started.

    The main folder is no stream, and the commit guard steps aside in its subfolder: found there,
    it must say nothing. What it does is seen from a subfolder of a worktree of the project, where
    the command finds no copy either and falls back to the project's.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    tree_deep = stand_in_worktree(project_with_skill, "wave9-deep") / "src" / "deep"
    tree_deep.mkdir(parents=True)
    deep = project_with_skill / "src" / "deep"
    deep.mkdir(parents=True)
    env = hook_env(project_with_skill, tmp_path)

    commit = {
        "session_id": "s-deep",
        "cwd": str(deep),
        "tool_input": {"command": 'git commit -m "the work"'},
    }
    aside = run_wired(commands["claim"], deep, commit, env)
    assert aside.returncode == 0 and not aside.stderr.strip(), (
        f"the commit guard was not found from a subfolder: {aside.stderr}"
    )
    assert aside.stdout.strip() == "", (
        f"the commit guard refused a commit from a subfolder of the main folder: {aside.stdout!r}"
    )
    commit = {
        "session_id": "s-tree-deep",
        "cwd": str(tree_deep),
        "tool_input": {"command": 'git commit -m "the work"'},
    }
    refused = run_wired(commands["claim"], tree_deep, commit, env)
    assert refused.returncode == 0 and not refused.stderr.strip(), (
        f"the commit guard was not found from a subfolder of a worktree: {refused.stderr}"
    )
    assert "never announced itself" in refused.stdout, (
        f"from a subfolder the commit of an unannounced stream went through: {refused.stdout!r}"
    )

    for stage in ("Start", "Prompt"):
        call = {"session_id": f"s-deep-{stage.lower()}", "cwd": str(deep)}
        delivered = run_wired(commands[f"deliver {stage}"], deep, call, env)
        assert delivered.returncode == 0 and not delivered.stderr.strip(), (
            f"the delivery guard ({stage}) was not found from a subfolder: {delivered.stderr}"
        )
    beacon = project_with_skill / ".claude" / ".cache" / "wave-board-alive.txt"
    assert beacon.exists() and "s-deep-prompt" in beacon.read_text(encoding="utf-8"), (
        "the delivery guard did not mark its tree alive from a subfolder — neighbours would take a "
        "live session for an abandoned one"
    )
    assert not (deep / ".claude").exists(), "the guards left a state folder in the subfolder"

    plan = project_with_skill / PLANS / "2026-01-01-wave3-draft.md"
    edit = {"session_id": "s-deep-plan", "tool_input": {"file_path": str(plan)}}
    nudged = run_wired(commands["nudge"], deep, edit, env)
    assert nudged.returncode == 0 and not nudged.stderr.strip(), nudged.stderr
    assert "The wave plan is being edited" in nudged.stdout, (
        f"the nudge stayed silent on a plan edit made from a subfolder: {nudged.stdout!r}"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
@pytest.mark.parametrize("place", ["a plain folder", "a neighbouring repository"])
def test_outside_the_project_the_wired_guards_are_found_and_say_nothing(
    project_with_skill: Path, tmp_path: Path, place: str, shell: str
) -> None:
    """The tab stepped out of the project: the guards are found — and do nothing there.

    Found, because the command falls back to the project the session started in. Silent, because
    what lies outside is someone else's: delivering its board, refusing its commits or leaving a
    state folder in it would be the kit acting on a project it was never installed into. Silence is
    ambiguous, so the nudge is asked about a plan of the project itself from the same folder — and
    must speak.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    if place == "a plain folder":
        outside = tmp_path / "plain-folder"
        outside.mkdir()
    else:
        outside = new_repo(tmp_path, "neighbour")
    env = hook_env(project_with_skill, tmp_path)
    before = every_entry(outside)

    calls: dict[str, dict[str, object]] = {
        "deliver Start": {"session_id": "s-out-start", "cwd": str(outside)},
        "deliver Prompt": {"session_id": "s-out-prompt", "cwd": str(outside)},
        "claim": {
            "session_id": "s-out-claim",
            "cwd": str(outside),
            "tool_input": {"command": 'git commit -m "their work"'},
        },
    }
    for what, call in calls.items():
        done = run_wired(commands[what], outside, call, env)
        assert done.returncode == 0 and not done.stderr.strip(), (
            f"{what}: the guard was not found from {place}: {done.stderr}"
        )
        assert done.stdout.strip() == "", f"{what}: the guard spoke up in {place}: {done.stdout!r}"

    plan = project_with_skill / PLANS / "2026-01-01-wave3-draft.md"
    edit = {"session_id": "s-out-plan", "tool_input": {"file_path": str(plan)}}
    nudged = run_wired(commands["nudge"], outside, edit, env)
    assert nudged.returncode == 0 and not nudged.stderr.strip(), nudged.stderr
    assert "The wave plan is being edited" in nudged.stdout, (
        f"from {place} the nudge stayed silent on a plan of its own project: {nudged.stdout!r}"
    )

    assert every_entry(outside) == before, (
        f"the guards wrote into {place} — a folder of no project they serve"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_the_tree_the_tab_stands_in_runs_its_own_copy(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """A tab at the root of a tree that carries its own copy runs THAT copy, not the session's.

    Worktrees of one project carry their own copies of the kit, and the versions differ until every
    tree is updated: a tree's guards must be the version of that tree. Only where the tab's folder
    has no copy does the command fall back to the project the session started in. The call carries
    a commit: the commit guard's bash form starts the guard only for a command that publishes.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    tree = tmp_path / "other-tree"
    copy = tree / SKILL_INSIDE / "hooks"
    copy.mkdir(parents=True)
    for name in HOOK_FILES:
        (copy / name).write_text(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            '"the copy of the other tree, called with [$args]"\n',
            encoding="utf-8",
        )
    env = hook_env(project_with_skill, tmp_path)
    call = {"session_id": "s-tree", "tool_input": {"command": "git commit -m x"}}

    for what, command in commands.items():
        done = run_wired(command, tree, call, env)
        assert done.returncode == 0, done.stderr
        assert "the copy of the other tree" in done.stdout, (
            f"{what}: from the root of a tree with its own copy, another copy ran: {done.stdout!r}"
        )
    started = run_wired(commands["deliver Start"], tree, {"session_id": "s-tree"}, env)
    assert "called with [-Stage Start]" in started.stdout, (
        f"the tree's own copy did not get the guard's arguments: {started.stdout!r}"
    )


# The forms of our command that older versions of the kit wrote, each under the guards' names
# before 1.19.0: from the working folder (up to 1.18.1), in full (a skill installed outside the
# project), and the form that looks in two places, as it stood before the files were renamed.
OLDER_FORMS = ("from the working folder", "a full path", "looked for in two places")


def older_form(command: str, form: str, former: Path) -> str:
    """Our command as an older version of the kit wrote it — under the guard's name before 1.19.0.

    Older versions wrote every entry in PowerShell; the current command may be the commit guard's
    bash form, so the guard's name and the stage are taken from it, not its shape.
    """
    found = re.search(r"/hooks/([\w.-]+\.ps1)", command)
    assert found, f"not one of our guard commands: {command}"
    name = found.group(1)
    stage = re.search(r" -Stage \w+$", command)
    tail = stage.group(0) if stage else ""
    old = FORMER_NAME[name]
    if form == "looked for in two places":
        return wired(old) + tail
    base = (
        f"$PWD/{SKILL_INSIDE.as_posix()}"
        if form == "from the working folder"
        else former.as_posix()
    )
    return f'& "{base}/hooks/{old}"{tail}'


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
@pytest.mark.parametrize("form", OLDER_FORMS)
def test_an_entry_of_an_older_form_is_rewritten_in_place(
    project_with_skill: Path, tmp_path: Path, form: str, shell: str
) -> None:
    """Our entries in an older form are rewritten where they stand — and nothing else moves a byte.

    Every project the kit was installed into carries an old form until the installer runs again:
    from the working folder (the form that found nothing from a subfolder), a full path (a skill
    once installed outside the project), or the two-place form under the old file names. Not
    recognized as ours, any one of them would get a second entry beside it, and the guard would speak
    twice. The older file is made from the current one by replacing only our commands and
    conditions, so every foreign byte in it is the current file's own — and the run must give back
    exactly the current file, under the new names. Older versions wrote the commit guard in
    PowerShell and without `onFailure`, so the run must also switch its shell and add the field.
    """
    write_settings(project_with_skill, FOREIGN_SETTINGS)
    install_in(project_with_skill, "-HookShell", shell)
    current = settings_path(project_with_skill).read_bytes()

    older = current.decode("utf-8")
    data = json.loads(older)
    former = tmp_path / "former-place" / "coordination"
    ours_now = [
        hook
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse")
        for hook in ours(data, event)
    ]
    assert len(ours_now) == 5, f"the install did not wire all five entries: {ours_now}"
    # The two nudge entries share one command: each value is replaced once, everywhere it stands.
    replaced: dict[str, str] = {}
    # The two-place form was written with the root-anchored conditions; the older ones were not.
    anchored = form == "looked for in two places"
    for hook in ours_now:
        replaced[hook["command"]] = older_form(hook["command"], form, former)
        if hook.get("if") and not anchored:
            replaced[hook["if"]] = hook["if"].replace("//**/", "")
    for new, old in replaced.items():
        new_text = json.dumps(new, ensure_ascii=False)
        assert new_text in older, f"our value is not where the check expects it: {new_text}"
        older = older.replace(new_text, json.dumps(old, ensure_ascii=False))
    assert not any(name in older for name in HOOK_FILES), (
        "the older file still names a guard by its current name — this case checks nothing"
    )
    assert anchored or ("CLAUDE_PROJECT_DIR" not in older and "//**/" not in older), (
        "the older file still carries the current form — this case checks nothing"
    )
    older, dropped = re.subn(r',\r?\n\s*"onFailure": "continue"', "", older)
    assert dropped == 1, "the commit guard's entry does not end with `onFailure: continue`"
    if shell == "Bash":
        older, switched = re.subn(r'"shell": "bash"', '"shell": "powershell"', older)
        assert switched == 1, "the commit guard's entry is not the only one in bash"
    settings_path(project_with_skill).write_bytes(older.encode("utf-8"))

    out = install_in(project_with_skill, "-HookShell", shell)

    assert settings_path(project_with_skill).read_bytes() == current, (
        "the run did not give back exactly what a fresh install writes: our entries were not "
        "rewritten in place (a second copy beside the old one, or the old one left), or a foreign byte moved"
    )
    assert "fixed up" in out, "rewriting our entries in place happened silently"


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_uninstall_takes_away_our_entries_in_every_form(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """Uninstalling removes our entries whatever form they were written in — and only them.

    A settings file that lived through several versions of the kit can carry the same guard in more
    than one form, under either of its names. Left behind, an old form would go on failing from
    every subfolder long after the human was told the channel is gone.
    """
    write_settings(project_with_skill, FOREIGN_HOOK)
    before = settings_path(project_with_skill).read_bytes()
    install_in(project_with_skill, "-HookShell", shell)

    data = settings_of(project_with_skill)
    former = tmp_path / "former-place" / "coordination"
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
        for entry in data["hooks"][event]:
            older = [
                {**hook, "command": older_form(hook["command"], form, former)}
                for hook in entry["hooks"]
                if is_ours(hook)
                for form in OLDER_FORMS
            ]
            for hook in older:
                if hook.get("if") and "CLAUDE_PROJECT_DIR" not in hook["command"]:
                    hook["if"] = hook["if"].replace("//**/", "")
            entry["hooks"].extend(older)
    write_settings(project_with_skill, data)
    assert len(ours(settings_of(project_with_skill), "SessionStart")) == 1 + len(OLDER_FORMS)

    install_in(project_with_skill, "-Mode", "Uninstall")

    assert settings_path(project_with_skill).read_bytes() == before, (
        "after uninstalling, the settings differ from the original — an entry of ours in some form "
        "was left behind, or something foreign was rewritten"
    )


def place_older_copy(folder: Path) -> None:
    """A copy of the kit from before 1.19.0 at the consumer path of `folder`: the old names only.

    Stand-ins, not the old scripts: a stand-in that runs prints a mark and leaves a file where it
    ran, so it cannot run unnoticed. A copy of 1.18.1 or older has no file under the new names.
    """
    hooks = folder / SKILL_INSIDE / "hooks"
    hooks.mkdir(parents=True)
    for name in FORMER_NAME.values():
        (hooks / name).write_text(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "Set-Content -LiteralPath (Join-Path $PWD.Path 'older-copy-ran.txt') -Value 'ran'\n"
            '"the older copy ran"\n',
            encoding="utf-8",
        )


def calls_from(folder: Path, session: str, plan: Path) -> dict[str, dict[str, object]]:
    """One call per installed command, made from `folder`: the commit guard is given a commit."""
    return {
        "deliver Start": {"session_id": f"{session}-start", "cwd": str(folder)},
        "deliver Prompt": {"session_id": f"{session}-prompt", "cwd": str(folder)},
        "claim": {
            "session_id": f"{session}-claim",
            "cwd": str(folder),
            "tool_input": {"command": 'git commit -m "the work"'},
        },
        "nudge": {"session_id": f"{session}-plan", "tool_input": {"file_path": str(plan)}},
    }


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
@pytest.mark.parametrize("place", ["a neighbouring repository", "a plain folder"])
def test_the_command_never_starts_an_older_copy_in_the_session_project(
    project_with_skill: Path, tmp_path: Path, place: str, shell: str
) -> None:
    """The project the session started in still holds a copy from before 1.19.0: none of it runs.

    The command falls back to that project when the tab's folder has no copy. Under the old file
    names it would find the OLD copy there and run it from any folder — and an old copy has no notion
    of "not our project": in someone else's repository it delivered that repository's board, left
    its state there and refused its commits. Under the new names it finds nothing, and the hook fails
    to start, exactly as the old command did from such a folder.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    still_old = new_repo(tmp_path, "still-old")
    place_older_copy(still_old)
    if place == "a plain folder":
        outside = tmp_path / "plain-folder"
        outside.mkdir()
    else:
        outside = new_repo(tmp_path, "neighbour")
    env = hook_env(still_old, tmp_path)
    watched = {folder: every_entry(folder) for folder in (outside, still_old)}

    calls = calls_from(outside, "s-old", still_old / PLANS / "2026-01-01-wave3-draft.md")
    for what, command in commands.items():
        done = run_wired(command, outside, calls[what], env)
        assert "the older copy ran" not in done.stdout, (
            f"{what}: from {place} the command started the old copy of the session's project"
        )
        assert done.stdout.strip() == "", (
            f"{what}: something answered from {place}: {done.stdout!r}"
        )
    for folder, before in watched.items():
        assert every_entry(folder) == before, f"a hook wrote into {folder.name}"


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_a_worktree_with_an_older_copy_is_served_by_the_session_project(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """A tab at the root of a worktree whose copy is from before 1.19.0 runs the project's new copy.

    That worktree has no file under the new names, so the command falls back to the project the
    session started in — and that copy counts the worktree as its own, because every worktree of the
    project shares its git directory. The worktree is guarded as before, by the new code, until it
    takes the new copy itself. Each guard is checked by what it does there.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    # A worktree sees only what is committed: the profile has to be there for the nudge to read it.
    git(project_with_skill, "add", ".parallel-streams.md")
    git(
        project_with_skill,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=probe",
        "commit",
        "-m",
        "profile",
    )
    tree = project_with_skill / ".claude" / "worktrees" / "older-tree"
    git(project_with_skill, "worktree", "add", "-b", "feat/older-tree", str(tree))
    place_older_copy(tree)
    env = hook_env(project_with_skill, tmp_path)

    calls = calls_from(tree, "s-tree", tree / PLANS / "2026-01-01-wave3-draft.md")
    answers = {
        what: run_wired(command, tree, calls[what], env) for what, command in commands.items()
    }
    for what, done in answers.items():
        assert done.returncode == 0 and not done.stderr.strip(), f"{what}: {done.stderr}"
        assert "the older copy ran" not in done.stdout, (
            f"{what}: the worktree's old copy ran instead of the project's"
        )
    assert "never announced itself" in answers["claim"].stdout, (
        f"the commit of an unannounced stream went through in a worktree: {answers['claim'].stdout!r}"
    )
    beacon = tree / ".claude" / ".cache" / "wave-board-alive.txt"
    assert beacon.exists() and "s-tree-prompt" in beacon.read_text(encoding="utf-8"), (
        "delivery did not mark the worktree alive — the project's copy did not count it as its own"
    )
    assert "The wave plan is being edited" in answers["nudge"].stdout, (
        f"the nudge stayed silent on the worktree's own plan: {answers['nudge'].stdout!r}"
    )
    assert not (tree / "older-copy-ran.txt").exists(), "the worktree's old copy ran"


# TRANSITIONAL: checks the forwarders under the old names; remove together with them.
@needs_pwsh
def test_settings_of_an_older_version_reach_the_renamed_guards(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Settings written before 1.19.0 still reach every guard, through the files under the old names.

    Before 1.19.0 an update was "copy the new folder over the old one", with no installer run, so
    such a project still calls the guards by their old names, from the working folder. Each guard is
    checked by what it does: delivery delivers (it got `-Stage`), the commit guard refuses (it got the
    call on standard input, and its answer and exit code came back), the nudge speaks. The main
    folder is no stream, and the commit guard steps aside there: its refusal is asked for from a
    worktree that carries its own committed copy of the kit, the files under the old names included.
    """
    install_in(project_with_skill)
    commands = {
        what: {
            "shell": "powershell",
            "command": older_form(hook["command"], "from the working folder", tmp_path),
        }
        for what, hook in wired_commands(project_with_skill).items()
    }
    board = project_with_skill / ".git" / "wave-board" / "board.jsonl"
    board.parent.mkdir(parents=True)
    record = {
        "id": "aaaa0001",
        "at": datetime.now().isoformat(timespec="seconds"),
        "wave": "",
        "to": "*",
        "title": "a finding for everyone",
        "where": "",
        "from": "wave9-author",
    }
    board.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    env = hook_env(project_with_skill, tmp_path)
    tree = stand_in_worktree(project_with_skill, "wave9-older")

    plan = project_with_skill / PLANS / "2026-01-01-wave3-draft.md"
    calls = calls_from(project_with_skill, "s-older", plan)
    calls["claim"] = calls_from(tree, "s-older", plan)["claim"]
    answers = {
        what: run_wired(command, tree if what == "claim" else project_with_skill, calls[what], env)
        for what, command in commands.items()
    }
    for what, done in answers.items():
        assert done.returncode == 0 and not done.stderr.strip(), (
            f"{what}: the old name did not reach the guard: {done.stderr}"
        )
    assert "a finding for everyone" in answers["deliver Start"].stdout, (
        f"delivery under the old name delivered nothing: {answers['deliver Start'].stdout!r}"
    )
    assert "never announced itself" in answers["claim"].stdout, (
        f"the commit guard under the old name let an unannounced commit through: {answers['claim'].stdout!r}"
    )
    assert "The wave plan is being edited" in answers["nudge"].stdout, (
        f"the nudge under the old name stayed silent: {answers['nudge'].stdout!r}"
    )


# TRANSITIONAL: checks the forwarders under the old names; remove together with them.
@needs_pwsh
@pytest.mark.parametrize("name", HOOK_FILES)
def test_a_forwarder_hands_over_the_whole_call(tmp_path: Path, name: str) -> None:
    """The file under a guard's old name hands the WHOLE call to the renamed guard.

    Arguments, standard input, output and exit code. Checked against a stand-in for the guard,
    because the real guards always end with zero: a code the guard exits with comes back as it is,
    and a guard that ends without `exit` after a failed git call still comes back as a success — the
    code git left behind is not the guard's answer.
    """
    assert pwsh
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    forwarder = hooks / FORMER_NAME[name]
    shutil.copy2(COORDINATION_DIR / "hooks" / FORMER_NAME[name], forwarder)
    (hooks / name).write_text(
        "param([string]$Stage)\n"
        "$reader = [System.IO.StreamReader]::new(\n"
        "    [Console]::OpenStandardInput(), [System.Text.UTF8Encoding]::new($false))\n"
        "$call = $reader.ReadToEnd() | ConvertFrom-Json\n"
        '"stage [$Stage], rest [$args], call [$($call.probe)]"\n'
        "& git --no-such-switch 2>$null\n"
        "if ($null -ne $call.code) { exit $call.code }\n",
        encoding="utf-8",
    )
    for code, expected in ((3, 3), (None, 0)):
        done = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(forwarder), "-Stage", "Start", "more"],
            input=json.dumps({"probe": "through", "code": code}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert "stage [Start], rest [more], call [through]" in done.stdout, (
            f"the forwarder lost the arguments or the input: {done.stdout!r} {done.stderr!r}"
        )
        assert done.returncode == expected, (
            f"the guard ended with {code!r}, the forwarder with {done.returncode}"
        )


@needs_pwsh
def test_the_bridge_of_this_project_is_the_one_the_installer_writes(
    project_with_skill: Path,
) -> None:
    """The bridge script sitting in this project is exactly the one the installer writes.

    Editing the bridge script by hand would let the launch command drift: in task briefs it is one
    command shared across every project.
    """
    assert REAL_BRIDGE.exists(), (
        "this repository has the channel installed — its bridge script must be there"
    )

    # Byte-for-byte comparison only holds where the skill sits at the consumer path: the bridge
    # script spells out where the skill is, so a repository that keeps the skill elsewhere writes a
    # different — and correct — file. This repository is the skill's own source and does exactly
    # that, which is why the installer's own Check is what proves the property here.
    if COORDINATION_DIR == REPO_ROOT / SKILL_INSIDE:
        install_in(project_with_skill)
        written = (project_with_skill / "scripts" / "wave-board.ps1").read_text(encoding="utf-8")
        assert written.strip() == REAL_BRIDGE.read_text(encoding="utf-8").strip(), (
            "the bridge script in the project is not the one the installer writes — the launch command would drift"
        )
        return

    told = run_installer(REPO_ROOT, "-Mode", "Check")
    assert told.returncode == 0, told.stderr
    assert "in place and pointing at the skill folder" in told.stdout, (
        "the bridge script of this repository does not point at the skill folder — "
        f"the launch command has drifted: {told.stdout}"
    )
    assert "pointing somewhere other than" not in told.stdout, told.stdout


@needs_pwsh
def test_uninstall_gives_the_settings_back(project: Path) -> None:
    """Uninstalling returns the settings to their prior shape and leaves no empty blocks behind.

    An empty event block reads, to the eye, as a connected guard — while the profile, in contrast,
    stays in place: it holds a human's own words, and the installer has no business erasing them.
    """
    with_plans(project)
    write_settings(project, FOREIGN_HOOK)
    before = settings_path(project).read_bytes()

    install(project)
    assert settings_path(project).read_bytes() != before, "the install connected nothing"

    out = install(project, "-Mode", "Uninstall")
    assert settings_path(project).read_bytes() == before, (
        "after uninstalling, the settings differ from the original — something was left behind or rewritten"
    )
    data = settings_of(project)
    assert "SessionStart" not in data.get("hooks", {}), "an empty event block was left behind"
    assert not (project / "scripts" / "wave-board.ps1").exists(), (
        "the bridge script was not removed"
    )
    profile = project / ".parallel-streams.md"
    assert profile.exists() and "## Coordination" in profile.read_text(encoding="utf-8"), (
        "the profile was erased — and it holds a human's own words about their project"
    )
    assert "left as is" in out, "nothing was said about the profile being left in place"


@needs_pwsh
def test_a_foreign_bridge_is_never_touched(project: Path) -> None:
    """A foreign file with the same name isn't ours: it may not be overwritten or removed."""
    bridge = project / "scripts" / "wave-board.ps1"
    bridge.parent.mkdir(parents=True)
    written = "# foreign script with the same name\nWrite-Output 'its own business'\n"
    bridge.write_text(written, encoding="utf-8")

    out = install(project)
    assert bridge.read_text(encoding="utf-8") == written, "the foreign file was overwritten"
    assert "FOREIGN file" in out, "swapping out the foreign file would have gone unnoticed"

    install(project, "-Mode", "Uninstall")
    assert bridge.exists(), "uninstalling removed the foreign file"


@needs_pwsh
def test_check_only_reports(project: Path) -> None:
    """Showing status must be a silent action: it's called to look, not to fix anything."""
    with_plans(project)
    install(project)
    before = snapshot(project)

    out = install(project, "-Mode", "Check")
    assert snapshot(project) == before, "showing status changed the project's files"
    assert "SessionStart" in out and "deliver-wave-board.ps1" in out, (
        "the status view does not say which guards are connected or where they point"
    )
    assert PLANS in out, "the status view does not name the wave-plan folder"
