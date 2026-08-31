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
import shutil
import subprocess
from pathlib import Path

import pytest

# Файл лежит в .claude/skills/parallel-streams/coordination/tests/ — до корня репозитория пять
# уровней вверх (tests -> coordination -> parallel-streams -> skills -> .claude -> корень).
REPO_ROOT = Path(__file__).resolve().parents[5]
COORDINATION_DIR = REPO_ROOT / ".claude" / "skills" / "parallel-streams" / "coordination"
INSTALLER = COORDINATION_DIR / "install.ps1"
NUDGE = COORDINATION_DIR / "hooks" / "pretooluse-wave-board-nudge.ps1"
REAL_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
REAL_BRIDGE = REPO_ROOT / "scripts" / "wave-board.ps1"

SKILL_INSIDE = Path(".claude") / "skills" / "parallel-streams" / "coordination"

# Папка планов подставного проекта — НАРОЧНО не такая, как в этом репозитории: канал обязан браться
# из профиля проекта, а не из того, как заведено здесь.
PLANS = "планы-волн/"

# Файлы сторожей канала. По ним узнаётся «наша» запись — и в установщике, и здесь.
OUR_HOOK_FILES = ("wave-board-deliver.ps1", "pretooluse-wave-board-nudge.ps1")

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh не найден — запускать скрипты нечем")


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
        if "pretooluse-wave-board-nudge.ps1" in hook.get("command", "")
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
def test_second_run_changes_nothing(project: Path) -> None:
    """Повторный прогон обязан быть пустым действием — байт в байт.

    Установку зовут заново после каждого обновления скилла. Клади она вторую такую же запись —
    сторож заговорил бы дважды, а находка пришла бы во вкладку дублем.
    """
    with_plans(project)
    install(project)
    before = snapshot(project)

    out = install(project)
    assert snapshot(project) == before, (
        "повторная установка изменила файлы, хотя менять было нечего"
    )

    data = settings_of(project)
    assert len(ours(data, "SessionStart")) == 1
    assert len(ours(data, "UserPromptSubmit")) == 1
    assert len(nudge_hooks(data)) == 2, "записи сторожа-подсказки размножились"
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
    assert filters == [f"Edit({PLANS}**)", f"Write({PLANS}**)"], (
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
    assert "сторож-подсказка не подключён" in out, "пропуск прошёл молча"
    assert "обратными кавычками" in out and "## Plans" in out, (
        f"отчёт не говорит, что вписать в профиль, чтобы сторож подключился: {out!r}"
    )


@needs_pwsh
def test_the_plans_folder_comes_from_the_profile(project: Path) -> None:
    """Папку планов называет профиль — иначе канал работает ровно в одном проекте.

    Проверяются обе стороны: условие в настройках и сам сторож. Разъедься они — сторож окажется
    подключённым к папке, которую сам не считает папкой планов, и промолчит.
    """
    (project / "waves").mkdir()
    (project / ".parallel-streams.md").write_text(
        "# Профиль\n\n## Coordination\n\nКоманды канала.\n\n"
        "## Plans\n\nПапка, где лежат планы волн: `waves/`.\n",
        encoding="utf-8",
    )
    install(project)

    filters = sorted(hook.get("if", "") for hook in nudge_hooks(settings_of(project)))
    assert filters == ["Edit(waves/**)", "Write(waves/**)"], (
        f"условия сторожа-подсказки не про папку из профиля: {filters}"
    )

    assert pwsh
    call = json.dumps(
        {"session_id": "s-plans", "tool_input": {"file_path": "waves/wave7-проба.md"}}
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
        "сторож промолчал при правке плана в папке, объявленной профилем, — значит папка в нём "
        "зашита, и в чужом проекте он немой"
    )


@needs_pwsh
def test_inside_project_paths_are_written_relative(project_with_skill: Path) -> None:
    """Скилл лежит внутри проекта — путь пишется от рабочей папки, а не полным.

    Полный путь привязал бы настройки к одной машине и к одному месту папки проекта: в рабочем
    дереве соседней вкладки такой сторож ведёт в чужую копию скилла.
    """
    install_in(project_with_skill)

    data = settings_of(project_with_skill)
    wired = ours(data, "SessionStart") + ours(data, "UserPromptSubmit") + nudge_hooks(data)
    assert len(wired) == 4, f"подключено не то число записей: {len(wired)}"
    for hook in wired:
        command = hook["command"]
        assert command.startswith('& "$PWD/.claude/skills/parallel-streams/coordination/hooks/'), (
            f"путь записан не от рабочей папки проекта: {command}"
        )

    bridge = (project_with_skill / "scripts" / "wave-board.ps1").read_text(encoding="utf-8")
    assert "$PSScriptRoot" in bridge, "переходник внутри проекта тоже обязан считать путь от себя"
    assert str(project_with_skill) not in bridge, "в переходник попал путь этой конкретной машины"


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
    assert "SessionStart" in out and "wave-board-deliver.ps1" in out, (
        "показ не говорит, какие сторожа подключены и куда ведут"
    )
    assert PLANS in out, "показ не называет папку планов волн"
