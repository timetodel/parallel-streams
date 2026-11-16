"""Установщик канала: разворачивает его в чужом проекте и ничего чужого при этом не ломает.

Установщик правит файлы, которые ему не принадлежат: настройки проекта, где уже стоят чужие
сторожа, профиль, где лежит текст человека, и папку `scripts/`, где может лежать чей угодно файл с
тем же именем. Каждая из этих правок портится тихо — испорченный файл выглядит целым, а канал после
неё выглядит подключённым. Отсюда проверки поведения на подставных проектах, а не сверка того, что
файлы существуют.

Свойства, на которых всё держится, проверяются поимённо:
  • свежая установка даёт РАБОТАЮЩИЙ канал — переходник запускается и отвечает;
  • повторный прогон не меняет ни байта: иначе он копит дубли, а сторож заговорит дважды;
  • переехавшая папка скилла правит СВОЮ прежнюю запись, а не кладёт рядом вторую;
  • чужой файл настроек переживает прогон: содержание и порядок чужих записей целы, чужие значения
    с обратными косыми внутри не портятся, а русские подписи не превращаются в \\uXXXX;
  • отчёт совпадает с делом: переписал файл единым стилем — сказал об этом;
  • ставшая ненужной НАША запись снимается, а не остаётся указывать в мёртвое место;
  • чужой хук из папки с таким же именем нашим не считается — ни при установке, ни при снятии;
  • подключённые установщиком сторожа находятся из ЛЮБОЙ папки, где стоит вкладка, — из подпапки,
    простой папки, чужого репозитория, — первой идёт копия дерева, где стоит вкладка; вне проекта,
    которому они служат, они ничего не говорят и ничего после себя не оставляют;
  • запись прежней формы переписывается на месте, а снятие убирает любую форму;
  • файлы сторожей в 1.19.0 переименованы, поэтому команда никогда не запускает копию старше — а
    настройки, записанные до неё, доходят до переименованных сторожей через файлы под старыми именами;
  • в профиль дописывается только недостающее, ни один прежний байт не двигается;
  • без папки планов сторож-подсказка не подключается вовсе: подключённый, он молчал бы всегда, и
    это выглядело бы как «напоминать не о чем»;
  • папку планов и сам сторож берут из профиля — значит канал работает не только в этом проекте;
  • снятие возвращает настройки к прежнему виду, а профиль оставляет человеку;
  • чужой файл на месте переходника не перезаписывается ни при установке, ни при снятии.

Подставной проект каждый раз свой, и папка планов в нём НЕ такая, как в этом репозитории: комплект
обещает переносимость, а зашитая сюда папка одного проекта проверяла бы ровно обратное. Настройки
этого репозитория берутся только копией и только там, где сверяться нужно именно с живым файлом.
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

# Все виды публикующей команды, на которые обязан отказать сам сторож: проверка на bash перед ним
# обязана отдать ему каждую.
from test_wave_board import PUBLISHING_SHAPES

# Комплект лежит не только здесь: ещё в пяти копиях, и у каждой своя приставка пути до папки
# coordination (например, в публичном репозитории — localization/ru/parallel-streams/coordination
# и skills/parallel-streams/coordination, вовсе без .claude/skills/ этого проекта). Прежний счёт
# уровней вверх (parents[5]) был зашитой приставкой именно этого проекта: в чужой копии он утыкался
# не в ту папку, установщик не находился, и весь набор падал красным. Папка tests лежит ВНУТРИ
# coordination всегда и везде, поэтому саму папку комплекта берём от файла проверки — на один
# уровень вверх, без счёта приставок.
COORDINATION_DIR = Path(__file__).resolve().parent.parent


def _find_repo_root(start: Path) -> Path:
    """Корень репозитория — первая папка вверх по дереву, где заведён `.git`.

    Нужен как рабочая папка для установщика и для сверки с настоящими файлами этого проекта
    (REAL_SETTINGS, REAL_BRIDGE ниже). Счёт уровней вверх был бы снова зашитой приставкой — у
    разных копий комплекта разная глубина до корня; ищем маркер `.git`, а не считаем папки. Не
    нашли — запасной вариант: прежний счёт уровней от файла проверки.
    """
    for candidate in start.parents:
        if (candidate / ".git").exists():
            return candidate
    return start.parents[5]


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
INSTALLER = COORDINATION_DIR / "install.ps1"
NUDGE = COORDINATION_DIR / "hooks" / "nudge-wave-plan-edit.ps1"
REAL_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
REAL_BRIDGE = REPO_ROOT / "scripts" / "wave-board.ps1"

SKILL_INSIDE = Path(".claude") / "skills" / "parallel-streams" / "coordination"


def wired(script: str) -> str:
    """Команда, которую установщик пишет для сторожа скилла ВНУТРИ проекта, — без аргументов.

    Копия ищется дважды: в папке, где стоит вкладка, иначе в проекте, где началась сессия
    (`CLAUDE_PROJECT_DIR`, его среда задаёт каждому хуку). Прежняя форма — голый `$PWD/...`: хук
    запускается в ТЕКУЩЕЙ папке вкладки, и из подпапки она не находила ничего.
    """
    rel = f"{SKILL_INSIDE.as_posix()}/hooks/{script}"
    return (
        f'& "$(if (Test-Path -LiteralPath "$PWD/{rel}") {{ "$PWD" }} '
        f'else {{ $env:CLAUDE_PROJECT_DIR }})/{rel}"'
    )


# Папка планов подставного проекта — НАРОЧНО не такая, как в этом репозитории: канал обязан браться
# из профиля проекта, а не из того, как заведено здесь.
PLANS = "планы-волн/"

# Файлы сторожей канала — имена, которые пишет установщик.
HOOK_FILES = (
    "deliver-wave-board.ps1",
    "nudge-wave-plan-edit.ps1",
    "guard-claim-before-publish.ps1",
)

# Имена, которые те же файлы носили до 1.19.0, по нынешнему имени. Сменены, чтобы команда, которая
# падает в проект, где началась сессия, никогда не запустила там СТАРУЮ копию — ту, что не знает,
# где кончается её проект. Записи под старыми именами всё равно наши: установщик переписывает их на
# новые имена, а снятие убирает.
FORMER_NAME = {
    "deliver-wave-board.ps1": "wave-board-deliver.ps1",
    "nudge-wave-plan-edit.ps1": "pretooluse-wave-board-nudge.ps1",
    "guard-claim-before-publish.ps1": "pretooluse-claim-before-publish.ps1",
}

# По любому из этих имён узнаётся «наша» запись — и в установщике, и здесь.
OUR_HOOK_FILES = HOOK_FILES + tuple(FORMER_NAME.values())

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh не найден — запускать скрипты нечем")


def _host_bash() -> str:
    """bash, которым среда запустит хук с `"shell": "bash"`, — найденный так, как ищет его среда.

    На Windows это Git Bash, в собственном порядке среды: CLAUDE_CODE_GIT_BASH_PATH, две папки Git по
    умолчанию, затем двумя уровнями выше `git` из PATH. `bash` из PATH там не годится: это может быть
    запускатель подсистемы Linux, а не то, чем запускает среда.
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
needs_bash = pytest.mark.skipif(not bash, reason="нет bash, которым среда запустила бы хуки")

# Обе формы записи сторожа фиксации — для проверок, которые обязаны держаться в каждой. Называются
# установщику явно: сам по себе он пишет форму bash, только если настройки уже её несут.
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
    """Подставной проект: пустой репозиторий, в котором ещё ничего нет."""
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
    """Прогон установщика, лежащего ВНУТРИ подставного проекта: пути тогда пишутся коротким видом."""
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
    """Запись канала: её команда ведёт в НАШ файл сторожа в папке сторожей канала.

    Одной папки для этого мало: чужой хук, случайно положенный в папку с таким же именем, своим не
    является — и снятие не имеет права уносить его с собой.
    """
    command = hook.get("command", "").replace("\\", "/")
    return any(f"coordination/hooks/{name}" in command for name in OUR_HOOK_FILES)


def ours(data: dict, event: str) -> list[dict]:
    return [hook for hook in hooks_for(data, event) if is_ours(hook)]


def foreign_records(data: dict) -> list[tuple[str, str, str]]:
    """Чужие записи по порядку: событие, отбор, сама запись со всеми полями и их порядком."""
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
    """Байты всех трёх файлов, которых касается установщик."""
    files = {
        "settings": settings_path(project),
        "profile": project / ".parallel-streams.md",
        "bridge": project / "scripts" / "wave-board.ps1",
    }
    return {name: path.read_bytes() if path.exists() else b"" for name, path in files.items()}


def write_settings(project: Path, data: dict, indent: int = 2) -> None:
    """Чужие настройки. Отступ 2 — тот же стиль, каким пишет установщик; другой — чужой стиль."""
    settings_path(project).parent.mkdir(parents=True, exist_ok=True)
    settings_path(project).write_text(
        json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8"
    )


def write_profile(project: Path, plans: str = PLANS) -> None:
    """Профиль проекта: раздел координации и раздел планов. Пустая папка — раздел без пути."""
    where = (
        f"Папка, где лежат планы волн: `{plans}`.\n" if plans else "Папку планов ещё не выбрали.\n"
    )
    (project / ".parallel-streams.md").write_text(
        f"# Профиль\n\n## Coordination\n\nКоманды канала.\n\n## Plans\n\n{where}",
        encoding="utf-8",
    )


def with_plans(project: Path, plans: str = PLANS) -> None:
    """Профиль называет папку планов, и папка заведена — сторож-подсказка подключится."""
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
                        "command": '& "$PWD/scripts/hooks/чужой-сторож.ps1"',
                        "timeout": 10,
                        "statusMessage": "Чужая подпись, её нельзя ломать",
                    }
                ],
            }
        ]
    }
}

# Чужой файл настроек, каким его пишет другой проект: несколько событий, свои отборы и условия,
# русские подписи. На нём проверяется, что прогон не двигает ни содержания, ни порядка.
FOREIGN_SETTINGS = {
    "$schema": "https://json.schemastore.org/claude-code-settings.json",
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/начало-сессии.ps1"',
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
                        "command": '& "$PWD/scripts/hooks/правка-договоров.ps1"',
                        "if": "Edit(договоры/**)",
                        "timeout": 20,
                    },
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/scripts/hooks/правка-договоров.ps1"',
                        "if": "Write(договоры/**)",
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
                        "command": '& "$PWD/scripts/hooks/после-команды.ps1"',
                        "timeout": 10,
                        "statusMessage": "Разбираю, чем кончилась команда",
                    }
                ],
            }
        ],
    },
}

# Чужой хук, лежащий в папке с таким же именем. Своим он не является: имя папки — не признак.
FOREIGN_LOOKALIKE = {
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "shell": "powershell",
                        "command": '& "$PWD/tools/coordination/hooks/сводка-по-задачам.ps1"',
                        "timeout": 10,
                        "statusMessage": "Чужой сторож из папки с таким же именем",
                    }
                ]
            }
        ]
    }
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return new_repo(tmp_path, "проект")


@pytest.fixture
def project_with_skill(tmp_path: Path) -> Path:
    """Проект, ВНУТРИ которого лежит папка скилла: тогда пути пишутся коротким видом от `$PWD`."""
    made = new_repo(tmp_path, "проект-со-скиллом")
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
    """Окружение, которое среда даёт хуку, — и ничего от того, кто гоняет набор.

    `CLAUDE_PROJECT_DIR` называет папку, где началась сессия. Свой номер сессии среды, её папка
    настроек и клапан сторожа фиксации убраны: любой из них, оставленный на месте, заставил бы
    проверку пройти или упасть по причине, которой в ней нет.
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
    """Запись из настроек — так, как её запускает среда: собственной оболочкой записи.

    В ТЕКУЩЕЙ папке вкладки, вызов — JSON на стандартный ввод. `shell: powershell` — PowerShell 7 с
    командой. `shell: bash` — `bash -c` с командой, как её запускает среда (на Windows — Git Bash
    среды, его папка первой в PATH, а `CLAUDE_PROJECT_DIR` — с прямыми косыми, как их отдаёт среда).
    Скрипт, которого команда не нашла, виден как ненулевой код и ошибка в потоке ошибок — тот самый
    сбой, что раньше сходил за «сторожу нечего сказать».
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
    """Подключённые записи сторожей — по тому, что каждая делает."""
    data = settings_of(project)
    found: dict[str, dict[str, object]] = {}
    for event, what in (("SessionStart", "deliver Start"), ("UserPromptSubmit", "deliver Prompt")):
        hooks = ours(data, event)
        assert len(hooks) == 1, f"на {event} не ровно одна запись доставки: {hooks}"
        found[what] = hooks[0]
    found["claim"] = claim_hooks(data)[0]
    found["nudge"] = nudge_hooks(data)[0]
    return found


def pwsh_stand_in(folder: Path, env: dict[str, str]) -> tuple[dict[str, str], Path]:
    """`pwsh`, который отмечает свой запуск: первый в PATH, он записывает, что пришло на его ввод.

    Проверка на bash перед сторожем фиксации существует ради того, чтобы PowerShell НЕ запускался
    на вызовах, которые ничего не публикуют, — поэтому видно должно быть именно то, запустился ли
    он. Возвращает окружение для запуска записи и файл, который появляется только при запуске
    PowerShell.
    """
    shim = folder / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    started = folder / "pwsh-started.txt"
    stand_in = shim / "pwsh"
    # Байты, а не текст: сценарий с концами строк `\r\n` в sh не идёт, а `newline` у `write_text`
    # есть только с Python 3.10.
    stand_in.write_bytes(f"#!/bin/sh\ncat > '{started.as_posix()}'\n".encode())
    stand_in.chmod(0o755)
    env = dict(env)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    return env, started


def front_call(command: str) -> str:
    """Вызов оболочки, как его пишет среда для хука: сжатый JSON, вокруг — собственные пути машины.

    Пути нарочно виндовые — с сегментом со строчной буквы после обратной косой (`C:\\Users\\user`):
    этот случай проверка не должна принять за экранированную букву.
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
    """Всё внутри папки, её служебный каталог git тоже: файл — со своими байтами, папка — None."""
    return {
        path.relative_to(root).as_posix(): (path.read_bytes() if path.is_file() else None)
        for path in sorted(root.rglob("*"))
    }


@needs_pwsh
def test_fresh_install_gives_a_working_channel(project: Path) -> None:
    """Свежий проект — одна команда, и канал работает.

    Проверяется не наличие файлов, а рабочее состояние: переходник обязан ЗАПУСКАТЬСЯ из корня
    проекта. Путь до папки скилла считается установщиком, и ошибка в нём даст файл, который лежит
    на месте и выглядит правильным, но при запуске падает.

    Профиль здесь берётся из заготовки, и папку планов она называть не обязана: доставка находок
    подключается в любом случае, а про сторожа-подсказку сказано отдельными проверками ниже.
    """
    out = install(project)

    data = settings_of(project)
    for event, stage in (("SessionStart", "-Stage Start"), ("UserPromptSubmit", "-Stage Prompt")):
        wired = [hook["command"] for hook in ours(data, event)]
        assert len(wired) == 1, f"на {event} не одна запись доставки, а {len(wired)}"
        assert stage in wired[0], f"на {event} сторож доставки запущен не с {stage}"

    profile = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert "## Coordination" in profile, (
        "в профиль не попали команды канала — вкладки о нём не узнают"
    )
    assert "## Plans" in profile

    bridge = project / "scripts" / "wave-board.ps1"
    assert bridge.exists(), "переходник не положен — команда запуска в этом проекте будет длинной"
    assert pwsh
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", "scripts/wave-board.ps1", "-Mode", "Show"],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, f"переходник не запустился: {done.stderr}"
    assert "доск" in done.stdout.lower(), f"инструмент ответил не про доску: {done.stdout!r}"

    for word in ("подключено", "профиль", "переходник"):
        assert word in out.lower(), f"в отчёте не сказано про «{word}» — работа сделана молча"


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_second_run_changes_nothing(project: Path, shell: str) -> None:
    """Повторный прогон обязан быть пустым действием — байт в байт.

    Установку зовут заново после каждого обновления скилла. Клади она вторую такую же запись —
    сторож заговорил бы дважды, а находка пришла бы во вкладку дублем.
    """
    with_plans(project)
    install(project, "-HookShell", shell)
    before = snapshot(project)

    out = install(project, "-HookShell", shell)
    assert snapshot(project) == before, (
        "повторная установка изменила файлы, хотя менять было нечего"
    )

    data = settings_of(project)
    assert len(ours(data, "SessionStart")) == 1
    assert len(ours(data, "UserPromptSubmit")) == 1
    assert len(nudge_hooks(data)) == 2, "записи сторожа-подсказки размножились"
    assert len(claim_hooks(data)) == 1, "записи сторожа фиксации размножились"
    assert "уже подключено" in out, "повторный прогон промолчал о том, что всё уже на месте"


@needs_pwsh
def test_moved_skill_repairs_its_own_record(project: Path) -> None:
    """Скилл переехал — прежняя запись правится, а не дублируется.

    Это ровно тот случай, ради которого «наша» запись узнаётся по папке сторожей, а не по полному
    пути: иначе рядом со старой (мёртвой) записью встала бы новая, и обе остались бы в файле.
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
                                "command": '& "$PWD/чужое/дело.ps1"',
                                "timeout": 5,
                            },
                            {
                                "type": "command",
                                "shell": "powershell",
                                "command": '& "$PWD/старое/место/coordination/hooks/wave-board-deliver.ps1" -Stage Start',
                                "timeout": 20,
                                "statusMessage": "Смотрю доску волны",
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
    assert len(wired) == 1, f"после переезда записей стало {len(wired)} — старая осталась рядом"
    assert "старое/место" not in wired[0]["command"], "запись всё ещё ведёт в прежнее место скилла"
    assert str(COORDINATION_DIR.name) in wired[0]["command"].replace("\\", "/")
    assert any("дело.ps1" in hook.get("command", "") for hook in hooks_for(data, "SessionStart")), (
        "чужая запись пропала — установщик тронул то, что ему не принадлежит"
    )
    assert "поправлена" in out, "правка чужого пути прошла молча"


@needs_pwsh
@pytest.mark.parametrize("source", ["образец чужого файла", "настоящие настройки этого проекта"])
def test_foreign_settings_survive_the_run(project_with_skill: Path, source: str) -> None:
    """Чужой файл настроек переживает прогон: содержание, порядок и русские подписи целы.

    Здесь сходится всё сразу: чужие записи и их порядок, русские подписи сторожей (уехав в \\uXXXX,
    они станут нечитаемыми) и опознание уже подключённых записей — после первого прогона второй
    обязан быть пустым действием, иначе каждая установка тонет в разнице при слиянии.

    Образец идёт первым и от этого репозитория не зависит вовсе: комплект ставят в чужие проекты, и
    проверка обязана работать там же. Настоящий файл проекта берётся вторым и только копией.
    """
    if source == "настоящие настройки этого проекта":
        if not REAL_SETTINGS.exists():
            pytest.skip("в этом проекте нет своего файла настроек — сверять не с чем")
        text = REAL_SETTINGS.read_text(encoding="utf-8")
    else:
        text = json.dumps(FOREIGN_SETTINGS, ensure_ascii=False, indent=2) + "\n"
    settings_path(project_with_skill).parent.mkdir(parents=True, exist_ok=True)
    settings_path(project_with_skill).write_text(text, encoding="utf-8")
    before = foreign_records(json.loads(text))

    install_in(project_with_skill)

    assert foreign_records(settings_of(project_with_skill)) == before, (
        "чужие записи или их порядок изменились — установщик тронул то, что ему не принадлежит"
    )
    after = settings_path(project_with_skill).read_text(encoding="utf-8")
    assert "Смотрю доску волны" in after, (
        "русская подпись сторожа больше не читается — её экранировало в \\uXXXX"
    )

    stable = settings_path(project_with_skill).read_bytes()
    install_in(project_with_skill)
    assert settings_path(project_with_skill).read_bytes() == stable, (
        "второй прогон переписал настройки, хотя всё в них уже было подключено — "
        "в диффе окажется весь файл, а чужие записи будут выглядеть тронутыми"
    )


@needs_pwsh
def test_a_foreign_path_with_backslashes_is_not_mangled(project: Path) -> None:
    """Чужое значение с обратными косыми обязано пережить прогон дословно.

    В файле путь `C:\\u0041pps\\bin` записан удвоенными косыми, и поиск escape-последовательностей
    по всему тексту принимает ВТОРУЮ косую за начало `\\u0041`. В файл уезжает `C:\\Apps` —
    незаконная escape-последовательность, после которой файл настроек не разбирается ЦЕЛИКОМ, и
    вместе с ним молча отключаются ВСЕ хуки проекта, а не только наши.
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
                                "command": f'& "{tricky}/чужой-сторож.ps1"',
                                "timeout": 10,
                                "statusMessage": f"путь {tricky}",
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
        pytest.fail(f"файл настроек перестал разбираться — отключены ВСЕ хуки проекта: {exc}")
    foreign = hooks_for(data, "PostToolUse")[0]
    assert foreign["statusMessage"] == f"путь {tricky}", "чужое значение изменилось"
    assert tricky in foreign["command"], "чужой путь в команде изменился"


@needs_pwsh
def test_the_report_admits_rewriting_the_file_in_one_style(project: Path) -> None:
    """Файл, написанный в другом стиле, будет переписан целиком — и отчёт обязан это сказать.

    Содержание и порядок чужих записей действительно целы, но раскладку (отступы, переносы) ставит
    сериализатор, и в диффе окажется весь файл. Обещание «чужие записи и их порядок не тронуты»
    человек читает как «дифф будет маленьким» — и получает обратное.
    """
    write_settings(project, FOREIGN_SETTINGS, indent=4)
    with_plans(project)
    before = foreign_records(FOREIGN_SETTINGS)

    out = install(project)

    assert "переписан" in out, "файл переписан единым стилем, а отчёт об этом промолчал"
    assert "не тронуты" not in out, "отчёт обещает больше, чем сделано"
    assert foreign_records(settings_of(project)) == before, (
        "предупредить мало — содержание и порядок чужих записей обязаны остаться прежними"
    )


@needs_pwsh
def test_a_record_that_is_no_longer_needed_is_taken_away(project: Path) -> None:
    """Наша запись, ставшая ненужной, снимается — и отчёт говорит, что снял.

    Пропала папка планов (или переименовали раздел профиля) — сторожа-подсказку подключать не к
    чему. Прежняя запись, оставшись, будет указывать в мёртвое место столько, сколько живёт проект,
    и это ровно то состояние, где отчёт установки говорит «не подключён», а показ состояния тут же
    выводит его подключённым.
    """
    with_plans(project)
    install(project)
    assert len(nudge_hooks(settings_of(project))) == 2, "сторож-подсказка не подключился"

    shutil.rmtree(project / PLANS)
    out = install(project)

    assert nudge_hooks(settings_of(project)) == [], (
        "прежняя запись сторожа-подсказки осталась в настройках и ведёт в мёртвое место"
    )
    assert "снята" in out, "запись снята молча — отчёт не совпадает с делом"
    assert "не тронуты" in out, (
        "файл и так был в нашем стиле — предупреждать о переписывании стиля не о чем"
    )

    check = install(project, "-Mode", "Check")
    assert "подсказка при правке плана волны" not in check, (
        "показ состояния выводит сторожа подключённым, хотя установка сказала обратное"
    )


@needs_pwsh
def test_a_foreign_hook_in_a_folder_with_the_same_name_is_never_touched(project: Path) -> None:
    """Чужой хук из папки с таким же именем не наш: снятие не имеет права уносить его с собой.

    Признак «наша запись» по одному имени папки слишком широк — под него попадает чей угодно хук,
    случайно оказавшийся в папке `coordination/hooks/`. Пропажу чужого сторожа заметят не сразу:
    молчащий хук неотличим от исправного.
    """
    write_settings(project, FOREIGN_LOOKALIKE)
    with_plans(project)

    install(project)
    survived = [
        hook
        for hook in hooks_for(settings_of(project), "SessionStart")
        if "сводка-по-задачам" in hook.get("command", "")
    ]
    assert survived, "чужой хук из похожей папки снят уже при установке"

    install(project, "-Mode", "Uninstall")
    survived = [
        hook
        for hook in hooks_for(settings_of(project), "SessionStart")
        if "сводка-по-задачам" in hook.get("command", "")
    ]
    assert survived, "снятие канала унесло с собой чужой хук из папки с таким же именем"


@needs_pwsh
def test_profile_keeps_every_byte_and_gets_only_what_is_missing(project: Path) -> None:
    """В профиле лежит текст человека: дописать можно только в конец и только недостающее."""
    written = "# Мой профиль\n\n## Tests\n\nСвоя команда проверок.\n"
    (project / ".parallel-streams.md").write_text(written, encoding="utf-8")

    install(project)
    grown = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert grown.startswith(written), "прежний текст профиля сдвинулся — а там слова человека"
    assert "## Coordination" in grown and "## Plans" in grown, "недостающие разделы не дописаны"
    assert grown.count("## Tests") == 1, "раздел человека продублирован"

    # Второй заход по тому же профилю: оба раздела на месте, дописывать нечего.
    before = (project / ".parallel-streams.md").read_bytes()
    out = install(project)
    assert (project / ".parallel-streams.md").read_bytes() == before
    assert "уже описывает канал" in out


@needs_pwsh
def test_only_the_missing_section_is_added(project: Path) -> None:
    """Раздел про канал уже написан по-своему — его нельзя ни тронуть, ни продублировать."""
    written = "# Мой профиль\n\n## Coordination\n\nУ нас канал зовётся иначе, и команды свои.\n"
    (project / ".parallel-streams.md").write_text(written, encoding="utf-8")

    out = install(project)
    grown = (project / ".parallel-streams.md").read_text(encoding="utf-8")
    assert grown.startswith(written), "существующий раздел канала переписан"
    assert grown.count("## Coordination") == 1, "рядом лёг второй раздел про то же самое"
    assert grown.count("## Plans") == 1, "недостающий раздел не дописан или дописан дважды"
    assert "дописано в профиль: «## Plans»" in out, (
        f"отчёт называет дописанным не то, что дописано на самом деле: {out!r}"
    )


@needs_pwsh
def test_a_first_level_heading_ends_the_section(project: Path) -> None:
    """Заголовок первого уровня обрывает раздел, а не втягивается внутрь него.

    Иначе раздел планов забирает весь остаток документа, и папкой планов становится первый путь из
    ЧУЖОЙ части профиля. Сторож при этом подключается — к чужой папке, — и молчит ровно там, где
    он и нужен.
    """
    (project / "черновики").mkdir()
    (project / ".parallel-streams.md").write_text(
        "# Профиль\n\n## Coordination\n\nКоманды канала.\n\n"
        "## Plans\n\nПапку планов волн ещё не выбрали.\n\n"
        "# Черновики\n\nСвалка: `черновики/`.\n",
        encoding="utf-8",
    )

    out = install(project)

    assert nudge_hooks(settings_of(project)) == [], (
        "сторож-подсказка подключён к папке из чужого раздела профиля"
    )
    assert "черновики/" not in out, "папкой планов названа папка из чужого раздела профиля"
    assert "не названа" in out, "отчёт не говорит, что папка планов не названа"


@needs_pwsh
def test_the_section_heading_is_read_regardless_of_case(project: Path) -> None:
    """Заголовок раздела читается без учёта регистра — профиль пишет человек.

    Прочитай его установщик иначе, чем сторож, — получился бы подключённый и немой сторож: условие
    в настройках стоит, а сам он папку планов в профиле не узнаёт.
    """
    (project / PLANS).mkdir(parents=True)
    (project / ".parallel-streams.md").write_text(
        "# Профиль\n\n## coordination\n\nКоманды канала.\n\n"
        f"## plans\n\nПапка, где лежат планы волн: `{PLANS}`.\n",
        encoding="utf-8",
    )

    out = install(project)

    filters = sorted(hook.get("if", "") for hook in nudge_hooks(settings_of(project)))
    assert filters == [f"Edit(//**/{PLANS}**)", f"Write(//**/{PLANS}**)"], (
        f"раздел планов, написанный другим регистром, не прочитан: {filters}"
    )
    assert "уже описывает канал" in out, (
        "разделы, написанные другим регистром, сочтены отсутствующими — рядом лягут вторые такие же"
    )


@needs_pwsh
@pytest.mark.parametrize("plans", ["", "<папка-планов>/"])
def test_without_a_plans_folder_the_nudge_stays_out(project: Path, plans: str) -> None:
    """Нет папки планов — сторожа-подсказку не подключаем вовсе, и говорим, что вписать.

    Подключённый, он не отличил бы план волны от любого другого файла и молчал бы всегда. Молчание
    сторожа неотличимо от «напоминать не о чем», поэтому недоделка выглядела бы как работа. Заготовка
    профиля папку планов не называет — значит в новом проекте это штатный исход, и человеку надо
    одной строкой сказать, что именно вписать.

    Место под заполнение в угловых скобках — то же самое «не названа»: приняв его за имя папки,
    установщик отчитался бы про несуществующую папку вместо того, чтобы попросить вписать свою.
    """
    write_profile(project, plans=plans)

    out = install(project)

    data = settings_of(project)
    assert nudge_hooks(data) == [], (
        "сторож-подсказка подключён, хотя папка планов в проекте не заведена — он будет молчать "
        "всегда, и это сойдёт за исправную работу"
    )
    assert ours(data, "SessionStart"), "доставка находок обязана подключаться и без планов"
    assert len(claim_hooks(data)) == 1, (
        "сторож фиксации не подключён без папки планов — а она ему не нужна: поток объявляется и в "
        "проекте, где нет ни волн, ни планов, и там канал сам даёт волну"
    )
    assert "сторож-подсказка не подключён" in out, "пропуск прошёл молча"
    assert "обратными кавычками" in out and "## Plans" in out, (
        f"отчёт не говорит, что вписать в профиль, чтобы сторож подключился: {out!r}"
    )


@needs_pwsh
def test_the_plans_folder_comes_from_the_profile(project: Path) -> None:
    """Папку планов называет профиль — иначе канал работает ровно в одном проекте.

    Проверяются обе стороны: условие в настройках и сам сторож. Разъедься они — сторож окажется
    подключённым к папке, которую сам не считает папкой планов, и промолчит.

    Условие привязано к корню файловой системы и ловит папку на любой глубине: без ведущей косой
    черты среда сверяет его с ТЕКУЩЕЙ папкой вкладки, и из подпапки оно не срабатывало вовсе.
    """
    (project / "waves").mkdir()
    (project / ".parallel-streams.md").write_text(
        "# Профиль\n\n## Coordination\n\nКоманды канала.\n\n"
        "## Plans\n\nПапка, где лежат планы волн: `waves/`.\n",
        encoding="utf-8",
    )
    install(project)

    filters = sorted(hook.get("if", "") for hook in nudge_hooks(settings_of(project)))
    assert filters == ["Edit(//**/waves/**)", "Write(//**/waves/**)"], (
        f"условия сторожа-подсказки не про папку из профиля: {filters}"
    )

    assert pwsh
    call = json.dumps(
        {"session_id": "s-plans", "tool_input": {"file_path": "waves/wave7-проба.md"}}
    )
    # Запускаемый здесь сторож лежит вне подставного проекта — это набор, поставленный снаружи, и
    # свой проект он знает по папке, где началась сессия.
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
        "сторож промолчал при правке плана в папке, объявленной профилем, — значит папка в нём "
        "зашита, и в чужом проекте он немой"
    )


@needs_pwsh
def test_the_commit_guard_watches_the_shell_with_no_condition(project: Path) -> None:
    """Сторож фиксации стоит на инструментах оболочки и без собственного условия.

    Условие в настройках пришлось бы расписать на все виды публикующей команды (`git -C … commit`,
    цепочка команд, `gh pr create`), и каждый пропущенный вид — это сторож, который читается
    подключённым и молчит ровно в том случае, ради которого заведён. Команду вместо этого разбирает
    сам сторож, а значит он обязан ВИДЕТЬ каждый вызов оболочки.
    """
    with_plans(project)
    install(project)

    wired = claim_hooks(settings_of(project))
    assert len(wired) == 1, f"запись сторожа фиксации должна быть ровно одна, а не {len(wired)}"
    assert not wired[0].get("if"), (
        f"сторожу фиксации задали условие — отсеянных им вызовов он не увидит: {wired[0]}"
    )
    matchers = [
        entry.get("matcher", "")
        for entry in settings_of(project)["hooks"]["PreToolUse"]
        for hook in entry.get("hooks", [])
        if "guard-claim-before-publish.ps1" in hook.get("command", "")
    ]
    assert matchers and "Bash" in matchers[0], (
        f"сторож фиксации не подключён к инструменту оболочки: {matchers}"
    )


def _plain_sh() -> str:
    """POSIX sh, который не bash, — чем среда может запустить запись формы bash вне Windows."""
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
    """Без `-HookShell` проект, где записи ещё нет, получает форму PowerShell — на любой машине.

    Форма bash работает только там, где среда находит Git Bash, а настройки общие: есть ли он у
    каждого участника на Windows, решать команде, а не машине, на которой случилось запустить
    установщик. Поэтому сам установщик форму bash не выбирает никогда, найден здесь Git Bash или нет.
    Запись говорит среде, что сбой сторожа — не отказ.
    """
    out = install(project)

    wired = claim_hooks(settings_of(project))
    assert len(wired) == 1, wired
    assert wired[0]["shell"] == "powershell", f"установщик сам выбрал {wired[0]['shell']}: {out}"
    assert wired[0].get("onFailure") == "continue", (
        f"сбой сторожа на машине без Git Bash считался бы отказом: {wired[0]}"
    )
    assert "сторож фиксации — через PowerShell" in out, out


BASH_FORM_REQUIREMENT = "у каждого участника на Windows был Git Bash, который находит Claude Code"


@needs_pwsh
def test_a_named_form_is_kept_by_a_run_without_the_parameter(project: Path) -> None:
    """Форма, названная командой, остаётся в настройках проекта: прогон без `-HookShell` её сохраняет.

    Установщик запускают заново после каждого обновления — кто обновляет и на какой угодно машине.
    Выбирай он форму каждый раз заново, общий файл переключался бы вместе с машиной: команда, выбравшая
    форму bash, теряла бы её, а участник без Git Bash — сторожа. Запись сама и есть отметка выбора:
    прогон без параметра оставляет файл ровно таким, каким он был, в любой из форм, а каждый прогон,
    который пишет форму bash, говорит, что эта форма требует.
    """
    write_settings(project, FOREIGN_SETTINGS)
    out = install(project, "-HookShell", "Bash")
    assert BASH_FORM_REQUIREMENT in out, f"отчёт умалчивает, что требует форма bash: {out}"
    named_bash = settings_path(project).read_bytes()

    out = install(project)
    assert settings_path(project).read_bytes() == named_bash, (
        "прогон без -HookShell не сохранил форму bash, названную командой"
    )
    assert "сторож фиксации — через Git Bash" in out, out
    assert BASH_FORM_REQUIREMENT in out, f"отчёт умалчивает, что требует форма bash: {out}"

    install(project, "-HookShell", "PowerShell")
    assert claim_hooks(settings_of(project))[0]["shell"] == "powershell"
    named_powershell = settings_path(project).read_bytes()
    out = install(project)
    assert settings_path(project).read_bytes() == named_powershell, (
        "прогон без -HookShell не сохранил форму PowerShell, названную, чтобы вернуться"
    )
    assert "сторож фиксации — через PowerShell" in out, out
    assert BASH_FORM_REQUIREMENT not in out, out


def hand_over_misses(
    claim: dict[str, object],
    project: Path,
    calls: dict[str, str],
    env: dict[str, str],
    started: Path,
) -> list[str]:
    """Каждый вызов — через форму bash, с подставным PowerShell: те, что не отданы целыми.

    Каждый вызов идёт дважды: в окружении прогона и в локали C, где bash не сводит регистр ни одной
    буквы за пределами ASCII, — окружение хука не обязано называть локаль UTF-8.
    """
    missed = []
    for locale, run_env in (("прогона", env), ("C", {**env, "LC_ALL": "C"})):
        for what, call in calls.items():
            if started.exists():
                started.unlink()
            done = run_wired(claim, project, call, run_env)
            handed = started.read_text(encoding="utf-8") if started.exists() else None
            if done.returncode != 0 or handed != call + "\n":
                missed.append(
                    f"{what!r} (локаль {locale}): код {done.returncode}, отдано {handed!r}, "
                    f"{done.stderr!r}"
                )
    return missed


@needs_pwsh
@needs_bash
def test_the_bash_front_hands_every_publishing_command_to_the_guard(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Каждая команда здесь, на которую сторож отказывает как на публикующую, доходит до него байт в байт.

    Проверка на bash перед сторожем построена так, чтобы сличать с сырым вызовом НАДМНОЖЕСТВО образца
    сторожа; здесь — доказательство для видов, которые названы. Каждый вид, на который обязан
    отказать сам сторож по его собственным проверкам, проходит через установленную запись, с
    подставным PowerShell, который отмечает свой запуск и то, что ему отдали. Так же проходят команда
    заглавными буквами, команда, чьи буквы записаны экранированием JSON (`\\u0067it`), сами по себе
    невидимые проверке, и команды, написанные через заглавную I с точкой (`İ`, U+0130), которую
    сравнение сторожа принимает за `i`.
    """
    install_in(project_with_skill, "-HookShell", "Bash")
    claim = claim_hooks(settings_of(project_with_skill))[0]
    env, started = pwsh_stand_in(tmp_path, hook_env(project_with_skill, tmp_path))

    calls = {command: front_call(command) for command in PUBLISHING_SHAPES}
    calls["GIT COMMIT -m x"] = front_call("GIT COMMIT -m x")
    calls["экранировано: \\u0067it push"] = front_call("git push").replace(
        "git push", "\\u0067it push"
    )
    calls["заглавная I с точкой: git commİt -m x"] = front_call("git commİt -m x")
    calls["заглавная I с точкой: gİt push"] = front_call("gİt push")
    missed = hand_over_misses(claim, project_with_skill, calls, env, started)
    assert not missed, "проверка на bash не отдала сторожу:\n" + "\n".join(missed)

    # Сырой нулевой байт останавливает `read`, не дав дочитать. Среда такого не пишет никогда — JSON
    # его экранирует, а экранирование выше отдаётся целым, — так что здесь видно лишь, что проверка
    # не обрывает такой вызов сама: PowerShell запускается, с частью до этого байта.
    if started.exists():
        started.unlink()
    cut = run_wired(claim, project_with_skill, front_call("echo hi") + "\0rest", env)
    assert cut.returncode == 0 and started.exists(), (
        f"вызов, остановленный нулевым байтом, кончился в проверке, сторож не запущен: {cut.stderr!r}"
    )
    assert started.read_text(encoding="utf-8") == front_call("echo hi") + "\n"


# Каждый знак за пределами ASCII, который сравнение .NET без учёта регистра приравнивает к букве слов
# сторожа (`git`, `commit`, `push`, `alias`, `gh`, `pr`, `create`), во всей базовой многоязычной
# плоскости Юникода: в культуре этого прогона и в турецкой и азербайджанской, где `i` своя. По строке
# «<код> <буква>» на каждый.
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
    """Публикующая команда, написанная знаком, который сторож читает как свою букву, доходит до него.

    Сторож сравнивает без учёта регистра так, как это делает .NET: некоторые знаки за пределами ASCII
    идут за букву ASCII, и какие именно — зависит от среды выполнения и культуры; bash в локали C не
    сводит ни одного. Поэтому знаки здесь не перечислены — спрашивается сам PowerShell, и каждый
    названный им знак вписывается в публикующие команды вместо буквы, за которую он идёт.
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
            "эта среда выполнения не приравнивает к буквам слов сторожа ни одного знака вне ASCII"
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
                    calls[f"U+{ord(char):04X} вместо {letter!r}: {spelled}"] = front_call(spelled)
    missed = hand_over_misses(claim, project_with_skill, calls, env, started)
    assert not missed, "проверка на bash не отдала сторожу:\n" + "\n".join(missed)


@needs_pwsh
@needs_bash
def test_the_bash_front_starts_no_powershell_for_other_calls(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Вызов, который ничего не публикует, кончается в bash: PowerShell не запускается, слов нет.

    Ради этого форма bash и есть — запуск PowerShell и есть вся цена сторожа на вызовах, до которых
    ему нет дела. Пути вокруг команды — собственные пути машины с Windows, среди них `C:\\Users\\user`:
    обратная косая и строчная `u`, которые не экранирование JSON.
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
        assert not started.exists(), f"PowerShell запустился на {command!r}"
        assert done.returncode == 0, f"{command!r}: {done.stderr!r}"
        assert done.stdout == "" and done.stderr == "", (
            f"{command!r}: {done.stdout!r} {done.stderr!r}"
        )


@needs_pwsh
@pytest.mark.skipif(not plain_sh, reason="нет POSIX sh, кроме bash, чтобы запустить запись")
def test_outside_bash_the_front_hands_every_call_to_the_guard(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Запущенная не в bash, форма bash отдаёт сторожу КАЖДЫЙ вызов, нетронутым.

    Настройки проекта общие, и вне Windows среда может запустить запись в sh. Проверке нужен bash;
    без него запись откатывается к тому, что всегда делала форма PowerShell, — сторож видит каждый
    вызов, — и ничего не читает заранее, так что вызов доходит до него целым.
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
        "вне bash вызов не дошёл до сторожа целым"
    )


@needs_pwsh
@needs_bash
def test_switching_the_form_rewrites_the_one_entry(project: Path) -> None:
    """Прогнанный с другой формой, установщик переписывает запись сторожа фиксации там, где она стоит.

    Форму называет команда, а настройки общие: один и тот же файл ходит из одной формы в другую и
    обратно. Каждый раз он обязан выйти ровно таким, каким его пишет свежая
    установка этой формы, — одна запись, ничего от другой формы, каждый чужой байт на месте.
    """
    second = new_repo(project.parent, "второй")
    for folder in (project, second):
        write_settings(folder, FOREIGN_SETTINGS)
    install(second, "-HookShell", "Bash")
    fresh_bash = settings_path(second).read_bytes()
    install(project, "-HookShell", "PowerShell")
    fresh_powershell = settings_path(project).read_bytes()
    assert fresh_bash != fresh_powershell

    out = install(project, "-HookShell", "Bash")
    assert settings_path(project).read_bytes() == fresh_bash, (
        "переход на форму bash оставил не тот файл, что пишет её свежая установка"
    )
    assert "поправлена запись" in out, "переписка записи на месте прошла молча"
    install(project, "-HookShell", "PowerShell")
    assert settings_path(project).read_bytes() == fresh_powershell, (
        "возврат к форме PowerShell оставил не тот файл, что пишет её свежая установка"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_inside_project_paths_are_written_relative(project_with_skill: Path, shell: str) -> None:
    """Скилл лежит внутри проекта — путь пишется от проекта, а не полным.

    Полный путь привязал бы настройки к одной машине и к одному месту папки проекта: в рабочем
    дереве соседней вкладки такой сторож ведёт в чужую копию скилла. А копия ищется сначала в папке
    вкладки, потом в проекте, где началась сессия, — голый `$PWD/...` не находил ничего, стоило
    вкладке шагнуть в подпапку. Форма bash сторожа фиксации ищет в тех же двух местах.
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
        f"сторожа записаны не в той форме, что находит их из любой папки: {commands}"
    )
    rel = f"{SKILL_INSIDE.as_posix()}/hooks/guard-claim-before-publish.ps1"
    if shell == "PowerShell":
        assert claim["command"] == wired("guard-claim-before-publish.ps1"), claim
    else:
        assert claim["command"].startswith(f'if [ -f "{rel}" ]; then g="{rel}"; '), claim
        assert f'elif [ -f "$CLAUDE_PROJECT_DIR/{rel}" ]' in claim["command"], claim
    machine = (str(project_with_skill), project_with_skill.as_posix())
    assert not any(path in hook["command"] for path in machine for hook in entries), (
        "в настройки попал путь этой конкретной машины"
    )

    bridge = (project_with_skill / "scripts" / "wave-board.ps1").read_text(encoding="utf-8")
    assert "$PSScriptRoot" in bridge, "переходник внутри проекта тоже обязан считать путь от себя"
    assert str(project_with_skill) not in bridge, "в переходник попал путь этой конкретной машины"


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_the_wired_guards_work_from_a_subfolder_of_the_project(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """Сессия, шагнувшая в подпапку, сохраняет всех своих сторожей.

    Команда писалась от голой рабочей папки, а хук запускается в ТЕКУЩЕЙ папке вкладки: из подпапки
    скрипт не находился, хук падал с неблокирующей ошибкой, и фиксация, которую он должен был
    остановить, проходила — и отличить это от «сказать нечего» было нечем. Поэтому каждый сторож
    проверяется по тому, что он оттуда делает, а не по тому, что запустился.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    deep = project_with_skill / "src" / "deep"
    deep.mkdir(parents=True)
    env = hook_env(project_with_skill, tmp_path)

    commit = {
        "session_id": "s-deep",
        "cwd": str(deep),
        "tool_input": {"command": 'git commit -m "работа"'},
    }
    refused = run_wired(commands["claim"], deep, commit, env)
    assert refused.returncode == 0 and not refused.stderr.strip(), (
        f"сторож фиксации не нашёлся из подпапки: {refused.stderr}"
    )
    assert "этот поток не объявлялся" in refused.stdout, (
        f"из подпапки фиксация необъявленного потока прошла: {refused.stdout!r}"
    )

    for stage in ("Start", "Prompt"):
        call = {"session_id": f"s-deep-{stage.lower()}", "cwd": str(deep)}
        delivered = run_wired(commands[f"deliver {stage}"], deep, call, env)
        assert delivered.returncode == 0 and not delivered.stderr.strip(), (
            f"сторож доставки ({stage}) не нашёлся из подпапки: {delivered.stderr}"
        )
    beacon = project_with_skill / ".claude" / ".cache" / "wave-board-alive.txt"
    assert beacon.exists() and "s-deep-prompt" in beacon.read_text(encoding="utf-8"), (
        "сторож доставки не отметил своё дерево живым из подпапки — соседи примут живую вкладку за "
        "брошенную"
    )
    assert not (deep / ".claude").exists(), "сторожа оставили папку состояния в подпапке"

    plan = project_with_skill / PLANS / "2026-01-01-wave3-черновик.md"
    edit = {"session_id": "s-deep-plan", "tool_input": {"file_path": str(plan)}}
    nudged = run_wired(commands["nudge"], deep, edit, env)
    assert nudged.returncode == 0 and not nudged.stderr.strip(), nudged.stderr
    assert "Правится план волны" in nudged.stdout, (
        f"подсказка промолчала при правке плана из подпапки: {nudged.stdout!r}"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
@pytest.mark.parametrize("place", ["простая папка", "соседний репозиторий"])
def test_outside_the_project_the_wired_guards_are_found_and_say_nothing(
    project_with_skill: Path, tmp_path: Path, place: str, shell: str
) -> None:
    """Вкладка вышла из проекта: сторожа находятся — и ничего там не делают.

    Находятся, потому что команда отступает к проекту, где началась сессия. Молчат, потому что
    снаружи — чужое: принести его доску, отказать его фиксациям или оставить в нём папку состояния
    значило бы, что набор хозяйничает в проекте, куда его не ставили. Молчание двусмысленно, поэтому
    подсказку из той же папки спрашивают о плане самого проекта — и она обязана заговорить.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    if place == "простая папка":
        outside = tmp_path / "простая-папка"
        outside.mkdir()
    else:
        outside = new_repo(tmp_path, "сосед")
    env = hook_env(project_with_skill, tmp_path)
    before = every_entry(outside)

    calls: dict[str, dict[str, object]] = {
        "deliver Start": {"session_id": "s-out-start", "cwd": str(outside)},
        "deliver Prompt": {"session_id": "s-out-prompt", "cwd": str(outside)},
        "claim": {
            "session_id": "s-out-claim",
            "cwd": str(outside),
            "tool_input": {"command": 'git commit -m "их работа"'},
        },
    }
    for what, call in calls.items():
        done = run_wired(commands[what], outside, call, env)
        assert done.returncode == 0 and not done.stderr.strip(), (
            f"{what}: сторож не нашёлся, место — {place}: {done.stderr}"
        )
        assert done.stdout.strip() == "", (
            f"{what}: сторож заговорил, место — {place}: {done.stdout!r}"
        )

    plan = project_with_skill / PLANS / "2026-01-01-wave3-черновик.md"
    edit = {"session_id": "s-out-plan", "tool_input": {"file_path": str(plan)}}
    nudged = run_wired(commands["nudge"], outside, edit, env)
    assert nudged.returncode == 0 and not nudged.stderr.strip(), nudged.stderr
    assert "Правится план волны" in nudged.stdout, (
        f"место — {place}: подсказка промолчала о плане своего же проекта: {nudged.stdout!r}"
    )

    assert every_entry(outside) == before, (
        f"сторожа записали что-то в папку ({place}), которая не принадлежит их проекту"
    )


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_the_tree_the_tab_stands_in_runs_its_own_copy(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """Вкладка в корне дерева со своей копией запускает ЕЁ, а не копию проекта сессии.

    Рабочие деревья одного проекта несут свои копии набора, и версии их расходятся, пока не обновлены
    все деревья: сторожа дерева обязаны быть версии этого дерева. Только если в папке вкладки копии
    нет, команда отступает к проекту, где началась сессия. Вызов несёт фиксацию: форма bash сторожа
    фиксации запускает сторожа только на публикующей команде.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    tree = tmp_path / "другое-дерево"
    copy = tree / SKILL_INSIDE / "hooks"
    copy.mkdir(parents=True)
    for name in HOOK_FILES:
        (copy / name).write_text(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            '"копия другого дерева, вызвана с [$args]"\n',
            encoding="utf-8",
        )
    env = hook_env(project_with_skill, tmp_path)
    call = {"session_id": "s-tree", "tool_input": {"command": "git commit -m x"}}

    for what, command in commands.items():
        done = run_wired(command, tree, call, env)
        assert done.returncode == 0, done.stderr
        assert "копия другого дерева" in done.stdout, (
            f"{what}: из корня дерева со своей копией запустилась чужая: {done.stdout!r}"
        )
    started = run_wired(commands["deliver Start"], tree, {"session_id": "s-tree"}, env)
    assert "вызвана с [-Stage Start]" in started.stdout, (
        f"своя копия дерева не получила аргументов сторожа: {started.stdout!r}"
    )


# Формы нашей команды, которые писали прежние версии набора, — каждая под именами сторожей до
# 1.19.0: от рабочей папки (до 1.18.1), полным путём (скилл, поставленный вне проекта) и форма, ищущая
# в двух местах, в том виде, в каком она стояла до переименования файлов.
OLDER_FORMS = ("от рабочей папки", "полный путь", "ищет в двух местах")


def older_form(command: str, form: str, former: Path) -> str:
    """Наша команда так, как её писала прежняя версия набора, — под именем сторожа до 1.19.0.

    Прежние версии писали каждую запись в PowerShell; нынешняя команда может быть формой bash
    сторожа фиксации, поэтому имя сторожа и стадия берутся из неё, а не её вид.
    """
    found = re.search(r"/hooks/([\w.-]+\.ps1)", command)
    assert found, f"это не команда нашего сторожа: {command}"
    name = found.group(1)
    stage = re.search(r" -Stage \w+$", command)
    tail = stage.group(0) if stage else ""
    old = FORMER_NAME[name]
    if form == "ищет в двух местах":
        return wired(old) + tail
    base = f"$PWD/{SKILL_INSIDE.as_posix()}" if form == "от рабочей папки" else former.as_posix()
    return f'& "{base}/hooks/{old}"{tail}'


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
@pytest.mark.parametrize("form", OLDER_FORMS)
def test_an_entry_of_an_older_form_is_rewritten_in_place(
    project_with_skill: Path, tmp_path: Path, form: str, shell: str
) -> None:
    """Наши записи прежней формы переписываются там, где стоят, — и больше ни байт не двигается.

    Каждый проект, куда ставили набор, несёт прежнюю форму, пока установщик не прогонят снова: от
    рабочей папки (та, что из подпапки не находила ничего), полным путём (скилл когда-то стоял вне
    проекта) или форму, ищущую в двух местах, под старыми именами файлов. Не узнай установщик любую
    из них своей — рядом легла бы вторая запись, и сторож заговорил бы дважды. Прежний файл делается
    из нынешнего заменой только наших команд и условий, так что каждый чужой байт в нём — байт
    нынешнего файла, и прогон обязан вернуть ровно нынешний, под новыми именами. Прежние версии
    писали сторожа фиксации в PowerShell и без `onFailure`, так что прогон обязан ещё и сменить его
    оболочку и дописать поле.
    """
    write_settings(project_with_skill, FOREIGN_SETTINGS)
    install_in(project_with_skill, "-HookShell", shell)
    current = settings_path(project_with_skill).read_bytes()

    older = current.decode("utf-8")
    data = json.loads(older)
    former = tmp_path / "прежнее-место" / "coordination"
    ours_now = [
        hook
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse")
        for hook in ours(data, event)
    ]
    assert len(ours_now) == 5, f"установка подключила не все пять записей: {ours_now}"
    # У двух записей подсказки одна команда: каждое значение заменяется один раз, везде, где стоит.
    replaced: dict[str, str] = {}
    # Форма в двух местах писалась с условиями, привязанными к корню; более ранние — без этого.
    anchored = form == "ищет в двух местах"
    for hook in ours_now:
        replaced[hook["command"]] = older_form(hook["command"], form, former)
        if hook.get("if") and not anchored:
            replaced[hook["if"]] = hook["if"].replace("//**/", "")
    for new, old in replaced.items():
        new_text = json.dumps(new, ensure_ascii=False)
        assert new_text in older, f"нашего значения нет там, где его ждёт проверка: {new_text}"
        older = older.replace(new_text, json.dumps(old, ensure_ascii=False))
    assert not any(name in older for name in HOOK_FILES), (
        "прежний файл всё ещё называет сторожа нынешним именем — этот случай ничего не проверяет"
    )
    assert anchored or ("CLAUDE_PROJECT_DIR" not in older and "//**/" not in older), (
        "в прежнем файле осталась нынешняя форма — этот случай ничего не проверяет"
    )
    older, dropped = re.subn(r',\r?\n\s*"onFailure": "continue"', "", older)
    assert dropped == 1, "запись сторожа фиксации не кончается на `onFailure: continue`"
    if shell == "Bash":
        older, switched = re.subn(r'"shell": "bash"', '"shell": "powershell"', older)
        assert switched == 1, "запись сторожа фиксации — не единственная в bash"
    settings_path(project_with_skill).write_bytes(older.encode("utf-8"))

    out = install_in(project_with_skill, "-HookShell", shell)

    assert settings_path(project_with_skill).read_bytes() == current, (
        "прогон вернул не ровно то, что пишет свежая установка: наши записи не переписаны на месте "
        "(рядом со старой легла вторая или старая осталась), либо сдвинулся чужой байт"
    )
    assert "поправлена запись" in out, "переписка наших записей на месте прошла молча"


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_uninstall_takes_away_our_entries_in_every_form(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """Снятие убирает наши записи, в какой бы форме они ни были написаны, — и только их.

    Файл настроек, переживший несколько версий набора, может нести одного и того же сторожа в
    нескольких формах, под любым из двух его имён. Оставленная, прежняя форма продолжала бы падать
    из каждой подпапки ещё долго после того, как человеку сказали, что канала больше нет.
    """
    write_settings(project_with_skill, FOREIGN_HOOK)
    before = settings_path(project_with_skill).read_bytes()
    install_in(project_with_skill, "-HookShell", shell)

    data = settings_of(project_with_skill)
    former = tmp_path / "прежнее-место" / "coordination"
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
        "после снятия настройки отличаются от исходных — наша запись в какой-то форме осталась, "
        "или переписалось чужое"
    )


def place_older_copy(folder: Path) -> None:
    """Копия набора до 1.19.0 на месте потребителя в `folder`: только старые имена.

    Подставные скрипты, а не старые: запущенный печатает метку и оставляет файл там, где запущен, так
    что незаметно не запустится. У копии 1.18.1 и старше файлов под новыми именами нет.
    """
    hooks = folder / SKILL_INSIDE / "hooks"
    hooks.mkdir(parents=True)
    for name in FORMER_NAME.values():
        (hooks / name).write_text(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "Set-Content -LiteralPath (Join-Path $PWD.Path 'older-copy-ran.txt') -Value 'ran'\n"
            '"запустилась старая копия"\n',
            encoding="utf-8",
        )


def calls_from(folder: Path, session: str, plan: Path) -> dict[str, dict[str, object]]:
    """По вызову на каждую подключённую команду, из `folder`: сторожу фиксации дают фиксацию."""
    return {
        "deliver Start": {"session_id": f"{session}-start", "cwd": str(folder)},
        "deliver Prompt": {"session_id": f"{session}-prompt", "cwd": str(folder)},
        "claim": {
            "session_id": f"{session}-claim",
            "cwd": str(folder),
            "tool_input": {"command": 'git commit -m "работа"'},
        },
        "nudge": {"session_id": f"{session}-plan", "tool_input": {"file_path": str(plan)}},
    }


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
@pytest.mark.parametrize("place", ["соседний репозиторий", "простая папка"])
def test_the_command_never_starts_an_older_copy_in_the_session_project(
    project_with_skill: Path, tmp_path: Path, place: str, shell: str
) -> None:
    """В проекте, где началась сессия, всё ещё копия до 1.19.0: ничего из неё не запускается.

    Команда отступает к этому проекту, когда в папке вкладки копии нет. Под старыми именами файлов
    она нашла бы там СТАРУЮ копию и запускала бы её из любой папки — а старая копия не знает, что
    такое «не наш проект»: в чужом репозитории она доставляла его доску, оставляла там своё
    состояние и отказывала в его фиксациях. Под новыми именами команда не находит ничего, и хук не
    запускается — ровно как прежняя команда из такой папки.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    still_old = new_repo(tmp_path, "всё-ещё-старый")
    place_older_copy(still_old)
    if place == "простая папка":
        outside = tmp_path / "простая-папка"
        outside.mkdir()
    else:
        outside = new_repo(tmp_path, "сосед")
    env = hook_env(still_old, tmp_path)
    watched = {folder: every_entry(folder) for folder in (outside, still_old)}

    calls = calls_from(outside, "s-old", still_old / PLANS / "2026-01-01-wave3-draft.md")
    for what, command in commands.items():
        done = run_wired(command, outside, calls[what], env)
        assert "запустилась старая копия" not in done.stdout, (
            f"{what}: из «{place}» команда запустила старую копию проекта сессии"
        )
        assert done.stdout.strip() == "", f"{what}: из «{place}» кто-то ответил: {done.stdout!r}"
    for folder, before in watched.items():
        assert every_entry(folder) == before, f"хук записал в {folder.name}"


@needs_pwsh
@pytest.mark.parametrize("shell", CLAIM_SHELLS)
def test_a_worktree_with_an_older_copy_is_served_by_the_session_project(
    project_with_skill: Path, tmp_path: Path, shell: str
) -> None:
    """Вкладка в корне рабочего дерева с копией до 1.19.0 запускает новую копию проекта.

    В таком дереве нет файла под новыми именами, и команда отступает к проекту, где началась
    сессия, — а та копия считает дерево своим: все рабочие деревья проекта делят его служебный
    каталог git. Дерево охраняется как прежде, новым кодом, пока само не возьмёт новую копию. Каждый
    сторож проверяется по тому, что он там делает.
    """
    install_in(project_with_skill, "-HookShell", shell)
    commands = wired_commands(project_with_skill)
    # Рабочее дерево видит только зафиксированное: профиль должен быть там, чтобы подсказка его
    # прочла.
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
        assert "запустилась старая копия" not in done.stdout, (
            f"{what}: вместо копии проекта запустилась старая копия дерева"
        )
    assert "этот поток не объявлялся" in answers["claim"].stdout, (
        f"фиксация необъявленного потока в рабочем дереве прошла: {answers['claim'].stdout!r}"
    )
    beacon = tree / ".claude" / ".cache" / "wave-board-alive.txt"
    assert beacon.exists() and "s-tree-prompt" in beacon.read_text(encoding="utf-8"), (
        "доставка не отметила дерево живым — копия проекта не сочла его своим"
    )
    assert "Правится план волны" in answers["nudge"].stdout, (
        f"подсказка промолчала на собственном плане дерева: {answers['nudge'].stdout!r}"
    )
    assert not (tree / "older-copy-ran.txt").exists(), "запустилась старая копия дерева"


# ПЕРЕХОДНОЕ: проверяет пересыльщиков под старыми именами; убрать вместе с ними.
@needs_pwsh
def test_settings_of_an_older_version_reach_the_renamed_guards(
    project_with_skill: Path, tmp_path: Path
) -> None:
    """Настройки, записанные до 1.19.0, доходят до каждого сторожа через файлы под старыми именами.

    До 1.19.0 обновление было «скопировать новую папку поверх старой», без запуска установщика, так
    что такой проект всё ещё зовёт сторожей старыми именами, от рабочей папки. Каждый сторож
    проверяется по тому, что он делает: доставка доставляет (она получила `-Stage`), сторож фиксации
    отказывает (он получил вызов на стандартный ввод, а его ответ и код выхода вернулись), подсказка
    говорит.
    """
    install_in(project_with_skill)
    commands = {
        what: {
            "shell": "powershell",
            "command": older_form(hook["command"], "от рабочей папки", tmp_path),
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
        "title": "находка для всех",
        "where": "",
        "from": "wave9-author",
    }
    board.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    env = hook_env(project_with_skill, tmp_path)

    plan = project_with_skill / PLANS / "2026-01-01-wave3-draft.md"
    calls = calls_from(project_with_skill, "s-older", plan)
    answers = {
        what: run_wired(command, project_with_skill, calls[what], env)
        for what, command in commands.items()
    }
    for what, done in answers.items():
        assert done.returncode == 0 and not done.stderr.strip(), (
            f"{what}: старое имя не довело до сторожа: {done.stderr}"
        )
    assert "находка для всех" in answers["deliver Start"].stdout, (
        f"доставка под старым именем ничего не доставила: {answers['deliver Start'].stdout!r}"
    )
    assert "этот поток не объявлялся" in answers["claim"].stdout, (
        f"сторож фиксации под старым именем пропустил необъявленную фиксацию: {answers['claim'].stdout!r}"
    )
    assert "Правится план волны" in answers["nudge"].stdout, (
        f"подсказка под старым именем промолчала: {answers['nudge'].stdout!r}"
    )


# ПЕРЕХОДНОЕ: проверяет пересыльщиков под старыми именами; убрать вместе с ними.
@needs_pwsh
@pytest.mark.parametrize("name", HOOK_FILES)
def test_a_forwarder_hands_over_the_whole_call(tmp_path: Path, name: str) -> None:
    """Файл под старым именем сторожа передаёт переименованному сторожу ВЕСЬ вызов.

    Аргументы, стандартный ввод, вывод и код выхода. Проверяется на подставном стороже, потому что
    настоящие всегда выходят нулём: код, с которым сторож вышел, возвращается как есть, а сторож,
    закончившийся без `exit` после неудачного вызова git, всё равно возвращается успехом — код,
    оставленный git, не ответ сторожа.
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
            f"пересыльщик потерял аргументы или ввод: {done.stdout!r} {done.stderr!r}"
        )
        assert done.returncode == expected, (
            f"сторож вышел с {code!r}, пересыльщик — с {done.returncode}"
        )


@needs_pwsh
def test_the_bridge_of_this_project_is_the_one_the_installer_writes(
    project_with_skill: Path,
) -> None:
    """Переходник, лежащий в этом проекте, — ровно тот, что пишет установщик.

    Правка переходника руками разъедет команду запуска: в задачах она одна на все проекты.
    """
    if not REAL_BRIDGE.exists() or COORDINATION_DIR != REPO_ROOT / SKILL_INSIDE:
        pytest.skip("в этом проекте канал не развёрнут — сверять не с чем")
    install_in(project_with_skill)

    written = (project_with_skill / "scripts" / "wave-board.ps1").read_text(encoding="utf-8")
    assert written.strip() == REAL_BRIDGE.read_text(encoding="utf-8").strip(), (
        "переходник в проекте не такой, какой пишет установщик — команда запуска разъедется"
    )


@needs_pwsh
def test_uninstall_gives_the_settings_back(project: Path) -> None:
    """Снятие возвращает настройки к прежнему виду и не оставляет пустых блоков.

    Пустой блок события читают глазами и принимают за подключённый сторож — а профиль наоборот
    остаётся: там текст человека, и стирать его установщику нечем.
    """
    with_plans(project)
    write_settings(project, FOREIGN_HOOK)
    before = settings_path(project).read_bytes()

    install(project)
    assert settings_path(project).read_bytes() != before, "установка ничего не подключила"

    out = install(project, "-Mode", "Uninstall")
    assert settings_path(project).read_bytes() == before, (
        "после снятия настройки отличаются от исходных — что-то осталось или переписалось"
    )
    data = settings_of(project)
    assert "SessionStart" not in data.get("hooks", {}), "осталcя пустой блок события"
    assert not (project / "scripts" / "wave-board.ps1").exists(), "переходник не убран"
    profile = project / ".parallel-streams.md"
    assert profile.exists() and "## Coordination" in profile.read_text(encoding="utf-8"), (
        "профиль стёрт — а в нём текст человека про его проект"
    )
    assert "оставлен как есть" in out, "про оставленный профиль не сказано"


@needs_pwsh
def test_a_foreign_bridge_is_never_touched(project: Path) -> None:
    """Чужой файл с тем же именем не наш: ни перезаписать, ни удалить его нельзя."""
    bridge = project / "scripts" / "wave-board.ps1"
    bridge.parent.mkdir(parents=True)
    written = "# чужой скрипт с тем же именем\nWrite-Output 'своё дело'\n"
    bridge.write_text(written, encoding="utf-8")

    out = install(project)
    assert bridge.read_text(encoding="utf-8") == written, "чужой файл перезаписан"
    assert "ЧУЖОЙ файл" in out, "подмена чужого файла прошла бы молча"

    install(project, "-Mode", "Uninstall")
    assert bridge.exists(), "снятие удалило чужой файл"


@needs_pwsh
def test_check_only_reports(project: Path) -> None:
    """Показ состояния обязан быть немым действием: его зовут, чтобы посмотреть, а не чтобы чинить."""
    with_plans(project)
    install(project)
    before = snapshot(project)

    out = install(project, "-Mode", "Check")
    assert snapshot(project) == before, "показ состояния изменил файлы проекта"
    assert "SessionStart" in out and "deliver-wave-board.ps1" in out, (
        "показ не говорит, какие сторожа подключены и куда ведут"
    )
    assert PLANS in out, "показ не называет папку планов волн"
