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
import shutil
import subprocess
from pathlib import Path

import pytest

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
NUDGE = COORDINATION_DIR / "hooks" / "pretooluse-wave-board-nudge.ps1"
REAL_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
REAL_BRIDGE = REPO_ROOT / "scripts" / "wave-board.ps1"

# Where a CONSUMER project keeps the installed skill, once the installer has placed it there. This
# repository is the skill's own source and keeps it directly under `skills/`, without a `.claude/`
# prefix — so COORDINATION_DIR and REPO_ROOT / SKILL_INSIDE are two different paths here.
SKILL_INSIDE = Path(".claude") / "skills" / "parallel-streams" / "coordination"

# The stand-in project's plans folder — DELIBERATELY unlike this repository's own: the channel must
# take it from the project's profile, not from however it happens to be set up here.
PLANS = "wave-plans/"

# The channel's guard files. An "our" entry is recognized by them — both in the installer and here.
OUR_HOOK_FILES = ("wave-board-deliver.ps1", "pretooluse-wave-board-nudge.ps1")

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh not found — nothing to run the scripts with")


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
        if "pretooluse-wave-board-nudge.ps1" in hook.get("command", "")
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
    where = f"Wave plans: `{plans}`.\n" if plans else "The wave-plan folder has not been picked yet.\n"
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
        assert len(wired) == 1, f"{event} does not have exactly one delivery entry, but {len(wired)}"
        assert stage in wired[0], f"on {event} the delivery guard is not run with {stage}"

    profile = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert "## Coordination" in profile, (
        "the channel commands did not make it into the profile — sessions will never learn about it"
    )
    assert "## Plans" in profile

    bridge = project / "scripts" / "wave-board.ps1"
    assert bridge.exists(), "the bridge script was not placed — the launch command in this project would be long"
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
    assert "board" in done.stdout.lower(), f"the tool did not answer about the board: {done.stdout!r}"

    for word in ("connected", "profile", "bridge script"):
        assert word in out.lower(), f'the report says nothing about "{word}" — the work was done silently'


@needs_pwsh
def test_second_run_changes_nothing(project: Path) -> None:
    """Running it again must be a no-op — byte for byte.

    The install is called again after every skill update. Were it to place a second, identical entry,
    a guard would speak twice, and a finding would arrive at the session as a duplicate.
    """
    with_plans(project)
    install(project)
    before = snapshot(project)

    out = install(project)
    assert snapshot(project) == before, (
        "installing again changed the files, though there was nothing to change"
    )

    data = settings_of(project)
    assert len(ours(data, "SessionStart")) == 1
    assert len(ours(data, "UserPromptSubmit")) == 1
    assert len(nudge_hooks(data)) == 2, "the nudge guard's entries multiplied"
    assert "already connected" in out, "running it again said nothing about everything already being in place"


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
    assert len(wired) == 1, f"after the move there are {len(wired)} entries — the old one stayed next to it"
    assert "old/location" not in wired[0]["command"], "the entry still points at the skill's prior location"
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
        pytest.fail(f"the settings file stopped parsing — EVERY hook in the project is disconnected: {exc}")
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

    assert "rewritten" in out, "the file was rewritten in one style, and the report said nothing about it"
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
    assert "removed our prior entry" in out, "the entry was removed silently — the report does not match what was done"
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
    assert survived, "uninstalling the channel carried away the foreign hook from a folder with the same name"


@needs_pwsh
def test_profile_keeps_every_byte_and_gets_only_what_is_missing(project: Path) -> None:
    """The profile holds a human's own words: only what is missing may be appended, only at the end."""
    written = "# My profile\n\n## Tests\n\nOur own test command.\n"
    (project / ".parallel-streams.md").write_text(written, encoding="utf-8")

    install(project)
    grown = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert grown.startswith(written), "the prior profile text shifted — and it holds a human's own words"
    assert "## Coordination" in grown and "## Plans" in grown, "the missing sections were not appended"
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
    assert grown.count("## Coordination") == 1, "a second section about the same thing landed beside it"
    assert grown.count("## Plans") == 1, "the missing section was not appended, or was appended twice"
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
    assert "drafts/" not in out, "a folder from an unrelated section of the profile was named as the plans folder"
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
    assert filters == [f"Edit({PLANS}**)", f"Write({PLANS}**)"], (
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
    """
    (project / "waves").mkdir()
    (project / ".parallel-streams.md").write_text(
        "# Profile\n\n## Coordination\n\nChannel commands.\n\n"
        "## Plans\n\nWave plans: `waves/`.\n",
        encoding="utf-8",
    )
    install(project)

    filters = sorted(hook.get("if", "") for hook in nudge_hooks(settings_of(project)))
    assert filters == ["Edit(waves/**)", "Write(waves/**)"], (
        f"the nudge guard's conditions are not about the folder from the profile: {filters}"
    )

    assert pwsh
    call = json.dumps(
        {"session_id": "s-plans", "tool_input": {"file_path": "waves/wave7-draft.md"}}
    )
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(NUDGE)],
        input=call,
        cwd=str(project),
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
def test_inside_project_paths_are_written_relative(project_with_skill: Path) -> None:
    """The skill sits inside the project — the path is written from the working folder, not in full.

    A full path would tie the settings to one machine and one location of the project folder: in a
    neighbouring session's worktree, such a guard would point at someone else's copy of the skill.
    """
    install_in(project_with_skill)

    data = settings_of(project_with_skill)
    wired = ours(data, "SessionStart") + ours(data, "UserPromptSubmit") + nudge_hooks(data)
    assert len(wired) == 4, f"the number of connected entries is wrong: {len(wired)}"
    for hook in wired:
        command = hook["command"]
        assert command.startswith('& "$PWD/.claude/skills/parallel-streams/coordination/hooks/'), (
            f"the path is not written from the project's working folder: {command}"
        )

    bridge = (project_with_skill / "scripts" / "wave-board.ps1").read_text(encoding="utf-8")
    assert "$PSScriptRoot" in bridge, "the bridge script inside the project must also count its path from itself"
    assert str(project_with_skill) not in bridge, "a path specific to this one machine leaked into the bridge script"


@needs_pwsh
def test_the_bridge_of_this_project_is_the_one_the_installer_writes(
    project_with_skill: Path,
) -> None:
    """The bridge script sitting in this project is exactly the one the installer writes.

    Editing the bridge script by hand would let the launch command drift: in task briefs it is one
    command shared across every project.
    """
    assert REAL_BRIDGE.exists(), "this repository has the channel installed — its bridge script must be there"

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
    assert not (project / "scripts" / "wave-board.ps1").exists(), "the bridge script was not removed"
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
    assert "SessionStart" in out and "wave-board-deliver.ps1" in out, (
        "the status view does not say which guards are connected or where they point"
    )
    assert PLANS in out, "the status view does not name the wave-plan folder"
