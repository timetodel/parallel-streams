"""Сторож: доска волны доставляет находку соседней вкладке и не шумит в остальных.

Доска существует ровно затем, чтобы дописка в план догнала УЖЕ РАБОТАЮЩУЮ вкладку: план читают
один раз, на старте, из своего рабочего дерева, и правка в него живого соседа не догоняет никак.
Механизм при этом молчаливый — оба хука глушат любую неожиданность и выходят нулём. Значит его
поломка выглядит как «соседу нечего сказать» и не проявится ничем. Отсюда проверка поведения, а не
существования файлов: запускаем настоящие скрипты со своей доской и своей папкой состояния.

Свойства, на которых всё держится, проверяются поимённо:
  • доска лежит в ОБЩЕМ каталоге репозитория — иначе сосед её не увидит;
  • чужая запись во вкладку не попадает, своя попадает один раз — иначе это шум в контексте;
  • после сжатия контекста открытая запись возвращается — иначе она теряется на длинной работе;
  • за раз показывается не больше пяти, а остальные приходят следующими ходами — иначе шестая и
    дальше не доходят вовсе;
  • адресат сверяется с реальными деревьями — иначе находка ложится в никуда с бодрым рапортом;
  • оборванная строка не съедает соседнюю запись;
  • напоминание при правке плана называет первыми вкладки, которые отметились, а про остальные
    говорит «неизвестно» — но закрытыми их не объявляет.

Тишина здесь двусмысленна: «нечего показывать» и «хук умер» выглядят одинаково. Поэтому там, где
проверяется молчание, следом проверяется, что в том же окружении хук заговорит.

Состояние машины на проверку не влияет: деревья, по которым сверяется живость, тест заводит сам,
в своём временном репозитории. Прежняя проверка смотрела на живые деревья проекта и пропускалась
ровно в том состоянии, которое вскрывало дефект.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Комплект лежит не только здесь: ещё в пяти копиях, и у каждой своя приставка пути до папки
# coordination (например, в публичном репозитории — localization/ru/parallel-streams/coordination
# и skills/parallel-streams/coordination, вовсе без .claude/skills/ этого проекта). Прежний счёт
# уровней вверх (parents[5]) был зашитой приставкой именно этого проекта: в чужой копии он утыкался
# не в ту папку, инструмент не находился, и весь набор падал красным, даже не начав работать —
# объявленный источник правды никто не стерёг. Папка tests лежит ВНУТРИ coordination всегда и
# везде, поэтому саму папку комплекта берём от файла проверки — на один уровень вверх, без счёта
# приставок.
COORDINATION_DIR = Path(__file__).resolve().parent.parent


def _find_repo_root(start: Path) -> Path:
    """Корень репозитория — первая папка вверх по дереву, где заведён `.git`.

    Он нужен не для поиска самого комплекта (тот берётся от файла проверки, см. COORDINATION_DIR
    выше), а как рабочая папка для запуска инструмента и для проверок, завязанных на устройство
    ИМЕННО ЭТОГО проекта (настройки Claude Code, профиль волн). У разных копий комплекта разная
    глубина до корня, поэтому счёт уровней здесь снова был бы зашитой приставкой — ищем маркер
    `.git`, а не считаем папки. Не нашли (например, архив без истории) — запасной вариант: прежний
    счёт уровней от файла проверки.
    """
    for candidate in start.parents:
        if (candidate / ".git").exists():
            return candidate
    return start.parents[5]


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
HOOKS_DIR = COORDINATION_DIR / "hooks"
TOOL = COORDINATION_DIR / "wave-board.ps1"
DELIVER = HOOKS_DIR / "wave-board-deliver.ps1"
NUDGE = HOOKS_DIR / "pretooluse-wave-board-nudge.ps1"

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh не найден — запускать скрипты нечем")


def settings() -> dict:
    """Настройки Claude Code ИМЕННО ЭТОГО проекта — их читают проверки сторожей, подключённых сюда.

    В чужой копии комплекта такого файла может не быть вовсе (сторожа туда ещё не подключал
    установщик) — тогда сверять нечего, и об этом надо сказать пропуском, а не уронить проверку
    неперехваченным исключением файловой системы.
    """
    if not SETTINGS.exists():
        pytest.skip(
            f"в этой копии нет {SETTINGS} — настройки Claude Code этого проекта не заведены, "
            "сверять подключение сторожей нечем"
        )
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def hooks_for(event: str) -> list[dict]:
    return [
        hook
        for entry in settings().get("hooks", {}).get(event, [])
        for hook in entry.get("hooks", [])
    ]


# Папка планов подставного проекта — НАРОЧНО не та, что в этом репозитории. Комплект обещает
# переносимость: сверяй проверки с папкой одного проекта — и в первом же чужом репозитории они
# упадут, а прочтут это как «комплект сломан».
STUB_PLANS = "planning/waves/"

# Папка планов ЧУЖОГО проекта — та самая, что была зашита в коде. В подставном профиле её не
# называют, поэтому для комплекта это обычная папка: сторож не должен считать её планами, а сосед,
# правящий в ней те же файлы, обязан попасть в пересечения.
ALIEN_PLANS = "docs/superpowers/plans/"


def profile_text(plans: str | None = STUB_PLANS, header: str = "## Plans") -> str:
    """Профиль подставного проекта: с папкой планов или вовсе без такого раздела."""
    head = "# Профиль подставного проекта\n\n## Isolation\n\nОтдельное дерево на поток.\n"
    if plans is None:
        return head
    return f"{head}\n{header}\n\nПапка, где лежат планы волн: `{plans}`.\n"


def plans_folder() -> str:
    """Папка планов волн ЭТОГО проекта — из его профиля, а не зашитая в проверке."""
    profile = REPO_ROOT / ".parallel-streams.md"
    if not profile.exists():
        return ""
    section = re.search(
        r"(?msi)^##\s+Plans\s*$(.*?)(?=^##\s|\Z)", profile.read_text(encoding="utf-8")
    )
    if not section:
        return ""
    for found in re.findall(r"`([^`]+)`", section.group(1)):
        value = found.strip().replace("\\", "/")
        if " " in value or (not value.endswith("/") and re.search(r"\.[A-Za-z0-9]{1,5}$", value)):
            continue
        return value if value.endswith("/") else value + "/"
    return ""


def test_delivery_guard_is_wired_to_both_events() -> None:
    """Дыры две, и каждая закрыта своим событием.

    Начало сессии несёт всё открытое (в том числе после сжатия контекста), обычный ход — только
    новое. Отвались любое, механизм замолчит наполовину, и это будет неотличимо от тишины.
    """
    for event, stage in (("SessionStart", "-Stage Start"), ("UserPromptSubmit", "-Stage Prompt")):
        commands = [hook.get("command", "") for hook in hooks_for(event)]
        wired = [cmd for cmd in commands if "wave-board-deliver.ps1" in cmd]
        assert wired, (
            f"сторож доставки не подключён на {event} — находка соседа не дойдёт до вкладки, "
            "и выглядеть это будет как «соседу нечего сказать»"
        )
        assert any(stage in cmd for cmd in wired), (
            f"на {event} сторож доставки запущен не с {stage} — событие обработается чужой веткой"
        )


def test_plan_edit_guard_is_wired_for_both_tools() -> None:
    """Половина правок идёт одним инструментом, половина другим: пропуск любого — молчание.

    Папку в условии сверяем с той, что назвал профиль ЭТОГО проекта: зашитая в проверке, она
    уронила бы её в любом другом репозитории, хотя канал там работал бы верно.
    """
    plans = plans_folder()
    if not plans:
        pytest.skip(
            "папка планов волн в профиле проекта не названа — сторожу-подсказке нечего слушать"
        )
    filters = " ".join(
        hook.get("if", "")
        for hook in hooks_for("PreToolUse")
        if "pretooluse-wave-board-nudge.ps1" in hook.get("command", "")
    )
    assert filters, "сторож правки плана волны не подключён вовсе"
    for tool_name in ("Edit", "Write"):
        assert f"{tool_name}({plans}**)" in filters, (
            f"сторож правки плана не слушает {tool_name} — правка плана пройдёт без напоминания "
            "о том, что до живой вкладки соседа она сама не дойдёт"
        )


@needs_pwsh
def test_board_lives_in_the_shared_git_directory() -> None:
    """Доска обязана лежать в общем каталоге репозитория, а не в рабочем дереве.

    Это и есть весь смысл места: каталог один на все деревья (сосед видит запись сразу, без
    слияния), он вне веток (в чужую заявку не попадёт) и переживает удаление дерева вместе с
    закрытой вкладкой. Уедет доска в дерево — механизм станет невидимым для тех, ради кого он есть.
    """
    assert pwsh
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(TOOL), "-Mode", "Path"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    path = done.stdout.strip().replace("\\", "/")
    assert "/.git/wave-board/" in path, f"доска лежит не в общем каталоге репозитория: {path}"
    assert "/.claude/worktrees/" not in path, (
        f"доска уехала внутрь рабочего дерева ({path}) — соседние вкладки её не увидят"
    )


def tool(
    board: Path, *args: str, cwd: Path = REPO_ROOT, known: bool = False
) -> subprocess.CompletedProcess[str]:
    """Запуск инструмента доски с отдельной доской теста.

    Адресаты в тестах выдуманы, поэтому проверка реальности потока обходится явным флагом: иначе
    тесты пришлось бы привязывать к рабочим деревьям конкретной машины. `known=True` — когда
    проверяется как раз проверка.
    """
    assert pwsh
    argv = [pwsh, "-NoProfile", "-File", str(TOOL), *args, "-BoardPath", str(board)]
    if "Add" in args and not known:
        argv.append("-AllowUnknownStream")
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        timeout=60,
    )


def run_tool(board: Path, *args: str, cwd: Path = REPO_ROOT, known: bool = False) -> str:
    done = tool(board, *args, cwd=cwd, known=known)
    assert done.returncode == 0, done.stderr
    return done.stdout


def add(board: Path, to: str, title: str, cwd: Path = REPO_ROOT) -> str:
    """Кладёт находку и возвращает её метку."""
    out = run_tool(board, "-Mode", "Add", "-To", to, "-Title", title, cwd=cwd)
    return out.split("метка ")[1].split(")")[0].strip()


def deliver(board: Path, cwd: Path, stage: str, session: str) -> subprocess.CompletedProcess[str]:
    assert pwsh
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(DELIVER), "-Stage", stage, "-BoardPath", str(board)],
        input=json.dumps({"session_id": session}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        timeout=60,
    )


def run_deliver(board: Path, cwd: Path, stage: str, session: str) -> str:
    done = deliver(board, cwd, stage, session)
    assert done.returncode == 0, done.stderr
    return done.stdout


def context_text(stdout: str) -> str:
    """Текст, который хук кладёт в контекст вкладки: наружу он уходит одной строкой JSON."""
    stripped = stdout.strip()
    if not stripped.startswith("{"):
        return stdout
    return json.loads(stripped)["hookSpecificOutput"]["additionalContext"]


def bullets(text: str) -> list[str]:
    """Строки-перечисления в показе хука (по одной на запись или на дерево)."""
    return [line for line in context_text(text).splitlines() if line.strip().startswith("•")]


@needs_pwsh
def test_record_travels_to_its_stream_and_only_there(tmp_path: Path) -> None:
    board = tmp_path / "board.jsonl"
    add(board, "feat/wave9-clock", "часы считать в обе стороны")
    add(board, "feat/wave9-truth", "признак заведён опущенным")

    # Вкладка знает себя по имени рабочей папки — поэтому папка названа как поток.
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    first = run_deliver(board, mine, "Start", "s1")
    assert "часы считать в обе стороны" in first, "своя находка до вкладки не дошла"
    assert "признак заведён опущенным" not in first, (
        "во вкладку пришла находка ЧУЖОГО потока — это шум в контексте, за который платят на "
        "каждом шаге до конца работы"
    )

    # Второй ход: нового нет, значит сторож обязан молчать.
    assert run_deliver(board, mine, "Prompt", "s1").strip() == "", (
        "находка показана второй раз — повтор копится в контексте и его переотправляют на каждом шаге"
    )

    # Молчание «нечего показывать» и молчание «хук умер» выглядят одинаково: оба выходят нулём с
    # пустым выводом. Отличает их только то, что живой сторож заговорит от новой записи.
    add(board, "wave9-clock", "поздняя находка")
    assert "поздняя находка" in run_deliver(board, mine, "Prompt", "s1"), (
        "после тишины сторож не заговорил от новой записи — значит тишина была поломкой, а не "
        "отсутствием находок"
    )


@needs_pwsh
def test_closed_record_stops_travelling(tmp_path: Path) -> None:
    board = tmp_path / "board.jsonl"
    mark = add(board, "wave9-clock", "учтённая находка")

    # Закрывает АДРЕСАТ: находку с именным адресом закрыть может только он — закрытие такой записи
    # общее, и чужая рука погасила бы её у настоящего получателя.
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    # Ищем по МЕТКЕ, а не по заголовку: закрытие рождает уведомление автору «учтено: …», и его
    # заголовок повторяет исходный. По заголовку проверка спутала бы одно с другим.
    assert f"[{mark}]" not in run_tool(board, "-Mode", "Show"), (
        "закрытая находка осталась в показе доски"
    )
    assert run_deliver(board, mine, "Start", "s2").strip() == "", (
        "закрытая находка продолжает приходить во вкладку — закрывать её тогда бессмысленно"
    )


@needs_pwsh
def test_open_record_returns_after_context_is_compacted(tmp_path: Path) -> None:
    """Сжатие контекста поднимает начало сессии заново — открытая находка обязана вернуться.

    Иначе она теряется ровно там, где механизм нужнее всего: на длинной работе, где сжатий много.
    """
    board = tmp_path / "board.jsonl"
    add(board, "wave9-clock", "ещё не учтённая находка")
    mine = tmp_path / "wave9-clock"
    mine.mkdir()

    assert "ещё не учтённая находка" in run_deliver(board, mine, "Start", "s3")
    assert run_deliver(board, mine, "Prompt", "s3").strip() == ""
    assert "ещё не учтённая находка" in run_deliver(board, mine, "Start", "s3"), (
        "после сжатия контекста открытая находка не вернулась — а старый показ из контекста уже ушёл"
    )


def crowd_the_board(board: Path, count: int) -> None:
    for number in range(count):
        add(board, "wave9-clock", f"находка {number}")


@needs_pwsh
def test_one_turn_shows_no_more_than_five_records(tmp_path: Path) -> None:
    """Потолок показа — пять записей за ход: больше это стена текста, которую перестают читать."""
    board = tmp_path / "board.jsonl"
    crowd_the_board(board, 7)
    mine = tmp_path / "wave9-clock"
    mine.mkdir()

    assert len(bullets(run_deliver(board, mine, "Start", "s-batch"))) == 5, (
        "за один ход показано не пять записей — потолок не соблюдён, а контекст вкладки "
        "переотправляется на каждом шаге"
    )


@needs_pwsh
def test_records_over_the_limit_arrive_on_the_next_turns(tmp_path: Path) -> None:
    """Шестая и дальше обязаны дойти следующими ходами, а не сгинуть.

    Журнал показанного помечает записи как показанные — если пометить ВСЕ подходящие, а показать
    первые пять, остаток не придёт никогда: ни на следующем ходу, ни после сжатия контекста.
    """
    board = tmp_path / "board.jsonl"
    crowd_the_board(board, 7)
    mine = tmp_path / "wave9-clock"
    mine.mkdir()

    first = run_deliver(board, mine, "Start", "s-rest")
    second = run_deliver(board, mine, "Prompt", "s-rest")
    seen = {number for number in range(7) if f"находка {number}" in first + second}
    assert seen == set(range(7)), (
        f"дошли не все находки, а только {sorted(seen)} — записи сверх потолка пропали навсегда"
    )
    assert len(bullets(second)) == 2, "остаток пришёл не следующим ходом"


@needs_pwsh
def test_broadcast_reaches_everyone_but_its_author(tmp_path: Path) -> None:
    """`*` — всем живым вкладкам, кроме положившей: автор свою находку и так знает.

    Автор узнаётся по полю записи, а оно заполняется у git. Не ответил git (вкладка запущена не из
    репозитория) — поле обязано взяться из имени рабочей папки, иначе находка вернётся автору.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-clock"
    author.mkdir()
    other = tmp_path / "wave9-truth"
    other.mkdir()

    add(board, "*", "общая находка волны", cwd=author)
    assert "общая находка волны" in run_deliver(board, other, "Start", "s-all"), (
        "находка «всем» не дошла до соседней вкладки"
    )
    assert run_deliver(board, author, "Start", "s-mine").strip() == "", (
        "находка «всем» вернулась тому, кто её положил — это шум в контексте автора"
    )


@needs_pwsh
@pytest.mark.parametrize(
    ("addressed", "folder"),
    [
        ("feat/wave9-clock", "wave9-clock"),
        ("feat/wave9-clock", "feat+wave9-clock"),
        ("worktree-wave9-clock", "wave9-clock"),
    ],
)
def test_naming_forms_resolve_to_one_stream(tmp_path: Path, addressed: str, folder: str) -> None:
    """Один поток зовут тремя способами, и все три обязаны сойтись в один ключ.

    Разъедься формы — находка, адресованная веткой, не найдёт вкладку, которая знает себя по имени
    папки, и это будет выглядеть как «соседу нечего сказать».
    """
    board = tmp_path / "board.jsonl"
    add(board, addressed, "находка по имени в другой форме")
    mine = tmp_path / folder
    mine.mkdir()
    assert "находка по имени в другой форме" in run_deliver(board, mine, "Start", "s-name"), (
        f"адресат «{addressed}» не сошёлся с рабочей папкой «{folder}»"
    )


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


BEACON = Path(".claude") / ".cache" / "wave-board-alive.txt"

# Деревья волны 7: у первых трёх маячок свежий, у остальных его нет или он протух.
ALIVE = ("wave7-alpha", "wave7-beta", "wave7-gamma")
UNKNOWN = ("wave7-eta", "wave7-iota", "wave7-kappa", "wave7-lambda", "wave7-mu", "wave7-theta")


def set_beacon(tree: Path, hours_ago: float) -> None:
    """Ставит маячок живой вкладки нужной свежести — так же, как это делает сторож доставки."""
    mark = tree / BEACON
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text("2026-08-21T10:00:00 сессия-проба\n", encoding="utf-8")
    when = time.time() - hours_ago * 3600
    os.utime(mark, (when, when))


@pytest.fixture(scope="module")
def wave_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Свой репозиторий с рабочими деревьями — проверка не зависит от состояния этой машины.

    Раньше напоминание проверялось на живых деревьях проекта, и тест пропускался ровно в том
    состоянии машины, которое вскрывает дефект: когда «живых» не видно вовсе. Здесь состав деревьев
    и свежесть маячков задаём сами, поэтому проверяются все три случая — вкладка отметилась, дерево
    без отметки, деревьев волны нет вовсе.

    Профиль подставного проекта называет СВОЮ папку планов и кладётся в первый коммит: рабочее
    дерево видит только закоммиченное, а сторож ищет профиль, не выходя за пределы своего дерева.
    """
    root = tmp_path_factory.mktemp("wave-repo")
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "проба")
    (root / "readme.md").write_text("проба\n", encoding="utf-8")
    (root / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "первый")
    trees = ("wave7-here", *ALIVE, *UNKNOWN, "wave8-other", "wave6-lonely")
    for name in trees:
        git(root, "worktree", "add", "-b", f"feat/{name}", f".claude/worktrees/{name}")
    # Дерево, у которого имя папки и имя ветки расходятся: их ключи разные, и поток, знающий себя
    # по ветке, должен узнаваться по папке — и наоборот. Имя без номера волны намеренно: в списки
    # напоминания по волнам оно попадать не должно.
    git(root, "worktree", "add", "-b", "feat/oddbranch-tab", ".claude/worktrees/oddfolder-tab")
    for name in ("wave7-here", *ALIVE, "wave8-other"):
        set_beacon(root / ".claude" / "worktrees" / name, 0.1)
    # Протухший маячок — это «неизвестно», а не «жива»: вкладку закрыли сутки назад.
    set_beacon(root / ".claude" / "worktrees" / "wave7-theta", 13)
    return root


def here_of(repo: Path) -> Path:
    """Дерево, из которого идёт проверка: себя напоминание в списке не называет."""
    return repo / ".claude" / "worktrees" / "wave7-here"


def named_streams(text: str) -> list[str]:
    return re.findall(r"feat/wave\d+-[a-z]+", text)


@needs_pwsh
def test_tool_refuses_an_addressee_without_a_worktree(tmp_path: Path, wave_repo: Path) -> None:
    """Адресат сверяется с реальными деревьями: иначе находка ложится в никуда с бодрым рапортом.

    Промахнуться легко тремя способами сразу — назвать поток словами, опечататься в ветке,
    оставить лишнюю косую. Все три выглядят как успех, потому что запись действительно ложится.
    """
    board = tmp_path / "board.jsonl"
    here = here_of(wave_repo)
    for wrong in ("поток 3", "feat/wave7-alfa"):
        done = tool(
            board, "-Mode", "Add", "-To", wrong, "-Title", "находка мимо", cwd=here, known=True
        )
        assert done.returncode != 0, (
            f"инструмент принял несуществующего адресата «{wrong}» и отрапортовал успехом"
        )
        assert "Хвосты волны" in done.stderr, (
            "отказ не говорит, куда девать находку для закрытого потока"
        )
    # В подсказке имена в канонической короткой форме — ровно в той, что принимает -To.
    assert (
        "wave7-alpha"
        in tool(
            board, "-Mode", "Add", "-To", "feat/wave7-alfa", "-Title", "мимо", cwd=here, known=True
        ).stderr
    ), "отказ по опечатке не подсказал похожее имя, хотя оно рядом"
    assert not board.exists(), "запись мимо адресата всё-таки легла на доску"

    # Заведённое дерево проверку проходит — иначе проверка просто запрещала бы всё.
    add(board, "feat/wave7-alpha", "находка в заведённое дерево", cwd=here)


@needs_pwsh
def test_tool_tells_whether_the_neighbour_tab_answered_recently(
    tmp_path: Path, wave_repo: Path
) -> None:
    """Рапорт не обещает доставки, которой не знает, и не округляет ни в одну сторону.

    Свежая отметка значит «вкладка работала недавно», а не «работает сейчас»: снимать маячок при
    закрытии некому, и закрывшаяся час назад вкладка ещё полсуток выглядит отметившейся. Твёрдое
    «дойдёт само» здесь опасно — автор находки на нём успокоится и не заведёт ей задание в хвостах.
    Дерево без свежей отметки — тем более «неизвестно»: вкладку могли закрыть, а могли и не
    открывать вовсе.
    """
    board = tmp_path / "board.jsonl"
    here = here_of(wave_repo)
    alive = run_tool(
        board, "-Mode", "Add", "-To", "feat/wave7-alpha", "-Title", "живому", cwd=here, known=True
    )
    assert "отмечалась недавно" in alive, (
        "про вкладку со свежей отметкой рапорт не говорит главного — что она недавно работала"
    )
    assert "скорее всего" in alive, (
        "рапорт обещает доставку твёрдо — а маячок означает лишь «работала в последние часы»: "
        "вкладку могли закрыть час назад, снимать отметку при закрытии некому"
    )
    unknown = run_tool(
        board, "-Mode", "Add", "-To", "feat/wave7-eta", "-Title", "молчуну", cwd=here, known=True
    )
    assert "отмечалась недавно" not in unknown, (
        "рапорт выдаёт за отметившуюся вкладку, которая давно молчит"
    )
    assert "неизвестно" in unknown, "рапорт не сознаётся, что о вкладке соседа ничего не знает"


@needs_pwsh
def test_tool_refuses_an_empty_addressee_even_with_the_bypass(tmp_path: Path) -> None:
    """Пустой ключ (`feat/wave3-plan-clock/`) — отказ всегда: такой записи не достанется никто."""
    board = tmp_path / "board.jsonl"
    done = tool(
        board, "-Mode", "Add", "-To", "feat/wave3-plan-clock/", "-Title", "находка в пустоту"
    )
    assert done.returncode != 0, "инструмент принял адресата, ключ которого схлопнулся в пустой"
    assert not board.exists(), "запись с пустым адресатом легла на доску"


@needs_pwsh
def test_a_broken_line_does_not_swallow_the_next_record(tmp_path: Path) -> None:
    """Оборванная запись не должна уносить с собой следующую.

    Дописывание без перевода строки приклеивает новую запись к обрывку: не разбираются обе, а
    инструмент рапортует об успехе — находка теряется молча.
    """
    board = tmp_path / "board.jsonl"
    whole = json.dumps(
        {
            "id": "aaaa1111",
            "at": "2026-08-20T10:00:00",
            "to": "wave9-clock",
            "title": "первая находка",
        },
        ensure_ascii=False,
    )
    board.write_text(whole + '\n{"id":"bbbb2222","at":"2026-08-2', encoding="utf-8")

    add(board, "wave9-clock", "вторая находка")
    show = run_tool(board, "-Mode", "Show")
    assert "первая находка" in show, "оборванная запись унесла с доски предыдущую"
    assert "вторая находка" in show, (
        "новая запись приклеилась к обрывку и не разобралась — а инструмент отрапортовал успехом"
    )


@needs_pwsh
def test_delivery_guard_stays_silent_on_a_wrong_stage(tmp_path: Path) -> None:
    """В шапке хука обещано «при любой неожиданности молча выходит нулём» — включая свой запуск.

    Разбор параметров идёт до тела скрипта, поэтому неверное значение отдавало ненулевой код и
    вываливало поданный на вход JSON наружу.
    """
    assert pwsh
    board = tmp_path / "board.jsonl"
    add(board, "wave9-clock", "находка для этого потока")
    # Рабочая папка — папка АДРЕСАТА: иначе хук молчал бы при любой стадии, и проверка была бы
    # зелёной, даже если разбор стадии выкинуть целиком.
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    call = json.dumps({"session_id": "s-stage", "tool_input": {"file_path": "секрет"}})
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(DELIVER), "-Stage", "Бред", "-BoardPath", str(board)],
        input=call,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(mine),
        timeout=60,
    )
    assert done.returncode == 0, "хук вернул ненулевой код — среда сочтёт его сломанным"
    assert done.stdout.strip() == "", f"хук вывалил наружу лишнее: {done.stdout!r}"

    # В ТОМ ЖЕ окружении верная стадия обязана заговорить — тогда тишина выше означает именно
    # «стадия не наша», а не «показывать нечего» и не «хук умер».
    assert "находка для этого потока" in run_deliver(board, mine, "Start", "s-stage"), (
        "при верной стадии хук тоже промолчал — значит тишина на неверной ничего не доказывает"
    )


@needs_pwsh
def test_cleanup_spares_the_live_session_and_runs_without_records(tmp_path: Path) -> None:
    """Чистка журналов не должна доставать журнал живой сессии — и обязана идти всегда.

    Время записи журнала менялось только от новых находок, а длинная сессия может не получать их
    сутками: её журнал попадал под чистку, и уже показанное приходило заново. Вторая половина —
    чистка стояла ПОСЛЕ раннего выхода и при пустой доске не выполнялась вовсе.
    """
    board = tmp_path / "board.jsonl"
    mark = add(board, "wave9-clock", "находка")
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    run_deliver(board, mine, "Start", "s-live")

    cache = mine / ".claude" / ".cache"
    journal = cache / "wave-board-shown-s-live.txt"
    assert journal.exists(), "журнал показанного не заведён — повторов теперь ничто не сдерживает"
    ancient = cache / "wave-board-shown-ancient.txt"
    ancient.write_text("deadbeef\n", encoding="utf-8")
    stale = time.time() - 3 * 24 * 3600
    os.utime(journal, (stale, stale))
    os.utime(ancient, (stale, stale))

    # Находку закрыли: показывать нечего, и раньше хук выходил ДО чистки. Закрывает адресат —
    # чужая рука погасила бы именную находку у настоящего получателя.
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    assert run_deliver(board, mine, "Prompt", "s-live").strip() == ""

    assert journal.exists(), (
        "чистка унесла журнал живой сессии — всё уже показанное придёт ей заново"
    )
    assert journal.stat().st_mtime > stale + 3600, (
        "отметка живой сессии не обновляется на ходу — через сутки её журнал снова попадёт "
        "под чистку"
    )
    assert not ancient.exists(), (
        "журнал давно закрытой сессии не убран — чистка не выполняется, когда показывать нечего"
    )


@needs_pwsh
def test_board_can_be_compacted_and_warns_when_it_grows(tmp_path: Path) -> None:
    """Доска разбирается целиком на каждом ходу каждой вкладки — её надо уметь уплотнять.

    Закрытая запись остаётся строкой в файле навсегда: разбор дорожает, а увидеть это неоткуда.
    Поэтому показ предупреждает о разросшейся доске, а уплотнение оставляет только открытое.
    """
    board = tmp_path / "board.jsonl"
    lines: list[str] = []
    for number in range(150):
        mark = f"old{number:05d}"
        lines.append(
            json.dumps(
                {
                    "id": mark,
                    "at": "2026-08-01T10:00:00",
                    "to": "wave9-clock",
                    "title": f"мелочь {number}",
                },
                ensure_ascii=False,
            )
        )
        lines.append(json.dumps({"id": mark, "at": "2026-08-01T11:00:00", "done": True}))
    # Открытую запись дописываем ТЕКСТОМ, а не инструментом: добавление само уплотняет разросшуюся
    # доску (отдельный тест ниже), и проверять было бы уже нечего.
    lines.append(
        json.dumps(
            {
                "id": "openone",
                "at": "2026-08-01T12:00:00",
                "to": "wave9-clock",
                "title": "открытая находка",
            },
            ensure_ascii=False,
        )
    )
    board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert "-Mode Compact" in run_tool(board, "-Mode", "Show"), (
        "показ не предупреждает о разросшейся доске — её разбирают целиком на каждом ходу"
    )

    run_tool(board, "-Mode", "Compact")
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(left) == 1, f"после уплотнения на доске осталось {len(left)} строк вместо одной"

    after = run_tool(board, "-Mode", "Show")
    assert "открытая находка" in after, "уплотнение унесло открытую запись"
    assert "-Mode Compact" not in after, "предупреждение осталось на уплотнённой доске"


@needs_pwsh
def test_adding_a_record_compacts_a_crowded_board(tmp_path: Path) -> None:
    """Разросшуюся доску уплотняет само добавление находки.

    Просьба «уплотните руками» адресована тому, кто пришёл сюда за другим: он кладёт находку соседу
    и уходит. Работа безопасная (при любом сомнении она отказывается), делать её вручную незачем —
    а платят за раздутую доску все вкладки, разбирая её целиком на каждом ходу.
    """
    board = tmp_path / "board.jsonl"
    lines: list[str] = []
    for number in range(150):
        mark = f"old{number:05d}"
        lines.append(
            json.dumps(
                {
                    "id": mark,
                    "at": "2026-08-01T10:00:00",
                    "to": "wave9-clock",
                    "title": f"мелочь {number}",
                },
                ensure_ascii=False,
            )
        )
        lines.append(json.dumps({"id": mark, "at": "2026-08-01T11:00:00", "done": True}))
    board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = run_tool(board, "-Mode", "Add", "-To", "wave9-clock", "-Title", "свежая находка")
    assert "уплотнена" in out, (
        "добавление промолчало об уплотнении — стёртое молча неотличимо от того, чего не было"
    )
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(left) == 1, f"после добавления на доске осталось {len(left)} строк вместо одной"
    assert "свежая находка" in run_tool(board, "-Mode", "Show"), (
        "уплотнение унесло ту самую запись, ради которой всё и затевалось"
    )


def run_nudge(cwd: Path, file_path: str, session: str) -> subprocess.CompletedProcess[str]:
    assert pwsh
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(NUDGE)],
        input=json.dumps({"session_id": session, "tool_input": {"file_path": file_path}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        timeout=60,
    )


@needs_pwsh
def test_plan_edit_guard_names_the_answering_tabs_first(wave_repo: Path) -> None:
    """Напоминание сужает выбор до тех, кто отметился, но остальных не хоронит.

    Отметку ставит сторож доставки в своём дереве на каждом ходу. Свежая отметка — вкладка точно
    жива; отметки нет — неизвестно (вкладка старая, хук до неё не доехал, работа идёт молча), и
    выдавать это за «поток закрыт» нельзя: находка уйдёт в «Хвосты волны» мимо живого соседа.
    Заодно проверяется относительный путь — по нему хук раньше молча выходил.
    """
    done = run_nudge(here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-nudge-alive")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), (
        "по относительному пути напоминание молчит — а правки плана приходят и в такой форме"
    )
    context = context_text(done.stdout)
    names = named_streams(context)
    assert names[:3] == [f"feat/{name}" for name in ALIVE], (
        f"отметившиеся вкладки названы не первыми: {names}"
    )
    assert "feat/wave7-here" not in names, "напоминание предлагает адресовать находку самому себе"
    assert "feat/wave8-other" not in names, "в списке сосед из ЧУЖОЙ волны"
    assert "feat/wave6-lonely" not in names, "в списке сосед из ЧУЖОЙ волны"
    assert len(names) == 8, f"потолок в восемь имён не соблюдён: {len(names)}"
    assert "и ещё 1" in context, "усечённый список не сознаётся, что показал не всех"
    assert "неизвестно" in context, (
        "дерево без свежей отметки выдано за живую вкладку — рапорт обещает то, чего не знает"
    )
    # Отдельно про порог свежести: у wave7-theta отметка полусуточной давности, и живой вкладкой
    # она уже не считается. Перестань порог работать — theta попала бы в первую строку четвёртой.
    answered = [line for line in context.splitlines() if "отмечались" in line]
    assert len(answered) == 1, "строка про отметившиеся вкладки потерялась или размножилась"
    assert named_streams(answered[0]) == [f"feat/{name}" for name in ALIVE], (
        f"в живых оказались не те: {named_streams(answered[0])} — похоже, порог свежести отметки "
        "не работает"
    )
    assert "Хвосты волны" in context, (
        "напоминание не называет, куда девать находку для закрытого потока — а это вторая "
        "половина правила"
    )

    second = run_nudge(
        here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-nudge-alive"
    )
    assert second.stdout.strip() == "", (
        "напоминание повторяется в той же сессии — правок плана в потоке много, а напоминание одно"
    )


@needs_pwsh
def test_plan_edit_guard_does_not_bury_a_wave_without_beacons(wave_repo: Path) -> None:
    """Волна, где ни одна вкладка ещё не отметилась, — это «неизвестно», а не «все закрыты».

    Прежний признак живости (блокировка рабочего дерева) давал ровно эту ошибку: механизм молча
    объявлял все потоки закрытыми и уводил находку в «Хвосты волны» мимо доски.
    """
    done = run_nudge(here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave6-probe.md", "s-nudge-quiet")
    assert done.returncode == 0, done.stderr
    context = context_text(done.stdout)
    assert "feat/wave6-lonely" in context, (
        "дерево волны без отметки не названо вовсе — находка уйдёт мимо живого соседа"
    )
    assert "неизвестно" in context, "про молчащее дерево не сказано, что о нём ничего не известно"
    assert "потоки закрыты" not in context, (
        "молчащее дерево объявлено закрытым потоком — это то самое утверждение о том, чего "
        "механизм не знает"
    )


@needs_pwsh
def test_plan_edit_guard_says_when_the_wave_has_no_worktrees(wave_repo: Path) -> None:
    """Деревьев волны нет вовсе — вот это и есть «потоки закрыты», и место находки в хвостах."""
    done = run_nudge(here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave5-probe.md", "s-nudge-empty")
    assert done.returncode == 0, done.stderr
    context = context_text(done.stdout)
    assert named_streams(context) == [], (
        "по волне без деревьев напоминание всё равно назвало чьи-то имена"
    )
    assert "Хвосты волны" in context, "не сказано, куда девать находку, когда адресовать некому"


def stub_project(root: Path, name: str, profile: str) -> Path:
    """Подставной проект со своим профилем: папка планов там СВОЯ, не как в этом репозитории."""
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    git(project, "init", "-b", "main")
    (project / ".parallel-streams.md").write_text(profile, encoding="utf-8")
    return project


@needs_pwsh
def test_plan_edit_guard_takes_the_plans_folder_from_the_profile(wave_repo: Path) -> None:
    """Папку планов называет профиль проекта, а не код сторожа.

    Зашитая папка одного проекта — тихая поломка: в чужом проекте условие не сходится никогда,
    сторож молча выходит нулём, и выглядит это как «напоминать не о чем».
    """
    here = here_of(wave_repo)
    mine = run_nudge(here, f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-profile-plans")
    assert mine.returncode == 0, mine.stderr
    assert mine.stdout.strip(), (
        "сторож промолчал о правке плана в папке, которую назвал профиль проекта"
    )
    alien = run_nudge(here, f"{ALIEN_PLANS}2026-01-01-wave7-probe.md", "s-profile-alien")
    assert alien.stdout.strip() == "", (
        "сторож принял за план волны файл в папке ЧУЖОГО проекта — значит папка зашита в коде"
    )


@needs_pwsh
def test_the_plans_section_is_found_whatever_the_case(tmp_path: Path) -> None:
    """Заголовок раздела ищется без учёта регистра — профиль пишет человек руками.

    Читателей у раздела двое, установщик и сторож, и читать они обязаны одинаково: разойдись они в
    регистре — установщик подключил бы сторожа к папке, которую тот сам папкой планов не считает,
    и сторож молчал бы всегда.
    """
    project = stub_project(tmp_path, "проект-строчный", profile_text(header="## plans"))
    done = run_nudge(project, f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-lower-case")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), (
        "раздел «## plans» строчными буквами не найден — сторож считает, что планов в проекте нет"
    )


@needs_pwsh
def test_without_a_plans_section_the_guard_has_no_folder_at_all(tmp_path: Path) -> None:
    """Папка не названа — значит её нет, и никаких значений по умолчанию на путь чужого проекта.

    Иначе сторож считал бы планом волны файлы в папке, о которой этот проект не знает вовсе.
    """
    project = stub_project(tmp_path, "проект-без-планов", profile_text(plans=None))
    quiet = run_nudge(project, f"{ALIEN_PLANS}2026-01-01-wave7-probe.md", "s-no-section")
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout.strip() == "", (
        "сторож заговорил о папке планов, которую профиль не называл, — путь взят по умолчанию"
    )

    # Тишина двусмысленна: «папки нет» и «сторож умер» выглядят одинаково. Назовём папку — и в том
    # же проекте сторож обязан заговорить.
    (project / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    named = run_nudge(project, f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-no-section-then")
    assert named.stdout.strip(), (
        "сторож молчит и после того, как папку назвали, — значит тишина выше была поломкой"
    )


@needs_pwsh
def test_delivery_guard_marks_its_worktree_alive(tmp_path: Path) -> None:
    """Маячок живой вкладки ставится на каждом ходу — даже когда показывать нечего.

    По нему сосед отличает работающую вкладку от брошенного дерева. Ставился бы он только вместе
    с показом находки — живой оказывалась бы ровно та вкладка, которой уже что-то положили.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    assert run_deliver(board, mine, "Prompt", "s-beacon").strip() == ""

    mark = mine / BEACON
    assert mark.exists(), "маячок не поставлен там, где доски нет вовсе — а ход был"
    assert "s-beacon" in mark.read_text(encoding="utf-8"), "в маячке не видно, чья это вкладка"

    stale = time.time() - 5 * 3600
    os.utime(mark, (stale, stale))
    run_deliver(board, mine, "Start", "s-beacon")
    assert mark.stat().st_mtime > stale + 3600, (
        "маячок не обновляется на ходу — через полсуток живую вкладку сочтут молчащей"
    )


@needs_pwsh
def test_tool_refuses_a_blank_title(tmp_path: Path) -> None:
    """Заголовок из одних пробелов не увидит никто: показ его прячет, доставка пропускает."""
    board = tmp_path / "board.jsonl"
    done = tool(board, "-Mode", "Add", "-To", "wave9-clock", "-Title", "   ")
    assert done.returncode != 0, "пустой заголовок принят с бодрым рапортом"
    assert not board.exists(), "невидимая запись всё-таки легла на доску"


@needs_pwsh
@pytest.mark.parametrize(
    "args",
    [
        ("-Mode", "Add", "-Title", "находка без адресата"),
        ("-Mode", "Add", "-To", "wave9-clock"),
        (
            "-Mode",
            "Done",
        ),
    ],
)
def test_tool_refuses_plainly(tmp_path: Path, args: tuple[str, ...]) -> None:
    """Отказ читает человек: рамка исключения PowerShell рвёт текст переносами и цветом."""
    board = tmp_path / "board.jsonl"
    done = tool(board, *args)
    assert done.returncode != 0, f"инструмент принял неполный вызов: {args}"
    assert "Exception" not in done.stderr, (
        f"отказ вылетел рамкой исключения вместо обычного текста: {done.stderr!r}"
    )
    assert "wave-board.ps1:" not in done.stderr, (
        "в отказе видны потроха скрипта — читать его человеку"
    )


def hold_the_board(board: Path, seconds: int = 30) -> subprocess.Popen[bytes]:
    """Держит доску на монопольный доступ — так её на доли секунды держат антивирус и архиватор.

    Возвращает держателя: его обязательно убить в finally, иначе файл останется занятым.
    """
    assert pwsh
    ready = board.parent / "holder-ready.txt"
    ready.unlink(missing_ok=True)
    script = (
        f"$s = [System.IO.File]::Open('{board}', 'Open', 'ReadWrite', 'None'); "
        f"Set-Content -LiteralPath '{ready}' -Value 'держим' -Encoding utf8; "
        f"Start-Sleep -Seconds {seconds}; $s.Dispose()"
    )
    holder = subprocess.Popen([pwsh, "-NoProfile", "-Command", script])
    deadline = time.time() + 30
    while time.time() < deadline:
        if ready.exists():
            return holder
        time.sleep(0.1)
    holder.kill()
    raise AssertionError("держатель доски так и не взял файл — проверять нечего")


@needs_pwsh
def test_compaction_refuses_when_the_board_cannot_be_read(tmp_path: Path) -> None:
    """Неудача чтения не должна выглядеть как пустая доска — иначе уплотнение сотрёт все находки.

    Чтение возвращало пустой список и когда файла нет, и когда его пять раз подряд не удалось
    открыть. Уплотнение проверяло только неизменность размера — а он как раз не менялся, никто не
    дописывал, — и заменяло доску пустым файлом с рапортом «было строк 0, осталось 0».
    """
    board = tmp_path / "board.jsonl"
    add(board, "wave9-clock", "находка, которую нельзя потерять")
    before = board.read_text(encoding="utf-8")

    holder = hold_the_board(board)
    try:
        done = tool(board, "-Mode", "Compact")
        assert done.returncode != 0, "уплотнение отработало вслепую, не сумев прочитать доску"
        assert "было строк 0" not in done.stdout, "уплотнение отрапортовало о пустой доске"
        assert "прочитать" in done.stderr, f"отказ не назвал причину: {done.stderr!r}"
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert board.read_text(encoding="utf-8") == before, (
        "доска изменилась, хотя прочитать её не вышло"
    )
    assert "находка, которую нельзя потерять" in run_tool(board, "-Mode", "Show"), (
        "находка исчезла с доски"
    )


@needs_pwsh
def test_commands_report_a_locked_board_instead_of_calling_it_empty(tmp_path: Path) -> None:
    """Занятая доска — это не пустая доска и не «запись уже закрыли»: причину надо назвать.

    Сбой чтения превращался в пустой список, и инструмент говорил «открытых записей нет» или
    «возможно, её уже закрыли» — то есть ровно противоположное правде. Дописывание после десяти
    попыток всегда говорило «доска занята», даже если места на диске не осталось.
    """
    board = tmp_path / "board.jsonl"
    mark = add(board, "wave9-clock", "находка под замком")

    holder = hold_the_board(board)
    try:
        shown = tool(board, "-Mode", "Show")
        assert shown.returncode != 0, "показ выдал занятую доску за прочитанную"
        assert "Открытых записей на доске волны нет" not in shown.stdout, (
            "занятая доска показана как пустая — это толкает завести дубль находки"
        )
        assert "прочитать" in shown.stderr, f"показ не назвал причину: {shown.stderr!r}"

        closing = tool(board, "-Mode", "Done", "-Id", mark)
        assert "уже закрыли" not in closing.stdout, (
            "закрытие соврало про «уже закрыли», хотя доску просто не удалось прочитать"
        )
        assert closing.returncode != 0

        adding = tool(board, "-Mode", "Add", "-To", "wave9-clock", "-Title", "ещё одна")
        assert adding.returncode != 0
        assert "Последняя причина:" in adding.stderr, (
            f"отказ дописывания не сохранил настоящую причину: {adding.stderr!r}"
        )
        # Причину читает человек, а системные сообщения приходят по-английски.
        assert "занят другим процессом" in adding.stderr, (
            f"причина отказа осталась английской: {adding.stderr!r}"
        )
        assert "being used by another process" not in adding.stderr
    finally:
        holder.kill()
        holder.wait(timeout=30)


@needs_pwsh
def test_compaction_refuses_a_board_that_parses_into_nothing(tmp_path: Path) -> None:
    """Непустой файл, из которого не разобралось ни одной записи, — повод остановиться, а не стереть.

    Так выглядит и сорванная запись, и чужая кодировка, и обрывок. Переписать такую доску значит
    потерять то, что в ней, возможно, ещё лежит.
    """
    board = tmp_path / "board.jsonl"
    board.write_text("что-то, чего мы не понимаем\nи ещё строка\n", encoding="utf-8")
    before = board.read_text(encoding="utf-8")

    done = tool(board, "-Mode", "Compact")
    assert done.returncode != 0, "уплотнение стёрло доску, в которой ничего не разобрало"
    assert board.read_text(encoding="utf-8") == before, "доска переписана вслепую"


@needs_pwsh
def test_broadcast_closes_only_for_the_stream_that_closed_it(tmp_path: Path) -> None:
    """Находку «всем» каждый адресат учитывает сам за себя.

    Общее закрытие по метке прятало её от всех разом: первый учёл — остальные не увидели никогда.
    Особенно тот, у кого с момента добавления не было ни хода, ни перезапуска.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "общая находка волны", cwd=author)
    assert "общая находка волны" in run_deliver(board, first, "Start", "s-first")
    closing = run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    assert "для потока" in closing, (
        "закрытие широковещательной записи не сказало, что оно персональное"
    )

    assert run_deliver(board, first, "Start", "s-first-again").strip() == "", (
        "закрывший поток продолжает получать учтённую находку"
    )
    assert "общая находка волны" in run_deliver(board, second, "Start", "s-second"), (
        "сосед не увидел находку «всем» после того, как её закрыл другой поток — а у него с момента "
        "добавления не было ни одного хода"
    )

    board_view = run_tool(board, "-Mode", "Show")
    assert "общая находка волны" in board_view, "показ спрятал ещё не всеми учтённую находку"
    assert "wave9-clock" in board_view, "показ не говорит, кто её уже учёл"


@needs_pwsh
def test_show_for_a_stream_includes_what_is_addressed_to_everyone(tmp_path: Path) -> None:
    """Показ по потоку обязан включать и то, что адресовано всем: доставка это принесёт.

    Ложное «записей нет» толкает уплотнить доску или завести дубль находки.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    add(board, "*", "общая находка волны", cwd=author)
    add(board, "wave9-truth", "чужая находка")

    view = run_tool(board, "-Mode", "Show", "-To", "wave9-clock")
    assert "общая находка волны" in view, (
        "показ по потоку скрыл запись «всем», хотя доставка принесёт её в ту же вкладку"
    )
    assert "чужая находка" not in view, "показ по потоку притащил чужую запись"


@needs_pwsh
def test_compaction_keeps_personal_closings_of_surviving_records(tmp_path: Path) -> None:
    """Уплотнение не должно возвращать потоку то, что он уже учёл.

    Запись «всем» переживает уплотнение, пока её учли не все, — а вместе с ней обязаны уцелеть
    именные закрытия, иначе учтивший поток получит находку заново.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "общая находка волны", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    run_tool(board, "-Mode", "Compact")

    assert run_deliver(board, first, "Start", "s-after-compact").strip() == "", (
        "после уплотнения учтённая находка вернулась к тому, кто её закрыл"
    )
    assert "общая находка волны" in run_deliver(board, second, "Start", "s-other-after-compact"), (
        "уплотнение унесло запись «всем», которую учли не все"
    )


def now_minus(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def board_line(**fields: object) -> str:
    """Строка доски, собранная руками: так задаётся давность, которую иначе не подделать."""
    record: dict[str, object] = {
        "id": "aaaa0001",
        "at": now_minus(0),
        "wave": "",
        "to": "*",
        "title": "находка",
        "where": "",
        "from": "wave9-author",
    }
    record.update(fields)
    return json.dumps(record, ensure_ascii=False)


def closing_line(mark: str, by: str = "", days_ago: float = 0) -> str:
    record: dict[str, object] = {"id": mark, "at": now_minus(days_ago), "done": True}
    if by:
        record["by"] = by
    return json.dumps(record, ensure_ascii=False)


@needs_pwsh
def test_broadcast_can_be_closed_for_everyone(tmp_path: Path) -> None:
    """У находки «всем» обязан быть и общий выход: тема исчерпана — снять её у всех разом.

    Без него запись не убрать с доски вовсе: персональное закрытие гасит её только у закрывшего,
    уплотнение бережёт её вместе с именными закрытиями, а каждое НОВОЕ рабочее дерево получает её
    на старте — хотя к той волне отношения не имеет.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "исчерпанная находка", cwd=author)
    closing = run_tool(board, "-Mode", "Done", "-Id", mark, "-ForAll", cwd=first)
    assert "для всех" in closing, "общее закрытие не сказало, что сняло запись у всех"

    assert run_deliver(board, second, "Start", "s-after-forall").strip() == "", (
        "снятая у всех находка продолжает приходить соседям"
    )
    assert "исчерпанная находка" not in run_tool(board, "-Mode", "Show"), (
        "снятая у всех находка осталась в показе доски"
    )
    run_tool(board, "-Mode", "Compact")
    assert "исчерпанная находка" not in board.read_text(encoding="utf-8"), (
        "уплотнение сохранило запись, снятую у всех"
    )


@needs_pwsh
def test_stale_broadcast_stops_travelling(tmp_path: Path) -> None:
    """Срок давности — предохранитель на случай, что общее закрытие забыли.

    Волна живёт недели; находка, не учтённая за две недели, устарела вместе с волной, а платят за
    неё контекстом все вкладки проекта, включая заведённые позже и к волне не относящиеся.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="stale001", at=now_minus(20), title="позапрошлая находка")
        + "\n"
        + closing_line("stale001", by="wave9-clock", days_ago=19)
        + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-truth"
    mine.mkdir()

    assert run_deliver(board, mine, "Start", "s-stale").strip() == "", (
        "просроченная находка «всем» всё ещё приходит во вкладки"
    )
    assert "позапрошлая находка" not in run_tool(board, "-Mode", "Show"), (
        "просроченная находка показана как открытая"
    )

    run_tool(board, "-Mode", "Compact")
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert left == [], f"уплотнение оставило просроченную запись или её именные закрытия: {left}"


@needs_pwsh
def test_addressed_record_never_goes_stale(tmp_path: Path) -> None:
    """Срок давности касается только записей «всем»: у адресной закрытие общее, выход у неё есть."""
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(
            id="old00001", at=now_minus(40), to="wave9-clock", title="давняя адресная находка"
        )
        + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    assert "давняя адресная находка" in run_deliver(board, mine, "Start", "s-old-addressed"), (
        "адресную находку заглушил срок давности — а её никто не закрывал"
    )


@needs_pwsh
def test_show_tells_apart_open_closed_and_stale(tmp_path: Path) -> None:
    """Показ различает три состояния: открыта, закрыта у вас, просрочена.

    Иначе непонятно, почему запись лежит в файле, но нигде не видна, и почему уплотнение то
    убирает её, то нет.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        "\n".join(
            [
                board_line(id="fresh001", title="свежая общая находка"),
                board_line(id="mine0001", title="учтённая мной находка"),
                closing_line("mine0001", by="wave9-clock"),
                board_line(id="stale002", at=now_minus(20), title="позапрошлая находка"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    view = run_tool(board, "-Mode", "Show", "-To", "wave9-clock")
    assert "свежая общая находка" in view, "открытая запись пропала из показа"
    assert "учтённая мной находка" not in view, "учтённая запись показана как открытая"
    assert "закрыто у вас" in view.lower(), (
        "показ не говорит, что запись закрыта именно у этого потока"
    )
    assert "просрочен" in view.lower(), "показ молчит о просроченных записях"


@needs_pwsh
def test_show_does_not_advise_compaction_that_would_remove_nothing(tmp_path: Path) -> None:
    """Совет уплотнить доску, с которой нечего убрать, — работа впустую и ложная надежда."""
    board = tmp_path / "board.jsonl"
    lines = [
        board_line(id=f"open{number:04d}", to="wave9-clock", title=f"находка {number}")
        for number in range(220)
    ]
    board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    view = run_tool(board, "-Mode", "Show")
    assert "-Mode Compact" not in view, (
        "показ советует уплотнение, хотя убирать с доски нечего — оно ничего не изменит"
    )


@needs_pwsh
def test_compaction_reports_lines_it_could_not_parse(tmp_path: Path) -> None:
    """Выброшенную нечитаемую строку надо назвать: молча стёртая находка неотличима от её отсутствия."""
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="good0001", to="wave9-clock", title="живая находка")
        + "\nобрывок записи, который не разбирается\n",
        encoding="utf-8",
    )
    out = run_tool(board, "-Mode", "Compact")
    assert "выброшено строк: 1" in out.lower(), (
        f"уплотнение молча выбросило нечитаемую строку: {out!r}"
    )
    assert "живая находка" in board.read_text(encoding="utf-8"), "уплотнение унесло здоровую запись"


@needs_pwsh
def test_show_for_a_stream_knows_both_of_its_names(tmp_path: Path, wave_repo: Path) -> None:
    """Поток зовут и веткой, и папкой, и сводятся они к РАЗНЫМ ключам, если имена разошлись.

    Закрытие пишет ключ по ветке, а показ спрашивали по имени папки — и он не узнавал собственное
    закрытие этого потока, показывая учтённую находку как открытую.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    odd = wave_repo / ".claude" / "worktrees" / "oddfolder-tab"

    mark = add(board, "*", "находка для потока с двумя именами", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=odd)

    # Показ идёт ИЗ репозитория с этими деревьями: обе формы имени потока git называет только там.
    view = run_tool(board, "-Mode", "Show", "-To", "oddfolder-tab", cwd=odd)
    assert "находка для потока с двумя именами" not in view, (
        "показ по имени папки не узнал закрытие, записанное по имени ветки того же дерева"
    )
    assert "закрыто у вас" in view.lower(), "показ не отнёс запись к закрытым у этого потока"


@needs_pwsh
def test_the_stream_that_closed_it_personally_can_still_close_it_for_everyone(
    tmp_path: Path,
) -> None:
    """Понять, что тема исчерпана, проще всего тому, кто её только что учёл.

    А он-то и оказывался заперт: своё персональное закрытие прячет запись от него самого, и общее
    закрытие ему уже отвечало «среди открытых нет» — то есть единственный выход с доски был закрыт
    ровно перед тем, кому он нужнее всего.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "исчерпанная тема", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    closing = run_tool(board, "-Mode", "Done", "-Id", mark, "-ForAll", cwd=first)
    assert "для всех" in closing, (
        "поток, уже учтивший находку, не смог снять её у всех — а он первым и понимает, что тема "
        "исчерпана"
    )
    assert run_deliver(board, second, "Start", "s-forall-after-personal").strip() == "", (
        "после снятия у всех находка продолжает приходить соседям"
    )


@needs_pwsh
def test_broadcast_with_a_broken_date_is_not_immortal(tmp_path: Path) -> None:
    """Испорченная дата не должна давать записи «всем» бессрочность.

    Строку доски правят руками, приносит её другая версия инструмента — и дата оказывается пустой,
    числовой или невнятной. Срок давности на такую запись не действовал вовсе: она доставлялась
    каждому НОВОМУ дереву и переживала уплотнение, то есть в узком случае возвращалась ровно та
    дыра, ради которой срок давности и заводили. Безопасная сторона — считать её просроченной:
    находка с испорченной датой к учёту всё равно не годится.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        "\n".join(
            [
                board_line(id="broke001", at="", title="находка без даты"),
                board_line(id="broke002", at=1234567890, title="находка с числом вместо даты"),
                board_line(id="broke003", at="позавчера", title="находка с невнятной датой"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-truth"
    mine.mkdir()

    assert run_deliver(board, mine, "Start", "s-broken").strip() == "", (
        "запись с испорченной датой всё ещё доставляется — а снять её с доски нечем"
    )
    view = run_tool(board, "-Mode", "Show")
    assert "находка без даты" not in view, "запись с испорченной датой показана как открытая"
    assert "испорчен" in view.lower(), (
        "показ молчит о записях с испорченной датой — человек не поймёт, куда делась находка"
    )

    run_tool(board, "-Mode", "Compact")
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert left == [], f"уплотнение сохранило записи с испорченной датой: {left}"


@needs_pwsh
def test_a_ten_day_old_broadcast_is_still_alive(tmp_path: Path) -> None:
    """Живой край срока давности: обещанные правилами две недели должны быть настоящими.

    Тихо укоротить срок легко (14 → 1), и ни один тест этого не замечал: проверялась только
    просроченная сторона. Тогда находка «всем» исчезала бы раньше, чем её успели учесть, а правила
    волн продолжали бы обещать две недели.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="fresh010", at=now_minus(10), title="ещё живая общая находка") + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-truth"
    mine.mkdir()

    assert "ещё живая общая находка" in run_deliver(board, mine, "Start", "s-ten-days"), (
        "находка десятидневной давности не доставлена — срок давности укоротили"
    )
    assert "ещё живая общая находка" in run_tool(board, "-Mode", "Show"), (
        "находка десятидневной давности пропала из показа"
    )
    run_tool(board, "-Mode", "Compact")
    assert "ещё живая общая находка" in board.read_text(encoding="utf-8"), (
        "уплотнение унесло находку, которой всего десять дней"
    )


@needs_pwsh
def test_closing_the_same_record_twice_tells_the_truth(tmp_path: Path) -> None:
    """Повторное закрытие своей же записи не должно врать про «закрыли у всех».

    Поток уже погасил находку «всем» у себя; закрывает ещё раз — и слышит, что её сняли у всех или
    что она просрочена, хотя не случилось ни того, ни другого. Ответ обязан назвать настоящее
    положение и подсказать существующий выход.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()

    mark = add(board, "*", "находка для двойного закрытия", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    again = run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)

    assert "у себя" in again, f"повторное закрытие не назвало настоящего положения: {again!r}"
    assert "-ForAll" in again, "повторное закрытие не подсказало, как снять запись у всех"
    assert "просрочена" not in again, "повторное закрытие соврало про просроченность"


@needs_pwsh
def test_show_counts_stale_records_of_the_asked_wave_only(tmp_path: Path) -> None:
    """Отбор по волне один на список и на счёт состояний: иначе строка «просрочено» врёт."""
    board = tmp_path / "board.jsonl"
    board.write_text(
        "\n".join(
            [
                board_line(id="w9stale1", at=now_minus(20), wave="9", title="просроченная волны 9"),
                board_line(id="w8stale1", at=now_minus(20), wave="8", title="просроченная волны 8"),
                board_line(id="w9open01", wave="9", title="живая волны 9"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    view = run_tool(board, "-Mode", "Show", "-Wave", "9")
    assert "живая волны 9" in view, "открытая запись нужной волны пропала"
    assert "просрочено (старше 14 дней) — 1" in view, (
        f"счёт просроченных считает чужие волны, хотя список отобран по волне: {view!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Реестр заявок: кто ведёт поток прямо сейчас.
#
# Доска отвечает «что передали», реестр — «кому и жив ли он». Без реестра адрес выводится из имени
# ветки, а имена врут: в плане волны 6 у двух потоков объявлена одна ветка, а вкладки работают на
# других, и одну папку вкладка успела занять под другую работу.
# ─────────────────────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Реестр КАК ЦЕЛОЕ: одно чтение всех заявок и четыре утверждения над ними.
#
# Помощники стенда прежде перебирали каталог и брали ПЕРВУЮ запись, совпавшую по рабочей папке,
# молча считая, что совпадение одно. Пока у каждой папки в реестре лежала ровно одна заявка, это
# сходилось. С появлением перенесённых записей — тех, у кого адрес забрала другая папка, — первая
# попавшаяся перестаёт быть той, о которой спрашивают: стенд начал бы закреплять поведение
# погашенной записи и не замечать живую, причём молча и по-разному от прогона к прогону (порядок
# описи каталога никто не обещал). Отсюда правило: помощник берёт НЕЗАКРЫТУЮ запись, а на
# неоднозначности падает вслух — молчаливый выбор здесь и есть тот дефект, который стенд обязан
# ловить.
#
# Сверх того здесь живут инварианты реестра целиком. До них ни одна проверка набора не смотрела на
# реестр как на целое: правка ключа вкладки могла оставить в нём призрака и не уронить ни одного
# теста. Снимаются они в ХВОСТЕ каждой проверки (приспособа `registry_invariants` ниже), а не
# отдельным тестом: отдельный тест закрепил бы одну искусственную сцену, а нужен присмотр за
# каждым сценарием объявления и сдачи — в том числе за теми, которых ещё не написали.
# ─────────────────────────────────────────────────────────────────────────────────────────────

# Поле преемства: новый владелец адреса называет в СВОЕЙ заявке рабочую папку, у которой этот адрес
# забран. Механизм его пишет — но только там, где перенос и вправду был: заявки прежней версии его
# не несут вовсе, и отсутствие поля читается как «переноса не было», а не как порча.
#
# Рядом с полем механизм пишет МОМЕНТ переноса (`taken_at`): им и только им решается, действует ли
# ребро переноса. Заявка, начавшаяся ПОЗЖЕ этого момента, ребром не гасится — иначе прежняя папка
# не смогла бы объявиться на освободившемся адресе заново, а возврат адреса тем же ключом гасил бы
# обе стороны разом.
#
# ‼️ Имя поля здесь и в механизме обязано совпадать. Разойдутся — инварианты перестанут видеть
# перенос и замолчат ровно там, где их завели: две записи одного адреса начнут считаться законными,
# а погашенная запись — живой.
TAKEN_FROM_FIELD = "taken_from"

# Момент переноса — вторая половина той же точки сговора: без него ребро переноса не отличить от
# вечного, и стенд разошёлся бы с механизмом ровно там, где тот перестал гасить свежие заявки.
TAKEN_AT_FIELD = "taken_at"

# Список ПРОШЛЫХ переездов этой заявки — третья половина той же точки сговора. Память о переезде
# лежит в заявке забравшей папки, а заявка на папку ОДНА: как только та же папка бралась за
# следующий поток, её файл переписывался, ребро исчезало — и брошенная запись прежней папки снова
# становилась ведущей, причём молча. Список переносит переезды прежней заявки папки в новую, и
# каждая его запись несёт СВОЙ адрес: у прошлого переезда он не тот, что у заявки сейчас.
#
# Внутри записи списка живут те же три имени, что и у нынешнего переезда (папка, момент), плюс
# волна и номер того адреса, у которого забирали.
PAST_TAKEOVERS_FIELD = "past_takeovers"

# След «кто сдал» в записи, закрытой ПО АДРЕСУ. Своя сдача его не пишет — там сдавший и владелец
# одна и та же вкладка; здесь запись закрыл посторонний, и без следа сдача сироты была бы
# неотличима от честной сдачи самой вкладкой.
RELEASED_FROM_FIELD = "released_from"


def registry_dir(board: Path) -> Path:
    """Каталог заявок этой доски — там же, где его ищет сам инструмент."""
    return board.parent / "streams"


def folder_key(path: object) -> str:
    """Рабочая папка в одном виде: слэши вперёд, без хвостового, без разницы в регистре букв."""
    return str(path or "").replace("\\", "/").rstrip("/").lower()


def moment_of(raw: object) -> datetime | None:
    """Время из поля заявки; пустота честно значит «не знаем», а не «начало времён».

    Разница важна: на ней стоит решение, действует ли ребро переноса. Выдумай стенд значение — он
    ответил бы иначе, чем механизм, и начал бы закреплять не то поведение.
    """
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def read_claim_json(path: Path) -> dict[str, object] | None:
    """Разбирает файл заявки; нечитаемое и неразбираемое даёт «нет записи», а не падение.

    Кодировки перебираем те же, что терпит инструмент: заявка приезжает и в UTF-16, и с меткой
    порядка байт. Порча (пустой файл, обрезанный на середине, заявка чужой версии) и занятость
    файла соседом — законные состояния стенда, набор заводит их нарочно; падать на них помощникам
    нельзя, для порчи в наборе есть свои проверки.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            parsed = json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


@dataclass(frozen=True)
class Takeover:
    """Один переезд адреса: у КАКОГО адреса, у чьей папки и когда его забрали.

    ‼️ Адрес хранится в самом переезде, а не берётся у нынешней заявки: заявка могла с тех пор
    уехать на другой номер или взяться за следующий поток, а ребро остаётся про тот адрес, у
    которого забирали.
    """

    wave: str
    stream: str
    taken_from: str
    taken_at: datetime | None

    @property
    def address(self) -> str:
        return f"{self.wave}/{self.stream}".lower()


@dataclass
class ClaimRecord:
    """Одна разобранная заявка реестра: её файл и её поля."""

    file: Path
    fields: dict[str, object]

    @property
    def worktree(self) -> str:
        return folder_key(self.fields.get("worktree"))

    @property
    def address(self) -> str:
        """Адрес потока — так, как его назовут соседи."""
        return f"{self.fields.get('wave', '')}/{self.fields.get('stream', '')}".lower()

    @property
    def addressed(self) -> bool:
        """Есть ли у записи адрес вовсе: у заявки чужой версии волны и номера может не быть."""
        return bool(self.fields.get("wave")) and bool(self.fields.get("stream"))

    @property
    def released(self) -> bool:
        return str(self.fields.get("state", "")) == "released"

    @property
    def taken_from(self) -> str:
        """Папка, у которой эта заявка забрала адрес; пусто — переноса не было."""
        return folder_key(self.fields.get(TAKEN_FROM_FIELD))

    @property
    def taken_at(self) -> datetime | None:
        """Момент переноса; пусто — заявка прежней версии, момента она не несёт."""
        return moment_of(self.fields.get(TAKEN_AT_FIELD))

    @property
    def claimed_at(self) -> datetime | None:
        """Момент объявления; пусто — заявка собрана руками или её время не разобрать."""
        return moment_of(self.fields.get("claimed_at"))

    @property
    def takeovers(self) -> list[Takeover]:
        """ВСЕ переезды этой записи: нынешний и каждый прошлый из списка.

        ‼️ Разбор обязан совпадать с механизмом слово в слово. Дубли ОДНОГО переезда (тот же адрес
        у той же папки) схлопываются, и остаётся позднейший по времени: ранний момент гасит меньше,
        чем нужно, — заявка потерпевшей, поданная между двумя переездами, ушла бы из-под ребра.
        Неизвестный момент считается самым поздним: ребро без момента действует безусловно.
        А переезды одного адреса у РАЗНЫХ папок не схлопываются никогда: это разные рёбра, и
        потеря любого воскрешает свою потерпевшую.
        """
        moves: list[Takeover] = []
        if self.taken_from:
            moves.append(
                Takeover(
                    wave=str(self.fields.get("wave", "")),
                    stream=str(self.fields.get("stream", "")),
                    taken_from=self.taken_from,
                    taken_at=self.taken_at,
                )
            )
        listed = self.fields.get(PAST_TAKEOVERS_FIELD)
        for past in listed if isinstance(listed, list) else []:
            if not isinstance(past, dict):
                continue
            moves.append(
                Takeover(
                    wave=str(past.get("wave", "")),
                    stream=str(past.get("stream", "")),
                    taken_from=folder_key(past.get(TAKEN_FROM_FIELD)),
                    taken_at=moment_of(past.get(TAKEN_AT_FIELD)),
                )
            )
        found: dict[tuple[str, str], Takeover] = {}
        for move in moves:
            # Безадресный переезд — это не переезд: гасить по нему нечего, а два безадресных
            # соседа сошлись бы «адресом» из двух пустот.
            if not move.wave or not move.stream or not move.taken_from:
                continue
            key = (move.address, move.taken_from)
            known = found.get(key)
            if known is not None and (known.taken_at or datetime.max) >= (
                move.taken_at or datetime.max
            ):
                continue
            found[key] = move
        return list(found.values())


def read_registry(folder: Path) -> list[ClaimRecord]:
    """Весь реестр разом, в устойчивом порядке имён файлов."""
    records: list[ClaimRecord] = []
    try:
        files = sorted(folder.glob("*.json"))
    except OSError:
        return records
    for path in files:
        fields = read_claim_json(path)
        if fields is not None:
            records.append(ClaimRecord(file=path, fields=fields))
    return records


def names_of(records: list[ClaimRecord]) -> str:
    """Записи в одну строку: по имени файла и папке владелец найдёт их в реестре руками."""
    return ", ".join(sorted(f"{record.file.name} (папка {record.worktree})" for record in records))


def supersessions(records: list[ClaimRecord]) -> tuple[set[int], list[str]]:
    """Кто в реестре погашен переносом — и всё, что в переносах не сходится.

    ‼️ Разбор обязан совпадать с механизмом слово в слово: разойдись он — стенд начнёт закреплять
    поведение, которого у механизма нет, и молчать там, где тот поднимает тревогу.

    Ребро ведёт от заявки, забравшей адрес, к заявке, у которой его забрали: заявка называет чужую
    рабочую папку, и адрес у обеих один. Действует ребро по ВРЕМЕНИ: оно не действует ровно тогда,
    когда ДОКАЗАНО, что заявка потерпевшей началась ПОЗЖЕ момента переноса. Момента у самого
    переезда нет (его не несёт заявка невыпущенной промежуточной версии) — берётся момент
    ОБЪЯВЛЕНИЯ забравшей записи, ровно как у механизма. Нет и его или неизвестно время объявления
    потерпевшей — ребро действует: незнание не должно воскрешать призрака.

    Взаимные рёбра (круг возврата, уложившийся в одну секунду) разводит полный ключ порядка —
    время объявления, при равенстве путь дерева: остаётся ребро СТАРШЕЙ записи. Тем же правилом
    разрешается спор за номер потока, и оно одинаково у всех вкладок.

    ‼️ Записи БЕЗ адреса в переносах не участвуют вовсе — так же, как их пропускает механизм. Иначе
    два безадресных соседа сходятся «адресом» из двух пустот, и стенд видит ребро, которого нет.

    ‼️ СДАННАЯ заявка перенос держит наравне с открытой. Прежде стенд её пропускал («вкладки нет,
    значит и забирать некому»), и это была его собственная ошибка: поток переехал, честно
    доработал и сдался — а брошенная запись в прежней папке снова становилась ведущей и опять
    держала адрес живым. Стенд при этом объявлял такую сцену выдачей номера второй раз, хотя
    записи связаны переносом. Перенос — событие в истории АДРЕСА, и сдача его не отменяет.
    """
    order = [(record.claimed_at or datetime.max, record.worktree) for record in records]
    # ‼️ Момент берётся у САМОГО ПЕРЕЕЗДА, а не у нынешних полей заявки: прошлый переезд той же
    # записи случился в другое время и был про другой адрес.
    drawn = {
        (i, j): at
        for i, j, at in succession_edges(records)
        if not (at and records[j].claimed_at) or records[j].claimed_at <= at
    }

    taken_by: dict[int, tuple[int, datetime | None]] = {}
    for (i, j), at in sorted(drawn.items(), key=lambda edge: edge[0]):
        if (j, i) in drawn and order[i] > order[j]:
            continue
        known = taken_by.get(j)
        if known is not None:
            # Забравших несколько — ведущим называем последнего: адрес сейчас у него. Момент
            # неизвестен — считаем самым поздним, как и механизм.
            rival = (known[1] or datetime.max, records[known[0]].worktree)
            if rival >= (at or datetime.max, records[i].worktree):
                continue
        taken_by[j] = (i, at)
    superseded = set(taken_by)

    faults: list[str] = []
    for address, group in sorted(by_address(records).items()):
        here = {index for index, _ in group}
        if not here or not here <= superseded:
            continue
        # ‼️ КРУГ — это когда записи адреса гасят ДРУГ ДРУГА: каждую погасила запись этого же
        # адреса, сама погашенная. Прежде условием было одно «все записи адреса погашены», и
        # раньше оно круг и означало: погасить последнюю запись адреса мог только сосед по тому
        # же адресу. С памятью переездов это перестало быть верным — гасит и запись ДРУГОГО
        # адреса (папка забрала адрес, а потом взялась за следующий поток), — и утверждение
        # начало кричать «круг» на самой частой законной сцене, ради которой правка и делалась.
        # Про сцену «у адреса не осталось ведущей записи» есть отдельное, пятое утверждение, и
        # называет оно её своим именем.
        if any(taken_by[index][0] not in here for index in here):
            continue
        faults.append(
            f"перенос адреса {address} ходит по кругу — записи гасят друг друга, и ведущей не "
            f"остаётся ни одной: {names_of([record for _, record in group])}"
        )
    for j in sorted(superseded):
        alive = [records[i] for i, loser in drawn if loser == j and i not in superseded]
        if len(alive) > 1:
            faults.append(
                f"адрес {records[j].address} забран у папки {records[j].worktree} сразу дважды "
                f"({names_of(alive)}) — какая из этих записей ведущая, реестр не говорит"
            )
    return superseded, faults


def by_address(records: list[ClaimRecord]) -> dict[str, list[tuple[int, ClaimRecord]]]:
    """Записи по адресам — только те, у кого адрес есть вовсе."""
    grouped: dict[str, list[tuple[int, ClaimRecord]]] = {}
    for index, record in enumerate(records):
        if record.addressed:
            grouped.setdefault(record.address, []).append((index, record))
    return grouped


def succession_edges(records: list[ClaimRecord]) -> list[tuple[int, int, datetime | None]]:
    """Рёбра переноса: кто у кого забрал адрес и КОГДА — по каждому переезду каждой записи.

    ‼️ Ребро строится по каждому переезду — и по нынешнему, и по каждому прошлому, — а адрес
    берётся у САМОГО ПЕРЕЕЗДА. Иначе память о переезде живёт ровно до того дня, когда ту же папку
    возьмут под следующий поток: файл заявки на папку один, он переписывается, и брошенная запись
    прежней папки снова становится ведущей.

    ‼️ Момента у переезда нет — берём момент ОБЪЯВЛЕНИЯ забравшей записи, ровно как механизм.
    Безусловное ребро запирало адрес за потерпевшей навсегда: сколько бы раз она ни объявлялась
    заново, ребро гасило каждую её свежую заявку, а напечатанный ей выход не работал. Поле
    преемства без момента могла написать только невыпущенная промежуточная версия, и писала она
    оба поля в один и тот же миг объявления, — значит потерь от подстановки нет.
    """
    found: list[tuple[int, int, datetime | None]] = []
    for i, taker in enumerate(records):
        for move in taker.takeovers:
            at = move.taken_at if move.taken_at is not None else taker.claimed_at
            for j, loser in enumerate(records):
                if i == j or loser.worktree != move.taken_from or loser.address != move.address:
                    continue
                found.append((i, j, at))
    return found


def held_addresses(record: ClaimRecord) -> set[str]:
    """Адреса, которые эта запись когда-либо держала: нынешний и каждый забранный ею прежде.

    Нужны ИСТОРИИ адреса, а не гашению. Папку переиспользуют: запись, забравшая когда-то адрес,
    может сегодня вести уже другой поток — но из истории того адреса она никуда не делась, и без
    неё цепочка переездов рвётся ровно посередине.
    """
    found = {record.address} if record.addressed else set()
    return found | {move.address for move in record.takeovers}


def succession_links(records: list[ClaimRecord]) -> set[tuple[int, int]]:
    """Пары «забравшая — потерпевшая»: заявка называет чужую рабочую папку, и адрес у обеих один.

    ‼️ Времени здесь не спрашивают вовсе, и это не упущение. Действует ли ребро — вопрос отдельный
    (его решает `supersessions`), а вот СВЯЗЬ записей друг с другом остаётся навсегда: ею и
    отличается история одного потока от выдачи одного номера двум разным.

    ‼️ И по той же причине потерпевшую здесь узнают ПО ВСЕЙ ЕЁ ИСТОРИИ адресов, а не по нынешнему:
    средняя папка цепочки A→B→C могла с тех пор взяться за другой поток, и связь C с A иначе
    порвалась бы — стенд объявил бы один номер выданным дважды там, где это одна история.
    """
    links: set[tuple[int, int]] = set()
    for i, taker in enumerate(records):
        for move in taker.takeovers:
            for j, loser in enumerate(records):
                if i == j or loser.worktree != move.taken_from:
                    continue
                if move.address not in held_addresses(loser):
                    continue
                links.add((i, j))
    return links


def succession_roots(records: list[ClaimRecord], links: set[tuple[int, int]]) -> dict[int, int]:
    """Каждой записи — корень её группы преемства. Группы считаются по ВСЕМУ реестру.

    ‼️ По всему, а не по записям одного адреса: связь двух записей адреса может идти ЧЕРЕЗ третью,
    которая сегодня ведёт другой поток (средняя папка цепочки переездов, взявшаяся за следующую
    работу). Считай мы группы внутри адреса, такая цепочка распалась бы на две — и стенд объявил бы
    один номер выданным дважды там, где это одна история одного потока.
    """
    parent = {index: index for index in range(len(records))}

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for taker, loser in sorted(links):
        parent[root(taker)] = root(loser)
    return {index: root(index) for index in parent}


def registry_faults(folder: Path) -> list[str]:
    """Пять утверждений о реестре целиком — по строке на каждое нарушение.

    1. Не больше одной ВЕДУЩЕЙ записи на адрес (ведущая — незакрытая и не перенесённая). Две
       ведущие означают, что кому придёт находка, решает порядок описи каталога.
    2. Не больше одной незакрытой записи на одну рабочую папку: вторая либо стёрла первую, либо
       двоит поток этой папки снаружи.
    3. У каждой перенесённой записи ровно одна переносящая, и перенос не ходит по кругу: круг —
       это когда все записи адреса погашены и каждую погасила запись ЭТОГО ЖЕ адреса.
       ‼️ Переносящая при этом сама может быть перенесённой: цепочка переездов A→B→C законна, в ней
       гасятся и A, и B, а ведёт один C. И ‼️ «погашены все записи адреса» само по себе кругом НЕ
       является: гасит и запись другого адреса — папка забрала адрес, а потом взялась за следующий
       поток. Про эту сцену говорит пятое утверждение, и говорит своим именем.
    4. Ни один номер, когда-либо занятый в волне, не выдаётся второй раз — ни после сдачи, ни
       после переноса: все записи одного адреса обязаны быть ОДНИМ потоком, связанным переносами.
    5. У адреса, за которым числятся незакрытые в своих файлах заявки, есть хоть одна ВЕДУЩАЯ
       запись. Ноль ведущих при живых файлах — это вкладка, которой снаружи не существует: она
       считает, что ведёт поток, а находку по адресу приём не примет.

    Записи без адреса и без рабочей папки в счёт не идут: так выглядит заявка чужой версии, и
    порчей она не считается — это отдельно оговорено в решении.
    """
    records = read_registry(folder)
    superseded, faults = supersessions(records)

    leading: dict[str, list[ClaimRecord]] = {}
    per_folder: dict[str, list[ClaimRecord]] = {}
    for i, record in enumerate(records):
        if record.worktree and not record.released:
            per_folder.setdefault(record.worktree, []).append(record)
        if record.addressed and i not in superseded and not record.released:
            leading.setdefault(record.address, []).append(record)

    doubled: set[str] = set()
    for address, found in sorted(leading.items()):
        if len(found) > 1:
            doubled.add(address)
            faults.append(
                f"адрес {address} ведут сразу {len(found)} записи ({names_of(found)}) — кому из "
                "них придёт находка, решает порядок описи каталога"
            )
    # ‼️ Обратная беда того же рода: у адреса есть незакрытые в своих файлах записи, а ведущей — ни
    # одной. Так выглядит вкладка, которой снаружи не существует: её файл открыт, она считает, что
    # ведёт поток, а находку по этому адресу приём не примет и сторож доставки не принесёт. Своим
    # сторожем это не стерёг никто: круг переноса ловится только тогда, когда погашены ВСЕ записи
    # адреса, а стоит одной из них быть сданной — и сцена проходила молча.
    for address, group in sorted(by_address(records).items()):
        if address in leading:
            continue
        orphans = [record for _, record in group if not record.released]
        if orphans:
            faults.append(
                f"у адреса {address} ведущей записи не осталось, а незакрытые заявки есть "
                f"({names_of(orphans)}) — снаружи этих вкладок не существует"
            )
    for here, found in sorted(per_folder.items()):
        if len(found) > 1:
            faults.append(
                f"в папке {here} незакрытых заявок сразу {len(found)} ({names_of(found)}) — "
                "поток этой папки снаружи двоится"
            )
    # ‼️ Номер, занятый в волне, не выдаётся второй раз. Записи одного адреса, связанные
    # преемством, — это история ОДНОГО потока: переезд, цепочка переездов, возврат адреса тем же
    # ключом и объявление прежней папки на честно сданном адресе. Связь здесь смотрится БЕЗ
    # времени: действует ребро или уже нет — вопрос отдельный, а связь остаётся навсегда. Две
    # несвязанные группы на один адрес — это и есть два разных потока с одним именем.
    roots = succession_roots(records, succession_links(records))
    for address, group in sorted(by_address(records).items()):
        if address in doubled:
            continue
        if len({roots[index] for index, _ in group}) > 1:
            faults.append(
                f"номер {address} выдан второй раз ({names_of([record for _, record in group])}): "
                "записи не связаны переносом, значит это два разных потока с одним адресом"
            )
    return faults


def assert_registry_invariants(board: Path) -> None:
    """Снимает инварианты реестра этой доски. Нарушения перечисляет ВСЕ, а не первое попавшееся."""
    folder = registry_dir(board)
    faults = registry_faults(folder)
    assert not faults, "реестр заявок противоречив ({}):\n  • {}".format(
        folder, "\n  • ".join(faults)
    )


class RegistryWatch:
    """Присмотр за реестром в хвосте проверки — и явный, названный причиной отказ от него."""

    def __init__(self) -> None:
        self.waived = ""

    def waive(self, reason: str) -> None:
        """Снимает присмотр с ЭТОЙ проверки. Причина пишется словами: молча пропускать нельзя."""
        self.waived = reason


@pytest.fixture(autouse=True)
def registry_invariants(tmp_path: Path) -> Iterator[RegistryWatch]:
    """Инварианты реестра снимаются в хвосте КАЖДОЙ проверки, а не отдельным тестом.

    Отдельный тест закрепил бы одну искусственную сцену. Нужен же присмотр за каждым сценарием
    объявления и сдачи — и за теми, которые напишут после этой правки, — иначе будущая правка
    оставит в реестре призрака и не уронит ни одного теста. Поэтому проверка идёт сама, по всем
    реестрам, заведённым во временной папке теста, и добавлять её в новый сценарий не надо.

    Отказаться можно ровно одним способом — попросив эту приспособу и назвав причину вслух:
    `registry_invariants.waive("почему")`. Молчаливого пропуска нет: нарушенный инвариант обязан
    быть либо починен, либо назван.
    """
    watch = RegistryWatch()
    yield watch
    if watch.waived:
        return
    for folder in sorted(tmp_path.rglob("streams")):
        if folder.is_dir():
            assert_registry_invariants(folder.parent / "board.jsonl")


# ‼️ ЕДИНСТВЕННАЯ бухгалтерия отказов от присмотра за реестром: имя проверки → почему сторож снят.
#
# Заводится она потому, что прежняя бухгалтерия жила комментарием у одной из проверок и была
# НЕВЕРНОЙ: комментарий уверял, что это «единственное место набора, где инвариант снят нарочно», а
# мест было шесть. На таком учёте держится вся дисциплина отказов — неверный он хуже отсутствующего:
# читающий верит комментарию и не идёт смотреть остальные.
#
# Мест три вида, и путать их нельзя.
#   • Проверки СЦЕНАРИЕВ, где реестр нарочно противоречив, а проверяется механизм. Все они
#     собирают задвоенный адрес РУКАМИ (`put_claim`) — так выглядит наследие дефекта 1 в реестре,
#     на который правку выкатывают. Сам механизм задвоить адрес больше не может: правило адреса
#     отказывает ДО записи. Разобрать наследие механизм тоже не берётся молча — показ кричит о нём
#     громкой строкой, а гасит его ключ переноса, по решению человека.
#   • Проверки САМОГО сторожа: они собирают реестры руками и спрашивают, ловит ли он их. Снимать
#     присмотр там нужно навсегда — иначе сторож упал бы на собственных подопытных сценах.
#   • Сцены, где адрес ЗАКОНЧИЛСЯ без ведущей записи, и это правильный исход: поток переехал и в
#     новой папке сдался или уехал дальше, а брошенная запись прежней папки осталась открытой.
#     Инвариант «у адреса есть ведущая запись» тут снимается намеренно — он для того и заведён,
#     чтобы такие сцены нельзя было завести МОЛЧА; проверяется же ровно то, что о них говорят
#     вслух: показ кричит, приём отказывает, объявление не рапортует успехом.
#
# Список сверяется машиной (проверка ниже), поэтому устареть молча он больше не может.
WAIVED_SCENES: dict[str, str] = {
    # Сценарии: реестр противоречив нарочно, проверяется механизм.
    "test_show_keeps_one_order_on_the_same_registry": (
        "инвариант «одна ведущая запись на адрес»: две записи одного адреса собраны руками как "
        "наследие дефекта — на них и проверяется полнота порядка показа"
    ),
    "test_show_shouts_about_a_doubled_address": (
        "инвариант «одна ведущая запись на адрес»: задвоение собрано руками — проверяется, что "
        "показ о нём кричит, а не молчит"
    ),
    "test_adding_a_finding_to_a_doubled_address_says_it_may_reach_the_wrong_tab": (
        "инвариант «одна ведущая запись на адрес»: задвоение собрано руками как наследие дефекта — "
        "проверяется, что о нём кричит и приём находки, а не только показ"
    ),
    "test_a_reclaim_that_names_only_the_wave_keeps_its_seniority": (
        "инвариант «одна ведущая запись на адрес»: соперник на тот же номер собран руками, и "
        "проверяется как раз то, что вкладка адреса НЕ отдаёт. Правило адреса эту пару не "
        "разводит намеренно: номер здесь УНАСЛЕДОВАН у своей же прежней записи, то есть остаётся "
        "выданным, а на выданный номер правило адреса не распространяется — там номер можно "
        "двигать, и спор разрешает круг уступки"
    ),
    # Проверки самого сторожа: реестры собраны руками, подопытный — он сам.
    "test_registry_invariants_catch_a_doubled_address": (
        "присмотр снят целиком: реестр собран противоречивым нарочно, подопытный — сам сторож"
    ),
    "test_registry_invariants_catch_every_broken_shape": (
        "присмотр снят целиком: реестры собраны противоречивыми нарочно, подопытный — сам сторож"
    ),
    "test_registry_invariants_pass_the_registries_the_tool_really_makes": (
        "присмотр снят целиком: реестры собраны руками, проверяется молчание сторожа на законных"
    ),
    "test_registry_invariants_catch_an_address_without_a_leader": (
        "присмотр снят целиком: реестр собран противоречивым нарочно, подопытный — сам сторож"
    ),
    "test_show_shouts_about_an_address_left_without_a_leader": (
        "присмотр снят целиком: реестр собран руками именно такой сценой — проверяется, что показ "
        "о ней кричит"
    ),
    # Сцены законного конца адреса: ведущей записи у него не остаётся, и это проверяемый исход.
    "test_a_finding_for_a_released_stream_is_refused_even_after_the_address_moved": (
        "инвариант «у адреса есть ведущая запись»: поток переехал и в новой папке честно сдался, "
        "а брошенная запись прежней папки осталась открытой — проверяется, что находку по такому "
        "адресу не принимают"
    ),
    "test_a_move_outlives_the_folder_taken_by_the_next_stream": (
        "инвариант «у адреса есть ведущая запись»: поток переехал, сдался, и папку заняли под "
        "следующий — проверяется, что призрак прежней папки ведущим не становится"
    ),
    "test_the_answer_names_the_folder_where_the_address_really_went": (
        "инвариант «у адреса есть ведущая запись»: поток переехал по цепочке и в последней папке "
        "закончился — проверяется, какую папку при этом называют потерпевшей"
    ),
    "test_a_dead_end_is_never_printed_as_the_way_out": (
        "инвариант «у адреса есть ведущая запись»: поток переехал и там закончился — проверяется, "
        "что напечатанный потерпевшей выход работает, а не советует ключ, которому нечего забрать"
    ),
    "test_the_invariant_never_calls_a_lawful_move_a_circle": (
        "присмотр снят целиком: реестр собран руками именно такой сценой — подопытный сам сторож, "
        "и проверяется, каким ИМЕНЕМ он её называет"
    ),
    "test_the_older_copy_keeps_the_memory_of_past_moves_it_does_not_understand": (
        "инвариант «у адреса есть ведущая запись»: та же сцена законного конца адреса — "
        "проверяется, что ход старой копии комплекта память о переезде не стирает"
    ),
}


def test_every_waiver_of_the_registry_watch_is_listed_in_the_ledger() -> None:
    """Бухгалтерия отказов сверяется машиной: список выше обязан совпадать с кодом набора.

    Прежде она была комментарием, и комментарий врал: он называл одно место, а их было шесть.
    Комментарий устаревает молча — читающий верит ему и не идёт пересчитывать сам, а на этом учёте
    держится вся дисциплина: отказ от инварианта допустим, пока он назван и объяснён.

    Поэтому список стал единственным, а сверка — машинной: добавили отказ и не вписали его (или
    вписали, а отказ убрали) — проверка падает и называет расхождение поимённо.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    waived: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "waive"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id == "registry_invariants"
            ):
                waived.add(node.name)

    listed = set(WAIVED_SCENES)
    assert waived == listed, (
        "бухгалтерия отказов разошлась с кодом набора — учёт, которому нельзя верить, хуже, чем "
        "никакого.\n  сняли присмотр, но не вписали: {}\n  вписаны, но присмотр не снимают: {}"
    ).format(sorted(waived - listed) or "нет", sorted(listed - waived) or "нет")


def put_claim(folder: Path, file_name: str, **fields: object) -> Path:
    """Кладёт в реестр заявку заданного вида — так собирается сцена, которой инструмент не даёт.

    Имя ФАЙЛА называется первым и позиционно: у самой заявки тоже есть поле «имя потока», и звались
    бы они одинаково — второе стало бы невыразимым.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_name}.json"
    path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    return path


def open_claim(
    worktree: str, wave: str = "wave9", stream: str = "3", **extra: object
) -> dict[str, object]:
    """Поля незакрытой заявки — заготовка для искусственных сцен реестра."""
    return {"wave": wave, "stream": stream, "worktree": worktree, "state": "open", **extra}


def test_registry_invariants_catch_a_doubled_address(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Две ведущие записи одного адреса — сторож обязан УПАСТЬ и назвать адрес.

    Это доказательство самого сторожа: без него он мог бы молча зеленеть на любом реестре, и вся
    затея с инвариантами свелась бы к строчкам кода, которые ничего не стерегут.
    """
    registry_invariants.waive("реестр собран противоречивым нарочно — проверяется сам сторож")
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "первая", **open_claim(str(tmp_path / "первая")))
    put_claim(folder, "вторая", **open_claim(str(tmp_path / "вторая")))

    with pytest.raises(AssertionError) as fault:
        assert_registry_invariants(board)
    assert "wave9/3" in str(fault.value), (
        f"сторож упал, но задвоенного адреса не назвал — искать его негде: {fault.value}"
    )


def test_registry_invariants_catch_every_broken_shape(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Остальные три утверждения тоже стерегут, а не значатся в списке.

    Сцены собраны руками: инструмент таких реестров сегодня не делает — в этом и смысл, сторож
    заводится против правки, которая начнёт их делать.
    """
    registry_invariants.waive("реестры собраны противоречивыми нарочно — проверяется сам сторож")
    scenes: dict[str, dict[str, dict[str, object]]] = {
        "две незакрытых заявки одной папки": {
            "первая": open_claim("d:/дерево", stream="3"),
            "вторая": open_claim("d:/дерево", stream="4"),
        },
        "номер выдан второй раз после сдачи": {
            "сданная": open_claim("d:/первое", state="released"),
            "новая": open_claim("d:/второе"),
        },
        "адрес забран у одной папки дважды": {
            "прежняя": open_claim("d:/первое"),
            "одна": open_claim("d:/второе", taken_from="d:/первое"),
            "другая": open_claim("d:/третье", taken_from="d:/первое"),
        },
        # ‼️ Круг из ТРЁХ записей, а не из двух: взаимную пару правило времени разводит само
        # (остаётся ребро старшей), и она законна — это круг возврата адреса. А вот круг, в котором
        # каждая забрала у следующей, оставляет адрес вовсе без ведущей записи: находки по нему не
        # достанутся никому, и молчать об этом нельзя.
        "перенос ходит по кругу втроём": {
            "одна": open_claim("d:/первое", taken_from="d:/третье"),
            "другая": open_claim("d:/второе", taken_from="d:/первое"),
            "третья": open_claim("d:/третье", taken_from="d:/второе"),
        },
    }
    for number, (scene, claims) in enumerate(scenes.items(), start=1):
        # Имя сцены в путь не годится: в нём двоеточие, а Windows такой папки не заведёт.
        board = tmp_path / f"сцена-{number}" / "board.jsonl"
        for name, fields in claims.items():
            put_claim(registry_dir(board), name, **fields)
        assert registry_faults(registry_dir(board)), (
            f"сторож промолчал на сцене «{scene}» — реестр противоречив, а проверка зеленеет"
        )


def test_registry_invariants_pass_the_registries_the_tool_really_makes(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """И обратная сторона: на законных реестрах сторож молчит, а не валит работу на ровном месте.

    Ложное срабатывание здесь дороже пропуска: оно упало бы в хвосте чужой проверки, и разбирать
    его пришлось бы тому, кто эту проверку писал и к реестру отношения не имеет.
    """
    registry_invariants.waive("реестры собраны руками — проверяется сам сторож, а не механизм")
    scenes: dict[str, dict[str, dict[str, object]]] = {
        "заявки нынешнего вида, поля преемства нет вовсе": {
            "одна": open_claim("d:/первое"),
            "другая": open_claim("d:/второе", stream="4"),
        },
        "поток сдан, номер за ним и остался": {
            "сданная": open_claim("d:/первое", state="released"),
        },
        "переезд: адрес забран, прежняя запись погашена": {
            "прежняя": open_claim("d:/первое"),
            "новая": open_claim("d:/второе", taken_from="d:/первое"),
        },
        "папку заняли под другой поток после переезда": {
            "прежняя": open_claim("d:/первое", stream="7"),
            "новая": open_claim("d:/второе", taken_from="d:/первое"),
        },
        # Круг возврата: обе записи забрали адрес друг у друга. Разводит их старшинство — ведущей
        # остаётся одна, и это законный, напечатанный самим механизмом сценарий.
        "адрес вернули тем же ключом": {
            "одна": open_claim("d:/первое", taken_from="d:/второе"),
            "другая": open_claim("d:/второе", taken_from="d:/первое"),
        },
        # Цепочка переездов: гасятся и первая, и вторая, ведёт третья.
        "переезжали дважды подряд": {
            "первая": open_claim("d:/первое"),
            "вторая": open_claim("d:/второе", taken_from="d:/первое"),
            "третья": open_claim("d:/третье", taken_from="d:/второе"),
        },
        # Адрес честно сдали, и ПРЕЖНЯЯ папка объявилась на нём заново: её заявка началась позже
        # переноса, значит старое ребро на неё не действует и гасить её нечем.
        "прежняя папка объявилась на освободившемся адресе": {
            "переехавшая": open_claim(
                "d:/второе",
                state="released",
                taken_from="d:/первое",
                taken_at=hours_ago(2),
            ),
            "новая": open_claim("d:/первое", claimed_at=hours_ago(1)),
        },
        "заявка чужой версии, без папки и без адреса": {
            "чужая": {"state": "open"},
            "своя": open_claim("d:/первое"),
        },
    }
    for number, (scene, claims) in enumerate(scenes.items(), start=1):
        board = tmp_path / f"сцена-{number}" / "board.jsonl"
        for name, fields in claims.items():
            put_claim(registry_dir(board), name, **fields)
        faults = registry_faults(registry_dir(board))
        assert not faults, f"ложное срабатывание на законной сцене «{scene}»: {faults}"


def claim_of(board: Path, worktree: Path, *, only_open: bool) -> ClaimRecord:
    """Заявка названной вкладки — ровно та, о которой спрашивает проверка.

    ‼️ Первую попавшуюся запись, совпавшую по папке, не берём. Совпадений может быть несколько, и
    молчаливый выбор закрепил бы поведение погашенной записи, не заметив живой. Неоднозначность —
    падение вслух: она означает, что механизм оставил в реестре призрака, и это находка, а не
    помеха проверке.

    `only_open` — когда нужна именно живая запись: правка полей заявки, снятие поля, чтение
    адреса. Закрытую там отдавать нельзя: проверка спрашивает про поток, который ведут сейчас.
    Без него (запрос файла заявки) незакрытая всё равно в приоритете, но за неимением её отдаётся
    единственная закрытая — набор нарочно ходит и к файлу СДАННОГО потока.
    """
    records = read_registry(registry_dir(board))
    superseded, _ = supersessions(records)
    here = folder_key(worktree)
    mine = [(i, record) for i, record in enumerate(records) if record.worktree == here]
    live = [record for i, record in mine if not record.released and i not in superseded]
    if len(live) > 1:
        raise AssertionError(
            f"в папке {here} незакрытых заявок сразу {len(live)} ({names_of(live)}) — "
            "какую из них имеет в виду проверка, стенд решать не вправе"
        )
    if live:
        return live[0]
    closed = [record for _, record in mine]
    if only_open:
        found = f"; закрытые записи есть: {names_of(closed)}" if closed else ""
        raise AssertionError(f"незакрытой заявки для {here} в реестре нет{found}")
    if not closed:
        raise AssertionError(f"заявки для {here} в реестре нет")
    if len(closed) > 1:
        raise AssertionError(
            f"в папке {here} закрытых заявок сразу {len(closed)} ({names_of(closed)}) — "
            "какую из них имеет в виду проверка, стенд решать не вправе"
        )
    return closed[0]


def write_claim(record: ClaimRecord) -> None:
    """Кладёт поправленную заявку обратно в тот же файл, из которого её прочитали."""
    record.file.write_text(json.dumps(record.fields, ensure_ascii=False), encoding="utf-8")


def claim(board: Path, cwd: Path, wave: str, stream: str, *extra: str) -> str:
    """Объявляет поток за вкладкой, работающей в папке `cwd`.

    ‼️ Имя потока подаётся ключом `-StreamName`, а не `-Name`, и переименовывать обратно нельзя.
    22.08.2026 под коротким именем значение на сборочном боксе приезжало ПОДМЕНЁННЫМ: в заявке
    вместо переданного имени оказывалось `GIT_ALTERNATE_OBJECT_DIRECTORIES` — имя переменной
    окружения, причём одинаково и для русских имён, и для латинских. Соседние ключи того же вызова
    (`-Wave`, `-Stream`, `-Tasks`) доезжали целиком, на машине разработки проходило верно всё.
    То есть дело было в самом коротком имени ключа, а не в кодировке и не в значении.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    return run_tool(board, "-Mode", "Claim", "-Wave", wave, "-Stream", stream, *extra, cwd=cwd)


def release(board: Path, cwd: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    assert pwsh
    return subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(TOOL),
            "-Mode",
            "Release",
            *extra,
            "-BoardPath",
            str(board),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        timeout=60,
    )


@needs_pwsh
def test_claim_makes_the_stream_addressable_by_its_number_in_the_plan(tmp_path: Path) -> None:
    """Адрес находки — номер потока в плане, а не имя ветки.

    Имя ветки к середине волны уже другое: объявленное в плане становится именем ПАПКИ, ветку
    заводят иначе, а папку успевают занять под другую работу. Номер потока в плане не меняется —
    он и есть единственное устойчивое имя.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-dispatch"
    claim(board, mine, "wave9", "3", "-StreamName", "Диспетчер")

    added = run_tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "договор поменялся")
    assert "ведёт вкладка" in added, f"рапорт не увидел живую заявку потока: {added!r}"

    shown = run_deliver(board, mine, "Start", "s-claim")
    assert "договор поменялся" in context_text(shown), (
        "находка, адресованная номером потока, не дошла до вкладки, которая этот поток объявила"
    )


@needs_pwsh
def test_record_for_a_stream_that_has_not_started_waits_for_its_claim(tmp_path: Path) -> None:
    """Находку можно оставить потоку, которого ещё не открывали.

    Сегодня такого случая нет вовсе: адресат без рабочего дерева отклоняется, и находке остаётся
    только раздел «Хвосты волны». А это обычное дело волны — сосед откроется завтра.
    """
    board = tmp_path / "board.jsonl"
    added = run_tool(board, "-Mode", "Add", "-To", "wave9/7", "-Title", "открой и посмотри")
    assert "ещё не объявлялся" in added, f"рапорт не сказал, что поток ещё не открывали: {added!r}"

    later = tmp_path / "wave9-late"
    claim(board, later, "9", "7")
    shown = run_deliver(board, later, "Start", "s-late")
    assert "открой и посмотри" in context_text(shown), (
        "запись, оставленная потоку впрок, не пришла ему после объявления — а это её единственный шанс"
    )


@needs_pwsh
def test_tool_refuses_a_record_for_a_released_stream(tmp_path: Path) -> None:
    """Сданному потоку находку класть нельзя: её некому получить.

    Раньше такая запись принималась (дерево-то на месте) и оставалась на доске навсегда — ровно
    тот дефект, ради которого доска и заводилась, только этажом ниже.

    Волна названа — значит план у неё есть, и совет про раздел «Хвосты волны» уместен. Файл плана
    заявке при этом не назван намеренно: команда объявления называет его не всегда, и правило про
    план на файл не опирается. Проект без волн проверяется отдельно — там у совета другой текст.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-done"
    claim(board, mine, "wave9", "5")
    assert release(board, mine).returncode == 0, "сдача пустого потока не прошла"

    done = tool(board, "-Mode", "Add", "-To", "wave9/5", "-Title", "поздняя находка")
    assert done.returncode != 0, "инструмент принял находку для сданного потока"
    assert "СДАН" in done.stderr, f"отказ не назвал причину: {done.stderr!r}"
    assert "Хвосты волны" in done.stderr, "отказ не сказал, куда девать находку"


@needs_pwsh
def test_release_refuses_while_the_inbox_is_not_empty(tmp_path: Path) -> None:
    """Сдача — единственное место, где спрашивают «всё ли дошедшее учтено».

    Запись, положенная за десять минут до закрытия вкладки, иначе не достаётся никому и никогда:
    отметка о живости держится ещё полсуток, и отправителю уже отрапортовали об успехе.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-busy"
    claim(board, mine, "wave9", "2")
    mark = add(board, "wave9/2", "непрочитанное")

    refused = release(board, mine)
    assert refused.returncode != 0, "поток сдался с непустым ящиком"
    assert "непрочитанное" in refused.stderr, "отказ не назвал, что именно осталось"

    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    assert release(board, mine).returncode == 0, "сдача не прошла и после того, как ящик разобрали"


@needs_pwsh
def test_release_is_not_blocked_by_a_self_closing_acknowledgement(tmp_path: Path) -> None:
    """Уведомление «учтено» сдаче не помеха: оно гаснет само и работы не несёт.

    Иначе сосед, закрывший вашу находку за минуту до сдачи, запирает вам сдачу — и предлагает
    перенести в «Хвосты волны» подтверждение, которое там никому не нужно.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-leaving"
    taker = tmp_path / "wave9-taker"
    claim(board, author, "wave9", "1")
    claim(board, taker, "wave9", "2")
    mark = add(board, "wave9/2", "поправьте договор", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=taker)

    done = release(board, author)
    assert done.returncode == 0, (
        f"сдачу заперло уведомление «учтено», которое гаснет само: {done.stderr!r}"
    )


@needs_pwsh
def test_project_wide_broadcast_crosses_waves_and_closes_personally(tmp_path: Path) -> None:
    """Адрес «всем вкладкам проекта» — второй широковещательный, и правила у него те же.

    Забыть его там, где проверяется одиночная звёздочка, — значит погасить запись у всех разом
    первым же учтившим потоком и отправить автору подтверждение, которого для записи со многими
    адресатами быть не должно.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    same = tmp_path / "wave9-mate"
    other = tmp_path / "wave8-stranger"
    claim(board, author, "wave9", "1")
    claim(board, same, "wave9", "2")
    claim(board, other, "wave8", "1")

    out = run_tool(board, "-Mode", "Add", "-To", "**", "-Title", "общее для проекта", cwd=author)
    mark = out.split("метка ")[1].split(")")[0].strip()
    assert "всех вкладок проекта" in out, f"рапорт не сказал, куда уйдёт запись: {out!r}"

    assert "общее для проекта" in run_deliver(board, other, "Start", "s-cross"), (
        "запись «всем вкладкам проекта» не дошла до вкладки другой волны — в этом весь её смысл"
    )

    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=same)
    lines = board.read_text(encoding="utf-8").splitlines()
    closings = [json.loads(line) for line in lines if line.strip() and json.loads(line).get("done")]
    assert closings and closings[-1].get("by"), (
        "закрытие записи со многими адресатами оказалось общим — первый учтивший погасил её у всех"
    )
    assert not any("учтено" in line for line in lines), (
        "автору ушло подтверждение о записи со многими адресатами — их было бы столько же, сколько адресатов"
    )


@needs_pwsh
def test_project_wide_broadcast_goes_stale_like_the_wave_one(tmp_path: Path) -> None:
    """Срок давности у «всем вкладкам проекта» тоже есть — иначе запись живёт вечно.

    Она приходит КАЖДОМУ новому рабочему дереву, переживает уплотнение и платится контекстом
    вкладок, к работе которых отношения не имеет. Это ровно та дыра, ради которой срок и заводили.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="wide0001", at=now_minus(40), to="**", title="древнее объявление") + "\n",
        encoding="utf-8",
    )
    view = run_tool(board, "-Mode", "Show")
    assert "древнее объявление" not in view, "просроченная запись всё ещё показана открытой"
    assert "просрочено" in view, f"показ промолчал о просроченной записи: {view!r}"

    tab = tmp_path / "wave9-fresh"
    tab.mkdir()
    assert "древнее объявление" not in run_deliver(board, tab, "Start", "s-wide"), (
        "просроченная запись «всем вкладкам проекта» всё ещё ездит по вкладкам"
    )

    forced = tmp_path / "wave9-forced"
    claim(board, forced, "wave9", "8")
    add(board, "wave9/8", "останется непрочитанным")
    done = release(board, forced, "-Force")
    assert done.returncode == 0, "осознанная сдача с ключом не прошла"
    assert "не достанутся никому" in done.stdout, (
        "сдача по ключу промолчала об оставленном — молча брошенное неотличимо от учтённого"
    )


@needs_pwsh
def test_broadcast_stays_inside_its_own_wave(tmp_path: Path) -> None:
    """`*` — всем потокам СВОЕЙ волны, а не всем двум десяткам деревьев проекта.

    Деревья соседних волн платят за чужую находку контекстом на каждом шаге и обязаны закрывать её
    персонально — при том что к их работе она не относится вовсе.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    same = tmp_path / "wave9-mate"
    other = tmp_path / "wave8-stranger"
    claim(board, author, "wave9", "1")
    claim(board, same, "wave9", "2")
    claim(board, other, "wave8", "1")

    run_tool(board, "-Mode", "Add", "-To", "*", "-Title", "общее для волны", cwd=author)

    assert "общее для волны" in context_text(run_deliver(board, same, "Start", "s-same")), (
        "запись «всем» не дошла до соседнего потока своей же волны"
    )
    assert "общее для волны" not in run_deliver(board, other, "Start", "s-other"), (
        "запись «всем» ушла во вкладку чужой волны — она платит за неё контекстом ни за что"
    )


@needs_pwsh
def test_streams_answers_whose_task_it_is(tmp_path: Path) -> None:
    """«Чей это кусок работы» — вопрос, на который сегодня нет ответа нигде.

    Вкладку тянет взять соседнюю задачу, она предлагает её владельцу, а владелец не знает, что
    задачу планировали другому потоку, и подтверждает. Реестр отвечает на это механически.
    """
    board = tmp_path / "board.jsonl"
    claim(
        board,
        tmp_path / "wave9-dispatch",
        "wave9",
        "3",
        "-StreamName",
        "Диспетчер",
        "-Tasks",
        "10-13",
    )

    mine = run_tool(board, "-Mode", "Streams", "-Task", "12")
    assert "wave9/3" in mine and "Диспетчер" in mine, (
        f"реестр не назвал поток, который ведёт задачу 12: {mine!r}"
    )

    nobody = run_tool(board, "-Mode", "Streams", "-Task", "20")
    assert "не объявлена ни одним потоком" in nobody, (
        "про чужую задачу реестр промолчал так же, как про свою, — по ответу их не различить"
    )


@needs_pwsh
def test_stream_address_is_not_confused_with_a_branch_name(tmp_path: Path) -> None:
    """`feat/wave6-compute` — ветка, `wave6/3` — поток. Спутать их нельзя.

    Разбор адреса решает это по форме: справа от косой черты обязан стоять НОМЕР потока, а не
    слово. Иначе ветка `feat/…` разбиралась бы как поток `feat` и находка уходила бы в никуда.
    Слева имя волны сверяется целиком: `wave6-compute` — имя папки, а не волна `wave6`, и адресом
    оно быть не должно, даже когда волна `wave6` в реестре есть.
    """
    board = tmp_path / "board.jsonl"
    accepted = run_tool(board, "-Mode", "Add", "-To", "wave6/3", "-Title", "потоку")
    assert "ещё не объявлялся" in accepted, "адрес «волна/поток» не разобран как поток"

    branchy = tool(
        board, "-Mode", "Add", "-To", "feat/never-existed", "-Title", "ветке", known=True
    )
    assert branchy.returncode != 0, (
        "имя ветки, которой нет ни в реестре, ни среди деревьев, принято как адрес потока"
    )

    claim(board, tmp_path / "wave6-compute", "wave6", "3")
    foldery = tool(board, "-Mode", "Add", "-To", "wave6-compute/3", "-Title", "папке", known=True)
    assert foldery.returncode != 0, (
        "имя папки «wave6-compute/3» принято как адрес потока wave6/3 — находка ушла бы чужому"
    )


def patch_claim(board: Path, worktree: Path, **fields: object) -> None:
    """Правит НЕЗАКРЫТУЮ заявку вкладки — так тест задаёт то, чего сам вычислить не может.

    Список тронутых файлов инструмент берёт у git, а тестовые папки репозиториями не являются:
    подставляем список руками и ставим свежую отметку времени, чтобы сторож его не пересчитывал.

    ‼️ Запись выбирает `claim_of`, а не первое совпадение по папке: правка погашенной записи
    закрепила бы поведение призрака, и проверка зеленела бы на сломанном механизме.
    """
    record = claim_of(board, worktree, only_open=True)
    record.fields.update(fields)
    write_claim(record)


@needs_pwsh
def test_tab_is_told_that_a_neighbour_edits_the_same_files(tmp_path: Path) -> None:
    """Пересечение правок видно ДО конфликта слияния — и до того, как чужую задачу возьмут.

    Вкладку тянет прихватить соседний кусок: она предлагает его владельцу, владелец не знает, что
    кусок планировали другому потоку, и подтверждает. Общие файлы — единственный признак этого,
    который машина видит сама, не полагаясь на дисциплину.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-mine"
    neighbour = tmp_path / "wave9-neighbour"
    claim(board, mine, "wave9", "1", "-StreamName", "Труба")
    claim(board, neighbour, "wave9", "5", "-StreamName", "Витрины")
    now = datetime.now().isoformat(timespec="seconds")
    patch_claim(board, mine, files=["packages/core/pipe.py", "docs/readme.md"], files_at=now)
    patch_claim(board, neighbour, files=["packages/core/pipe.py"], files_at=now)

    first = context_text(run_deliver(board, mine, "Prompt", "s-overlap"))
    assert "те же файлы" in first, f"вкладка не узнала о соседе, правящем те же файлы: {first!r}"
    assert "wave9/5" in first and "Витрины" in first, "предупреждение не назвало, чей это поток"
    assert "packages/core/pipe.py" in first, "предупреждение не назвало общий файл"

    second = run_deliver(board, mine, "Prompt", "s-overlap")
    assert "те же файлы" not in second, (
        "предупреждение повторяется каждый ход — контекст вкладки переотправляется на каждом шаге"
    )


@needs_pwsh
def test_shared_plan_file_is_not_counted_as_an_overlap(tmp_path: Path) -> None:
    """План волны правят все потоки по устройству работы — это не пересечение, а норма.

    Сторож, который кричит на каждый общий план, перестают читать вместе со всем остальным.

    Папка планов здесь СВОЯ, из профиля подставного проекта: зашитая в код, она делала бы норму
    нормой ровно в одном репозитории, а во всех прочих план волны попадал бы в пересечения — то
    есть предупреждение приходило бы на каждый чужой ход.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-one"
    neighbour = tmp_path / "wave9-two"
    claim(board, mine, "wave9", "1")
    claim(board, neighbour, "wave9", "2")
    for tab in (mine, neighbour):
        (tab / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    plan = f"{STUB_PLANS}2026-08-13-server-wave9.md"
    patch_claim(board, mine, files=[plan], files_at=now)
    patch_claim(board, neighbour, files=[plan], files_at=now)

    assert "те же файлы" not in run_deliver(board, mine, "Prompt", "s-plan"), (
        "общий план волны выдан за пересечение работы — сторож станет шумным и его перестанут читать"
    )


@needs_pwsh
def test_a_plan_folder_of_another_project_is_an_ordinary_overlap(tmp_path: Path) -> None:
    """Общим по устройству место делает профиль, а не имя папки из соседнего проекта.

    Останься папка зашитой — в чужом проекте сторож молчал бы о настоящем пересечении работы,
    стоило ему случиться в папке с тем же именем.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-three"
    neighbour = tmp_path / "wave9-four"
    claim(board, mine, "wave9", "3", "-StreamName", "Труба")
    claim(board, neighbour, "wave9", "4", "-StreamName", "Витрины")
    for tab in (mine, neighbour):
        (tab / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    alien = f"{ALIEN_PLANS}2026-08-13-server-wave9.md"
    patch_claim(board, mine, files=[alien], files_at=now)
    patch_claim(board, neighbour, files=[alien], files_at=now)

    first = context_text(run_deliver(board, mine, "Prompt", "s-alien-plan"))
    assert "те же файлы" in first, (
        f"пересечение в папке, которую профиль папкой планов не называл, спрятано: {first!r}"
    )


@needs_pwsh
def test_author_learns_that_the_finding_was_taken_into_account(tmp_path: Path) -> None:
    """Автор находки узнаёт её судьбу — иначе он не узнаёт её никогда.

    От судьбы находки зависит, заводить ли ей задание в «Хвостах волны». Уведомление гасится само
    при показе: оно заведено, чтобы снять вопрос, а не добавить работы по закрытию записей.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    taker = tmp_path / "wave9-taker"
    claim(board, author, "wave9", "1")
    claim(board, taker, "wave9", "2")

    mark = add(board, "wave9/2", "поправьте договор", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=taker)

    first = context_text(run_deliver(board, author, "Start", "s-ack"))
    assert "учтено" in first and "поправьте договор" in first, (
        f"автор не узнал, что его находку учли: {first!r}"
    )

    second = run_deliver(board, author, "Start", "s-ack-again")
    assert "учтено" not in second, (
        "уведомление пришло снова — оно обязано гаситься само, а не требовать закрытия"
    )


@needs_pwsh
def test_owner_sees_stuck_records_only_in_the_main_folder(tmp_path: Path, wave_repo: Path) -> None:
    """Застрявшая запись всплывает у владельца — иначе её не видит никто.

    Адресат сдан, молчит или не существует: запись лежит открытой, а отправителю уже отрапортовали
    об успехе. Это единственное место, где механизм признаётся, что доставка не состоялась.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="lost0001", at=now_minus(3), to="wave9/44", title="некому получить") + "\n",
        encoding="utf-8",
    )

    owner = context_text(run_deliver(board, wave_repo, "Start", "s-owner"))
    assert "застряло" in owner, f"владелец не увидел застрявшую запись: {owner!r}"
    assert "некому получить" in owner, "сводка не назвала саму находку"
    assert "Хвосты волны" in owner, "сводка не сказала, что с находкой делать"

    inside = run_deliver(board, here_of(wave_repo), "Start", "s-inside")
    assert "застряло" not in inside, (
        "сводка чужих застрявших записей пришла в рабочее дерево — это шум в контексте потока"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Проект без волн: волна подставляется сама.
#
# Канал ставят и туда, где волн нет вовсе, а плана волны может не быть и там, где они есть. Раньше
# вкладка упиралась в тупик: объявиться нельзя (инструмент требовал волну), а сдача советовала
# вписать строку в раздел плана, которого не существует.
#
# Тексты, которые видит человек, владелец утвердил ДОСЛОВНО, поэтому сверяются они целыми строками,
# а не по куску: случайно переписанная формулировка тут же валит проверку. Это и есть их защита —
# больше её взять неоткуда.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def today_wave() -> str:
    """Имя волны, которое инструмент подставляет сам, когда её не назвали, — сегодняшняя дата."""
    return datetime.now().strftime("%Y-%m-%d")


def claim_bare(board: Path, cwd: Path, *extra: str) -> str:
    """Объявление без волны и без номера потока — так объявляется вкладка в проекте без волн."""
    cwd.mkdir(parents=True, exist_ok=True)
    return run_tool(board, "-Mode", "Claim", *extra, cwd=cwd)


def said(text: str) -> list[str]:
    """Строки вывода без отступов — по ним сверяются утверждённые формулировки целиком."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def strip_claim_field(board: Path, worktree: Path, field: str) -> None:
    """Убирает поле из НЕЗАКРЫТОЙ заявки — так выглядит заявка, заведённая прежней версией."""
    record = claim_of(board, worktree, only_open=True)
    record.fields.pop(field, None)
    write_claim(record)


@needs_pwsh
def test_claim_without_a_wave_opens_one_by_todays_date(tmp_path: Path) -> None:
    """Волну не назвали и назвать неоткуда — инструмент заводит её сам, по сегодняшней дате.

    Отказ на этом месте был тупиком: без объявления поток снаружи неотличим от «ещё не открывали»,
    а взять номер волны в проекте без волн человеку негде.
    """
    board = tmp_path / "board.jsonl"
    out = claim_bare(board, tmp_path / "solo")
    today = today_wave()

    assert f"Поток {today}/1 объявлен за этой вкладкой" in out, (
        f"объявление без волны не завело волну по дате: {out!r}"
    )
    assert (
        f"Волна не названа — взята по сегодняшней дате. Адрес потока для соседей: {today}/1."
        in said(out)
    ), f"строка про подставленную волну переписана или пропала: {out!r}"
    assert "Номер потока не назван — выдан следующий свободный: 1." in said(out), (
        f"вкладке не сказали, что номер потока выдан ей самим инструментом: {out!r}"
    )


@needs_pwsh
def test_second_tab_joins_the_work_that_is_already_running(tmp_path: Path) -> None:
    """Вторая вкладка попадает в ТУ ЖЕ волну, а не заводит свою.

    Заведи она свою — соседи не увидели бы друг друга ни в карте потоков, ни в адресах, и весь
    канал согласования распался бы на одиночные волны по одной вкладке в каждой.
    """
    board = tmp_path / "board.jsonl"
    claim_bare(board, tmp_path / "first")
    out = claim_bare(board, tmp_path / "second")
    today = today_wave()

    assert f"Поток {today}/2 объявлен за этой вкладкой" in out, (
        f"вторая вкладка не присоединилась к идущей работе: {out!r}"
    )
    assert (
        f"Волна не названа — вкладка присоединена к уже идущей работе {today}, в ней потоков: 2."
        in said(out)
    ), f"строка про присоединение переписана или пропала: {out!r}"
    assert "Номер потока не назван — выдан следующий свободный: 2." in said(out), (
        f"второй вкладке выдан не следующий свободный номер: {out!r}"
    )


@needs_pwsh
def test_a_named_wave_is_never_joined_automatically(tmp_path: Path) -> None:
    """‼️ К названной волне вкладка не присоединяется никогда.

    У волны из плана номера потоков объявлены В ПЛАНЕ. Присоединившись, вкладка заняла бы чужой
    номер, и половина находок волны ушла бы не туда — молча, с бодрым рапортом обеим сторонам.
    """
    board = tmp_path / "board.jsonl"
    claim(board, tmp_path / "wave6-compute", "wave6", "4")
    out = claim_bare(board, tmp_path / "loner")

    assert "wave6" not in out, (
        f"вкладка присоединилась к волне из плана и заняла в ней номер: {out!r}"
    )
    assert f"Поток {today_wave()}/1 объявлен за этой вкладкой" in out, (
        f"своя волна по дате не заведена: {out!r}"
    )


@needs_pwsh
def test_an_old_claim_without_the_flag_is_treated_as_a_named_wave(tmp_path: Path) -> None:
    """Заявка прежней версии признака «волна подставлена сама» не несёт — и присоединяться к ней
    нельзя: она заводилась с названной волной, где номера идут из плана.

    Заодно проверяется адрес волны, названной словом: она есть в реестре, и адресовать ей находку
    можно так же, как волне из плана.
    """
    board = tmp_path / "board.jsonl"
    elder = tmp_path / "elder"
    claim(board, elder, "sprint-alpha", "1")
    strip_claim_field(board, elder, "wave_auto")

    addressed = run_tool(board, "-Mode", "Add", "-To", "sprint-alpha/1", "-Title", "словом")
    assert "ведёт вкладка" in addressed, (
        f"адрес волны, названной словом, не разобрался, хотя она есть в реестре: {addressed!r}"
    )

    out = claim_bare(board, tmp_path / "newcomer")
    assert "sprint-alpha" not in out, (
        f"вкладка присоединилась к заявке старого вида, где признака нет вовсе: {out!r}"
    )
    assert f"Поток {today_wave()}/1 объявлен за этой вкладкой" in out, (
        f"своя волна по дате не заведена: {out!r}"
    )


@needs_pwsh
def test_a_finding_reaches_a_stream_of_a_date_named_wave(tmp_path: Path) -> None:
    """Находка доходит и в волне, названной датой.

    Разбор адреса раньше принимал слева только `wave<номер>` или число, поэтому `2026-08-24/2` не
    разбирался вовсе: находка ложилась на доску и не доходила ни до кого — молча, как и всё,
    что этот механизм чинит.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "first"
    mate = tmp_path / "second"
    claim_bare(board, author)
    claim_bare(board, mate)
    today = today_wave()

    added = run_tool(
        board, "-Mode", "Add", "-To", f"{today}/2", "-Title", "договор поменялся", cwd=author
    )
    assert "ведёт вкладка" in added, f"рапорт не увидел живую заявку соседа: {added!r}"

    shown = run_deliver(board, mate, "Start", "s-date-wave")
    assert "договор поменялся" in context_text(shown), (
        "находка, адресованная потоку волны-даты, не дошла до вкладки, которая этот поток ведёт"
    )


@needs_pwsh
def test_release_without_a_plan_sends_the_result_to_the_owner(tmp_path: Path) -> None:
    """Плана нет — сдача не посылает вписывать строку в его раздел.

    Совет вписать строку в файл, которого не существует, — тупик: вкладка не может ни выполнить
    его, ни понять, что делать вместо него.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "no-plan"
    claim_bare(board, mine)

    done = release(board, mine)
    assert done.returncode == 0, done.stderr
    assert "Плана волны нет — строку потока вписывать некуда, итог идёт в ответ владельцу." in said(
        done.stdout
    ), f"строка про отсутствие плана переписана или пропала: {done.stdout!r}"
    assert (
        "Последнее действие — строка своего потока в разделе «Состояние потоков» плана волны."
        not in said(done.stdout)
    ), "сдача без плана всё равно послала вписывать строку в план"
    assert f"Поток {today_wave()}/1 сдан. Находки ему больше не примут." in said(done.stdout), (
        f"строка о сдаче без плана переписана или пропала: {done.stdout!r}"
    )


@needs_pwsh
def test_release_of_a_named_wave_keeps_the_line_in_the_plan(tmp_path: Path) -> None:
    """Волна названа — тексты сдачи прежние, слово в слово, и файл плана для этого не нужен.

    ‼️ Правило про план опирается на ОДНО: подставлена ли волна сама. Опирайся оно на файл плана в
    заявке — волны этого проекта увидели бы тексты «плана нет», потому что команда объявления файл
    плана не называет. Поэтому здесь план заявке намеренно не назван.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-planned"
    claim(board, mine, "wave9", "1")

    done = release(board, mine)
    assert done.returncode == 0, done.stderr
    assert (
        "Последнее действие — строка своего потока в разделе «Состояние потоков» плана волны."
        in said(done.stdout)
    ), f"у потока названной волны пропала прежняя последняя строка сдачи: {done.stdout!r}"
    assert "Поток wave9/1 сдан. Находки ему больше не примут — их место в «Хвостах волны»." in said(
        done.stdout
    ), f"у потока названной волны переписана строка о сдаче: {done.stdout!r}"


@needs_pwsh
def test_a_wave_taken_from_the_plan_name_is_not_an_invented_one(tmp_path: Path) -> None:
    """Волна из имени плана — названная: номера потоков в ней идут из плана, тексты прежние.

    Смешай её с подставленной — и вкладка соседа присоединилась бы к волне плана, заняв в ней
    чужой номер.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "planned"
    mine.mkdir()
    plan = mine / "2026-08-24-wave9.md"
    plan.write_text("# план волны 9\n", encoding="utf-8")

    out = claim_bare(board, mine, "-Plan", str(plan))
    assert "Волна взята из имени плана." in said(out), (
        f"строка про волну из имени плана переписана или пропала: {out!r}"
    )
    assert "Поток wave9/1 объявлен за этой вкладкой" in out, (
        f"волна взята не из имени плана: {out!r}"
    )

    later = claim_bare(board, tmp_path / "loner")
    assert "wave9" not in later, f"вкладка присоединилась к волне, взятой из имени плана: {later!r}"

    done = release(board, mine)
    assert done.returncode == 0, done.stderr
    assert (
        "Последнее действие — строка своего потока в разделе «Состояние потоков» плана волны."
        in said(done.stdout)
    ), f"у потока с планом пропала прежняя последняя строка сдачи: {done.stdout!r}"


@needs_pwsh
def test_forced_release_without_a_plan_names_the_leftovers_to_the_owner(tmp_path: Path) -> None:
    """Сдача силой: без плана оставшееся в ящике называют владельцу, а не несут в «Хвосты волны»."""
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "forced-no-plan"
    claim_bare(board, mine)
    add(board, f"{today_wave()}/1", "непрочитанное")

    done = release(board, mine, "-Force")
    assert done.returncode == 0, done.stderr
    assert (
        "‼️ Сдано с непустым ящиком: осталось записей 1 — они не достанутся никому, "
        "назовите их в ответе владельцу." in said(done.stdout)
    ), f"строка про сдачу силой без плана переписана или пропала: {done.stdout!r}"
    assert "Хвосты волны" not in done.stdout, (
        "сдача силой послала вкладку в раздел плана, которого в этом проекте нет"
    )


@needs_pwsh
def test_forced_release_of_a_named_wave_keeps_the_tails_line(tmp_path: Path) -> None:
    """Сдача силой в волне с планом — прежний текст: оставшееся переносят в «Хвосты волны»."""
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "forced-wave9"
    claim(board, mine, "wave9", "2")
    add(board, "wave9/2", "непрочитанное")

    done = release(board, mine, "-Force")
    assert done.returncode == 0, done.stderr
    assert (
        "‼️ Сдано с непустым ящиком: осталось записей 1 — они не достанутся никому, "
        "перенесите их в «Хвосты волны»." in said(done.stdout)
    ), f"строка про сдачу силой в волне с планом переписана: {done.stdout!r}"


@needs_pwsh
def test_stuck_summary_without_a_plan_points_at_the_owner(tmp_path: Path, wave_repo: Path) -> None:
    """Сводка застрявшего у владельца — по тому же правилу, что и остальные советы.

    Это единственное место, где механизм признаётся, что доставка не состоялась. Указание на
    раздел плана, которого нет, оставляет находку вовсе без места.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "gone"
    claim_bare(board, mine)
    assert release(board, mine).returncode == 0, "сдача пустого потока не прошла"
    board.write_text(
        board_line(id="lost0002", at=now_minus(3), to=f"{today_wave()}/1", title="некому получить")
        + "\n",
        encoding="utf-8",
    )

    owner = context_text(run_deliver(board, wave_repo, "Start", "s-owner-no-plan"))
    assert "застряло" in owner, f"владелец не увидел застрявшую запись: {owner!r}"
    assert "Плана волны нет — назовите находку в ответе владельцу." in said(owner), (
        f"сводка послала владельца в раздел плана, которого нет: {owner!r}"
    )
    assert "Хвосты волны" not in owner, "в сводке остался совет про раздел плана"


@needs_pwsh
def test_release_without_a_plan_names_the_leftovers_to_the_owner(tmp_path: Path) -> None:
    """Отказ при непустом ящике тоже не посылает в раздел плана, которого нет."""
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "busy-no-plan"
    claim_bare(board, mine)
    add(board, f"{today_wave()}/1", "непрочитанное")

    refused = release(board, mine)
    assert refused.returncode != 0, "поток сдался с непустым ящиком"
    assert "Не ваша работа — назовите её в ответе владельцу и сдайте поток ключом -Force." in said(
        refused.stderr
    ), f"строка про чужую работу без плана переписана или пропала: {refused.stderr!r}"
    assert "Хвосты волны" not in refused.stderr, (
        "отказ послал вкладку в раздел плана, которого в этом проекте нет"
    )


@needs_pwsh
def test_a_finding_for_a_released_stream_without_a_plan_goes_to_the_owner(tmp_path: Path) -> None:
    """Сданный поток без плана: находку называют владельцу, а не несут в «Хвосты волны».

    Отказ обязан говорить, что с находкой делать. Раздел плана в проекте без волн — это указание
    в пустоту, и находка после него не попадает никуда вовсе.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "no-plan-done"
    claim_bare(board, mine)
    assert release(board, mine).returncode == 0, "сдача пустого потока не прошла"

    done = tool(board, "-Mode", "Add", "-To", f"{today_wave()}/1", "-Title", "поздняя находка")
    assert done.returncode != 0, "инструмент принял находку для сданного потока"
    assert "Плана волны нет — назовите находку в ответе владельцу." in said(done.stderr), (
        f"строка про находку без плана переписана или пропала: {done.stderr!r}"
    )
    assert "Хвосты волны" not in done.stderr, (
        "отказ послал вкладку в раздел плана, которого в этом проекте нет"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Спор за номер потока: вкладки волны открывают РАЗОМ.
#
# Это не редкий случай, а обычный ход работы: план делят на потоки и открывают вкладки одну за
# другой в одну минуту. До этих проверок все они объявлялись потоком №1 одной волны — снимок
# реестра читался один раз, ДО записи своей заявки, и следующий свободный номер выходил у всех
# одинаковым. Находка такому потоку приходила всем троим, а «чей это кусок работы» отвечало неверно.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def claim_file_of(board: Path, worktree: Path) -> Path:
    """Файл заявки названной вкладки: незакрытой, а за неимением — единственной закрытой.

    Закрытую отдаём потому, что набор нарочно ходит и к файлу СДАННОГО потока — держит его
    занятым, портит, удаляет. Но пока в папке есть живая запись, речь всегда о ней.
    """
    return claim_of(board, worktree, only_open=False).file


def address_of(board: Path, worktree: Path) -> str:
    """Адрес потока, который вкладка ведёт СЕЙЧАС, — так, как его назовут соседи."""
    fields = claim_of(board, worktree, only_open=True).fields
    return f"{fields['wave']}/{fields['stream']}"


@needs_pwsh
def test_tabs_started_at_once_do_not_share_one_stream_number(tmp_path: Path) -> None:
    """Шесть вкладок, запущенных по общему сигналу времени, обязаны разойтись по шести адресам.

    Ровно так их и открывают: план поделили на потоки — и открыли вкладки разом. Общий адрес у
    них молчаливый: находка уходит всем сразу, а вопрос «чей это кусок работы» получает неверный
    ответ, и обе стороны об этом не узнают.

    Вкладок именно шесть, а не три: на трёх дефект ловится далеко не каждым прогоном, а волну
    делят и на шесть потоков — чем их больше, тем плотнее сходятся заявки и тем чаще совпадают
    номера.
    """
    assert pwsh
    board = tmp_path / "board.jsonl"
    tabs = [tmp_path / f"разом-{number}" for number in range(6)]
    for tab in tabs:
        tab.mkdir()
    # Общий сигнал: все ждут одного и того же момента и стартуют с него, а не по очереди.
    start = (datetime.now() + timedelta(seconds=6)).isoformat(timespec="seconds")
    running = [
        subprocess.Popen(
            [
                pwsh,
                "-NoProfile",
                "-Command",
                f"$t=[datetime]::Parse('{start}',[cultureinfo]::InvariantCulture); "
                f"while((Get-Date) -lt $t){{Start-Sleep -Milliseconds 2}}; "
                f"& '{TOOL}' -Mode Claim -BoardPath '{board}'",
            ],
            cwd=str(tab),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for tab in tabs
    ]
    for process in running:
        out, err = process.communicate(timeout=180)
        # Причину срыва называем сразу: без неё падение читается как «что-то пошло не так», и
        # разбираться приходится повторным воспроизведением редкой гонки.
        assert process.returncode == 0, (
            f"объявление сорвалось: {err.decode('utf-8', 'replace')}{out.decode('utf-8', 'replace')}"
        )

    addresses = [address_of(board, tab) for tab in tabs]
    assert len(set(addresses)) == len(tabs), (
        f"одновременно стартовавшие вкладки поделили один адрес: {addresses}"
    )


# Один заход одного процесса в критическую часть под замком реестра. Отдельным файлом, а не
# строкой в команде: заход должен выглядеть ровно так же, как в инструменте, — тот же общий файл
# комплекта, те же две функции замка вокруг работы.
LOCK_STAND = """#Requires -Version 7
param([string]$Lib, [string]$Dir, [string]$Marks, [string]$StartAt, [int]$HoldMs)
. $Lib
# Общий сигнал времени: без него процессы расходятся на разогреве оболочки, и на реализации ВОВСЕ
# без замка они могли бы не встретиться — страж молчал бы там, где обязан кричать.
$moment = [datetime]::Parse($StartAt, [cultureinfo]::InvariantCulture)
while ((Get-Date) -lt $moment) { Start-Sleep -Milliseconds 2 }
$handle = Enter-RegistryLock -Dir $Dir
if (-not $handle) { exit 2 }
$mark = Join-Path $Marks "внутри-$PID"
try {
    [System.IO.File]::WriteAllText($mark, "$PID")
    # Сколько нас внутри прямо сейчас. Больше одного — взаимное исключение не работает.
    $together = @(Get-ChildItem -LiteralPath $Marks -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'внутри-*' })
    if ($together.Count -gt 1) {
        # Нарушитель пишет СВОЙ файл: общий журнал пришлось бы делить, а это снова замок.
        [System.IO.File]::WriteAllText((Join-Path $Marks "нарушение-$PID"),
            "внутри было: $($together.Count)")
    }
    Start-Sleep -Milliseconds $HoldMs
} finally {
    Remove-Item -LiteralPath $mark -Force -ErrorAction SilentlyContinue
    # Убеждаемся, что отметка ПРОПАЛА, и только потом отпускаем замок: удаление файла, который в
    # этот миг держит антивирус, Windows откладывает — имя остаётся видно, и следующий заходящий
    # насчитал бы двоих внутри на пустом месте.
    $till = (Get-Date).AddSeconds(5)
    while ((Test-Path -LiteralPath $mark) -and (Get-Date) -lt $till) { Start-Sleep -Milliseconds 10 }
    Exit-RegistryLock -Handle $handle
}
"""

# Стенд на роли: держатель замка и желающий его отнять. Предел ожидания подменяется коротким —
# иначе проверка «пока держат, второго не пускают» шла бы полминуты. Подмена действует потому, что
# имя функции разбирается в момент вызова, а не при подключении файла.
LOCK_ROLES = """#Requires -Version 7
param(
    [string]$Lib,
    [string]$Dir,
    [ValidateSet('hold', 'grab')]
    [string]$Role,
    [string]$Signal,
    [string]$Until = '',
    [int]$HoldMs = 0,
    [int]$WaitSeconds = 30
)
. $Lib
function Get-RegistryLockWaitSeconds { return $WaitSeconds }
# Стенд не должен сорить в поток ошибок рассказом про ожидание.
function Get-RegistryLockSpeakAfterSeconds { return 3600 }
switch ($Role) {
    'hold' {
        # Взял замок и сидит внутри. Отметку кладёт ПОСЛЕ взятия: по её появлению проверка узнаёт,
        # что держатель уже внутри, и не гадает по времени.
        $handle = Enter-RegistryLock -Dir $Dir
        if (-not $handle) { exit 2 }
        [System.IO.File]::WriteAllText($Signal, "держу")
        if ($Until) {
            # Отпускаем по СИГНАЛУ проверки, а не по часам: иначе запас проверки — это разница
            # между «сколько держим» и «за сколько запустится соседний процесс», и на загруженной
            # машине он обнуляется. Часы остаются только предохранителем от зависания.
            $till = (Get-Date).AddMilliseconds($HoldMs)
            while (-not (Test-Path -LiteralPath $Until) -and (Get-Date) -lt $till) {
                Start-Sleep -Milliseconds 10
            }
        } else {
            Start-Sleep -Milliseconds $HoldMs
        }
        Exit-RegistryLock -Handle $handle
    }
    'grab' {
        # Пробует войти. «нет» в файле значит «не пустили» — этого и ждём, пока замок держат.
        $handle = Enter-RegistryLock -Dir $Dir
        [System.IO.File]::WriteAllText($Signal, $(if ($handle) { 'вошёл' } else { 'нет' }))
        Exit-RegistryLock -Handle $handle
    }
}
"""


def lock_stand_args(stand: Path, registry: Path, **extra: object) -> list[str]:
    """Общая часть запуска любого стенда замка."""
    assert pwsh
    argv = [
        pwsh,
        "-NoProfile",
        "-File",
        str(stand),
        "-Lib",
        str(COORDINATION_DIR / "lib" / "wave-board-lib.ps1"),
        "-Dir",
        str(registry),
    ]
    for name, value in extra.items():
        argv += [f"-{name}", str(value)]
    return argv


def wait_for_file(path: Path, seconds: float = 60) -> str:
    """Ждёт появления непустого файла — так стенд сообщает, что уже внутри."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.02)
    raise AssertionError(f"стенд не отметился в {path} за {seconds} с")


@needs_pwsh
def test_two_tabs_are_never_inside_the_claim_at_once(tmp_path: Path) -> None:
    """Внутри критической части объявления всегда ровно один — проверяется прямо, а не по итогу.

    Проверка на шесть вкладок рядом сторожит ИТОГ работы (адреса разошлись) и делает это
    ВЕРОЯТНОСТНО: на реализации без замка она падает не каждым прогоном — замерено четыре падения
    из пяти. Значит одиночный зелёный прогон на сборке отсутствия гонки не доказывает, и оставлять
    волну под таким стражем нельзя.

    Здесь ловится сама причина. Восемь процессов заходят в критическую часть по общему сигналу
    времени; каждый на входе кладёт свою отметку и смотрит, сколько отметок лежит рядом. Две —
    значит внутри двое, и это остаётся файлом на диске, который переживёт выход процесса. Общий
    сигнал обязателен: без него процессы расходятся на разогреве оболочки и могут не встретиться
    даже там, где замка нет вовсе.

    ‼️ Этот же страж отвечает за переносимость. Взаимное исключение держит система (файл открыт без
    права совместного доступа); на Windows это проверено, на прочих системах его изображает .NET
    через советующие блокировки ядра. Проверить это на машине разработки нечем — но страж идёт на
    ТОЙ системе, где комплект запущен, и молчаливого отказа не допустит.
    """
    assert pwsh
    registry = tmp_path / "streams"
    registry.mkdir()
    marks = tmp_path / "отметки"
    marks.mkdir()
    stand = tmp_path / "стенд-замка.ps1"
    stand.write_text(LOCK_STAND, encoding="utf-8")

    for _ in range(3):
        start = (datetime.now() + timedelta(seconds=3)).isoformat(timespec="seconds")
        running = [
            subprocess.Popen(
                lock_stand_args(stand, registry, Marks=marks, StartAt=start, HoldMs=200),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(8)
        ]
        for process in running:
            out, err = process.communicate(timeout=180)
            # Вывод вычитываем, а не ждём молча: полный канал повесил бы процесс намертво, и стенд
            # не упал бы, а завис до предела ожидания.
            assert process.returncode == 0, (
                f"процесс стенда не взял замок вовсе: {err.decode('utf-8', 'replace')}"
                f"{out.decode('utf-8', 'replace')}"
            )
        left = [path.name for path in marks.glob("внутри-*")]
        assert not left, f"процесс вышел, не убрав свою отметку: {left}"

    breached = sorted(path.read_text(encoding="utf-8") for path in marks.glob("нарушение-*"))
    assert not breached, f"в критическую часть объявления вошли двое разом: {breached}"


@needs_pwsh
def test_a_held_lock_keeps_the_neighbour_out(tmp_path: Path) -> None:
    """Пока замок держат, соседа внутрь не пускают — и он честно уходит ни с чем.

    Обратная сторона стенда взаимного исключения: тот ловит вход двоих, а этот — что отказ вообще
    случается. Без него «замок» мог бы пускать всех и выглядеть исправным: отметок двоих внутри не
    появилось бы только потому, что процессы разошлись во времени.

    Заодно проверяется, что ожидание кончается отказом, а не зависанием: предел ожидания стенд
    подменяет коротким.
    """
    registry = tmp_path / "streams"
    registry.mkdir()
    stand = tmp_path / "стенд-ролей.ps1"
    stand.write_text(LOCK_ROLES, encoding="utf-8")

    held = tmp_path / "держу.txt"
    release_now = tmp_path / "отпускай.txt"
    # Держатель сидит внутри, пока проверка не разрешит выйти: так между «сосед пробует войти» и
    # «замок ещё держат» нет никакого запаса по времени, который могла бы съесть медленная машина.
    holder = subprocess.Popen(
        lock_stand_args(
            stand, registry, Role="hold", Signal=held, Until=release_now, HoldMs=120000
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for_file(held)
        answer = tmp_path / "ответ.txt"
        taker = subprocess.run(
            lock_stand_args(stand, registry, Role="grab", Signal=answer, WaitSeconds=2),
            capture_output=True,
            timeout=120,
        )
        assert taker.returncode == 0, taker.stderr.decode("utf-8", "replace")
        assert answer.read_text(encoding="utf-8").strip() == "нет", (
            "сосед вошёл в критическую часть, пока замок держат"
        )
    finally:
        release_now.write_text("выходи", encoding="utf-8")
        kept, failed = holder.communicate(timeout=180)
        assert holder.returncode == 0, (
            f"держатель стенда сорвался: {failed.decode('utf-8', 'replace')}"
            f"{kept.decode('utf-8', 'replace')}"
        )


@needs_pwsh
def test_a_killed_holder_frees_the_lock_at_once(tmp_path: Path) -> None:
    """Убитая вкладка отпускает замок немедленно — ждать и перехватывать нечего.

    Это и есть причина, по которой замок держит ДЕСКРИПТОР, а не существование файла. По файлу
    упавшая вкладка запирала бы доску, пока замок не «протухнет», а перехват протухшего безопасным
    подручными средствами не сделать: решение «отнимаю» принимается по одному состоянию файла, а
    отнимается уже другое — и отнимают свежий замок соседа, пока тот работает внутри.

    Файл замка при этом ОСТАЁТСЯ на диске, и это тоже проверяется: удалять его нельзя — второй
    процесс завёл бы файл заново и взял замок на новом, пока первый держит старый.
    """
    registry = tmp_path / "streams"
    registry.mkdir()
    stand = tmp_path / "стенд-ролей.ps1"
    stand.write_text(LOCK_ROLES, encoding="utf-8")

    held = tmp_path / "держу.txt"
    holder = subprocess.Popen(
        lock_stand_args(stand, registry, Role="hold", Signal=held, HoldMs=60000),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    wait_for_file(held)
    # Убиваем держателя, не дав ему закрыть дескриптор: так уходит закрытая на полуслове вкладка.
    holder.kill()
    # Вывод вычитываем и здесь: канал мог наполниться ещё до убийства, и тогда ожидание конца
    # процесса встало бы намертво.
    holder.communicate(timeout=60)

    answer = tmp_path / "ответ.txt"
    taker = subprocess.run(
        lock_stand_args(stand, registry, Role="grab", Signal=answer, WaitSeconds=5),
        capture_output=True,
        timeout=120,
    )
    assert taker.returncode == 0, taker.stderr.decode("utf-8", "replace")
    # ‼️ Судим по ОТВЕТУ, а не по часам. Предел ожидания стенду задан коротким: не отпусти убитая
    # вкладка замок — желающий вернулся бы с «нет». Замер настенного времени тут ничего не
    # добавлял, зато привязывал проверку к скорости машины: на загруженном сборочном боксе один
    # запуск оболочки уже съедает секунды, и проверка падала бы на исправном коде.
    assert answer.read_text(encoding="utf-8").strip() == "вошёл", (
        "замок убитой вкладки не отпущен — доска заперта до предела ожидания"
    )
    assert (registry / ".claim.lock").exists(), (
        "файл замка удалили — на новом файле замок возьмёт второй, пока первый держит старый"
    )


# Держит названный файл, никого к нему не пуская, — так его на доли секунды забирает антивирус,
# служба поиска Windows, резервное копирование или папка облачной синхронизации.
HOLD_FILE_STAND = """#Requires -Version 7
param([string]$Path, [string]$Signal, [int]$HoldMs, [string]$UntilLockTaken = '', [int]$ExtraMs = 0)
$stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'None')
try {
    [System.IO.File]::WriteAllText($Signal, 'держу')
    if ($UntilLockTaken) {
        # Отпускаем по СОБЫТИЮ, а не по часам: ждём, пока замок реестра возьмёт объявляющаяся
        # вкладка, и только потом досиживаем короткую добавку. Иначе запас проверки — это разница
        # между «сколько держим» и «за сколько вкладка добегает до чтения», и на машине втрое
        # медленнее он обнуляется: захват кончится раньше, чем до файла дойдут, и проверка станет
        # зеленеть впустую.
        $till = (Get-Date).AddMilliseconds($HoldMs)
        while ((Get-Date) -lt $till) {
            try {
                $probe = [System.IO.File]::Open($UntilLockTaken, 'OpenOrCreate', 'Write', 'None')
                $probe.Dispose()
            } catch {
                # Замок держат — значит вкладка уже внутри и вот-вот полезет читать заявку.
                break
            }
            Start-Sleep -Milliseconds 10
        }
        Start-Sleep -Milliseconds $ExtraMs
    } else {
        Start-Sleep -Milliseconds $HoldMs
    }
} finally {
    $stream.Dispose()
}
"""


def hold_file(
    tmp_path: Path,
    target: Path,
    hold_ms: int,
    until_lock_taken: Path | None = None,
    extra_ms: int = 0,
) -> subprocess.Popen:
    """Забирает файл у всех остальных и возвращает держателя, когда тот уже держит.

    `until_lock_taken` — путь к замку реестра: тогда захват держится, пока замок не возьмёт
    объявляющаяся вкладка, плюс `extra_ms` сверху. Так проверка не зависит от скорости машины.
    """
    assert pwsh
    stand = tmp_path / "держатель-файла.ps1"
    stand.write_text(HOLD_FILE_STAND, encoding="utf-8")
    signal = tmp_path / f"держу-{target.name}.txt"
    argv = [
        pwsh,
        "-NoProfile",
        "-File",
        str(stand),
        "-Path",
        str(target),
        "-Signal",
        str(signal),
        "-HoldMs",
        str(hold_ms),
    ]
    if until_lock_taken is not None:
        argv += ["-UntilLockTaken", str(until_lock_taken), "-ExtraMs", str(extra_ms)]
    holder = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wait_for_file(signal)
    return holder


@needs_pwsh
def test_a_briefly_locked_claim_file_is_waited_out_not_skipped(tmp_path: Path) -> None:
    """Заявку соседа, которую на миг забрали, ЖДУТ — а не считают несуществующей.

    Файл заявки на доли секунды забирает антивирус, служба поиска Windows, резервное копирование и
    папка облачной синхронизации. Читалась она одной попыткой, и любая такая неудача молча
    превращала соседа в несуществующего: вторая вкладка брала его номер, бодро отчитывалась и не
    предупреждала ни словом. Круг разрешения спора следом читал тот же испорченный снимок и
    соперника тоже не видел — совпадение адресов оставалось навсегда.

    Доска находок от этой же беды защищена повторами с самого начала; реестр заявок остался без
    них, а оправдание «писатель у файла один» отвечает на вопрос про запись, а не про чтение.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    claim_bare(board, first)
    assert address_of(board, first).endswith("/1"), (
        "проверка собрана неверно: первый номер не занят"
    )

    # Захват держится, пока объявляющаяся вкладка не возьмёт замок реестра, и ещё полсекунды
    # сверху: столько же она держалась бы на любой машине, быстрой или втрое медленнее.
    holder = hold_file(
        tmp_path,
        claim_file_of(board, first),
        20000,
        until_lock_taken=board.parent / "streams" / ".claim.lock",
        extra_ms=500,
    )
    try:
        second = tmp_path / "вторая"
        out = claim_bare(board, second)
        assert address_of(board, second).endswith("/2"), (
            f"сосед с занятым файлом заявки посчитан несуществующим — номер выдан второй раз: {out!r}"
        )
    finally:
        holder.communicate(timeout=60)


@needs_pwsh
def test_an_unreadable_claim_file_refuses_the_claim_instead_of_reusing_the_number(
    tmp_path: Path,
) -> None:
    """Заявку соседа не прочитать вовсе — объявление отказывает вслух, а не выдаёт его номер снова.

    Повторы спасают от короткой помехи, но не от долгой. Дальше выбор один: отказать на несколько
    секунд, попросив повторить, — или молча выдать соседу его же номер во второй раз и оставить
    два потока с одним адресом навсегда. Отказ поправим, совпадение адресов — нет.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    claim_bare(board, first)

    holder = hold_file(tmp_path, claim_file_of(board, first), 20000)
    try:
        second = tmp_path / "вторая"
        second.mkdir()
        started = time.monotonic()
        done = tool(board, "-Mode", "Claim", cwd=second)
        spent = time.monotonic() - started

        assert done.returncode != 0, (
            f"объявление прошло, не увидев соседа, — адреса совпали молча: {done.stdout!r}"
        )
        assert "не прочитать" in done.stderr, f"причина отказа не названа: {done.stderr!r}"
        assert spent < 15, f"отказ занял {spent:.1f} с — вкладку заставили ждать без толку"
        # Заявок в реестре по-прежнему одна — первой вкладки. Содержимое не читаем: её файл
        # держит стенд, и заглянуть в него нельзя ровно по условию проверки.
        #
        # ‼️ Проверяется ПЕРВОЕ чтение реестра — то, что идёт до записи своей заявки. У второго,
        # в круге разрешения спора, отказ оставит заявку в реестре: она уже записана. Вреда от
        # этого немного (номер выбран по полному снимку, повторное объявление вернёт тот же), но
        # обещать «после отказа заявки не будет» вообще эта проверка не может.
        assert len(list((board.parent / "streams").glob("*.json"))) == 1, (
            "отказ пришёл, а заявка всё равно легла — соседи увидят призрак потока"
        )
    finally:
        holder.kill()
        holder.communicate(timeout=60)


# Виды порчи файла заявки. Все четыре — не выдумка: пустой файл и обрезанный на середине
# остаются от записи, прерванной на полуслове; заявка без рабочей папки — это заявка от ДРУГОЙ
# версии комплекта; файл из одних пробелов даёт та же прерванная запись на иной файловой системе.
BROKEN_CLAIMS = {
    "пустой файл": "",
    "одни пробелы": "   \n  \n",
    "обрезан на середине": '{"wave":"wave6","stream":"3","worktr',
    "заявка чужой версии, без рабочей папки": '{"wave":"wave6","stream":"3"}',
}


def spoil_claim(board: Path, worktree: Path, text: str) -> None:
    """Портит файл заявки названной вкладки — так, как его портит прерванная запись."""
    claim_file_of(board, worktree).write_text(text, encoding="utf-8")


@needs_pwsh
@pytest.mark.parametrize("porch", sorted(BROKEN_CLAIMS), ids=lambda name: name.split(",")[0])
def test_a_broken_claim_file_never_makes_its_stream_invisible(tmp_path: Path, porch: str) -> None:
    """Испорченная заявка не делает поток невидимым молча — ни соседям, ни владельцу.

    Тот же корень, что и у занятого файла: файл лежит на месте, а поток из списка исчезает. Разница
    только в том, что занятость проходит сама, а порча — нет; для того, кто по реестру выбирает
    номер или ищет владельца задачи, разницы нет никакой.

    Молчаливым этот исход был до последней правки, и цена ему — два потока с одним адресом:
    вторая вкладка объявлялась, получала адрес соседа и не предупреждала ни словом. Владелец
    испорченной заявки при этом был невидим и сам себе: сдача отвечала «заявки на этой вкладке
    нет — сдавать нечего» и выходила успехом, а список потоков говорил «заявок на потоки нет».
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    claim_bare(board, first)
    taken = address_of(board, first)
    spoil_claim(board, first, BROKEN_CLAIMS[porch])

    second = tmp_path / "вторая"
    second.mkdir()
    claimed = tool(board, "-Mode", "Claim", cwd=second)
    assert claimed.returncode != 0, (
        f"вторая вкладка объявилась поверх невидимого соседа: {claimed.stdout!r}"
    )
    assert "испорчена" in claimed.stderr, f"причина отказа не названа: {claimed.stderr!r}"
    assert taken not in claimed.stdout, "адрес соседа выдан второй раз"

    # Владелец испорченной заявки не должен слышать «сдавать нечего»: поток числится за ним.
    given = tool(board, "-Mode", "Release", cwd=first)
    assert given.returncode != 0, f"сдача прошла успехом при испорченной заявке: {given.stdout!r}"
    assert "сдавать нечего" not in given.stdout, (
        f"владельцу сказали, что заявки нет, — поток останется числиться за ним: {given.stdout!r}"
    )

    # И вопрос «кто какой поток ведёт» не должен отвечать «никаких».
    listed = tool(board, "-Mode", "Streams", cwd=first)
    assert "Заявок на потоки нет" not in listed.stdout, (
        f"список потоков объявил реестр пустым, хотя заявка лежит: {listed.stdout!r}"
    )


@needs_pwsh
def test_an_unusual_but_valid_claim_file_is_still_read(tmp_path: Path) -> None:
    """Законная, но непривычная заявка читается — строгость не должна ловить своих.

    Отказ на испорченной заявке останавливает работу всем вкладкам проекта, пока файл не поправят.
    Такой ценой нельзя платить за ложное срабатывание, поэтому проверяется и обратная сторона:
    заявка в UTF-16, в UTF-8 с меткой порядка байт, с виндовыми переводами строк и с лишним
    незнакомым полем — это законные заявки, и поток по ним обязан быть виден.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    claim_bare(board, first)
    path = claim_file_of(board, first)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["невиданное_поле"] = "из будущей версии"
    text = json.dumps(record, ensure_ascii=False, indent=2).replace("\n", "\r\n")

    for encoding in ("utf-16", "utf-8-sig"):
        path.write_text(text, encoding=encoding)
        second = tmp_path / f"вторая-{encoding}"
        out = claim_bare(board, second)
        assert address_of(board, second).endswith("/2"), (
            f"законная заявка в {encoding} принята за испорченную — адреса совпали: {out!r}"
        )
        claim_file_of(board, second).unlink()


@needs_pwsh
def test_the_task_owner_question_never_answers_nobody_when_a_claim_is_busy(tmp_path: Path) -> None:
    """Вопрос «чей это кусок работы» не отвечает «никто не взял», пока заявку соседа не прочитать.

    Этот вопрос правила проекта велят задать ПЕРЕД тем, как предлагать владельцу работу за
    пределами своих задач. Ответ «задачу никто не взял» — прямое разрешение занять чужой кусок, и
    владелец подтвердит, потому что знать не может. Цена — две вкладки делают одну работу и сводят
    её конфликтом слияния, а вовсе не «невидимая строка в показе», которой оправдана терпимость.
    """
    board = tmp_path / "board.jsonl"
    owner = tmp_path / "хозяин-задач"
    claim(board, owner, "wave6", "3", "-Tasks", "10-13")

    asking = tmp_path / "спрашивающий"
    asking.mkdir()
    holder = hold_file(tmp_path, claim_file_of(board, owner), 20000)
    try:
        asked = tool(board, "-Mode", "Streams", "-Task", "11", cwd=asking)
        assert "не объявлена ни одним потоком" not in asked.stdout, (
            f"чужую задачу объявили ничьей — вкладка займёт её с разрешения владельца: {asked.stdout!r}"
        )
        assert asked.returncode != 0, f"ответ выдан уверенно, хотя реестр неполон: {asked.stdout!r}"
    finally:
        holder.kill()
        holder.communicate(timeout=60)


@needs_pwsh
def test_a_finding_for_a_released_stream_is_refused_even_when_its_claim_is_busy(
    tmp_path: Path,
) -> None:
    """Находку СДАННОМУ потоку не принимают и тогда, когда его заявку в этот миг не прочитать.

    При исправном реестре отказ приходит как задумано. При занятом файле заявки того же потока
    запись принималась с рапортом «поток ещё не объявлялся — дождётся его заявки и придёт в первую
    же минуту работы». Заявки не будет никогда: поток сдан. Цена — потерянная находка плюс
    успокоенный автор, который после такого рапорта не заведёт себе запасного пункта в плане.
    """
    board = tmp_path / "board.jsonl"
    gone = tmp_path / "сданный"
    claim(board, gone, "wave6", "4")
    given = release(board, gone)
    assert given.returncode == 0, given.stderr

    holder = hold_file(tmp_path, claim_file_of(board, gone), 20000)
    try:
        author = tmp_path / "автор"
        author.mkdir()
        done = tool(board, "-Mode", "Add", "-To", "wave6/4", "-Title", "находка", cwd=author)
        assert done.returncode != 0, f"находка принята сданному потоку: {done.stdout!r}"
        assert "ещё не объявлялся" not in done.stdout, (
            f"сданный поток выдан за неоткрытый — автор успокоится, а находка пропадёт: {done.stdout!r}"
        )
    finally:
        holder.kill()
        holder.communicate(timeout=60)


@needs_pwsh
def test_refusals_about_the_registry_directory_name_the_real_cause(tmp_path: Path) -> None:
    """Отказ про каталог заявок называет настоящую причину, а не одну заготовку на все беды.

    Прежде на любую беду отвечали «на месте каталога заявок лежит не каталог, уберите его» —
    и на несуществующем диске, и в папке без прав убирать было нечего. Потом появилась развилка с
    причиной, но причина оказалась пустой: создание каталога поверх перекрытого пути молча
    рапортует успехом, ничего не создав. А до несуществующего диска дело не доходило вовсе:
    инструмент падал раньше, на разборе пути, и выносил наружу сырое системное сообщение.
    """
    blocked = tmp_path / "мешает"
    blocked.write_text("я файл, а не папка", encoding="utf-8")
    tab = tmp_path / "вкладка"
    tab.mkdir()

    denied = tool(blocked / "board.jsonl", "-Mode", "Claim", cwd=tab)
    assert denied.returncode != 0, "объявление прошло поверх перекрытого пути"
    assert "перекрыт файлом" in denied.stderr and str(blocked) in denied.stderr, (
        f"виновник не назван: {denied.stderr!r}"
    )

    # Несуществующий диск: отказ обязан быть нашим и по-русски, а не сырым системным сообщением.
    missing = tool(dead_board_path(), "-Mode", "Claim", cwd=tab)
    assert missing.returncode != 0, "объявление прошло на несуществующем диске"
    assert "каталог заявок" in missing.stderr, (
        f"отказ пришёл не от инструмента, а сырым системным сообщением: {missing.stderr!r}"
    )


@needs_pwsh
def test_an_unopenable_lock_refuses_at_once_instead_of_waiting_for_a_neighbour(
    tmp_path: Path,
) -> None:
    """Замок, который не открыть в принципе, отказывает сразу — а не выдаёт себя за занятость соседом.

    Спор за замок и неустранимая помеха приходят одинаково: отказом открыть файл. Спутаешь — и
    вкладка честно ждёт полминуты, печатает пугающее «рядом объявляется другая вкладка», а следом
    всё равно падает. До замка отказ здесь был мгновенным и честным.

    Помеха взята такая, чтобы дойти именно до РАЗЛИЧЕНИЯ: каталог реестра настоящий (иначе всё
    кончится раньше, на проверке каталога), а на месте самого файла замка лежит каталог — открыть
    его файлом нельзя ни сейчас, ни через полминуты.
    """
    board = tmp_path / "board.jsonl"
    registry = board.parent / "streams"
    registry.mkdir(parents=True)
    (registry / ".claim.lock").mkdir()

    tab = tmp_path / "вкладка"
    tab.mkdir()
    started = time.monotonic()
    done = tool(board, "-Mode", "Claim", cwd=tab)
    spent = time.monotonic() - started

    assert done.returncode != 0, f"объявление прошло поверх незаводимого замка: {done.stdout!r}"
    assert "замок реестра заявок не завести" in done.stderr, (
        f"причина отказа не названа: {done.stderr!r}"
    )
    assert spent < 15, f"отказ занял {spent:.1f} с — помеху приняли за занятость соседом"


@needs_pwsh
def test_a_blocked_registry_path_refuses_at_once_instead_of_waiting(tmp_path: Path) -> None:
    """Неустранимая помеха отказывает сразу и по-настоящему, а не выдаёт себя за занятость соседом.

    Спор за замок и помеху легко спутать: и то, и другое приходит отказом открыть файл. Спутаешь —
    и вкладка честно ждёт предел ожидания, печатает пугающее «рядом объявляется другая вкладка», а
    следом всё равно падает на записи заявки. До замка отказ здесь был мгновенным и честным.

    Помеха взята самая простая: на месте каталога заявок лежит файл. Проверяется и текст отказа, и
    время: ожидание замка выдало бы себя секундами.
    """
    board = tmp_path / "board.jsonl"
    # На месте каталога заявок — файл. Каталог там не завести, и ждать этого бессмысленно.
    (board.parent / "streams").write_text("не каталог", encoding="utf-8")

    tab = tmp_path / "вкладка"
    tab.mkdir()
    started = time.monotonic()
    done = tool(board, "-Mode", "Claim", cwd=tab)
    spent = time.monotonic() - started

    assert done.returncode != 0, f"объявление прошло поверх негодного реестра: {done.stdout!r}"
    assert "не каталог" in done.stderr, f"причина отказа не названа: {done.stderr!r}"
    assert spent < 15, f"отказ занял {spent:.1f} с — помеху приняли за занятость соседом"


needs_windows_acl = pytest.mark.skipif(
    os.name != "nt" or not shutil.which("icacls"),
    reason="закрыть доступ к каталогу нечем — правила доступа Windows недоступны",
)


def deny_listing(folder: Path) -> None:
    """Закрывает чтение СОДЕРЖИМОГО каталога, оставляя доступ к самим файлам.

    Так ведут себя защита папок Windows и часть средств защиты рабочих мест, а на сетевой шаре —
    право захода без права перечисления. Именно в этом положении опись каталога отвечала пустотой.
    """
    who = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    done = subprocess.run(
        ["icacls", str(folder), "/deny", f"{who}:(RD)"], capture_output=True, timeout=60
    )
    assert done.returncode == 0, done.stdout.decode("utf-8", "replace")


def dead_board_path() -> Path:
    """Путь к доске на диске, которого на этой машине НЕТ.

    ‼️ Букву не зашиваем: на сборочном боксе и на машине с сетевыми дисками зашитая буква может
    оказаться живой, и проверка молча поменяет смысл — вместо мёртвого пути получится обычный.
    Свободной буквы не нашлось — честно пропускаем, а не зеленеем впустую.
    """
    if os.name != "nt":
        pytest.skip("подбор несуществующего диска написан под Windows")
    for letter in "ZYXWVUT":
        if not os.path.exists(f"{letter}:\\"):
            return Path(f"{letter}:/нет-такого/board.jsonl")
    pytest.skip("свободной буквы диска нет — мёртвый путь собрать не из чего")


def deny_listing_or_skip(folder: Path) -> None:
    """Закрывает доступ и УБЕЖДАЕТСЯ, что он закрыт; не подействовало — пропускаем с причиной.

    ‼️ Запрет доступа действует не под всякой учётной записью: у части служебных он обходится, и
    тогда проверка молча меняет смысл — помехи нет, а проверка либо зеленеет впустую, либо падает
    на исправном коде. Ровно это и случилось на сборочном боксе. Молча зеленеть проверка не должна,
    но и падать там, где проверять нечем, — тоже.
    """
    deny_listing(folder)
    try:
        os.listdir(folder)
    except PermissionError:
        return
    allow_listing(folder)
    pytest.skip("здесь запрет доступа к каталогу не действует — проверять нечем")


def allow_listing(folder: Path) -> None:
    """Возвращает права — иначе каталог не удалит и уборка временных папок."""
    who = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    subprocess.run(["icacls", str(folder), "/remove:d", who], capture_output=True, timeout=60)


@needs_pwsh
@needs_windows_acl
def test_an_unlistable_registry_is_never_taken_for_an_empty_one(tmp_path: Path) -> None:
    """Каталог заявок, который не перечислить, — это не «заявок нет».

    Самая тихая дыра всего механизма, и лежала она не в чтении файлов, а в САМОМ ПЕРВОМ вопросе:
    что вообще лежит в папке. Перечисление средствами оболочки с образцом имени при закрытом
    доступе к содержимому каталога возвращает пустой список и НЕ СООБЩАЕТ ОБ ОШИБКЕ — ловить
    нечего, и весь строгий заслон выше по коду просто не вызывался ни разу.

    Наружу это выходило так: сосед ведёт первый поток, вторая вкладка объявляется и получает тот
    же номер с кодом успеха; вопрос «чей это кусок» отвечает «никто не взял»; находка живому
    потоку — «поток ещё не объявлялся». Всё молча.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    claim(board, first, "wave6", "1", "-Tasks", "10-13")
    registry = board.parent / "streams"

    deny_listing_or_skip(registry)
    try:
        second = tmp_path / "вторая"
        second.mkdir()
        claimed = tool(board, "-Mode", "Claim", "-Wave", "wave6", cwd=second)
        assert claimed.returncode != 0, (
            f"вкладка объявилась по невидимому реестру — номер совпадёт с соседним: {claimed.stdout!r}"
        )
        assert "не перечислить" in claimed.stderr, f"причина не названа: {claimed.stderr!r}"

        asked = tool(board, "-Mode", "Streams", "-Task", "11", cwd=second)
        assert "не объявлена ни одним потоком" not in asked.stdout, (
            f"чужую задачу объявили ничьей по невидимому реестру: {asked.stdout!r}"
        )
        assert asked.returncode != 0, f"ответ выдан уверенно: {asked.stdout!r}"

        offered = tool(board, "-Mode", "Add", "-To", "wave6/1", "-Title", "находка", cwd=second)
        assert "ещё не объявлялся" not in offered.stdout, (
            f"живой поток выдан за неоткрытый: {offered.stdout!r}"
        )
        assert offered.returncode != 0, f"находка принята по невидимому реестру: {offered.stdout!r}"
    finally:
        allow_listing(registry)


@needs_pwsh
@needs_windows_acl
def test_handing_over_a_stream_does_not_pass_on_an_unreadable_registry(tmp_path: Path) -> None:
    """Сдача потока не проходит, пока реестр не прочитан: иначе находка остаётся ничьей навсегда.

    Тонкий шов: разбор адреса «волна/поток» опирается на список имён объявленных волн тогда, когда
    волна названа СЛОВОМ. Список этот — производная от реестра, и строился он один раз, терпимо.
    Неполный список означает, что адрес не узнаётся, находка не считается находкой этого потока,
    ящик выглядит пустым — и сдача проходит успехом, оставляя запись на доске навсегда. Автору
    находки при этом уже отрапортовали «поток ведёт вкладка, скорее всего дойдёт сама».

    Волна названа словом намеренно: на волнах, названных номером или датой, разбор адреса в
    реестр не ходит, и эта помеха ничего не меняет.
    """
    board = tmp_path / "board.jsonl"
    owner = tmp_path / "ведущий"
    claim(board, owner, "sprint-alpha", "1")
    neighbour = tmp_path / "сосед"
    claim(board, neighbour, "sprint-alpha", "2")
    add(board, "sprint-alpha/1", "важная находка", cwd=neighbour)

    # Контроль: при исправном реестре сдача отклоняется — в ящике лежит непрочитанная находка.
    control = release(board, owner)
    assert control.returncode != 0, "проверка собрана неверно: ящик оказался пуст"
    assert "осталось открытым" in control.stderr, control.stderr

    registry = board.parent / "streams"
    deny_listing_or_skip(registry)
    try:
        given = release(board, owner)
        assert given.returncode != 0, (
            f"поток сдан по невидимому реестру — находка останется на доске ничьей: {given.stdout!r}"
        )
        assert "сдан" not in given.stdout, f"вкладке сказали, что поток сдан: {given.stdout!r}"
    finally:
        allow_listing(registry)


needs_git = pytest.mark.skipif(
    not shutil.which("git"), reason="git не найден — деревьев не завести"
)


def real_worktrees(root: Path, tabs: dict[str, str]) -> None:
    """Настоящий репозиторий с рабочими деревьями: {имя папки: имя ветки}.

    Настоящий он потому, что часть имён потока берётся у git, и на подставных папках такие имена
    просто не появляются — проверка молча слабела бы.
    """
    main = root / "repo"

    def git(*args: str, cwd: Path = main) -> None:
        done = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120
        )
        assert done.returncode == 0, done.stderr

    main.mkdir(parents=True)
    git("init", "-q", "-b", "main", ".")
    git("config", "user.email", "проверка@стенд")
    git("config", "user.name", "проверка")
    (main / "readme.md").write_text("проба", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "начало")
    for folder, branch in tabs.items():
        git("worktree", "add", "-q", "-b", branch, str(root / folder))


def rename_branch(tab: Path, name: str) -> None:
    """Переименовывает ветку рабочего дерева — так же, как это делают руками к середине волны."""
    done = subprocess.run(
        ["git", "branch", "-m", name], cwd=str(tab), capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr


@needs_pwsh
@needs_git
def test_a_reused_branch_name_belongs_to_the_one_who_carries_it_now(tmp_path: Path) -> None:
    """Имя, которое поток лишь ПОМНИТ, отдаётся тому, кто носит его сейчас, — и только ему.

    Имена веток в живом репозитории переиспользуют. Стоило памяти прежних имён появиться без этого
    правила — имя стало законно указывать на два потока разом, а закрытие находки с именным
    адресом ОБЩЕЕ: кто закрыл первым, погасил её у всех. Воспроизведено: находка уходила обеим
    вкладкам, обеим говорилось «учли — закройте», и та, что имя лишь помнит, гасила чужую находку.
    Истинный адресат не видел ничего и сдавался зелёным с пустым ящиком, а автору приходило
    «учтено» от потока, которого он не называл.

    Отсюда правило: на каждое имя отзывается не больше одного потока, и носящий имя сейчас
    выигрывает у помнящего.
    """
    real_worktrees(tmp_path, {"первая": "альфа", "вторая": "запас", "третья": "третья-ветка"})
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    second = tmp_path / "вторая"
    claim(board, first, "wave9", "1")
    rename_branch(first, "гамма")
    # Первая помнит «альфа»; заново объявившись, она это имя удержит.
    claim(board, first, "wave9", "1")
    # Вторая берёт освободившееся имя — обычное дело в живом репозитории.
    rename_branch(second, "альфа")
    claim(board, second, "wave9", "2")

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "альфа",
        "-Title",
        "для нынешней альфы",
        cwd=tmp_path / "третья",
        known=True,
    )
    assert offered.returncode == 0, f"находка не принята: {offered.stderr!r}"

    to_carrier = run_deliver(board, second, "Start", "носитель")
    assert "для нынешней альфы" in to_carrier, (
        f"находка не дошла до того, кто носит имя сейчас: {to_carrier!r}"
    )
    to_rememberer = run_deliver(board, first, "Start", "помнящий")
    assert "для нынешней альфы" not in to_rememberer, (
        "находка ушла и тому, кто имя лишь помнит, — он погасит её у настоящего адресата: "
        f"{to_rememberer!r}"
    )

    # И закрыть чужую именную находку он не должен даже зная метку.
    mark = offered.stdout.split("метка ")[1].split(")")[0].strip()
    denied = tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    assert denied.returncode != 0, "чужую именную находку погасили — адресат её больше не увидит"
    assert "адресована не вам" in denied.stderr, f"причина отказа не названа: {denied.stderr!r}"

    # Человеку видно, что имя у потока отняли.
    listed = run_tool(board, "-Mode", "Streams", cwd=first)
    assert "имена отняты: альфа" in listed, (
        f"отнятое имя нигде не показано — человек не заметит расхождения: {listed!r}"
    )


@needs_pwsh
@needs_windows_acl
def test_a_released_stream_gets_nothing_even_when_the_registry_is_unreadable(
    tmp_path: Path,
) -> None:
    """Сданному потоку не носят находок и тогда, когда его заявки нет в снимке реестра.

    Обычно сданный поток молчит потому, что не отзывается ни на одно имя, — это решается по всему
    реестру сразу. Но снимок реестра может оказаться неполным: каталог заявок не перечислился, а
    свой файл при этом читается напрямую. Тогда имена берутся запасным путём — те, что поток носит,
    — и сданный поток снова получил бы находку, а вместе с ней указание её закрыть.

    Заслон на этот случай стоит в самом стороже доставки. Проверка ставит именно то положение:
    перечисление каталога закрыто, свой файл доступен.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "уходящая"
    claim_bare(board, mine)
    add(board, "уходящая", "оставшаяся находка", cwd=tmp_path)
    given = release(board, mine, "-Force")
    assert given.returncode == 0, given.stderr

    registry = board.parent / "streams"
    deny_listing_or_skip(registry)
    try:
        brought = run_deliver(board, mine, "Start", "сданный-без-реестра")
        assert "оставшаяся находка" not in brought, (
            f"сданному потоку принесли находку по запасным именам: {brought!r}"
        )
    finally:
        allow_listing(registry)


@needs_pwsh
@needs_git
def test_a_name_two_streams_remember_is_refused_with_the_real_reason(tmp_path: Path) -> None:
    """Имя, которое помнят двое и не носит никто, отказывается с НАСТОЯЩЕЙ причиной.

    Такое имя нарочно не достаётся никому: иначе находка ушла бы обоим, а закрытие именного адреса
    общее — любой погасил бы её у другого. Отказ при этом говорил, что адресата нет среди рабочих
    деревьев, и предлагал три объяснения, из которых не верно ни одно; правда была видна только в
    показе потоков, куда отказ и не отсылал. За вводящие в заблуждение отказы мы ловили дефекты
    круг за кругом.
    """
    real_worktrees(tmp_path, {"первая": "общее", "вторая": "запас", "третья": "третья-ветка"})
    board = tmp_path / "board.jsonl"
    first, second = tmp_path / "первая", tmp_path / "вторая"
    claim(board, first, "wave9", "1")
    rename_branch(first, "первая-новая")
    claim(board, first, "wave9", "1")
    # Второй берёт то же имя и тоже уходит с него: теперь «общее» помнят двое и не носит никто.
    rename_branch(second, "общее")
    claim(board, second, "wave9", "2")
    rename_branch(second, "вторая-новая")
    claim(board, second, "wave9", "2")

    denied = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "общее",
        "-Title",
        "кому?",
        cwd=tmp_path / "третья",
        known=True,
    )
    assert denied.returncode != 0, f"находка принята по имени, которое ничьё: {denied.stdout!r}"
    assert "помнят два потока" in denied.stderr, f"настоящая причина не названа: {denied.stderr!r}"
    assert "рабочих деревьев нет" not in denied.stderr, (
        f"отказ по-прежнему объясняет беду неверно: {denied.stderr!r}"
    )
    assert "-Mode Streams" in denied.stderr, (
        f"человека не отправили туда, где расхождение видно: {denied.stderr!r}"
    )


@needs_pwsh
def test_a_released_stream_neither_gets_findings_nor_kills_them(tmp_path: Path) -> None:
    """Сданный поток не получает доставку и не гасит находку.

    Ему только что сказали «находки больше не примут», а доставка продолжала носить их — и текст
    доставки прямо велит закрыть запись, если она к работе не относится. Закрытие именного адреса
    ОБЩЕЕ, так что сданный поток гасил находку насовсем: её автору приходило «учтено», хотя учитывать
    её было некому. Ящик сданного потока не пуст ровно тогда, когда сдавались ключом -Force —
    оставшееся тогда переносят в «Хвосты волны» руками, а не закрывают вкладкой, которой уже нет.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "уходящая"
    claim_bare(board, mine)
    mark = add(board, "уходящая", "оставшаяся находка", cwd=tmp_path)

    given = release(board, mine, "-Force")
    assert given.returncode == 0, given.stderr
    assert "сдан" in given.stdout, given.stdout

    brought = run_deliver(board, mine, "Start", "после-сдачи")
    assert "оставшаяся находка" not in brought, (
        f"сданному потоку принесли находку — ему нечего с ней делать: {brought!r}"
    )

    denied = tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    assert denied.returncode != 0, "сданный поток погасил находку, которую больше некому учесть"


@needs_pwsh
@needs_git
def test_a_finding_by_the_current_branch_name_reaches_the_stream(tmp_path: Path) -> None:
    """Находку по НЫНЕШНЕМУ имени ветки узнают все трое, хотя объявлялся поток под другим.

    Третья сторона согласия — приём находки. Он сверяется с именами потока, и если брать только
    записанные в заявке, то после переименования ветки поток по её новому имени не узнаётся:
    рапорт автору становится осторожнее правды («жива вкладка или нет — неизвестно») там, где на
    самом деле известно, что поток ведут. Находка при этом не теряется, но незамеченный откат
    такой правки — ровно тот механизм, которым дефекты выживали круг за кругом.

    Деревья настоящие: нынешнее имя ветки берётся у git, и на подставных папках его нет вовсе.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа", "сосед": "сосед-ветка"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave6", "1")

    renamed = subprocess.run(
        ["git", "branch", "-m", "бета"], cwd=str(tab), capture_output=True, text=True, timeout=120
    )
    assert renamed.returncode == 0, renamed.stderr

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "бета",
        "-Title",
        "по новому имени",
        cwd=tmp_path / "сосед",
        known=True,
    )
    assert offered.returncode == 0, (
        f"приём не узнал поток по новому имени ветки: {offered.stderr!r}"
    )
    assert "Поток ведёт вкладка" in offered.stdout, (
        f"поток опознан не по заявке — рапорт автору осторожнее правды: {offered.stdout!r}"
    )

    brought = run_deliver(board, tab, "Start", "новое-имя")
    assert "по новому имени" in brought, f"доставка находку не принесла: {brought!r}"

    given = release(board, tab)
    assert given.returncode != 0 and "осталось открытым" in given.stderr, (
        f"сдача находку не увидела: {given.stdout!r} {given.stderr!r}"
    )


@needs_pwsh
@needs_git
def test_a_finding_by_a_former_branch_name_still_reaches_the_stream(tmp_path: Path) -> None:
    """Находка по ПРЕЖНЕМУ имени ветки доходит и после того, как вкладка объявилась заново.

    Объявиться можно второй раз, и объявление молча переписывает заявку. Переименуй вкладка ветку
    и объявись заново — старое имя стёрлось бы отовсюду, а находка, посланная по нему, не дошла бы
    и сдачу не задержала: приём её принял бы (дерево-то на месте) и пообещал автору доставку.

    Поэтому объявление переносит прежние имена ветки в новую заявку, и они остаются именами потока.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа", "сосед": "сосед-ветка"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave6", "1")

    subprocess.run(["git", "branch", "-m", "бета"], cwd=str(tab), capture_output=True, timeout=120)
    # Второе объявление: заявка переписывается целиком, и прежнее имя обязано в ней уцелеть.
    claim(board, tab, "wave6", "1")

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "альфа",
        "-Title",
        "по прежнему имени",
        cwd=tmp_path / "сосед",
        known=True,
    )
    assert offered.returncode == 0, (
        f"поток забыл своё прежнее имя — находка по нему не принята: {offered.stderr!r}"
    )

    brought = run_deliver(board, tab, "Start", "прежнее-имя")
    assert "по прежнему имени" in brought, (
        f"находка по прежнему имени не доставлена — она не дойдёт никогда: {brought!r}"
    )

    given = release(board, tab)
    assert given.returncode != 0 and "осталось открытым" in given.stderr, (
        f"сдача находку по прежнему имени не увидела: {given.stdout!r} {given.stderr!r}"
    )


@needs_pwsh
def test_all_three_sides_know_the_stream_by_the_same_names(tmp_path: Path) -> None:
    """Приём, доставка и сдача обязаны опознавать поток ОДНИМ И ТЕМ ЖЕ набором имён.

    Дыра здесь другого устройства, чем молчаливые чтения: файловая система не врёт вовсе, просто
    две стороны механизма договорились по-разному. Приём сверялся с именами НА МОМЕНТ ОБЪЯВЛЕНИЯ,
    доставка — только с теми, что выяснены ПРЯМО СЕЙЧАС. Ветку переименовали или переключили (к
    середине волны — обычное дело), сосед положил находку по имени ветки, как поток назван в
    плане: приём её принял и пообещал «поток ведёт вкладка — скорее всего, дойдёт сама», а
    доставка не принесла её ни в этот ход, ни назавтра.

    Поэтому проверяются не три стороны по отдельности, а их СОГЛАСИЕ: одно и то же имя проходит
    через приём, доставку и сдачу. Разъедься любая пара — падает.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "своя-папка"
    claim_bare(board, mine)
    # Ветка потока названа НЕ так, как папка: именно в этом расхождении и жила потеря.
    patch_claim(board, mine, branch="feat/ветка-потока")

    taken = tool(
        board, "-Mode", "Add", "-To", "feat/ветка-потока", "-Title", "находка соседа", cwd=tmp_path
    )
    assert taken.returncode == 0, f"приём не узнал поток по имени его ветки: {taken.stderr!r}"

    brought = run_deliver(board, mine, "Start", "с-именем-ветки")
    assert "находка соседа" in brought, (
        f"приём находку принял, а доставка её не знает — она не дойдёт никогда: {brought!r}"
    )

    given = release(board, mine)
    assert given.returncode != 0, (
        f"поток сдан вместе с находкой, адресованной именем его ветки: {given.stdout!r}"
    )
    assert "осталось открытым" in given.stderr, (
        f"находка по имени ветки в ящик не попала: {given.stderr!r}"
    )


@needs_pwsh
def test_the_owner_of_a_broken_claim_is_told_which_file_to_remove(tmp_path: Path) -> None:
    """Владелец испорченной заявки должен узнать ПУТЬ к файлу и выполнимый выход.

    Отказ без пути — тупик на ровном месте: «поправьте файл» не говорит какой, а «объявитесь
    заново» невозможно — объявление читает весь реестр строго, натыкается на тот же файл и тоже
    отказывает. Человек читает свой собственный отказ и выхода из него не получает.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "моя"
    claim_bare(board, mine)
    path = claim_file_of(board, mine)
    path.write_text("", encoding="utf-8")

    given = tool(board, "-Mode", "Release", cwd=mine)
    assert given.returncode != 0, f"сдача прошла при испорченной заявке: {given.stdout!r}"
    assert str(path) in given.stderr, f"путь к своему файлу не назван: {given.stderr!r}"
    assert "Уберите этот файл" in given.stderr, f"выполнимый выход не назван: {given.stderr!r}"


@needs_pwsh
def test_a_dead_path_is_never_taken_for_an_empty_registry(tmp_path: Path) -> None:
    """Пропавший диск или шара — это не «заявок ещё нет», и путать их нельзя.

    Опись каталога один вид отказа переводит в законный ответ «реестра ещё нет»: его заводит
    первая же объявившаяся вкладка. Но тем же самым отказом система отвечает и на МЁРТВЫЙ ПУТЬ —
    отвалившийся диск, пропавшую сетевую шару. Пока их не различали, на мёртвом пути вопрос «чей
    это кусок» отвечал «задачу никто не взял», а сдача — «заявки на этой вкладке нет», обе кодом
    успеха: то же последствие, что и у всех дыр этого класса.

    Различие простое: жив ли хоть один каталог выше по пути.
    """
    board = dead_board_path()
    tab = tmp_path / "вкладка"
    tab.mkdir()

    asked = tool(board, "-Mode", "Streams", "-Task", "11", cwd=tab)
    assert "не объявлена ни одним потоком" not in asked.stdout, (
        f"на мёртвом пути чужую задачу объявили ничьей: {asked.stdout!r}"
    )
    assert asked.returncode != 0, f"ответ выдан уверенно по мёртвому пути: {asked.stdout!r}"

    given = tool(board, "-Mode", "Release", cwd=tab)
    assert "сдавать нечего" not in given.stdout, (
        f"на мёртвом пути сказали, что заявки нет: {given.stdout!r}"
    )
    assert given.returncode != 0, f"сдача прошла по мёртвому пути: {given.stdout!r}"


@needs_pwsh
def test_a_finding_on_a_missing_drive_is_refused_in_our_own_words(tmp_path: Path) -> None:
    """На негодном пути отказывает инструмент, а не система своим английским сообщением.

    Разбор пути средствами оболочки спрашивает у неё про диск и на несуществующем диске (а равно
    на отвалившейся сетевой шаре) срывается насмерть. В объявлении это вылечили кругом раньше, а
    приём находки падал ровно так же — и наружу выходило сырое системное сообщение, из которого
    человеку неясно ни что случилось, ни что делать.
    """
    tab = tmp_path / "вкладка"
    tab.mkdir()
    done = tool(
        dead_board_path(),
        "-Mode",
        "Add",
        "-To",
        "wave6/3",
        "-Title",
        "находка",
        cwd=tab,
    )
    assert done.returncode != 0, "находка легла на несуществующий диск"
    # Отказать может любое из звеньев по пути (опись реестра, запись на доску) — важно, что
    # отказывает ИНСТРУМЕНТ своими словами, а не система своим английским сообщением.
    assert "заявок" in done.stderr or "доску" in done.stderr, (
        f"отказ пришёл не от инструмента: {done.stderr!r}"
    )
    assert not done.stderr.strip().startswith("Cannot find drive"), (
        f"наружу вышло сырое системное сообщение: {done.stderr!r}"
    )


@needs_pwsh
def test_a_leftover_lock_file_is_neither_a_barrier_nor_a_claim(tmp_path: Path) -> None:
    """Оставшийся файл замка не держит объявление и не считается заявкой потока.

    Файл замка живёт вечно и намеренно: держит замок открытый дескриптор, а не файл, и удалять его
    нельзя — второй процесс завёл бы файл заново и взял замок на новом, пока первый держит старый.
    Отсюда два свойства, которые обязаны выполняться на каждом объявлении.

    Первое: файл на месте не значит «занято». Он остаётся от каждой прошлой вкладки, и упрись
    объявление в него — доска заперлась бы после первой же закрытой вкладки.

    Второе: лежит он В реестре заявок, а реестр читают целиком. Подхвати его выдача — в списке
    соседей появился бы поток-призрак, и следующий номер уехал бы мимо.
    """
    board = tmp_path / "board.jsonl"
    registry = board.parent / "streams"
    registry.mkdir(parents=True)
    lock = registry / ".claim.lock"
    lock.write_text("процесс 31337@машина, взят 2026-01-01T10:00:00", encoding="utf-8")

    tab = tmp_path / "после-падения"
    out = claim_bare(board, tab)
    assert "объявлен за этой вкладкой" in out, f"оставшийся файл замка запер объявление: {out!r}"
    assert "Замок реестра заявок не отдали" not in out, (
        f"вкладка ждала замок, которого никто не держит: {out!r}"
    )
    assert address_of(board, tab).endswith("/1"), (
        "замок посчитали чужой заявкой, и номер потока уехал на следующий"
    )

    assert lock.exists(), "файл замка удалён — на новом файле замок возьмёт второй"
    streams = run_tool(board, "-Mode", "Streams")
    assert "Заявок на потоки: 1" in streams, f"файл замка попал в выдачу заявок: {streams!r}"


@needs_pwsh
def test_a_number_taken_by_a_neighbour_is_given_up_after_the_claim_is_written(
    tmp_path: Path,
) -> None:
    """Одинаковый номер не должен пережить объявление молча.

    Собираем ровно то, что даёт одновременный старт: заявка соседа появляется в реестре ПОСЛЕ
    того, как вкладка выбрала себе номер. Уступает объявившийся позже — порядок один и тот же у
    обеих сторон, иначе они менялись бы номерами без конца.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первая"
    second = tmp_path / "вторая"
    claim_bare(board, first)
    # Прячем заявку соседа: так вкладка выбирает номер, ещё не видя его, — и берёт тот же самый.
    hidden = claim_file_of(board, first)
    kept = hidden.read_text(encoding="utf-8")
    hidden.unlink()
    claim_bare(board, second)
    assert address_of(board, second).endswith("/1"), "проверка собрана неверно: номера не совпали"

    hidden.write_text(kept, encoding="utf-8")
    patch_claim(board, first, claimed_at="2026-01-01T10:00:00")
    out = claim_bare(board, second)

    assert address_of(board, second) == f"{today_wave()}/2", (
        f"вкладка осталась на чужом номере — находка придёт обеим: {out!r}"
    )
    assert "сдвинут на следующий свободный" in out, (
        f"о сдвиге номера не сказано ни слова, и соседям назовут прежний адрес: {out!r}"
    )


@needs_pwsh
def test_a_number_from_the_plan_is_never_moved_and_never_doubled(tmp_path: Path) -> None:
    """Номер из плана — имя потока, им адресуют находки: двигать его нельзя, а двоить тем более.

    Прежде эта проверка закрепляла СЕГОДНЯШНЕЕ поведение — вторая вкладка получала тот же адрес,
    предупреждение печаталось ПОСЛЕ записи, и адрес вели две живые заявки. Это и был дефект 1:
    кому придёт находка, решал порядок описи каталога. Теперь на этом месте отказ ДО записи, и
    выход из него один явный — ключ переноса; закреплённое прежде поведение отменено осознанно.

    Двигать номер по-прежнему нельзя: он назван в плане, им адресуют находки, — поэтому вторая
    вкладка не «уезжает на следующий свободный», а получает отказ, и решает спор человек.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "wave9-первая"
    second = tmp_path / "wave9-вторая"
    second.mkdir()
    claim(board, first, "wave9", "3")
    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=second)

    assert denied.returncode != 0, (
        f"вторая вкладка получила тот же адрес — адрес снова ведут двое: {denied.stdout!r}"
    )
    assert "На этот же поток есть открытая заявка другого дерева" not in denied.stdout, (
        f"предупреждение ПОСЛЕ записи вернулось вместо отказа ДО неё: {denied.stdout!r}"
    )
    assert not list(registry_dir(board).glob("*wave9-вторая*")), (
        "заявка второй вкладки всё-таки записана — отказ идёт не до записи"
    )
    assert address_of(board, first) == "wave9/3", (
        "названный номер сдвинули — адрес из плана уехал сам собой"
    )
    # Выход из отказа — один, явный и названный: перехват бывает осознанным (первую вкладку
    # закрыли, не сдав), и разрешает такой спор человек, а не механизм.
    taken = claim(board, second, "wave9", "3", "-TakeOver")
    assert address_of(board, second) == "wave9/3", f"ключ переноса адрес не отдал: {taken!r}"


@needs_pwsh
def test_claim_warns_about_a_number_that_cannot_be_addressed(tmp_path: Path) -> None:
    """Номер потока словом делает поток неадресуемым — сказать об этом надо сразу.

    Разбор адреса требует справа номер: `сбои/каналсбоев` не разбирается вовсе, и находку такому
    потоку основным способом не пошлют. Отказывать нельзя — работа уже идёт; но молчать значит
    оставить вкладку с адресом, которого не существует, и узнается это через недели.
    """
    board = tmp_path / "board.jsonl"
    worded = claim(board, tmp_path / "сбои-канал", "сбои", "каналсбоев")
    assert "адресовать поток нельзя" in worded, (
        f"номер словом принят молча — поток остался без адреса: {worded!r}"
    )

    numbered = claim(board, tmp_path / "сбои-второй", "сбои", "2")
    assert "адресовать поток нельзя" not in numbered, (
        f"предупреждение пришло на обычный номер потока: {numbered!r}"
    )


@needs_pwsh
def test_a_finding_for_a_silent_stream_without_a_plan_goes_to_the_owner(tmp_path: Path) -> None:
    """Совет продублировать находку в разделе плана — только там, где план есть.

    Ветка «поток объявлен, но вкладка давно не отмечалась» шла мимо общей проверки, хотя заявка
    получателя в этот момент уже найдена. В проекте без волн она посылала в раздел плана, которого
    не существует, — и находка после такого совета не попадала никуда вовсе.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "автор"
    silent = tmp_path / "молчун"
    claim_bare(board, author)
    claim_bare(board, silent)
    long_ago = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    patch_claim(board, silent, seen_at=long_ago)

    out = run_tool(
        board, "-Mode", "Add", "-To", f"{today_wave()}/2", "-Title", "поправьте договор", cwd=author
    )
    assert "вкладка давно не отмечалась" in out, f"рапорт не про молчащую вкладку: {out!r}"
    assert "Назовите находку в ответе владельцу." in out, (
        f"без плана волны находку послали в раздел плана: {out!r}"
    )
    assert "Хвост" not in out, "в проекте без волн остался совет про раздел плана"

    # Вторая ветка: у волны с планом текст прежний, слово в слово.
    planned = tmp_path / "wave9-автор"
    quiet = tmp_path / "wave9-молчун"
    claim(board, planned, "wave9", "1")
    claim(board, quiet, "wave9", "2")
    patch_claim(board, quiet, seen_at=long_ago)
    planned_out = run_tool(
        board, "-Mode", "Add", "-To", "wave9/2", "-Title", "поправьте договор", cwd=planned
    )
    assert "Продублируйте находку пунктом в «Хвостах волны»." in planned_out, (
        f"у волны с планом переписан прежний совет: {planned_out!r}"
    )


@needs_pwsh
def test_an_old_claim_outside_a_wave_is_told_that_it_has_no_plan(tmp_path: Path) -> None:
    """Заявка прежней версии признака не несёт — судим её по имени волны.

    Такие заявки уже лежат в реестрах, и волна у них бывает названа словом, хотя плана волны за
    ней нет. Прежнее «нет признака — значит план есть» посылало их вписать строку в раздел файла,
    которого не существует, — тупик ровно там, где вкладка заканчивает работу.
    """
    board = tmp_path / "board.jsonl"
    outside = tmp_path / "вне-волны"
    claim(board, outside, "сбои", "1")
    strip_claim_field(board, outside, "wave_auto")

    done = release(board, outside)
    assert done.returncode == 0, done.stderr
    assert "Плана волны нет — строку потока вписывать некуда, итог идёт в ответ владельцу." in said(
        done.stdout
    ), f"заявке без признака и без плана посоветовали раздел плана: {done.stdout!r}"

    # Вторая ветка: у той же заявки старого вида, но с волной ПЛАНА, тексты остаются прежними.
    planned = tmp_path / "wave9-старая"
    claim(board, planned, "wave9", "1")
    strip_claim_field(board, planned, "wave_auto")
    kept = release(board, planned)
    assert kept.returncode == 0, kept.stderr
    assert (
        "Последнее действие — строка своего потока в разделе «Состояние потоков» плана волны."
        in said(kept.stdout)
    ), f"у заявки старого вида с волной плана пропала прежняя строка сдачи: {kept.stdout!r}"


@needs_pwsh
def test_a_wave_with_mixed_claims_is_judged_by_its_first_claim(
    tmp_path: Path, wave_repo: Path
) -> None:
    """Волну заводит тот, кто объявился первым, — по его заявке и решается, есть ли у неё план.

    Прежнее «есть хоть одна подставленная заявка» делало бесплановой целую волну плана, стоило
    одной вкладке объявиться в ней без волны: тексты про разделы плана пропадали у всех её потоков
    разом. Ответ обязан быть одинаковым у всех вкладок и не меняться от прогона к прогону.
    """
    board = tmp_path / "board.jsonl"
    invented = tmp_path / "сама-завела"
    named = tmp_path / "названа-явно"
    claim_bare(board, invented)
    claim(board, named, today_wave(), "2")
    board.write_text(
        board_line(id="lost0003", at=now_minus(3), to=f"{today_wave()}/9", title="некому получить")
        + "\n",
        encoding="utf-8",
    )

    # Первой объявилась вкладка, которая волну выдумала сама, — плана у волны нет.
    patch_claim(board, invented, claimed_at="2026-01-01T10:00:00")
    patch_claim(board, named, claimed_at="2026-01-02T10:00:00")
    first = context_text(run_deliver(board, wave_repo, "Start", "s-mixed-invented"))
    assert "Плана волны нет — назовите находку в ответе владельцу." in said(first), (
        f"волну, заведённую без плана, сочли волной плана: {first!r}"
    )

    # Поменяли местами только время объявления — ответ обязан перевернуться вместе с ним.
    patch_claim(board, invented, claimed_at="2026-01-02T10:00:00")
    patch_claim(board, named, claimed_at="2026-01-01T10:00:00")
    second = context_text(run_deliver(board, wave_repo, "Start", "s-mixed-named"))
    assert "Хвосты волны" in second, (
        f"волна, заведённая названной, объявлена бесплановой из-за соседней заявки: {second!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Ключ вкладки — КОРЕНЬ рабочего дерева, а не та папка, из которой её случилось запустить.


def subfolder_of(tab: Path) -> Path:
    """Подкаталог рабочего дерева — вкладку часто запускают именно из такого."""
    deep = tab / "docs" / "глубоко"
    deep.mkdir(parents=True, exist_ok=True)
    return deep


@needs_pwsh
@needs_git
def test_a_claim_and_its_release_meet_whatever_subfolder_the_tab_started_in(tmp_path: Path) -> None:
    """Объявились из корня дерева, сдаётесь из подкаталога — заявка та же самая.

    Прежде ключом вкладки была текущая папка, и эти две команды расходились ключами: сдача не
    находила своей заявки и отвечала «сдавать нечего» КОДОМ УСПЕХА. Вкладка закрывалась, а соседи
    продолжали адресовать находки живому, как им кажется, потоку — самая дорогая из потерь
    механизма, потому что отправителю при этом рапортуют об успехе.

    Проверяется и обратный ход: объявление из подкаталога тоже обязано лечь в заявку КОРНЯ, иначе в
    реестре появился бы второй файл на то же дерево и поток задвоился бы снаружи.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-вкладки"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)

    claim(board, tab, "wave9", "3", "-StreamName", "Ключ вкладки")
    given = release(board, deep)

    assert given.returncode == 0, f"сдача из подкаталога сорвалась: {given.stderr!r}"
    assert "Поток wave9/3 сдан." in given.stdout, (
        f"сдача из подкаталога не нашла заявки, поданной из корня: {given.stdout!r}"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, (
        f"на одно дерево завелось файлов заявки: {len(records)} — поток снаружи двоится"
    )
    assert records[0].released, "заявка осталась открытой, хотя сдача отрапортовала успехом"
    assert records[0].worktree == folder_key(tab), (
        f"в заявке записан не корень дерева, а {records[0].worktree}: под этим ключом её не найдут "
        "ни сдача, ни соседи"
    )

    # Обратный ход: объявление из подкаталога и сдача из корня. Номер другой — прежний уже занят
    # этой волной, а выдавать занятый номер второй раз нельзя.
    claim(board, deep, "wave9", "7", "-StreamName", "Обратный ход")
    back = release(board, tab)
    assert back.returncode == 0, f"сдача из корня сорвалась: {back.stderr!r}"
    assert "Поток wave9/7 сдан." in back.stdout, (
        f"сдача из корня не нашла заявки, поданной из подкаталога: {back.stdout!r}"
    )
    assert len(read_registry(registry_dir(board))) == 1, (
        "объявление из подкаталога завело вторую заявку на то же дерево"
    )


@needs_pwsh
@needs_git
def test_a_tab_started_in_a_subfolder_is_seen_alive_by_its_neighbours(tmp_path: Path) -> None:
    """Вкладка работает из подкаталога — соседи всё равно видят её живой.

    Маячок живости пишет сторож доставки, а ищут его по путям, которые называет git. Адресуйся
    писатель текущей папкой — при работе из подкаталога они расходятся, живая вкладка выглядит
    брошенной, и находка уходит в «Хвосты волны» мимо человека, который сидит за экраном.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа", "сосед": "бета"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)

    assert run_deliver(board, deep, "Prompt", "с-подкаталога").strip() == "", (
        "сторож доставки заговорил там, где показывать нечего"
    )
    assert (tab / BEACON).exists(), (
        "маячок лёг не в корень дерева — разбор рабочих деревьев ищет его именно там"
    )
    assert not (deep / BEACON).exists(), "маячок остался в подкаталоге, где его никто не читает"

    seen = run_tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "альфа",
        "-Title",
        "живому соседу",
        cwd=tmp_path / "сосед",
        known=True,
    )
    assert "отмечалась недавно" in seen, (
        f"сосед считает вкладку, работающую из подкаталога, брошенной: {seen!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_filed_by_the_older_version_from_a_subfolder_is_still_released(
    tmp_path: Path,
) -> None:
    """Заявку, поданную прежней версией из подкаталога, сдача находит второй дорогой.

    Имя файла заявки выводится из пути, поэтому у таких заявок ключ — подкаталог, а нынешний ключ —
    корень дерева. По имени файла своя заявка не находится, и без второй дороги правка осиротила бы
    ровно те потоки, ради которых делается.

    Имя файла здесь нарочно не каноничное: сдача обязана искать по записанной в заявке рабочей
    папке, а не по имени файла. Это чтение, а не перенос — запись правится на своём месте, ни один
    файл не заводится и не удаляется.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-сироты"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "заявка-прежней-версии",
        **open_claim(str(deep), wave="wave9", stream="4"),
    )

    given = release(board, deep)

    assert given.returncode == 0, f"сдача сорвалась: {given.stderr!r}"
    assert "Поток wave9/4 сдан." in given.stdout, (
        f"сдача не нашла заявку прежней версии и осиротила поток: {given.stdout!r}"
    )
    fields = read_claim_json(orphan)
    assert fields is not None and fields.get("state") == "released", (
        f"сдача записала не в тот файл — заявка прежней версии осталась открытой: {fields!r}"
    )
    assert len(read_registry(registry_dir(board))) == 1, (
        "сдача завела новый файл заявки вместо того, чтобы записать в найденный"
    )


@needs_pwsh
def test_when_git_cannot_name_the_tree_root_claim_and_release_refuse_aloud(tmp_path: Path) -> None:
    """git не отвечает — объявление и сдача отказывают вслух, сторож доставки работает дальше.

    Молчаливый откат на текущую папку сменил бы вкладке ЛИЧНОСТЬ, и удар пришёлся бы в худшее
    место: сдача перестала бы находить свою заявку и вышла бы успехом. Терпимому читателю тот же
    откат безвреден — там пропуск стоит одной невидимой строки, а не потока.

    Сцена: маркер репозитория на месте, а git о нём сказать ничего не может. Это и есть та
    развилка, где откат опасен; там, где репозитория нет вовсе, дерева нет тоже — текущая папка и
    есть единственная личность вкладки, и расходиться ключам не с чем.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    tab.mkdir()
    (tab / ".git").write_text("gitdir: Q:/такого-пути-нет/.git", encoding="utf-8")

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "5", cwd=tab)
    assert denied.returncode != 0, (
        f"объявление прошло с неизвестным ключом вкладки: {denied.stdout!r}"
    )
    assert "корень рабочего дерева не вычислить" in denied.stderr, (
        f"отказ не назвал причины: {denied.stderr!r}"
    )
    assert not list(registry_dir(board).glob("*.json")), (
        "заявка всё-таки легла — под ключом, по которому её потом не найдут"
    )

    refused = release(board, tab)
    assert refused.returncode != 0, f"сдача отрапортовала успехом вслепую: {refused.stdout!r}"
    assert "корень рабочего дерева не вычислить" in refused.stderr, (
        f"сдача не назвала причины отказа: {refused.stderr!r}"
    )

    assert run_deliver(board, tab, "Prompt", "с-молчащим-git").strip() == "", (
        "сторож доставки заговорил там, где показывать нечего"
    )
    assert (tab / BEACON).exists(), (
        "сторож доставки замолчал из-за git — а ему велено работать по текущей папке и молча"
    )


@needs_pwsh
@needs_git
def test_release_closes_the_claim_of_the_very_folder_it_was_run_from(tmp_path: Path) -> None:
    """Встали ровно в папку записи — сдаётся ИМЕННО она, а не запись корня дерева.

    Сцена проверяющего целиком. В реестре две записи: живая, поданная из корня дерева, и призрак,
    чья рабочая папка — подкаталог этого же дерева (так лежат заявки прежней версии). Показ печатает
    папку ИЗ ЗАПИСИ и советует «сдайте лишнюю, встав ровно в её папку» — человек так и делает.

    Пока сдача сперва разрешала папку в КОРЕНЬ дерева, этот единственный напечатанный выход был не
    просто неисполним, а вреден: из папки призрака закрывалась ЖИВАЯ запись, призрак оставался
    открытым и продолжал держать адрес, а человеку рапортовали об успехе. Воспроизведено на живой
    сцене.

    Обратный ход (вкладка из подкаталога, её собственная запись в корне) при этом обязан работать
    как прежде — он проверяется отдельно, там же, где сходятся объявление из корня и сдача из
    подкаталога.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-вкладки"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)

    claim(board, tab, "wave9", "3", "-StreamName", "Живой поток")
    live = claim_of(board, tab, only_open=True)
    live_before = live.file.read_bytes()
    ghost = put_claim(
        registry_dir(board),
        "призрак-из-подкаталога",
        **open_claim(str(deep), wave="wave9", stream="4", name="Призрак", seen_at=now_minus(3)),
    )

    given = release(board, deep)

    assert given.returncode == 0, f"сдача из папки призрака сорвалась: {given.stderr!r}"
    assert "Поток wave9/4 сдан." in given.stdout, (
        "сдача закрыла не ту запись: человек стоял в папке призрака, а сдался поток корня — "
        f"призрак остался держать адрес: {given.stdout!r}"
    )
    assert (read_claim_json(ghost) or {}).get("state") == "released", (
        f"призрак остался открытым: {read_claim_json(ghost)!r}"
    )
    assert live.file.read_bytes() == live_before, (
        "живая запись корня изменилась — её сдали вместо призрака, и вкладка об этом не узнает"
    )

    # ‼️ Сдача обязана назвать, ЧТО закрыла: адрес, имя и рабочую папку. Пока печатался один адрес,
    # подмена записи человеку видна не была вовсе.
    assert "Призрак" in given.stdout, (
        f"сдача не назвала имени закрытой заявки — подмена записи не видна: {given.stdout!r}"
    )
    assert folder_key(deep) in folder_key(given.stdout), (
        f"сдача не назвала рабочей папки закрытой заявки: {given.stdout!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# «Не видно» — это не «нет». Ловушка, отвечающая на вопрос «есть ли здесь репозиторий вообще»,
# знает ТРИ ответа, и третий («выяснить не удалось») в строгом режиме отказ, а не тихий откат.

MARKER_STAND = """param([string]$Lib, [string]$StartDir, [string]$Marker)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. $Lib
if ($StartDir) {
    (Get-RepoMarkerState -StartDir $StartDir).Kind
    exit 0
}
# Ответ ловушки подставляем. Сцену «диск отвалился прямо под ногами работающего процесса» на живой
# машине не собрать: текущую папку у процесса не отнять, а запустить его в несуществующей нельзя.
# Саму ловушку проверяет соседняя проверка, по настоящему мёртвому пути; здесь проверяется
# РАЗВИЛКА, которая на её ответе стоит.
function Get-RepoMarkerState {
    param([string]$StartDir)
    return [pscustomobject]@{ Kind = $Marker; Reason = 'подставлено проверкой' }
}
try { "строгий: $(Get-TreeRoot -Strict)" } catch { "строгий отказал: $($_.Exception.Message)" }
"терпимый: $(Get-TreeRoot)"
"""


def dead_folder_path() -> Path:
    """Папка на диске, которого на этой машине НЕТ, — недостижимый путь целиком.

    Букву подбираем тем же приёмом, что и мёртвая доска: зашитая может оказаться живой, и сцена
    молча поменяла бы смысл.
    """
    return dead_board_path().parent / "вкладка"


def marker_stand(tmp_path: Path) -> Path:
    """Стенд, дот-сорсящий библиотеку: иначе до самой ловушки не добраться."""
    stand = tmp_path / "стенд-маркера.ps1"
    stand.write_text(MARKER_STAND, encoding="utf-8")
    return stand


def ask_marker(stand: Path, start: Path) -> str:
    assert pwsh
    done = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(stand),
            "-Lib",
            str(COORDINATION_DIR / "lib" / "wave-board-lib.ps1"),
            "-StartDir",
            str(start),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def ask_tree_root(stand: Path, cwd: Path, marker: str) -> str:
    """Что ответят на подставленный ответ ловушки строгий и терпимый читатели ключа вкладки."""
    assert pwsh
    done = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(stand),
            "-Lib",
            str(COORDINATION_DIR / "lib" / "wave-board-lib.ps1"),
            "-Marker",
            marker,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@needs_pwsh
@needs_git
def test_the_repo_marker_tells_apart_no_repo_from_cannot_tell(tmp_path: Path) -> None:
    """Три ответа вместо двух: маркер найден, маркера точно нет, выяснить не удалось.

    Прежде ответов было два, и недостижимый путь (отвалился диск, пропала сетевая шара) выдавался
    за «репозитория тут нет». Разница между ними несущая: «нет» разрешает вкладке работать текущей
    папкой, потому что дерева нет и расходиться ключам не с чем, а «не видно» такого разрешения не
    даёт — там дерево может быть, и молчаливый откат сменит вкладке ЛИЧНОСТЬ.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-вкладки"})
    stand = marker_stand(tmp_path)
    plain = tmp_path / "без-репозитория"
    plain.mkdir()

    assert ask_marker(stand, plain) == "none", "обычная папка вне репозитория названа не «нет»"
    assert ask_marker(stand, tmp_path / "вкладка") == "found", (
        "в рабочем дереве маркер репозитория не найден"
    )
    assert ask_marker(stand, dead_folder_path()) == "unknown", (
        "недостижимый путь выдан за «репозитория здесь нет» — а это разрешение вкладке сменить "
        "себе личность молча"
    )


@needs_pwsh
def test_an_unreadable_marker_refuses_aloud_to_the_strict_and_stays_silent_for_the_rest(
    tmp_path: Path,
) -> None:
    """«Выяснить не удалось» — отказ вслух там, где от ключа зависит судьба потока.

    Объявление и сдача читают ключ вкладки строго: их молчаливый откат на текущую папку бьёт в самое
    дорогое место — сдача перестаёт находить свою заявку и выходит УСПЕХОМ. Сторожу и показу тот же
    откат безвреден, там пропуск стоит одной невидимой строки, и им отказывать нельзя.

    Ответ «репозитория тут нет вовсе» при этом обязан остаться тихим для обоих: дерева нет, значит и
    расходиться ключам не с чем, — иначе комплект перестал бы работать всюду, где на месте
    репозитория обычная папка.
    """
    stand = marker_stand(tmp_path)
    plain = tmp_path / "без-репозитория"
    plain.mkdir()

    unknown = ask_tree_root(stand, plain, "unknown")
    assert "строгий отказал" in unknown, (
        f"на ответе «выяснить не удалось» строгий читатель сменил личность вкладки молча: {unknown!r}"
    )
    assert "«не видно» это не «нет»" in unknown and "подставлено проверкой" in unknown, (
        f"отказ не назвал ни сути, ни причины — человеку не с чем идти разбираться: {unknown!r}"
    )
    assert "терпимый: " in unknown, (
        f"терпимый читатель тоже отказал — сторож доставки обязан быть немым: {unknown!r}"
    )

    none = ask_tree_root(stand, plain, "none")
    assert "строгий отказал" not in none and "строгий: " in none, (
        f"там, где репозитория нет вовсе, строгий читатель отказал на ровном месте: {none!r}"
    )


@needs_pwsh
@needs_git
def test_a_worktree_written_in_another_case_is_still_the_same_folder(tmp_path: Path) -> None:
    """Записанная в заявке рабочая папка сравнивается с ключом вкладки БЕЗ учёта регистра.

    ‼️ Честно: против кода до этой правки проверка ЗЕЛЁНАЯ — оболочка сравнивает строки без учёта
    регистра сама, и все пять сравнений это молча наследовали. Заводится она не как доказательство
    починки, а как страж свойства, которое перестало держаться на умолчании оболочки: приведение
    рабочей папки теперь одно на весь комплект и гасит регистр ЯВНО, потому что тем же ключом
    пользуются упорядочение (оно сравнивает строки побайтово) и наборы, где уговор оболочки не
    действует.

    Разойдись ключ вкладки с записанной папкой хоть регистром буквы диска — вкладка сочла бы
    соперником собственную заявку, потеряла бы память прежних имён ветки и не узнала бы себя в
    показе.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-вкладки"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"

    claim(board, tab, "wave9", "3", "-StreamName", "Регистр")

    def spell_loudly() -> None:
        """Переписывает рабочую папку заявки другим регистром — как её мог записать другой источник."""
        record = claim_of(board, tab, only_open=True)
        record.fields["worktree"] = str(record.fields["worktree"]).upper()
        write_claim(record)

    spell_loudly()
    again = claim(board, tab, "wave9", "3", "-StreamName", "Регистр")
    assert "открытая заявка другого дерева" not in again, (
        f"вкладка сочла соперником собственную заявку: {again!r}"
    )
    assert len(read_registry(registry_dir(board))) == 1, (
        "объявление завело вторую заявку — записанную папку не узнали своей"
    )

    spell_loudly()
    listed = run_tool(board, "-Mode", "Streams", cwd=tab)
    assert "это вы" in listed, f"вкладка не узнала себя в показе: {listed!r}"

    spell_loudly()
    given = release(board, tab)
    assert "Поток wave9/3 сдан." in given.stdout, (
        f"сдача не нашла своей заявки из-за регистра: {given.stdout!r} / {given.stderr!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Видимость: рабочая папка в показе, метка «это вы», полный порядок строк, громкая строка про
# задвоенный адрес. Без неё оба будущих отказа («папка занята», «адрес занят») неисполнимы: они
# называют человеку ЧУЖУЮ рабочую папку, а увидеть её ему сегодня негде.


def shown_streams(text: str) -> list[str]:
    """Строки потоков в показе — в том порядке, в каком их напечатали.

    Строка потока начинается с двух пробелов; пояснение к громкой строке — с трёх, и в порядок
    оно не входит.
    """
    return [
        line for line in text.splitlines() if line.startswith("  ") and not line.startswith("   ")
    ]


def stream_line_of(text: str, folder: Path) -> str:
    """Строка показа, принадлежащая ИМЕННО этой рабочей папке.

    Отбирать её вхождением пути нельзя: у перенесённой записи в той же строке названа папка, КУДА
    уехал адрес, — и строка соседа нашлась бы вместе со своей. Поэтому сверяем ровно ту папку,
    которую строка называет своей.
    """
    wanted = folder_key(folder)
    found = [
        line
        for line in shown_streams(text)
        if (named := re.search(r", папка ([^,)]+)", line)) and folder_key(named.group(1)) == wanted
    ]
    assert len(found) == 1, f"строк показа для папки {folder} не одна, а {len(found)}: {text!r}"
    return found[0]


def shown_folders(text: str) -> list[str]:
    """Рабочие папки, названные строками потоков, — в том же порядке."""
    found: list[str] = []
    for line in shown_streams(text):
        folder = re.search(r", папка ([^,)]+)", line)
        if folder:
            found.append(folder_key(folder.group(1)))
    return found


@needs_pwsh
def test_show_keeps_one_order_on_the_same_registry(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Два прогона показа на одном реестре дают ОДИН порядок строк, и порядок этот полный.

    Прежде показ сортировал только по волне и номеру потока. Двух записей одного адреса в
    исправном реестре не бывает — но правку выкатывают на грязный, где такие пары уже лежат как
    наследие дефекта «переезд раздваивает адрес». На них ключ сортировки кончался, и порядок
    решала опись каталога: человек не мог сверить два прогона глазами, а решения по такой паре
    принимает приём находки.

    Сцена собрана так, чтобы опись каталога СПОРИЛА с правильным порядком: файл поздней заявки
    назван раньше по алфавиту, чем файл ранней. Совпади они — проверка молча зеленела бы и на
    несортированном показе.
    """
    registry_invariants.waive(
        "две незакрытых записи одного адреса собраны нарочно: это наследие дефекта, ради которого "
        "порядок и доводится до полного"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    late = tmp_path / "поздняя"
    early = tmp_path / "ранняя"
    put_claim(
        folder,
        "aa-поздняя",
        **open_claim(str(late), wave="wave9", stream="3", claimed_at="2026-09-02T18:00:00"),
    )
    put_claim(
        folder,
        "zz-ранняя",
        **open_claim(str(early), wave="wave9", stream="3", claimed_at="2026-09-01T09:00:00"),
    )
    put_claim(
        folder,
        "mm-другой-номер",
        **open_claim(str(tmp_path / "другой"), wave="wave9", stream="1"),
    )

    first = run_tool(board, "-Mode", "Streams")
    second = run_tool(board, "-Mode", "Streams")

    assert shown_streams(first) == shown_streams(second), (
        "два прогона показа на одном реестре дали разный порядок строк — сверить их глазами нельзя"
    )
    assert shown_folders(first) == [
        folder_key(tmp_path / "другой"),
        folder_key(early),
        folder_key(late),
    ], (
        "показ идёт не по полному ключу (волна, номер, время объявления, путь): порядок двух "
        f"записей одного адреса решает опись каталога — {shown_streams(first)!r}"
    )


@needs_pwsh
@needs_git
def test_show_prints_the_worktree_and_marks_your_own(tmp_path: Path) -> None:
    """Строка потока называет рабочую папку и метит вашу собственную запись.

    Оба отказа называют человеку чужую папку. Пока показ печатал только адрес, имя и ветку, найти
    эту папку было негде — то есть отказ был неисполним: человек не мог ни узнать в ней себя, ни
    посмотреть, жива ли она.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    gone = tmp_path / "исчезнувшая"
    claim(board, tab, "wave9", "3", "-StreamName", "Видимость")
    put_claim(registry_dir(board), "чужая", **open_claim(str(gone), wave="wave9", stream="8"))

    listed = run_tool(board, "-Mode", "Streams", cwd=tab)

    mine = [line for line in shown_streams(listed) if line.startswith("  wave9/3")]
    theirs = [line for line in shown_streams(listed) if line.startswith("  wave9/8")]
    assert len(mine) == 1 and len(theirs) == 1, f"показ назвал не оба потока: {listed!r}"
    assert folder_key(tab) in folder_key(mine[0]), (
        f"своя строка не назвала рабочей папки — отказ по ней неисполним: {mine[0]!r}"
    )
    assert "это вы" in mine[0], (
        f"своя запись не помечена — человек не отличит её от чужой: {mine[0]!r}"
    )
    assert folder_key(gone) in folder_key(theirs[0]), (
        f"чужая строка не назвала рабочей папки: {theirs[0]!r}"
    )
    assert "это вы" not in theirs[0], f"чужая запись помечена вашей: {theirs[0]!r}"
    assert "папки нет" in theirs[0], (
        f"папки записи на диске нет, а улика об этом не напечатана: {theirs[0]!r}"
    )
    assert "папки нет" not in mine[0], f"живая папка объявлена исчезнувшей: {mine[0]!r}"


@needs_pwsh
def test_show_shouts_about_a_doubled_address(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Задвоенный адрес называется вслух отдельной строкой, с обеими рабочими папками.

    Две похожие строки в списке человек пролистает; решения же по такой паре принимает приём
    находки, и принимает их порядком описи каталога. Поэтому о задвоении говорится громко и с
    уликами: обе папки названы, идти разбираться есть куда.
    """
    registry_invariants.waive(
        "задвоенный адрес собран нарочно — проверяется, что показ о нём кричит, а не молчит"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    first = tmp_path / "первая"
    second = tmp_path / "вторая"
    put_claim(folder, "одна", **open_claim(str(first), wave="wave9", stream="3"))
    put_claim(folder, "другая", **open_claim(str(second), wave="wave9", stream="3"))

    listed = run_tool(board, "-Mode", "Streams")

    loud = [line for line in listed.splitlines() if line.startswith("‼️")]
    assert len(loud) == 1, f"показ не сказал о задвоенном адресе вслух: {listed!r}"
    assert "wave9/3" in loud[0], f"громкая строка не назвала задвоенного адреса: {loud[0]!r}"
    assert folder_key(first) in folder_key(loud[0]) and folder_key(second) in folder_key(loud[0]), (
        f"громкая строка назвала не обе рабочие папки — идти разбираться некуда: {loud[0]!r}"
    )

    # ‼️ И главное: громкая строка не должна пропадать при ПУСТОМ отборе по задаче. Считалась она и
    # раньше до отбора, а печаталась после — то есть исчезала ровно в самом опасном ответе. «Задачу
    # никто не взял» читают как разрешение занять кусок, а он в этот момент ведётся двумя записями
    # сразу, и кому придёт находка, решает порядок описи каталога.
    empty = run_tool(board, "-Mode", "Streams", "-Task", "42")
    assert "не объявлена ни одним потоком" in empty, (
        f"сцена собрана неверно — отбор по задаче что-то нашёл: {empty!r}"
    )
    still_loud = [line for line in empty.splitlines() if line.startswith("‼️")]
    assert len(still_loud) == 1 and "wave9/3" in still_loud[0], (
        "в ответе «задачу никто не взял» про задвоенный адрес не сказано ничего — а это и есть "
        f"разрешение занять кусок, который ведут двое: {empty!r}"
    )

    # И обратная сторона: на исправном реестре показ молчит, а не кричит на ровном месте.
    calm = tmp_path / "тихая" / "board.jsonl"
    put_claim(registry_dir(calm), "одна", **open_claim(str(first), wave="wave9", stream="3"))
    quiet = [
        line for line in run_tool(calm, "-Mode", "Streams").splitlines() if line.startswith("‼️")
    ]
    assert not quiet, "показ кричит о задвоении там, где на адрес приходится одна запись"


@needs_pwsh
@needs_git
def test_a_claim_filed_by_the_older_version_from_a_subfolder_still_gets_its_mail(
    tmp_path: Path,
) -> None:
    """Заявку прежней версии из подкаталога находит не только сдача, но и сторож доставки.

    Ключ вкладки теперь — корень рабочего дерева, а такие заявки лежат под ключом подкаталога.
    Сдача ищет их второй дорогой (по записанной в заявке рабочей папке), а сторож доставки читал
    только каноничный ключ — и выходило худшее из возможного: приём находки принимает её с бодрым
    рапортом «поток ведёт вкладка, дойдёт сама», а до вкладки она не доходит. Адрес потока сторож
    берёт ИМЕННО из своей заявки: без неё он не знает, как поток зовут.

    ‼️ Ожидание про неизменность файла тут ПЕРЕВЁРНУТО осознанно. Прежде проверка закрепляла, что
    сторож не трогает найденную второй дорогой заявку ни на байт, — и вместе с этим закрепляла её
    вечное молчание: отметку живости и список тронутых файлов сторож ставил только по каноничному
    ключу, а у такой заявки он другой. Через сутки она попадала в сводку застрявшего у владельца, а
    соседи переставали считать вкладку живой — то есть находка ушла бы в «Хвосты волны» мимо
    человека, который сидит за экраном.

    Безопасно это ровно потому, что вторая дорога ищет по ТОЧНОМУ совпадению рабочей папки: значит
    найденная запись принадлежит этой же вкладке, второго писателя у файла не появляется. Общий
    запрет «не писать в ЧУЖОЙ файл заявки» остаётся в силе, и здесь же проверяется, что сторож
    по-прежнему не ЗАВОДИТ файлов: имя файла остаётся тем же, а второго в реестре не появляется.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-сироты"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "заявка-прежней-версии",
        **open_claim(str(deep), wave="wave9", stream="4", seen_at=now_minus(3)),
    )
    before = read_claim_json(orphan) or {}

    add(board, "wave9/4", "находка по адресу", cwd=tmp_path)
    brought = run_deliver(board, deep, "Start", "сирота-из-подкаталога")

    assert "находка по адресу" in brought, (
        "находка, принятая с рапортом «дойдёт сама», до вкладки не дошла: заявку прежней версии "
        f"сторож доставки не нашёл — {brought!r}"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, (
        "сторож доставки завёл в реестре второй файл — заводить файлы ему нельзя и после правки"
    )
    assert records[0].file == orphan, (
        f"сторож переложил заявку в другой файл ({records[0].file.name}) — ему разрешено только "
        "писать в найденный"
    )
    after = read_claim_json(orphan) or {}
    assert str(after.get("seen_at", "")) > str(before.get("seen_at", "")), (
        "отметка живости не обновилась: заявка прежней версии получает почту, но снаружи выглядит "
        f"молчащей — через сутки уедет в сводку застрявшего ({before=}, {after=})"
    )
    assert after.get("state") == "open" and after.get("wave") == "wave9", (
        f"сторож переписал заявку, а не отметил её: {after!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Правило папки: в одной рабочей папке живёт не больше ОДНОЙ незакрытой заявки.
#
# Заявка на папку одна физически — её имя выводится из пути. Поэтому объявление другого потока из
# занятой папки не «спорит», а СТИРАЕТ прежнюю запись молча, кодом успеха: задачи прежнего потока
# выглядят невзятыми, находки ему не адресуют, а его собственная сдача в конце закрывает уже чужую
# запись. Так 31.08.2026 в соседнем проекте бесследно исчез поток 9 волны 5.
#
# Отсюда и место отказа — ДО записи. Предупреждение ПОСЛЕ здесь бессмысленно: стирать уже нечего.


def tool_source() -> str:
    """Текст самого инструмента — по нему проверяется ОТСУТСТВИЕ ключа, а не только поведение."""
    return TOOL.read_text(encoding="utf-8")


def tool_switches() -> set[str]:
    """Ключи-переключатели инструмента, объявленные в его собственном блоке параметров."""
    source = tool_source()
    start = source.index("\nparam(")
    return set(re.findall(r"\[switch\]\$(\w+)", source[start : source.index("\n)", start)]))


def claim_block() -> str:
    """Текст блока объявления — от его заголовка до заголовка сдачи."""
    source = tool_source()
    start = source.index("\n    'Claim' {")
    return source[start : source.index("\n    'Release' {", start)]


def folder_taken_refusal(
    board: Path, tab: Path, *, address: str, name: str, tasks: str, branch: str, state: str
) -> list[str]:
    """Отказ правила папки целиком — теми же строками, какие обещаны в решении.

    Сверяется ЦЕЛИКОМ, а не вхождением адреса: половина работы отказа — три выхода с уже
    подставленными значениями. Проверь мы только адрес, и отказ мог бы остаться тупиком (вкладке
    сказали «нельзя» и не сказали, что делать), а проверка этого не заметила бы.
    """
    kept = claim_of(board, tab, only_open=True)
    wave = str(kept.fields.get("wave", ""))
    stream = str(kept.fields.get("stream", ""))
    stamp = datetime.fromisoformat(str(kept.fields["claimed_at"])).strftime("%Y-%m-%d %H:%M")
    return [
        f"в этой рабочей папке уже числится другой поток: {wave}/{stream} «{name}», "
        f"задачи {tasks} — {state}, ветка {branch}, заявлен {stamp}.",
        f"Заявка на папку ОДНА: объявление потока {address} стёрло бы её молча — задачи прежнего "
        "потока выглядели бы невзятыми, находки ему не адресовали бы, а его сдача в конце закрыла "
        "бы чужую запись.",
        "Прежний поток закончен — сдайте его прямо здесь: pwsh scripts/wave-board.ps1 -Mode Release",
        "Это он и есть, объявляетесь заново — назовите его адрес: pwsh scripts/wave-board.ps1 "
        f"-Mode Claim -Wave {wave} -Stream {stream}",
        "Работа новая — заведите отдельное рабочее дерево и объявитесь из него.",
    ]


@needs_pwsh
@needs_git
def test_another_address_from_a_taken_folder_is_refused_before_a_single_byte_is_written(
    tmp_path: Path,
) -> None:
    """Другой поток из занятой папки — отказ, и прежняя заявка цела ПОБАЙТОВО.

    Побайтово — потому что «поток не потерялся» и «файл переписан теми же полями» снаружи выглядят
    одинаково, а стоят разного: переписанная заявка теряет время объявления (то есть старшинство в
    споре за номер) и память прежних имён ветки, по которым до потока ещё идут находки.

    Отказ обязан быть исполнимым: он называет прежний поток целиком (адрес, имя, задачи, состояние,
    ветку и время объявления) и печатает ТРИ выхода готовыми строками. Иначе вкладка упрётся в
    ненулевой код на своём первом действии, сочтёт инструмент сломанным и пойдёт работать без
    заявки — а невидимая снаружи вкладка хуже любого из дефектов, которые правило чинит.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-занятой-папки"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "8", "-StreamName", "Занявший", "-Tasks", "10-13")
    kept = claim_of(board, tab, only_open=True)
    before = kept.file.read_bytes()
    expected = folder_taken_refusal(
        board,
        tab,
        address="wave9/3",
        name="Занявший",
        tasks="10-13",
        branch="ветка-занятой-папки",
        state="ведёт",
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab)

    assert denied.returncode != 0, (
        f"объявление другого потока из занятой папки прошло: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, (
        "файл прежней заявки тронут — а вместе с ним ушли старшинство потока и память имён ветки, "
        "по которым до него ещё идут находки"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"строка отказа переписана или пропала: {line!r} — {denied.stderr!r}"
        )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, f"в реестре завелась вторая запись: {names_of(records)}"
    assert records[0].address == "wave9/8" and not records[0].released, (
        f"прежний поток исчез из реестра или закрылся сам: {records[0].fields!r}"
    )


@needs_pwsh
@needs_git
def test_a_different_wave_with_the_same_stream_number_is_refused_before_a_single_byte_is_written(
    tmp_path: Path,
) -> None:
    """Тот же НОМЕР потока, но из ДРУГОЙ волны, — тоже отказ, и прежняя заявка цела побайтово.

    Соседняя проверка выше берёт грань «та же волна, другой номер». Эта — зеркальная: номер тот же
    самый, волна другая. Адрес потока — пара (волна, номер) ЦЕЛИКОМ, и не совпасть может любая из
    двух половин порознь. Сверяй правило только номер, забыв волну, — оно решило бы, что объявление
    волны B с чужим номером волны A это то же самое объявление заново, и стёрло бы заявку A молча:
    тот же дефект 31.08.2026 (см. соседнюю проверку), только с другой стороны пары.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-занятой-папки-чужой-волны"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "8", "-StreamName", "Занявший", "-Tasks", "10-13")
    kept = claim_of(board, tab, only_open=True)
    before = kept.file.read_bytes()
    expected = folder_taken_refusal(
        board,
        tab,
        address="wave10/8",
        name="Занявший",
        tasks="10-13",
        branch="ветка-занятой-папки-чужой-волны",
        state="ведёт",
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave10", "-Stream", "8", cwd=tab)

    assert denied.returncode != 0, (
        f"объявление другой волны с тем же номером потока из занятой папки прошло: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, (
        "файл прежней заявки тронут — а вместе с ним ушли старшинство потока и память имён ветки, "
        "по которым до него ещё идут находки"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"строка отказа переписана или пропала: {line!r} — {denied.stderr!r}"
        )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, f"в реестре завелась вторая запись: {names_of(records)}"
    assert records[0].address == "wave9/8" and not records[0].released, (
        f"прежний поток исчез из реестра или закрылся сам: {records[0].fields!r}"
    )


@needs_pwsh
@needs_git
def test_the_same_claim_goes_through_once_the_previous_stream_is_released(tmp_path: Path) -> None:
    """Сдали прежний поток — то же объявление проходит. Это и есть первый выход из отказа.

    Без этой половины правило папки было бы тупиком: отказ советует сдать прежний поток прямо
    здесь, и если после сдачи объявление всё равно не проходит, совет — обман. Сданная заявка
    помехой не считается: вкладки, которая вела тот поток, больше нет, и следующий поток в той же
    папке — обычный порядок работы.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-по-кругу"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "8", "-StreamName", "Прежний", "-Tasks", "10-13")
    assert (
        tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab).returncode != 0
    ), "проверка собрана неверно: объявление другого потока из занятой папки не отказало"

    assert release(board, tab).returncode == 0, "сдача прежнего потока не прошла"
    passed = claim(board, tab, "wave9", "3", "-StreamName", "Следующий")

    assert "Поток wave9/3 объявлен за этой вкладкой" in passed, (
        f"после сдачи прежнего объявление всё равно не прошло — выход из отказа обманывает: {passed!r}"
    )
    assert claim_of(board, tab, only_open=True).address == "wave9/3", (
        "новый поток не встал на место сданного"
    )


@needs_pwsh
@needs_git
def test_no_key_at_all_lets_a_claim_overwrite_the_stream_that_holds_the_folder(
    tmp_path: Path,
) -> None:
    """Ключа принудительного затирания в инструменте нет ВОВСЕ — и завестись он не должен.

    У соседа такой ключ есть, и он перегружен вторым, безобидным смыслом (сдача с непустым ящиком),
    который инструмент сам советует, — отсюда привычка нажимать его не глядя. Разрушителен он при
    этом без нужды: сдача прежнего потока лежит в той же самой папке, ничего не теряет и даёт тот
    же исход. Поэтому вырезан весь класс, а не случай.

    Проверяется именно ОТСУТСТВИЕ, четырьмя способами сразу: ключей-переключателей у инструмента
    ровно четыре известных (новый обход пришлось бы объявлять пятым), в блоке объявления не
    поминается ключ принуждения (второй смысл ему не приделать незаметно), отказ правила папки не
    печатает ни одного ключа — иначе вкладка воспользовалась бы единственным напечатанным выходом,
    — и ключ ПЕРЕНОСА адреса правило папки тоже не обходит.

    ‼️ Последнее и есть настоящая защита сегодня. Ключ переноса — единственный ключ, добавленный к
    объявлению после того, как класс обходов вырезали, и соблазн приделать ему второй смысл
    («заодно затри, что тут лежало») ровно тот же, из-за которого у соседа поток исчез молча.
    Перенос забирает АДРЕС у другой папки и в чужой файл не пишет ни байта; заявку СВОЕЙ папки он
    не трогает и правило папки не отменяет.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-без-обхода"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "8", "-StreamName", "Держит папку", "-Tasks", "10-13")
    kept = claim_of(board, tab, only_open=True)
    before = kept.file.read_bytes()

    forced = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", "-Force", cwd=tab)

    assert forced.returncode != 0, (
        f"ключ сдачи с непустым ящиком сработал как обход правила папки: {forced.stdout!r}"
    )
    assert kept.file.read_bytes() == before, "заявка прежнего потока затёрта ключом"
    assert tool_switches() == {"AllowUnknownStream", "ForAll", "Force", "TakeOver"}, (
        f"у инструмента завёлся новый ключ-переключатель: {sorted(tool_switches())} — если это "
        "обход правила папки, поток снова начнёт исчезать молча"
    )
    assert "$Force" not in claim_block(), (
        "блок объявления снова поминает ключ принуждения — второй смысл делает его ключом, "
        "который нажимают не глядя"
    )
    offered = [line for line in said(forced.stderr) if "-Force" in line]
    assert not offered, f"отказ предлагает ключ вместо трёх безобидных выходов: {offered!r}"

    taken = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", "-TakeOver", cwd=tab)

    assert taken.returncode != 0, (
        f"ключ переноса сработал как обход правила папки: {taken.stdout!r}"
    )
    assert "в этой рабочей папке уже числится другой поток" in taken.stderr, (
        f"отказал не тот сторож — правило папки обошли: {taken.stderr!r}"
    )
    assert kept.file.read_bytes() == before, "заявка прежнего потока затёрта ключом переноса"


@needs_pwsh
@needs_git
def test_the_registry_lock_is_free_the_moment_the_folder_rule_refuses(tmp_path: Path) -> None:
    """После отказа замок реестра отпущен: соседняя вкладка объявляется сразу, не ожидая.

    Отказ идёт из-под замка, взятого на выбор номера. Уйди инструмент, не отпустив его, и каждая
    соседняя вкладка платила бы за чужой отказ полминуты ожидания на своём первом действии — а
    после ожидания объявлялась бы БЕЗ замка, то есть с номером, который мог совпасть с соседним.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-отказа", "сосед": "ветка-соседа"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    mate = tmp_path / "сосед"
    claim(board, tab, "wave9", "8", "-StreamName", "Занявший", "-Tasks", "10-13")
    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab)
    assert denied.returncode != 0, "проверка собрана неверно: отказа не было"

    started = time.monotonic()
    neighbour = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "4", cwd=mate)
    elapsed = time.monotonic() - started

    assert neighbour.returncode == 0, f"соседняя вкладка не объявилась: {neighbour.stderr!r}"
    assert "Жду освобождения замка" not in neighbour.stdout, (
        f"сосед ждал замок, брошенный отказом: {neighbour.stdout!r}"
    )
    assert "Замок реестра заявок не отдали" not in neighbour.stdout, (
        "сосед не дождался замка и выбрал номер без него — тот самый случай, когда два потока "
        f"получают один адрес: {neighbour.stdout!r}"
    )
    assert elapsed < 15, (
        f"объявление соседа заняло {elapsed:.1f} с при пределе ожидания замка 30 с — похоже, "
        "замок после отказа остался взятым"
    )


@needs_pwsh
@needs_git
def test_a_claim_from_a_subfolder_of_a_neighbours_tree_never_erases_their_stream(
    tmp_path: Path,
) -> None:
    """Объявление из подкаталога ЧУЖОГО дерева не стирает заявку соседа.

    Ключ вкладки теперь — корень рабочего дерева, поэтому объявление из любой папки соседского
    дерева попадает В ТОТ ЖЕ ключ, что и заявка соседа, и переписало бы её молча, кодом успеха.
    Прежде такое объявление заводило отдельный файл и никому не вредило — то есть без правила папки
    правка ключа сама создаёт дефект, который чинит.

    Случай не выдуманный: все рабочие деревья проекта лежат в одной папке, от любого до любого
    ровно один переход, а правила прямо велят вкладке сходить и посмотреть, чей это кусок работы.
    """
    real_worktrees(tmp_path, {"сосед": "ветка-соседа", "вкладка": "своя-ветка"})
    board = tmp_path / "board.jsonl"
    mate = tmp_path / "сосед"
    claim(board, mate, "wave9", "5", "-StreamName", "Сосед", "-Tasks", "20-22")
    kept = claim_of(board, mate, only_open=True)
    before = kept.file.read_bytes()

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "6", cwd=subfolder_of(mate))

    assert denied.returncode != 0, (
        f"объявление из подкаталога чужого дерева прошло — заявка соседа стёрта: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, "заявка соседа переписана поверх"
    assert "wave9/5 «Сосед», задачи 20-22" in denied.stderr, (
        f"отказ не назвал потока, который держит эту папку: {denied.stderr!r}"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1 and records[0].address == "wave9/5", (
        f"реестр изменился, хотя объявление отказано: {names_of(records)}"
    )


@needs_pwsh
@needs_git
def test_a_claim_of_the_older_version_from_a_subfolder_is_written_back_in_place(
    tmp_path: Path,
) -> None:
    """Заявка прежней версии из подкаталога плюс объявление тем же адресом дают ОДНУ запись.

    Имя файла заявки выводится из пути, и у таких заявок ключ — подкаталог, а нынешний каноничный
    ключ — корень дерева. Без второй дороги объявление заводило рядом ВТОРУЮ открытую запись того
    же потока: инструмент печатал вкладке предупреждение, что она спорит сама с собой, и всё равно
    писал вторую запись кодом успеха. Дальше кому придёт находка, решала опись каталога — это и
    есть тот самый дефект, ради которого затевалась вся правка.

    Вторая дорога — та же, какой уже пользуются сдача и сторож доставки: точное совпадение
    записанной в заявке рабочей папки. Пишем обратно в НАЙДЕННЫЙ файл: ни один файл не заводится,
    не удаляется и не переименовывается.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-сироты"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "заявка-прежней-версии",
        **open_claim(str(deep), wave="wave9", stream="4"),
    )

    out = claim(board, deep, "wave9", "4", "-StreamName", "Тот же поток")

    records = read_registry(registry_dir(board))
    assert len(records) == 1, (
        f"объявление завело вторую запись того же потока: {names_of(records)} — кому придёт "
        "находка, решает опись каталога"
    )
    assert records[0].file == orphan, (
        f"запись легла в новый файл вместо найденного: {records[0].file.name}"
    )
    assert records[0].fields.get("name") == "Тот же поток", (
        f"объявление не дошло до найденной заявки: {records[0].fields!r}"
    )
    assert "открытая заявка другого дерева" not in out, (
        f"вкладке сказали, что она спорит сама с собой: {out!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_of_the_older_version_from_a_subfolder_holds_the_folder_too(tmp_path: Path) -> None:
    """Заявка прежней версии из подкаталога — своя, и правило папки её видит.

    Иначе вторая дорога сама стала бы дырой: не признай правило такую заявку заявкой ЭТОЙ папки, и
    объявление другим адресом записало бы новый поток прямо в её файл — то есть стёрло бы прежний
    поток тем же способом, только тише.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-сироты"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "заявка-прежней-версии",
        **open_claim(str(deep), wave="wave9", stream="4"),
    )
    before = orphan.read_bytes()

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "9", cwd=deep)

    assert denied.returncode != 0, (
        f"другой поток записан поверх заявки прежней версии: {denied.stdout!r}"
    )
    assert orphan.read_bytes() == before, "заявка прежней версии переписана поверх"
    assert "wave9/4" in denied.stderr, f"отказ не назвал держащего папку потока: {denied.stderr!r}"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Короткое повторное объявление: вкладка объявляется заново из СВОЕЙ ЖЕ папки, волну и номер не
# называя.
#
# В лестнице подстановки волны не было ступени «своя прежняя заявка», поэтому поток уезжал в волну
# по сегодняшней дате и терял вместе с адресом имя, задачи, путь плана, признак «есть ли план» и
# старшинство в споре за номер. Соседи при этом продолжали адресовать находки по прежнему адресу, а
# его в реестре уже не было. С появлением правила папки случай стал ЗАМЕТНЫМ (объявление получало
# отказ), но правильным не стал: вкладка упиралась в отказ там, где должна просто продолжать свой
# поток.
#
# Проверяется запись ЦЕЛИКОМ, а не вхождением строки. Половина потерянного (время объявления,
# признак «есть ли план», путь плана) снаружи ничем не видна, и сверка по кускам пропустила бы
# ровно её — а на признаке «есть ли план» висит вся развилка «Хвосты волны или ответ владельцу».


def claim_stamp(fields: dict[str, object]) -> str:
    """Время объявления заявки — тем же видом, каким его печатает инструмент."""
    return datetime.fromisoformat(str(fields["claimed_at"])).strftime("%Y-%m-%d %H:%M")


def reclaimed_the_same_stream(before: dict[str, object], after: dict[str, object]) -> None:
    """Запись после короткого переобъявления обязана совпасть с прежней ЦЕЛИКОМ.

    Кроме отметки живости: ею и говорится «вкладка на ходу», обновляться ей положено. Всё
    остальное — адрес, имя, задачи, путь плана, признак «есть ли план», время объявления, память
    имён ветки, рабочая папка, состояние — принадлежит ПОТОКУ, а не вызову, и переобъявление его
    не трогает.
    """
    was = {key: value for key, value in before.items() if key != "seen_at"}
    now = {key: value for key, value in after.items() if key != "seen_at"}
    assert now == was, (
        "короткое переобъявление изменило запись потока — значит, поток потерял часть себя:\n"
        f"  было:  {was}\n"
        f"  стало: {now}"
    )


@needs_pwsh
def test_a_short_reclaim_continues_a_stream_of_a_named_wave(tmp_path: Path) -> None:
    """Волну назвали в первом объявлении — короткое переобъявление продолжает ТОТ ЖЕ поток.

    Случай самый дорогой из трёх: у волны из плана номера потоков объявлены В ПЛАНЕ, и поток,
    уехавший в волну по сегодняшней дате, становится невидим соседям ровно под тем адресом,
    которым его зовут в плане. Находки уходят в пустоту с бодрым рапортом отправителю.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "3", "-StreamName", "Личность потока", "-Tasks", "10-13")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert before["wave_auto"] is False, (
        f"проверка собрана неверно: у названной волны признак «есть ли план» не тот: {before!r}"
    )

    out = claim_bare(board, tab)

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        "Волна не названа — унаследована от вашей прежней записи. Продолжаете поток wave9/3 "
        f"«Личность потока», заявленный {claim_stamp(before)}." in said(out)
    ), f"четвёртый источник волны не назван или его строка переписана: {out!r}"
    assert (
        "Работа другая — сдайте прежний поток здесь же (-Mode Release) и объявитесь заново."
        in said(out)
    ), f"выход из унаследованного адреса не назван: {out!r}"
    assert "выдан следующий свободный" not in out, (
        f"унаследованный номер выдан за свежевыданный — вкладке соврали о её адресе: {out!r}"
    )

    # Признак «есть ли план» проверяется и по делу: на нём висит вся развилка сдачи.
    done = release(board, tab)
    assert done.returncode == 0, done.stderr
    assert (
        "Последнее действие — строка своего потока в разделе «Состояние потоков» плана волны."
        in said(done.stdout)
    ), f"после переобъявления поток волны из плана остался без плана: {done.stdout!r}"


@needs_pwsh
def test_a_short_reclaim_continues_a_stream_whose_wave_came_from_the_plan_name(
    tmp_path: Path,
) -> None:
    """Волна взята из имени плана — короткое переобъявление и её, и путь плана сохраняет.

    Путь плана в заявке лежит отдельным полем, и потерять его тише всего: снаружи он не виден
    вовсе, а нужен там, где вкладке говорят, в какой раздел плана вписывать итог.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    tab.mkdir()
    plan = tab / "2026-08-24-wave9.md"
    plan.write_text("# план волны 9\n", encoding="utf-8")
    claim_bare(board, tab, "-Plan", str(plan), "-StreamName", "Из имени плана", "-Tasks", "1-4")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert before["plan"] == str(plan) and before["wave_auto"] is False, (
        f"проверка собрана неверно: волна взята не из имени плана: {before!r}"
    )

    out = claim_bare(board, tab)

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        "Волна не названа — унаследована от вашей прежней записи. Продолжаете поток wave9/1 "
        f"«Из имени плана», заявленный {claim_stamp(before)}." in said(out)
    ), f"четвёртый источник волны не назван или его строка переписана: {out!r}"

    done = release(board, tab)
    assert done.returncode == 0, done.stderr
    assert (
        "Последнее действие — строка своего потока в разделе «Состояние потоков» плана волны."
        in said(done.stdout)
    ), f"после переобъявления поток потерял план, взятый из имени файла: {done.stdout!r}"


@needs_pwsh
def test_a_short_reclaim_continues_a_stream_of_a_wave_the_tool_invented(tmp_path: Path) -> None:
    """Волну подставил сам инструмент — переобъявление не заводит вторую и не теряет старшинства.

    Здесь адрес не менялся и до правки (номер этой папки инструмент возвращал прежний), поэтому
    потеря была самой незаметной: уезжали имя, задачи и ВРЕМЯ ОБЪЯВЛЕНИЯ — то есть старшинство в
    споре за номер. Вкладка, объявившаяся заново, начинала уступать номер соседям, которые пришли
    в волну ПОЗЖЕ неё.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim_bare(board, tab, "-StreamName", "Без плана", "-Tasks", "5-7")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert before["wave_auto"] is True, (
        f"проверка собрана неверно: волна не подставлена самим инструментом: {before!r}"
    )

    out = claim_bare(board, tab)

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        "Волна не названа — унаследована от вашей прежней записи. Продолжаете поток "
        f"{today_wave()}/1 «Без плана», заявленный {claim_stamp(before)}." in said(out)
    ), f"четвёртый источник волны не назван или его строка переписана: {out!r}"

    done = release(board, tab)
    assert done.returncode == 0, done.stderr
    assert "Плана волны нет — строку потока вписывать некуда, итог идёт в ответ владельцу." in said(
        done.stdout
    ), f"после переобъявления бесплановый поток обзавёлся планом: {done.stdout!r}"


@needs_pwsh
def test_a_short_reclaim_of_an_old_claim_does_not_invent_the_flag_it_never_had(
    tmp_path: Path,
) -> None:
    """Признака «волна подставлена сама» у заявки прежней версии нет — и заводить его нельзя.

    Все читатели судят такую заявку по имени волны: `waveN` — волна плана, слово или дата — своя.
    Выдумай переобъявление ей значение, и волна, названная СЛОВОМ, вдруг стала бы «подставленной
    самой» — а к такой сосед вправе присоединиться и занять в ней номер, объявленный в плане.
    Половина находок волны ушла бы не туда, молча.

    Поэтому наследуется не вычисленное значение, а поле как оно лежало — вместе с его отсутствием.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "sprint-alpha", "1", "-StreamName", "Заявка прежней версии")
    strip_claim_field(board, tab, "wave_auto")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert "wave_auto" not in before, "проверка собрана неверно: признак из заявки не убран"

    claim_bare(board, tab)

    after = dict(claim_of(board, tab, only_open=True).fields)
    reclaimed_the_same_stream(before, after)
    assert "wave_auto" not in after, (
        f"переобъявление завело признак, которого у заявки не было: {after!r}"
    )

    # Проверка по делу: право соседа присоединиться к этой волне появиться не должно.
    newcomer = claim_bare(board, tmp_path / "сосед")
    assert "sprint-alpha" not in newcomer, (
        f"сосед присоединился к волне, названной словом, и занял в ней номер: {newcomer!r}"
    )


@needs_pwsh
@needs_git
def test_a_short_reclaim_keeps_the_names_the_stream_is_remembered_by(tmp_path: Path) -> None:
    """Переобъявились коротко — память прежних имён ветки при потоке осталась.

    Наследование имён привязано к АДРЕСУ, а короткое объявление адреса не называет. Значит эти две
    правки держат друг друга: разъедься они — вкладка, объявившаяся заново, получила бы новый адрес
    и вместе с ним потеряла бы имена, по которым до неё ещё идут находки. Потеря молчаливая:
    приём такую находку принимает (дерево на месте) и обещает автору доставку, а доставки нет.

    Деревья настоящие: имя ветки берётся у git, и на подставных папках его нет вовсе.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа", "сосед": "сосед-ветка"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    neighbour = tmp_path / "сосед"
    claim(board, tab, "wave6", "1", "-StreamName", "Тот же поток")
    rename_branch(tab, "бета")

    claim_bare(board, tab)

    record = claim_of(board, tab, only_open=True)
    assert record.address == "wave6/1", (
        f"короткое переобъявление увело поток на другой адрес: {record.fields!r}"
    )
    assert record.fields["former_branches"] == ["альфа"], (
        f"поток забыл имя, под которым его знали до переименования ветки: {record.fields!r}"
    )

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "альфа",
        "-Title",
        "по прежнему имени ветки",
        cwd=neighbour,
        known=True,
    )
    assert offered.returncode == 0, (
        f"приём не узнал поток по его прежнему имени ветки: {offered.stderr!r}"
    )

    brought = run_deliver(board, tab, "Start", "короткое-переобъявление")
    assert "по прежнему имени ветки" in brought, (
        f"находка по прежнему имени не доставлена — она не дойдёт никогда: {brought!r}"
    )


@needs_pwsh
@needs_git
def test_a_new_stream_over_a_released_one_does_not_inherit_its_names(tmp_path: Path) -> None:
    """Поток сдан, из той же папки объявился следующий — имена сданного ему не достаются.

    Прежде перенос прежних имён ветки шёл по одному лишь совпадению ПАПКИ. Папку же переиспользуют
    постоянно: поток сдали, в той же папке объявили следующий — и новый начинал отзываться на имя
    сданного и получать адресованные тому находки. Автор, пославший находку по имени ветки
    сданного, читал «учтено» от потока, которого он не называл, а сам поток так и не увидел её.

    Имя ветки — способ адресовать находку ПОТОКУ, поэтому наследуется оно по адресу (волна и
    номер), а не по папке. Что при совпавшем адресе наследование осталось прежним, стережёт
    соседняя проверка про короткое переобъявление.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа", "сосед": "сосед-ветка"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    neighbour = tmp_path / "сосед"
    claim(board, tab, "wave6", "1")
    # Ветку переименовали и объявились заново тем же адресом — прежнее имя поток теперь ПОМНИТ.
    rename_branch(tab, "бета")
    claim(board, tab, "wave6", "1")
    assert claim_of(board, tab, only_open=True).fields["former_branches"] == ["альфа"], (
        "проверка собрана неверно: поток не запомнил прежнего имени своей ветки"
    )

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "альфа",
        "-Title",
        "сданному по прежнему имени",
        cwd=neighbour,
        known=True,
    )
    assert offered.returncode == 0, f"находка по прежнему имени не принята: {offered.stderr!r}"

    given = release(board, tab, "-Force")
    assert given.returncode == 0, f"сдача потока не прошла: {given.stdout!r} {given.stderr!r}"

    claim(board, tab, "wave6", "2", "-StreamName", "Следующий")
    record = claim_of(board, tab, only_open=True)
    assert record.fields["former_branches"] == [], (
        f"новый поток забрал себе имена сданного: {record.fields!r}"
    )

    # Положительная сверка В ТОМ ЖЕ окружении: своё новому потоку приходит. Без неё молчание
    # проверки ниже было бы неотличимо от умершего сторожа доставки — и она зеленела бы при любом
    # коде.
    mine = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "wave6/2",
        "-Title",
        "новому потоку по адресу",
        cwd=neighbour,
        known=True,
    )
    assert mine.returncode == 0, f"находка новому потоку не принята: {mine.stderr!r}"

    brought = run_deliver(board, tab, "Start", "переиспользование-папки")
    assert "новому потоку по адресу" in brought, (
        f"сторож доставки молчит вовсе — проверка ниже была бы зелёной при любом коде: {brought!r}"
    )
    assert "сданному по прежнему имени" not in brought, (
        f"находка сданного потока пришла тому, кто просто сел в его папку: {brought!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Личность потока: кто именно продолжается, а кто просто сел в ту же папку.
#
# Проверки ниже закрывают четыре находки независимой проверки этой правки. Общее у них одно: «тот
# же поток» опознавался по признакам, которые ПОТОКУ не принадлежат — по папке, по совпавшему
# номеру, по молчанию про волну, — и личность утекала то к новому жильцу папки, то к сданному
# соседу, то терялась вовсе.


@needs_pwsh
@needs_git
def test_a_stream_claimed_after_a_release_gets_a_free_number_and_none_of_the_names(
    tmp_path: Path,
) -> None:
    """Поток честно сдан, из той же папки объявляется следующий — номер СВОБОДНЫЙ, память чужая.

    Память имён ветки переходила новому жильцу папки сама собой, без единого ключа, и корень тут
    глубже наследования: подбор номера возвращал номер СВОЕЙ прежней заявки этой папки, не глядя на
    её состояние. Сданная тоже считалась своей, поэтому следующий поток в переиспользованной папке
    получал тот же адрес — а вместе с адресом ему доставалась и память имён сданного, и его почта.
    Инструмент при этом печатает при сдаче обещание, что находки сданному больше не примут.

    Решение говорит про этот случай прямо: правило папки не действует, объявление обычное, номер
    СВОБОДНЫЙ, память имён НЕ наследуется — она переходит только между заявками одного адреса.

    Деревья настоящие: имена потока берутся у git, на подставных папках их не появляется вовсе.
    """
    real_worktrees(tmp_path, {"вкладка": "альфа", "сосед": "сосед-ветка"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    neighbour = tmp_path / "сосед"
    claim(board, tab, "wave6", "1", "-StreamName", "Сдающийся")
    # Ветку переименовали и объявились тем же адресом — прежнее имя поток теперь ПОМНИТ.
    rename_branch(tab, "бета")
    claim(board, tab, "wave6", "1")
    assert claim_of(board, tab, only_open=True).fields["former_branches"] == ["альфа"], (
        "проверка собрана неверно: поток не запомнил прежнего имени своей ветки"
    )
    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "альфа",
        "-Title",
        "сданному по прежнему имени",
        cwd=neighbour,
        known=True,
    )
    assert offered.returncode == 0, f"находка по прежнему имени не принята: {offered.stderr!r}"
    assert release(board, tab, "-Force").returncode == 0, "сдача потока не прошла"

    claim_bare(board, tab, "-Wave", "wave6", "-StreamName", "Следующий")

    record = claim_of(board, tab, only_open=True)
    assert record.address == "wave6/2", (
        f"следующий поток той же папки получил номер сданного: {record.fields!r} — соседи "
        "продолжат слать по этому адресу находки, которых ждал не он"
    )
    assert record.fields["former_branches"] == [], (
        f"новый поток забрал себе имена сданного: {record.fields!r}"
    )

    # Положительная сверка В ТОМ ЖЕ окружении: своё новому потоку приходит. Без неё молчание
    # проверки ниже было бы неотличимо от умершего сторожа доставки.
    mine = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        record.address,
        "-Title",
        "новому потоку по адресу",
        cwd=neighbour,
        known=True,
    )
    assert mine.returncode == 0, f"находка новому потоку не принята: {mine.stderr!r}"

    brought = run_deliver(board, tab, "Start", "после-сдачи")
    assert "новому потоку по адресу" in brought, (
        f"сторож доставки молчит вовсе — проверка ниже была бы зелёной при любом коде: {brought!r}"
    )
    assert "сданному по прежнему имени" not in brought, (
        f"находка сданного потока пришла тому, кто просто сел в его папку: {brought!r}"
    )


@needs_pwsh
def test_a_reclaim_that_names_only_the_wave_keeps_the_rest_of_the_stream(tmp_path: Path) -> None:
    """Назвали волну, номер не назвали — поток продолжается ЦЕЛИКОМ, а не наполовину.

    Вся ступень наследования висела на условии «волну не назвали». Вкладка же вправе объявиться
    заново, назвав свою же волну словом (или подав путь плана) и не называя номера: адрес от этого
    не менялся — номер по-прежнему подбирался её собственный. А вот имя, задачи и путь плана
    стирались, время объявления сбрасывалось, и инструмент вдобавок печатал неправду, будто номер
    выдан следующим свободным.

    Продолжение потока не бывает частичным: неназванные поля берутся у своей незакрытой заявки того
    же АДРЕСА, откуда бы ни взялась волна. Сверяется запись целиком — половина потерянного (время
    объявления, путь плана) снаружи ничем не видна.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    tab.mkdir()
    plan = tab / "2026-08-24-wave9.md"
    plan.write_text("# план волны 9\n", encoding="utf-8")
    claim(
        board,
        tab,
        "wave9",
        "3",
        "-StreamName",
        "Личность потока",
        "-Tasks",
        "10-13",
        "-Plan",
        str(plan),
    )
    before = dict(claim_of(board, tab, only_open=True).fields)

    out = claim_bare(board, tab, "-Wave", "wave9")

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        f"Продолжаете поток wave9/3 «Личность потока», заявленный {claim_stamp(before)}."
        in said(out)
    ), f"вкладке не сказали, что она продолжает свой поток, или строка переписана: {out!r}"
    assert (
        "Работа другая — сдайте прежний поток здесь же (-Mode Release) и объявитесь заново."
        in said(out)
    ), f"выход из унаследованного адреса не назван: {out!r}"
    assert "выдан следующий свободный" not in out, (
        f"унаследованный номер выдан за свежевыданный — вкладке соврали о её адресе: {out!r}"
    )


@needs_pwsh
def test_a_reclaim_that_names_only_the_wave_keeps_its_seniority(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """И старшинство при этом цело: круг уступки не отдаёт адрес соседу, пришедшему позже.

    Из всего, что терялось при частичном переобъявлении, время объявления опаснее прочего: им и
    только им меряется старшинство в споре за номер. Сбрось его — и вкладка уступает адрес всякому,
    кто объявился между её двумя объявлениями, то есть молча уезжает на номер, о котором соседи не
    знают, а по прежнему её адресу находки принимает кто-то другой.
    """
    registry_invariants.waive(
        "инвариант «одна ведущая запись на адрес»: соперник на тот же номер собран руками, и "
        "проверяется как раз то, что вкладка адреса НЕ отдаёт. Правило адреса эту пару не "
        "разводит намеренно: номер здесь УНАСЛЕДОВАН у своей же прежней записи, то есть остаётся "
        "выданным, а на выданный номер правило адреса не распространяется — там номер можно "
        "двигать, и спор разрешает круг уступки"
    )
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "3", "-StreamName", "Старший", "-Tasks", "10-13")
    # Поток идёт со вчера. Иначе проверять нечего: заявка, заведённая секунду назад, останется
    # старшей даже со сброшенным временем — сброс сдвигает его на эту самую секунду.
    patch_claim(
        board,
        tab,
        claimed_at=(datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),
    )
    put_claim(
        registry_dir(board),
        "сосед-пришедший-позже",
        **open_claim(
            str(tmp_path / "сосед"),
            wave="wave9",
            stream="3",
            claimed_at=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        ),
    )

    out = claim_bare(board, tab, "-Wave", "wave9")

    assert claim_of(board, tab, only_open=True).address == "wave9/3", (
        "вкладка уступила свой адрес соседу, объявившемуся ПОЗЖЕ неё — значит переобъявление "
        f"сбросило время объявления, а с ним и старшинство: {out!r}"
    )
    assert "сдвинут на следующий свободный" not in out, (
        f"поток сдвинут с собственного адреса при живом старшинстве: {out!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_never_writes_over_the_live_record_of_a_dirty_folder(tmp_path: Path) -> None:
    """В папке лежат сданная и открытая записи — объявление не смеет переписать ЖИВУЮ.

    Выборов «прежней заявки этой папки» было два, и они расходились: правило папки брало ПЕРВУЮ по
    порядку описи запись (сданная в счёт не идёт, значит отказ не поднимался), а запись файла шла в
    ту, что нашлась по каноничному ключу, — то есть в живую. Объявление другого потока проходило
    кодом успеха и стирало работающий: ровно тот инцидент, ради которого затевалась вся правка,
    только заходящий с другой стороны.

    Такая папка не выдумана: заявку прежней версии из подкаталога объявление пишет на месте, поэтому
    её файл остаётся лежать под старым именем рядом с новым.
    """
    real_worktrees(tmp_path, {"вкладка": "ветка-грязной-папки"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim(board, tab, "wave9", "8", "-StreamName", "Живой", "-Tasks", "10-13")
    live = claim_of(board, tab, only_open=True)
    before = live.file.read_bytes()
    # Сданная запись ТОЙ ЖЕ папки под своим именем файла. Номер у неё меньше — значит в описи
    # реестра она идёт первой, и прежний выбор «первая по порядку» доставался именно ей.
    put_claim(
        registry_dir(board),
        "сданная-прежней-версии",
        **open_claim(
            str(tab),
            wave="wave9",
            stream="1",
            state="released",
            claimed_at=str(live.fields["claimed_at"]),
            released_at=str(live.fields["claimed_at"]),
        ),
    )
    expected = folder_taken_refusal(
        board,
        tab,
        address="wave9/3",
        name="Живой",
        tasks="10-13",
        branch="ветка-грязной-папки",
        state="ведёт",
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab)

    assert denied.returncode != 0, (
        f"объявление другого потока прошло поверх живой записи папки: {denied.stdout!r}"
    )
    assert live.file.read_bytes() == before, (
        "живой поток переписан молча — правило папки смотрело на сданную запись, а писали поверх "
        "работающей"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"отказ назвал не тот поток или строка переписана: {line!r} — {denied.stderr!r}"
        )
    records = read_registry(registry_dir(board))
    assert len(records) == 2, f"реестр изменился, хотя объявление отказано: {names_of(records)}"


@needs_pwsh
def test_the_continued_stream_is_named_by_the_address_it_really_had(tmp_path: Path) -> None:
    """«Продолжаете поток …» называет адрес, который у потока БЫЛ, а не выданный только что.

    Строка печатается после круга уступки. Старший сосед держит номер, вкладку сдвигают на
    следующий свободный — и вкладке сообщали, что она продолжает поток по адресу, которого у неё не
    было никогда, да ещё и «заявленный вчера». Следующей строкой инструмент честно говорит про
    сдвиг, но первой строке верят раньше, чем дочитывают до второй, а адрес из неё вкладка называет
    соседям.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "вкладка"
    claim_bare(board, tab, "-StreamName", "Сдвинутый", "-Tasks", "5-7")
    mine = claim_of(board, tab, only_open=True)
    before = dict(mine.fields)
    earlier = datetime.fromisoformat(str(mine.fields["claimed_at"])) - timedelta(hours=1)
    put_claim(
        registry_dir(board),
        "старший-сосед",
        **open_claim(
            str(tmp_path / "сосед"),
            wave=today_wave(),
            stream="1",
            wave_auto=True,
            claimed_at=earlier.isoformat(timespec="seconds"),
        ),
    )

    out = claim_bare(board, tab)

    assert claim_of(board, tab, only_open=True).address == f"{today_wave()}/2", (
        f"проверка собрана неверно: круг уступки поток не сдвинул — {out!r}"
    )
    assert (
        "Волна не названа — унаследована от вашей прежней записи. Продолжаете поток "
        f"{today_wave()}/1 «Сдвинутый», заявленный {claim_stamp(before)} — номер уступлен соседу, "
        "новый адрес назван ниже." in said(out)
    ), f"строка про продолжение потока называет чужой адрес или переписана: {out!r}"
    assert f"Продолжаете поток {today_wave()}/2" not in out, (
        f"вкладке назвали продолжаемым адрес, которого у её потока не было: {out!r}"
    )
    assert (
        f"‼️ Номером {today_wave()}/1 в тот же миг объявилась соседняя вкладка — ваш поток сдвинут "
        f"на следующий свободный: {today_wave()}/2." in said(out)
    ), f"про сам сдвиг вкладке не сказали: {out!r}"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# ДЕФЕКТ 1: переезд раздваивает адрес.
#
# Вкладка объявилась из общей папки, завела рабочее дерево и объявилась оттуда тем же адресом — в
# реестре стало две открытые записи одного адреса, и кому придёт находка, решал порядок описи
# каталога. Лечится двумя правками разом, и порядок между ними жёсткий:
#   • правило адреса — отказ ДО записи, с уликами и одним явным ключом переноса;
#   • единый признак «запись закрыта» — иначе проигравшая вкладка продолжает получать почту нового
#     владельца и гасить её у него.
#
# ‼️ Переезд записывается полем В СВОЕЙ заявке, а не правкой чужого файла: у чужой заявки есть
# второй писатель (сторож доставки той папки), он правит её целиком на каждом ходу и замка не
# берёт. Этим же куплена совместимость со старой копией комплекта в двух десятках живых деревьев.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def address_taken_refusal(
    board: Path, rival: Path, *, address: str, state: str, disk: str, distinct: str
) -> list[str]:
    """Отказ правила адреса целиком — теми же строками, какие обещаны в решении.

    Сверяется ЦЕЛИКОМ, а не вхождением адреса: половина работы отказа — улики (чужая папка, её
    состояние, есть ли она на диске) и три выхода с уже подставленными значениями. Порядок выходов
    тоже часть обещания: первым БЕЗОБИДНЫЙ, разрушительный — последним, потому что вкладка
    пользуется первым напечатанным.
    """
    fields = claim_of(board, rival, only_open=False).fields
    stamp = datetime.fromisoformat(str(fields["seen_at"])).strftime("%Y-%m-%d %H:%M")
    where = str(fields["worktree"])
    wave, stream = address.split("/")
    return [
        f"адрес {address} уже ведёт незакрытая заявка ДРУГОЙ рабочей папки: {where} — {state}, "
        f"отметка {stamp}, {disk}.",
        "Ведущая запись на адрес ОДНА: объявись вы второй, кому придёт находка, решал бы порядок "
        "описи каталога — половина адресованного пропала бы с бодрым рапортом об успехе.",
        "Другая нарезка той же волны — объявитесь своим номером: pwsh scripts/wave-board.ps1 "
        f"-Mode Claim -Wave {wave} -Stream {distinct}",
        f"Тот поток закончен — сдайте его, встав ровно в его папку {where}: "
        "pwsh scripts/wave-board.ps1 -Mode Release",
        "Это ваш поток, вы переехали сюда (или перехватываете брошенную вкладку) — заберите адрес "
        f"себе, у папки {where} он будет отнят: pwsh scripts/wave-board.ps1 -Mode Claim "
        f"-Wave {wave} -Stream {stream} -TakeOver",
    ]


@needs_pwsh
def test_a_move_with_a_named_address_is_refused_before_a_single_byte_is_written(
    tmp_path: Path,
) -> None:
    """Переезд без ключа переноса — отказ ДО записи, и прежняя заявка не тронута побайтово.

    Это основная форма дефекта 1: вкладка объявилась из общей папки, завела рабочее дерево и
    объявляется оттуда тем же явно названным адресом. Сегодня в реестре становилось две открытые
    записи одного адреса — отказ ставится там, где раздвоение ещё не случилось.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    tree.mkdir()
    claim(board, common, "wave9", "3", "-StreamName", "Переезд", "-Tasks", "10-13")
    kept = claim_of(board, common, only_open=True)
    before = kept.file.read_bytes()

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tree)

    assert denied.returncode != 0, f"переезд прошёл молча — адрес ведут двое: {denied.stdout!r}"
    assert kept.file.read_bytes() == before, (
        "файл прежней заявки тронут — а на «ни байта в чужой файл» стоит вся совместимость со "
        "старой копией комплекта в живых рабочих деревьях"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, f"в реестре завелась вторая запись: {names_of(records)}"
    expected = address_taken_refusal(
        board, common, address="wave9/3", state="ведёт", disk="папка на месте", distinct="3k"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"строка отказа переписана или пропала: {line!r} — {denied.stderr!r}"
        )
    # ‼️ Порядок выходов: безобидный ПЕРВЫМ, разрушительный ПОСЛЕДНИМ. Вкладка пользуется первым
    # напечатанным, и перепутай мы их местами — отказ сам подталкивал бы отнимать чужой адрес.
    order = [said_lines.index(line) for line in expected[2:]]
    assert order == sorted(order), (
        f"выходы напечатаны не в том порядке — разрушительный оказался не последним: {said_lines!r}"
    )


@needs_pwsh
def test_the_refusal_names_a_folder_that_is_gone_and_frees_the_registry_lock(
    tmp_path: Path,
) -> None:
    """Перехват брошенной вкладки: улика говорит прямо «папки на диске больше нет».

    Исход перехвата и переезда одинаков, значит и механизм один. Разница только в громкости улик —
    и её достаточно, чтобы человек решил за секунду.

    Вторая половина: после отказа замок реестра отпущен. Уйди инструмент, не отпустив его, и каждая
    соседняя вкладка платила бы за чужой отказ полминуты ожидания на своём первом действии.
    """
    board = tmp_path / "board.jsonl"
    gone = tmp_path / "исчезнувшая"
    tree = tmp_path / "дерево"
    tree.mkdir()
    put_claim(
        registry_dir(board),
        "брошенная",
        **open_claim(str(gone), wave="wave9", stream="3", seen_at=now_minus(3)),
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tree)

    assert denied.returncode != 0, f"адрес брошенной вкладки занят молча: {denied.stdout!r}"
    expected = address_taken_refusal(
        board,
        gone,
        address="wave9/3",
        state="молчит",
        disk="папки на диске больше нет",
        distinct="3k",
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"улика про исчезнувшую папку переписана или пропала: {line!r} — {denied.stderr!r}"
        )

    started = time.monotonic()
    neighbour = claim(board, tmp_path / "сосед", "wave9", "8")
    assert time.monotonic() - started < 20, (
        "соседняя вкладка ждала замок, оставшийся от чужого отказа"
    )
    assert "Замок реестра заявок не отдали" not in neighbour, (
        f"замок после отказа не отпущен — сосед объявился без него: {neighbour!r}"
    )


@needs_pwsh
@needs_git
def test_the_take_over_key_moves_the_address_the_inbox_and_the_branch_names(
    tmp_path: Path,
) -> None:
    """Ключ переноса: ведущая запись адреса одна, ящик и имена ветки переходят в новую папку.

    Ящик — это работа, а не бухгалтерия: перенос адреса без переноса ящика был бы той же потерей,
    только с другой стороны. Имена ветки — тоже: находка, УЖЕ ПОСЛАННАЯ по прежнему имени потока,
    обязана дойти до папки, куда поток переехал.
    """
    real_worktrees(tmp_path, {"общая": "ветка-до-переезда", "дерево": "ветка-после-переезда"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд", "-Tasks", "10-13")
    mark = add(board, "wave9/3", "находка на адрес")
    by_branch = add(board, "ветка-до-переезда", "находка по прежнему имени ветки")
    lost = str(claim_of(board, common, only_open=False).fields["worktree"])

    moved = claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")

    assert address_of(board, tree) == "wave9/3", "перенос адреса не состоялся"
    assert f"Адрес wave9/3 забран у папки {lost}" in moved, (
        f"о переносе адреса не сказано вслух: {moved!r}"
    )
    # Громкое предупреждение: соперник отмечался только что, то есть похоже, что он работает.
    assert (
        "‼️ Та вкладка отмечалась только что — похоже, она работает. Адрес отнят у работающего "
        "соседа: убедитесь, что это ваш переезд, а не спор двух живых вкладок." in said(moved)
    ), f"адрес отняли у работающего соседа молча: {moved!r}"

    won = str(claim_of(board, tree, only_open=True).fields["worktree"])
    listed = run_tool(board, "-Mode", "Streams")
    ghost = [line for line in shown_streams(listed) if folder_key(lost) in folder_key(line)]
    assert len(ghost) == 1 and f"перенесён в {won}" in ghost[0], (
        f"прежняя запись не показана перенесённой и не названо, куда уехал адрес: {listed!r}"
    )
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"показ кричит о задвоении там, где адрес перенесён явным ключом: {listed!r}"
    )

    arrived = bullets(run_deliver(board, tree, "Start", "новый"))
    assert any(mark in line for line in arrived), (
        f"находка с перенесённого адреса не дошла до новой папки: {arrived!r}"
    )
    assert any(by_branch in line for line in arrived), (
        f"имя ветки прежнего потока не отзывается на новую заявку: {arrived!r}"
    )


@needs_pwsh
@needs_git
def test_the_losing_tab_is_disarmed_the_moment_its_address_moves(tmp_path: Path) -> None:
    """Проигравшая вкладка обезврежена целиком — это и есть единый признак «запись закрыта».

    Ровно этого не хватало исходному проекту: погашение жило только в разобранном реестре, а
    доставка, сдача, закрытие находки и обе отметки живости читали состояние из СВОЕГО файла
    напрямую. Значит проигравшая продолжала бы получать почту нового владельца и гасить её у него —
    та же беда, что чинилась у соседа, только в зеркале.
    """
    real_worktrees(tmp_path, {"общая": "ветка-проигравшей", "дерево": "ветка-победившей"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    mark = add(board, "wave9/3", "находка нового владельца")
    loser = claim_of(board, common, only_open=False)
    before = loser.file.read_bytes()
    # Папку называем ТАК ЖЕ, как её записала заявка: инструмент печатает её из записи, а не
    # собирает из системного пути, и сверка целыми строками этого не прощает.
    won = str(claim_of(board, tree, only_open=True).fields["worktree"])

    # 1. Сторож доставки не носит ей находок и не обновляет ей отметку живости.
    #
    # ‼️ Утверждение сужено осознанно: прежде оно требовало от сторожа ПОЛНОГО молчания, и это
    # закрепляло ровно тот изъян, из-за которого проигравшая сторона уходила в тишину. Теперь
    # сторож обязан не носить ей НАХОДОК — и обязан сказать, почему он их больше не носит.
    walked = run_deliver(board, common, "Start", "проигравшая")
    assert not bullets(walked), (
        f"проигравшая вкладка получает почту нового владельца адреса: {bullets(walked)!r}"
    )
    assert mark not in context_text(walked), (
        f"находка нового владельца дошла до проигравшей вкладки: {context_text(walked)!r}"
    )
    assert f"‼️ Ваш поток wave9/3 забран в {won}" in context_text(walked), (
        f"вкладка обезврежена молча — почему замолчал сторож, ей не сказали: {walked!r}"
    )
    assert loser.file.read_bytes() == before, (
        "сторож обновил отметку живости перенесённой записи — любая новая сессия в старой папке "
        "воскрешала бы призрака первым же ходом"
    )

    # 2. Её сдача говорит «перенесён», а не «уже сдан».
    given = release(board, common)
    assert given.returncode == 0, given.stderr
    assert said(given.stdout) == [
        f"Поток wave9/3 перенесён в {won} — сдавать здесь нечего: адрес ведёт та вкладка.",
        "Эта вкладка больше не адресуема: находки по адресу приходят туда, и закрывать их отсюда "
        "нельзя.",
        "Это ваш поток и переносили его по ошибке — верните адрес себе: "
        "pwsh scripts/wave-board.ps1 -Mode Claim -Wave wave9 -Stream 3 -TakeOver",
    ], f"ответ сдачи переписан или выдаёт перенос за сдачу: {given.stdout!r}"
    assert loser.file.read_bytes() == before, "сдача закрыла перенесённую запись как свою"

    # 3. Её попытка закрыть находку нового владельца отклоняется.
    closing = tool(board, "-Mode", "Done", "-Id", mark, cwd=common)
    assert closing.returncode != 0, (
        f"проигравшая погасила находку нового владельца — закрытие именного адреса ОБЩЕЕ, и он не "
        f"увидел бы её ни в доставке, ни на доске: {closing.stdout!r}"
    )
    assert "адресована не вам" in closing.stderr, f"отказал не тот сторож: {closing.stderr!r}"
    assert any(mark in line for line in bullets(run_deliver(board, tree, "Start", "владелец"))), (
        "находка нового владельца погашена чужой рукой"
    )


@needs_pwsh
def test_the_succession_survives_a_short_reclaim_of_the_new_owner(tmp_path: Path) -> None:
    """Поле преемства наследуется при коротком переобъявлении — призрак не воскресает.

    Иначе первое же переобъявление нового владельца стирало бы пометку, и погашенная запись
    возвращалась бы ведущей вместе со своим адресом и своей почтой.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд", "-Tasks", "10-13")
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    taken = claim_of(board, tree, only_open=True)
    assert taken.taken_from == folder_key(common), (
        "перенос не записан в СВОЮ заявку — гасить призрака нечем"
    )

    out = claim_bare(board, tree)

    assert claim_of(board, tree, only_open=True).taken_from == folder_key(common), (
        f"короткое переобъявление стёрло поле преемства — призрак воскрес: {out!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"после переобъявления адрес снова задвоился: {listed!r}"
    )


@needs_pwsh
def test_a_claim_of_the_older_version_without_the_succession_field_is_not_a_corruption(
    tmp_path: Path,
) -> None:
    """Заявка прежней версии поля преемства не несёт — правила на ней работают, порчей она не считается.

    Механизм живёт в пяти копиях без синхронизации, и разновозрастные заявки — норма, а не
    исключение. Отсутствие поля читается как «переноса не было».
    """
    board = tmp_path / "board.jsonl"
    old = tmp_path / "прежняя-версия"
    tree = tmp_path / "дерево"
    old.mkdir()
    tree.mkdir()
    put_claim(
        registry_dir(board),
        "заявка-прежней-версии",
        wave="wave9",
        stream="3",
        name="Прежняя версия",
        worktree=str(old),
        state="open",
        seen_at=now_minus(0.1),
    )
    assert TAKEN_FROM_FIELD not in claim_of(board, old, only_open=True).fields, (
        "проверка собрана неверно: у заявки прежней версии оказалось поле преемства"
    )

    # Правило адреса на ней действует: отказ, а не молчаливое раздвоение.
    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tree)
    assert denied.returncode != 0, (
        f"заявку прежней версии не сочли соперником — адрес ведут двое: {denied.stdout!r}"
    )
    # И правило папки: другой поток из ЕЁ папки не проходит.
    other = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "8", cwd=old)
    assert other.returncode != 0, (
        f"заявка прежней версии не удержала свою папку — поток стёрся бы молча: {other.stdout!r}"
    )
    # И ключ переноса на ней работает — иначе выход из отказа был бы обманом.
    taken = claim(board, tree, "wave9", "3", "-TakeOver")
    assert address_of(board, tree) == "wave9/3", f"ключ переноса адрес не отдал: {taken!r}"
    assert claim_of(board, tree, only_open=True).taken_from == folder_key(old), (
        "перенос у заявки прежней версии не записан"
    )


@needs_pwsh
def test_a_finding_for_a_released_stream_is_refused_even_after_the_address_moved(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Регресс дефекта 1 с другой стороны: настоящий поток честно сдан — приём ОТКАЗЫВАЕТ.

    Это самое дорогое следствие дефекта: брошенная запись держала адрес живым, находка на него
    принималась с бодрым рапортом об успехе, отправитель успокаивался и не заводил ей запасного
    пункта — а она не доставалась никому.
    """
    registry_invariants.waive(
        "инвариант «у адреса есть ведущая запись»: адрес здесь заканчивается НАМЕРЕННО — поток "
        "переехал и в новой папке честно сдался, а брошенная запись прежней папки осталась "
        "открытой. Ровно это и проверяется: ведущей записи у адреса нет, и находку по нему не "
        "принимают"
    )
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    assert release(board, tree).returncode == 0, "сдача настоящего потока не прошла"

    denied = tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "поздняя находка", cwd=common)

    assert denied.returncode != 0, (
        f"находка на сданный адрес принята — брошенная запись держит его живым: {denied.stdout!r}"
    )
    assert "поток «wave9/3» СДАН" in denied.stderr, (
        f"отказ говорит не про сдачу — значит адрес держит призрак: {denied.stderr!r}"
    )


def older_copy(root: Path) -> Path:
    """Комплект из main ДО этой работы — ровно та копия, что стоит сегодня в живых деревьях.

    Копий около двадцати, синхронизации между ними нет, и со старым кодом встречаются ОБЕ стороны
    переноса: победившая — у соседа, проигравшая — у себя же. Поэтому копию заводят две проверки, и
    заводит её один помощник.
    """
    old = root / "старая-копия"
    if old.is_dir():
        return old
    before_the_series = "1649e4ff"
    known = subprocess.run(
        ["git", "cat-file", "-e", f"{before_the_series}^{{commit}}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if known.returncode != 0:
        pytest.skip("истории до этой работы нет (поверхностный клон) — старую копию взять неоткуда")
    inside = ".claude/skills/parallel-streams/coordination"
    # Все файлы, из которых старая копия состоит: библиотека подключает ещё два своих, и без них
    # она сорвалась бы на первой строке — то есть проверка молча мерила бы не то.
    for name in (
        "wave-board.ps1",
        "lib/wave-board-lib.ps1",
        "lib/git-env-clean.ps1",
        "lib/hook-io.ps1",
        "hooks/wave-board-deliver.ps1",
    ):
        shown = subprocess.run(
            ["git", "show", f"{before_the_series}:{inside}/{name}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert shown.returncode == 0, shown.stderr
        target = old / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(shown.stdout, encoding="utf-8")
    return old


def older_deliver(
    old: Path, board: Path, cwd: Path, session: str
) -> subprocess.CompletedProcess[str]:
    """Ход СТАРОГО сторожа доставки из названной рабочей папки — как у живого соседа сегодня."""
    assert pwsh
    return subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(old / "hooks" / "wave-board-deliver.ps1"),
            "-Stage",
            "Prompt",
            "-BoardPath",
            str(board),
        ],
        input=json.dumps({"session_id": session}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        timeout=60,
    )


@needs_pwsh
@needs_git
def test_the_older_copy_of_the_tool_reads_the_new_registry_as_it_did_yesterday(
    tmp_path: Path,
) -> None:
    """Старая копия комплекта из соседнего дерева не ломается и ничего не портит.

    Их сейчас около двадцати, и синхронизации между ними нет. Старая копия видит незнакомое поле
    преемства и молча его игнорирует; проигравшую запись читает как открытую — то есть ровно так
    же, как читала вчера. Ни новых поломок, ни воскрешения.

    ‼️ Главное здесь — что старая копия ПЕРЕЖИВАЕТ поле, а не стирает его. Её отметка живости
    переписывает файл заявки целиком, и потеряй она при этом поле преемства, погашенная запись
    воскресла бы у всех сразу, без единого предупреждения.
    """
    old = older_copy(tmp_path)
    real_worktrees(tmp_path, {"общая": "ветка-проигравшей", "дерево": "ветка-победившей"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    winner = claim_of(board, tree, only_open=True)
    loser_file = claim_of(board, common, only_open=False).file
    loser_before = loser_file.read_bytes()

    assert pwsh
    listed = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(old / "wave-board.ps1"),
            "-Mode",
            "Streams",
            "-BoardPath",
            str(board),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tree),
        timeout=60,
    )
    assert listed.returncode == 0, f"старая копия сломалась на новом реестре: {listed.stderr!r}"
    assert "Заявок на потоки: 2" in listed.stdout, (
        f"старая копия читает новый реестр иначе, чем читала вчера: {listed.stdout!r}"
    )

    # Её сторож доставки переписывает заявку ЭТОЙ папки целиком — поле обязано уцелеть.
    walked = older_deliver(old, board, tree, "старая")
    assert walked.returncode == 0, f"старый сторож доставки сорвался: {walked.stderr!r}"
    after = read_claim_json(winner.file)
    assert after is not None and folder_key(after.get(TAKEN_FROM_FIELD)) == folder_key(common), (
        f"старая копия стёрла поле преемства — погашенная запись воскресла бы у всех: {after!r}"
    )
    assert loser_file.read_bytes() == loser_before, "старая копия тронула файл проигравшей записи"


@needs_pwsh
@needs_git
def test_the_older_copy_in_the_losing_folder_still_carries_away_the_new_owners_mail(
    tmp_path: Path,
) -> None:
    """Вторая сторона переноса: старая копия стоит у ПРОИГРАВШЕЙ — реестр цел, но почту ей носят.

    ‼️ Ограничение названо вслух и из своей папки НЕ ЧИНИТСЯ ничем: код, которым проигравшая
    вкладка делает ход, лежит в ЕЁ рабочем дереве, и он старый — про перенос он не знает и знать
    не может, пока правка не доедет до её копии (а едет она днями). Поэтому проверка закрепляет
    ровно то, что нам обещано и что мы держим: реестр остаётся цел, поле преемства уцелевает,
    погашенная запись не воскресает. А находку нового владельца старый сторож ей всё же приносит и
    велит закрыть — это и есть цена разновозрастных копий, названная вслух.
    """
    old = older_copy(tmp_path)
    real_worktrees(tmp_path, {"общая": "ветка-проигравшей", "дерево": "ветка-победившей"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    mark = add(board, "wave9/3", "находка нового владельца")
    winner = claim_of(board, tree, only_open=True)

    walked = older_deliver(old, board, common, "проигравшая-со-старой-копией")

    assert walked.returncode == 0, f"старый сторож сорвался в папке проигравшей: {walked.stderr!r}"
    assert mark in walked.stdout, (
        "сцена собрана неверно: старый сторож не принёс проигравшей ничего — тогда и ограничение "
        f"называть не о чем: {walked.stdout!r}"
    )
    after = read_claim_json(winner.file)
    assert after is not None and folder_key(after.get(TAKEN_FROM_FIELD)) == folder_key(common), (
        f"ход старой копии в папке проигравшей стёр поле преемства у нового владельца: {after!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"после хода старой копии в папке проигравшей погашенная запись воскресла: {listed!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Извещение проигравшей вкладки и сдача сироты по адресу.
#
# Обе правки закрывают одну дыру с двух сторон. Вкладка, у которой адрес забрали, обязана УЗНАТЬ
# об этом: иначе она работает вслепую по адресу, которого у неё нет, и объявляет соседям и
# владельцу, что ведёт поток. А запись, от которой не осталось даже рабочей папки, обязана
# поддаваться сдаче: иначе она держит адрес живым навсегда и принимает находки, которые не
# достанутся никому.
#
# ‼️ Сдача по адресу — ЕДИНСТВЕННАЯ операция комплекта, пишущая в чужой файл заявки, и условие ей
# выбрано не по молчанию, а по отсутствию писателя: в несуществующей папке сторож доставки
# запуститься не может. Молчание доказывает ровно одно — вкладка не делала ходов.
# ─────────────────────────────────────────────────────────────────────────────────────────────


@needs_pwsh
def test_the_losing_tab_learns_on_its_next_turn_that_its_address_was_taken(
    tmp_path: Path,
) -> None:
    """Проигравшая вкладка узнаёт о переносе на своём же ходу — отдельной строкой от сторожа.

    Без неё проигравшая сторона уходит в тишину МОЛЧА: находок ей больше не носят, сдача отвечает
    «перенесён», попытка закрыть находку отклоняется — а почему, вкладка не знает. Её заявка на
    диске выглядит открытой (чужой файл при переносе не трогают ни байтом), и по своему файлу она
    о переносе не узнает никогда.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")

    before = context_text(run_deliver(board, common, "Prompt", "проигравшая"))
    assert "забран" not in before, f"о переносе сказано до самого переноса: {before!r}"

    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    won = str(claim_of(board, tree, only_open=True).fields["worktree"])

    after = said(context_text(run_deliver(board, common, "Prompt", "проигравшая")))
    assert (
        f"‼️ Ваш поток wave9/3 забран в {won} — эта вкладка больше не адресуема: находки по "
        "адресу приходят туда, и закрывать их отсюда нельзя."
    ) in after, f"проигравшая вкладка ушла в тишину молча: {after!r}"
    # Выход печатается готовой строкой с подставленными значениями: перенос обратим, файл
    # проигравшей не тронут, и вернуть адрес она вправе тем же ключом.
    assert (
        "Это ваш поток и переносили его по ошибке — верните адрес себе: "
        "pwsh scripts/wave-board.ps1 -Mode Claim -Wave wave9 -Stream 3 -TakeOver"
    ) in after, f"выход из положения не назван готовой строкой: {after!r}"


@needs_pwsh
def test_release_by_address_closes_an_orphan_whose_folder_is_gone(tmp_path: Path) -> None:
    """Сдача по адресу снимает запись, чьей рабочей папки на диске нет, и оставляет след.

    Это единственная операция комплекта, пишущая в ЧУЖОЙ файл заявки. Разрешена она потому, что
    писателя у того файла не существует: сторож доставки в несуществующей папке запуститься не
    может. След «кто и когда сдал» обязателен — без него сдача посторонним неотличима от честной
    сдачи самой вкладкой.
    """
    board = tmp_path / "board.jsonl"
    gone = tmp_path / "исчезнувшая"
    tab = tmp_path / "вкладка"
    tab.mkdir()
    put_claim(
        registry_dir(board),
        "сирота",
        **open_claim(str(gone), wave="wave9", stream="3", name="Сирота", seen_at=now_minus(5)),
    )

    given = release(board, tab, "-Wave", "wave9", "-Stream", "3")

    assert given.returncode == 0, given.stderr
    closed = claim_of(board, gone, only_open=False)
    assert closed.released, f"запись сироты не сдана: {closed.fields!r}"
    assert folder_key(closed.fields.get(RELEASED_FROM_FIELD)) == folder_key(tab), (
        f"следа «кто сдал» в записи нет — сдача посторонним неотличима от честной: {closed.fields!r}"
    )
    assert closed.fields.get("released_at"), f"следа «когда сдали» в записи нет: {closed.fields!r}"
    assert (
        f"Запись wave9/3 сдана по адресу: рабочей папки {gone} на диске нет, писателя у её "
        "заявки не существует."
    ) in said(given.stdout), f"о сделанном не сказано вслух: {given.stdout!r}"

    # Адрес больше не держится призраком: находка на него отказом, а не бодрым рапортом об успехе.
    denied = tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "поздняя находка", cwd=tab)
    assert denied.returncode != 0, (
        f"сданная по адресу запись всё ещё держит адрес живым: {denied.stdout!r}"
    )
    assert "поток «wave9/3» СДАН" in denied.stderr, (
        f"отказ говорит не про сдачу — значит адрес держит призрак: {denied.stderr!r}"
    )


@needs_pwsh
def test_release_by_address_refuses_while_the_folder_is_still_on_disk(tmp_path: Path) -> None:
    """Папка на месте — отказ, даже когда запись молчит пятые сутки. Ключа обхода нет.

    Молчание доказывает ровно одно: вкладка не делала ходов. Прогон субагентов, ожидание сборки и
    ночная пауза выглядят так же. А там, где папка есть, у заявки есть и второй писатель — сторож
    доставки той вкладки: он правит документ целиком на каждом ходу и замка не берёт, поэтому наша
    пометка о сдаче стёрлась бы его ближайшей отметкой живости уже ПОСЛЕ рапорта об успехе.
    """
    board = tmp_path / "board.jsonl"
    silent = tmp_path / "молчащая"
    tab = tmp_path / "вкладка"
    silent.mkdir()
    tab.mkdir()
    kept = put_claim(
        registry_dir(board),
        "молчит-пятые-сутки",
        **open_claim(str(silent), wave="wave9", stream="3", name="Молчит", seen_at=now_minus(5)),
    )
    before = kept.read_bytes()

    denied = release(board, tab, "-Wave", "wave9", "-Stream", "3")

    assert denied.returncode != 0, f"чужую живую папку сдали по молчанию: {denied.stdout!r}"
    assert kept.read_bytes() == before, "файл чужой заявки тронут при отказе"
    said_lines = said(denied.stderr)
    assert (
        f"рабочая папка потока wave9/3 на месте: {silent} — идите в неё и сдайте поток оттуда: "
        "pwsh scripts/wave-board.ps1 -Mode Release"
    ) in said_lines, f"отказ не назвал папку и выполнимый выход: {denied.stderr!r}"
    # ‼️ Ключа обхода здесь нет вовсе: напечатай отказ хоть один ключ — вкладка воспользовалась бы
    # единственным напечатанным выходом, и запись живого соседа исчезла бы кодом успеха.
    offered = [line for line in said_lines if "-Force" in line or "-TakeOver" in line]
    assert not offered, f"отказ предлагает ключ обхода: {offered!r}"


@needs_pwsh
def test_release_by_address_tells_an_unreachable_path_from_a_missing_folder(
    tmp_path: Path,
) -> None:
    """Недостижимый путь — это «неизвестно», а не «папки нет», и выдавать одно за другое нельзя.

    Отвалившийся диск и пропавшая сетевая шара отвечают тем же отказом, что и удалённая папка.
    Прими механизм одно за другое — и он записал бы в чужую заявку по адресу, где вкладка в этот
    самый миг живёт и работает, а её сторож доставки нашу пометку тут же стёр бы.
    """
    board = tmp_path / "board.jsonl"
    unreachable = dead_board_path().parent / "дерево"
    tab = tmp_path / "вкладка"
    tab.mkdir()
    kept = put_claim(
        registry_dir(board),
        "на-мёртвом-пути",
        **open_claim(
            str(unreachable), wave="wave9", stream="3", name="Сирота", seen_at=now_minus(5)
        ),
    )
    before = kept.read_bytes()

    denied = release(board, tab, "-Wave", "wave9", "-Stream", "3")

    assert denied.returncode != 0, f"запись на недостижимом пути сдана: {denied.stdout!r}"
    assert kept.read_bytes() == before, "файл чужой заявки тронут при отказе"
    said_lines = said(denied.stderr)
    assert (
        f"путь к рабочей папке потока wave9/3 недостижим целиком: {unreachable} — жива она или "
        "нет, неизвестно."
    ) in said_lines, f"отказ не назвал недостижимость пути: {denied.stderr!r}"
    guessed = [line for line in said_lines if "на диске нет" in line or "папки нет" in line]
    assert not guessed, f"«не вижу» выдано за «нет»: {guessed!r}"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Ребро переноса действует по ВРЕМЕНИ, а не по топологии.
#
# Выход «верните адрес себе тем же ключом» печатает сам механизм — значит взаимные рёбра переноса
# это не выдумка стенда, а обещанный сценарий. Пока рёбра разбирались топологически, такая пара не
# давала нулевого счётчика ожидания ни одной записи: очередь готовых пуста, не гасился НИКТО, и
# адрес снова вели двое — тот же дефект 1, только тише прежнего. Ровно так же воскресала первая
# запись в цепочке переездов A→B→C.
#
# Правило времени закрывает оба случая разом: ребро «i забрал адрес у папки j» НЕ действует только
# тогда, когда доказано, что заявка j началась позже момента переноса i. Отсюда и третья часть
# правила: момент объявления не наследуется, если своя прежняя запись погашена переносом. Иначе
# вернувшаяся вкладка выглядела бы объявившейся раньше, чем у неё забрали адрес, — и обе записи
# погасили бы друг друга.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def hours_ago(hours: float) -> str:
    """Время в прошлом, часами: порядок событий проверка задаёт сама, а не мерит скорость машины.

    Времена в заявке идут с точностью до секунды, а три запуска подряд укладываются в одну секунду
    легко. Тогда проверка проверяла бы не правило, а то, насколько быстра машина.
    """
    return (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")


@needs_pwsh
@needs_git
def test_the_address_returned_by_the_same_key_leaves_exactly_one_leader(tmp_path: Path) -> None:
    """Круг переноса A↔B: адрес вернули напечатанной командой — ведущая запись остаётся ОДНА.

    Обещание обратимости переноса стоит ровно на этом: механизм сам печатает проигравшей вкладке
    команду возврата. Пока рёбра разбирались топологически, обе записи ссылались друг на друга и не
    гасилась ни одна — находка приходила обеим, а первая закрывшая гасила её у второй.
    """
    real_worktrees(tmp_path, {"общая": "ветка-первой", "дерево": "ветка-второй"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Круг", "-Tasks", "10-13")
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Круг", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    back = claim(board, common, "wave9", "3", "-TakeOver")

    assert address_of(board, common) == "wave9/3", (
        f"вернувшая себе адрес вкладка его не получила: {back!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"после возврата адрес снова ведут двое — круг переноса не разведён: {listed!r}"
    )
    won = str(claim_of(board, common, only_open=True).fields["worktree"])
    assert f"перенесён в {won}" in stream_line_of(listed, tree), (
        f"забравшая запись возвратом не погашена: {listed!r}"
    )

    mark = add(board, "wave9/3", "находка после возврата")
    arrived = bullets(run_deliver(board, common, "Start", "вернувшаяся"))
    assert any(mark in line for line in arrived), (
        f"находка не дошла до вкладки, вернувшей себе адрес: {arrived!r}"
    )
    lost = context_text(run_deliver(board, tree, "Start", "отдавшая"))
    assert mark not in lost, f"находка ушла обеим сторонам круга сразу: {lost!r}"
    assert f"‼️ Ваш поток wave9/3 забран в {won} — эта вкладка больше не адресуема" in lost, (
        f"вторая сторона круга ушла в тишину молча: {lost!r}"
    )


@needs_pwsh
def test_the_returning_tab_gets_its_stream_back_but_not_its_seniority(tmp_path: Path) -> None:
    """Возврат адреса возвращает и поток — имя, задачи, план. А старшинство не возвращает.

    Наследуется всё, кроме момента объявления: старшинство на этом адресе потеряно вместе с самим
    адресом. И дело не только в честности: унаследованный момент оказался бы РАНЬШЕ момента, когда
    адрес забрали, — ребро соперника снова начало бы действовать, и две записи погасили бы друг
    друга. Сегодня вкладка возвращается вовсе безымянной, то есть теряет поток целиком.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    plan = "docs/superpowers/plans/2026-09-02-круг.md"
    claim(board, common, "wave9", "3", "-StreamName", "Круг", "-Tasks", "10-13", "-Plan", plan)
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Забрала", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    back = claim(board, common, "wave9", "3", "-TakeOver")

    returned = claim_of(board, common, only_open=True).fields
    assert returned["name"] == "Круг" and returned["tasks"] == "10-13", (
        f"вернувшаяся вкладка объявилась безымянной — поток потерян вместе с задачами: {back!r}"
    )
    assert returned["plan"] == plan, f"путь плана при возврате адреса потерян: {returned!r}"
    assert str(returned["claimed_at"]) > hours_ago(1), (
        "момент объявления унаследован у погашенной записи — вкладка выглядит объявившейся раньше, "
        f"чем у неё забрали адрес, и это снова сталкивает две записи лбами: {returned!r}"
    )


@needs_pwsh
def test_a_chain_of_moves_leaves_one_leader_and_no_resurrected_ghost(tmp_path: Path) -> None:
    """Цепочка переездов A→B→C гасит и A, и B: ведёт один C, задвоения нет, показ молчит.

    Правило «перенос от уже перенесённой не действует» было костылём против циклов и стоило ровно
    этого: при двух переездах подряд запись A снова становилась ведущей, адрес числился задвоенным,
    а разбирать это предлагалось человеку. Развилка закрыта — призрак больше не воскресает.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первое"
    second = tmp_path / "второе"
    third = tmp_path / "третье"
    claim(board, first, "wave9", "3", "-StreamName", "Цепочка")
    patch_claim(board, first, claimed_at=hours_ago(3))
    claim(board, second, "wave9", "3", "-StreamName", "Цепочка", "-TakeOver")
    patch_claim(board, second, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    last = claim(board, third, "wave9", "3", "-StreamName", "Цепочка", "-TakeOver")

    assert address_of(board, third) == "wave9/3", f"последний переезд адреса не получил: {last!r}"
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"цепочка переездов воскресила первую запись — адрес задвоен: {listed!r}"
    )
    for lost in (first, second):
        assert "перенесён" in stream_line_of(listed, lost), (
            f"после цепочки переездов запись папки {lost} осталась ведущей: {listed!r}"
        )


@needs_pwsh
def test_a_fresh_claim_in_the_old_folder_is_not_quenched_by_the_old_edge(tmp_path: Path) -> None:
    """Адрес забрали, поток переехал и честно сдался — прежняя папка объявляется на нём заново.

    Ключ переноса тут не нужен: незакрытых заявок у адреса нет. А старое ребро переноса, действуй
    оно вечно, погасило бы свежую заявку молча — папка объявилась бы кодом успеха и осталась
    невидимой и соседям, и находкам. Это скрытый дефект: снаружи он не виден нигде, кроме реестра.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Первый")
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Первый", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, tree).returncode == 0, "сдача переехавшего потока не прошла"

    again = claim(board, common, "wave9", "3", "-StreamName", "Второй")

    assert address_of(board, common) == "wave9/3", (
        f"свежую заявку прежней папки погасило старое ребро переноса: {again!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"показ считает адрес задвоенным после законного переобъявления: {listed!r}"
    )
    mark = add(board, "wave9/3", "находка новому потоку")
    arrived = bullets(run_deliver(board, common, "Start", "новая"))
    assert any(mark in line for line in arrived), (
        f"находка не дошла до вкладки, объявившейся на освободившемся адресе: {arrived!r}"
    )


@needs_pwsh
def test_the_yield_ring_never_leaves_a_succession_of_another_address(tmp_path: Path) -> None:
    """Круг уступки сдвинул номер — поле преемства вместе с ним не переезжает, но и не пропадает.

    Поле называет папку, у которой забран ИМЕННО ЭТОТ адрес. Останься оно при сдвиге — заявка
    утверждает, что забрала адрес у папки, которая его никогда не вела: настоящее ребро переноса
    исчезает, а погашенная запись воскресает вместе со своим ящиком и своими именами.

    ‼️ И выбросить его тоже нельзя: переезд БЫЛ, и запись прежней папки им погашена. Поэтому он
    уходит в список прошлых ВМЕСТЕ СО СВОИМ адресом — тем, который поток вёл до сдвига.
    """
    board = tmp_path / "board.jsonl"
    tree = tmp_path / "дерево"
    tree.mkdir()
    put_claim(
        registry_dir(board),
        "переехавшая",
        **open_claim(
            str(tree),
            wave="wave9",
            stream="3",
            name="Переехавшая",
            claimed_at=hours_ago(1),
            seen_at=now_minus(0),
            taken_from=str(tmp_path / "общая"),
            taken_at=hours_ago(1),
        ),
    )
    put_claim(
        registry_dir(board),
        "сосед-постарше",
        **open_claim(
            str(tmp_path / "сосед"),
            wave="wave9",
            stream="3",
            claimed_at=hours_ago(3),
            seen_at=now_minus(0),
        ),
    )

    out = claim_bare(board, tree)

    assert "сдвинут на следующий свободный" in out, (
        f"сцена собрана неверно — круг уступки номер не сдвинул: {out!r}"
    )
    moved = claim_of(board, tree, only_open=True)
    assert moved.address != "wave9/3", f"номер остался прежним: {moved.fields!r}"
    assert TAKEN_FROM_FIELD not in moved.fields and "taken_at" not in moved.fields, (
        "после сдвига заявка утверждает, что забрала адрес, которого у неё нет, — настоящее ребро "
        f"переноса при этом исчезло: {moved.fields!r}"
    )
    remembered = moved.fields.get(PAST_TAKEOVERS_FIELD)
    assert isinstance(remembered, list) and len(remembered) == 1, (
        f"переезд при сдвиге номера просто выброшен — погашенная им запись воскресает: {moved.fields!r}"
    )
    assert remembered[0]["stream"] == "3" and folder_key(
        remembered[0][TAKEN_FROM_FIELD]
    ) == folder_key(tmp_path / "общая"), (
        f"прошлый переезд записан не своим адресом и не своей папкой: {remembered!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_of_the_older_version_from_a_subfolder_never_closes_the_new_owners_finding(
    tmp_path: Path,
) -> None:
    """Закрытие находки ищет свою заявку обеими дорогами — иначе проигравшая гасит чужую почту.

    Сдача и сторож доставки вторую дорогу к своей заявке уже получили, а закрытие находки — нет.
    Заявка, поданная прежней версией из подкаталога, лежит под ключом того подкаталога, по
    каноничному ключу не находится вовсе, и проверка закрытости не срабатывает. Значит вкладка, у
    которой ЗАБРАЛИ адрес, гасит находку нового владельца — а закрытие именного адреса ОБЩЕЕ, и он
    не увидит её ни в доставке, ни на доске, зато автору придёт «учтено».
    """
    real_worktrees(tmp_path, {"общая": "ветка-проигравшей", "дерево": "ветка-победившей"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    deep = subfolder_of(common)
    put_claim(
        registry_dir(board),
        "заявка-прежней-версии",
        **open_claim(
            str(deep),
            wave="wave9",
            stream="3",
            name="Проигравшая",
            branch="ветка-проигравшей",
            seen_at=now_minus(0),
        ),
    )
    claim(board, tree, "wave9", "3", "-StreamName", "Победившая", "-TakeOver")
    mark = add(board, "ветка-проигравшей", "находка нового владельца", cwd=tmp_path)

    closing = tool(board, "-Mode", "Done", "-Id", mark, cwd=deep)

    assert closing.returncode != 0, (
        f"вкладка, у которой забрали адрес, погасила находку нового владельца: {closing.stdout!r}"
    )
    assert "адресована не вам" in closing.stderr, f"отказал не тот сторож: {closing.stderr!r}"
    assert any(mark in line for line in bullets(run_deliver(board, tree, "Start", "владелец"))), (
        "находка нового владельца погашена чужой рукой"
    )


@needs_pwsh
def test_adding_a_finding_to_a_doubled_address_says_it_may_reach_the_wrong_tab(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Приём находки на задвоенный адрес кричит так же, как показ, — иначе автора успокоили зря.

    Показ о задвоении говорит громкой строкой, а приём отвечал «поток ведёт вкладка — скорее всего,
    дойдёт сама». Успокаивает автора именно приём: после бодрого рапорта он не заводит находке
    запасного пункта, а достаться она может не тому — кому именно, решает порядок описи каталога.
    """
    registry_invariants.waive(
        "инвариант «одна ведущая запись на адрес»: задвоение собрано руками как наследие дефекта — "
        "проверяется, что о нём кричит и приём находки, а не только показ"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    first = tmp_path / "первая"
    second = tmp_path / "вторая"
    put_claim(
        folder, "одна", **open_claim(str(first), wave="wave9", stream="3", seen_at=now_minus(0))
    )
    put_claim(
        folder, "другая", **open_claim(str(second), wave="wave9", stream="3", seen_at=now_minus(0))
    )

    out = run_tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "находка на задвоенный адрес")

    loud = [line for line in out.splitlines() if line.startswith("‼️")]
    assert len(loud) == 1, f"приём находки о задвоенном адресе промолчал: {out!r}"
    assert "wave9/3" in loud[0], f"громкая строка не назвала задвоенного адреса: {loud[0]!r}"
    assert folder_key(first) in folder_key(out) and folder_key(second) in folder_key(out), (
        f"громкая строка не назвала обеих папок — идти разбираться некуда: {out!r}"
    )


def test_the_stand_reads_supersessions_the_way_the_tool_does(tmp_path: Path) -> None:
    """Стендовый разбор переносов отсеивает записи БЕЗ адреса — ровно как их отсеивает механизм.

    У заявки чужой версии волны и номера может не быть вовсе. Механизм такую запись в переносах не
    считает (адреса нет — забирать нечего), а стенд считал: два безадресных соседа сходились
    «адресом» из двух пустот, и стенд видел ребро, которого у механизма нет. Расхождение стенда с
    механизмом дороже дефекта в самом стенде: стенд начинает закреплять не то поведение.
    """
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "чужая-забравшая", state="open", worktree="d:/второе", taken_from="d:/первое")
    put_claim(folder, "чужая-прежняя", state="open", worktree="d:/первое")

    superseded, faults = supersessions(read_registry(folder))

    assert not superseded, "стенд погасил безадресную запись — такого ребра механизм не видит"
    assert not faults, f"стенд нашёл нарушение там, где механизм переноса не видит вовсе: {faults}"


@needs_pwsh
def test_a_move_outlives_the_folder_taken_by_the_next_stream(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Память о переезде переживает ПЕРЕИСПОЛЬЗОВАНИЕ папки, а не только сдачу.

    Поле преемства лежит в заявке забравшей папки, а заявка на папку ОДНА: как только та же папка
    бралась за следующий поток, её файл переписывался, ребро исчезало — и брошенная запись прежней
    папки снова становилась ведущей. Молча: показ не кричал, приём рапортовал «дойдёт сама», сторож
    доставки нёс находку брошенной вкладке. Это ровно самое дорогое следствие дефекта 1.
    """
    registry_invariants.waive(
        "инвариант «у адреса есть ведущая запись»: адрес здесь заканчивается НАМЕРЕННО — поток "
        "переехал, сдался и папку заняли под следующий. Проверяется как раз то, что призрак "
        "прежней папки ведущим не становится"
    )
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")
    patch_claim(board, common, claimed_at=hours_ago(3))
    ghost_file = claim_of(board, common, only_open=True).file
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, tree).returncode == 0, "сдача переехавшего потока не прошла"
    before = ghost_file.read_bytes()

    claim(board, tree, "wave9", "8", "-StreamName", "Следующий")

    assert ghost_file.read_bytes() == before, "чужая заявка тронута — писатель у файла не один"
    denied = tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "поздняя находка", cwd=common)
    assert denied.returncode != 0, (
        f"находка на брошенный адрес принята — призрак снова ведёт поток: {denied.stdout!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    line = stream_line_of(listed, common)
    assert "адрес забрала папка" in line and "wave9/8" in line, (
        f"показ выдаёт брошенную запись за перенос в живую вкладку того же адреса: {line!r}"
    )
    loud = [text for text in listed.splitlines() if text.startswith("‼️")]
    assert any("wave9/3" in text for text in loud), (
        f"показ промолчал об адресе, у которого не осталось ведущей записи: {listed!r}"
    )


@needs_pwsh
def test_a_chain_of_moves_outlives_a_reclaim_of_its_middle_folder(tmp_path: Path) -> None:
    """Цепочка A→B→C переживает переобъявление СРЕДНЕЙ папки: призрак не воскресает.

    Пока переезд помнила лишь нынешняя заявка папки, объявление следующего потока в средней папке
    стирало её ребро — и первая запись оживала рядом с последней. Адрес снова вели двое.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первое"
    second = tmp_path / "второе"
    third = tmp_path / "третье"
    claim(board, first, "wave9", "3", "-StreamName", "Цепочка")
    patch_claim(board, first, claimed_at=hours_ago(4))
    claim(board, second, "wave9", "3", "-StreamName", "Цепочка", "-TakeOver")
    patch_claim(board, second, claimed_at=hours_ago(3), taken_at=hours_ago(3))
    claim(board, third, "wave9", "3", "-StreamName", "Цепочка", "-TakeOver")
    patch_claim(board, third, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    claim(board, second, "wave9", "9", "-StreamName", "Следующий")

    assert address_of(board, third) == "wave9/3", "последняя запись цепочки адрес потеряла"
    listed = run_tool(board, "-Mode", "Streams")
    assert not [text for text in listed.splitlines() if text.startswith("‼️")], (
        f"переобъявление средней папки воскресило первую запись — адрес задвоен: {listed!r}"
    )
    assert "перенесён" in stream_line_of(listed, first), (
        f"первая запись цепочки снова стала ведущей: {listed!r}"
    )
    mark = add(board, "wave9/3", "находка после переобъявления средней папки")
    arrived = bullets(run_deliver(board, third, "Start", "последняя"))
    assert any(mark in text for text in arrived), f"находка не дошла до ведущей записи: {arrived!r}"
    lost = context_text(run_deliver(board, first, "Start", "первая"))
    assert mark not in lost, f"находка ушла и брошенной вкладке: {lost!r}"


@needs_pwsh
def test_a_claim_quenched_the_moment_it_is_written_never_reports_plain_success(
    tmp_path: Path,
) -> None:
    """Своя запись вышла погашенной сразу — объявление кричит об этом и ОТКАЗЫВАЕТ, а не рапортует.

    Заявка легла в свой файл, но ведущая она или уже погашена, решает реестр как целое: соседняя
    вкладка забрала адрес ровно тогда, когда наша читала реестр, — и в записанном виде наша заявка
    оказалась перенесённой. Нулевой код вкладка прочитала бы как «объявился, работаю» и ушла бы
    вести поток, которого снаружи не существует.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "моя"
    rival = tmp_path / "соперник"
    mine.mkdir(parents=True, exist_ok=True)
    # ‼️ Момент чужого переноса — на минуту ВПЕРЁД: так выглядит гонка, ради которой сцена и
    # написана (сосед забрал адрес, пока наша вкладка читала реестр). Он же делает исход
    # повторимым: поставь мы «сейчас», решала бы разница в доли секунды.
    put_claim(
        registry_dir(board),
        "соперник",
        **open_claim(
            str(rival),
            wave="wave9",
            stream="3",
            claimed_at=hours_ago(3),
            seen_at=hours_ago(3),
            taken_from=str(mine),
            taken_at=hours_ago(-1 / 60),
        ),
    )

    done = tool(
        board,
        "-Mode",
        "Claim",
        "-Wave",
        "wave9",
        "-Stream",
        "3",
        "-StreamName",
        "Свежая",
        "-TakeOver",
        cwd=mine,
    )

    assert done.returncode != 0, (
        f"объявление отрапортовало успехом там, где запись сразу погашена: {done.stdout!r}"
    )
    loud = [text for text in done.stdout.splitlines() if text.startswith("‼️")]
    assert any("ПОГАШЕНА" in text for text in loud), (
        f"о том, что запись сразу погашена, объявление промолчало: {done.stdout!r}"
    )
    assert "Адрес wave9/3 забран у папки" in done.stdout, (
        f"рапорт о переносе не напечатан вовсе: {done.stdout!r}"
    )
    assert folder_key(str(rival)) in folder_key(done.stdout), (
        f"не названа папка, за которой остался адрес, — идти разбираться некуда: {done.stdout!r}"
    )
    assert "-TakeOver" in done.stdout, (
        f"адрес ведёт живая запись, а рабочего выхода не напечатано: {done.stdout!r}"
    )
    # ‼️ Записана заявка или нет — смотрим по ЗАКРЫТЫМ тоже: погашенная открытой не считается, а
    # отказ прямо говорит, что файл на месте и повторять команду не надо.
    assert claim_of(board, mine, only_open=False).address == "wave9/3", (
        "файл заявки не записан, а отказ говорит обратное"
    )


@needs_pwsh
def test_an_edge_without_a_moment_no_longer_locks_the_address_forever(tmp_path: Path) -> None:
    """Ребро переноса без момента больше не гасит того, кто объявился ПОЗЖЕ.

    Поле преемства без момента писала только невыпущенная промежуточная версия, и писала она оба
    поля в один и тот же миг объявления. Пока такое ребро действовало безусловно, оно запирало
    адрес за потерпевшей навсегда: сколько бы раз она ни объявлялась заново, ребро гасило каждую её
    свежую заявку, а напечатанный ей выход не работал — забирать адрес было уже не у кого.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "моя"
    rival = tmp_path / "соперник"
    put_claim(
        registry_dir(board),
        "соперник",
        **open_claim(
            str(rival),
            wave="wave9",
            stream="3",
            claimed_at=hours_ago(3),
            seen_at=hours_ago(3),
            taken_from=str(mine),
        ),
    )

    out = claim(board, mine, "wave9", "3", "-StreamName", "Возврат", "-TakeOver")

    assert "Адрес wave9/3 забран у папки" in out, "перенос вообще не был назван"
    assert "ПОГАШЕНА" not in out, (
        f"свежую заявку погасило ребро, о котором известно лишь то, что оно старее: {out!r}"
    )
    assert address_of(board, mine) == "wave9/3", "вернувшая себе адрес вкладка его не получила"
    mark = add(board, "wave9/3", "находка после возврата адреса")
    arrived = bullets(run_deliver(board, mine, "Start", "вернувшаяся"))
    assert any(mark in text for text in arrived), (
        f"находка по адресу до вернувшей его вкладки не дошла: {arrived!r}"
    )


@needs_pwsh
def test_show_shouts_about_an_address_left_without_a_leader(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Показ кричит и об обратной беде: у адреса есть незакрытые заявки, а ведущей — ни одной.

    Так выглядит вкладка, которой снаружи не существует: её файл заявки открыт, она считает, что
    ведёт поток, а находку по адресу приём не примет и сторож доставки не принесёт. Про задвоенный
    адрес показ кричал, про этот — молчал.
    """
    registry_invariants.waive(
        "присмотр снят целиком: реестр собран руками именно такой сценой — проверяется, что показ "
        "о ней кричит"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    lost = tmp_path / "брошенная"
    gone = tmp_path / "уехавшая"
    put_claim(
        folder, "брошенная", **open_claim(str(lost), claimed_at=hours_ago(3), seen_at=hours_ago(0))
    )
    put_claim(
        folder,
        "уехавшая",
        **open_claim(
            str(gone),
            state="released",
            claimed_at=hours_ago(2),
            seen_at=hours_ago(0),
            taken_from=str(lost),
            taken_at=hours_ago(2),
        ),
    )

    listed = run_tool(board, "-Mode", "Streams")

    loud = [text for text in listed.splitlines() if text.startswith("‼️")]
    assert len(loud) == 1 and "wave9/3" in loud[0], (
        f"показ промолчал об адресе без ведущей записи: {listed!r}"
    )
    assert folder_key(str(lost)) in folder_key(listed), (
        f"громкая строка не назвала папки брошенной вкладки: {listed!r}"
    )


def test_registry_invariants_catch_an_address_without_a_leader(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Тот же вопрос — сторожу стенда: реестр без ведущей записи он обязан назвать нарушением.

    ‼️ Красной против кода без правки эта проверка быть не может: подопытный здесь не механизм, а
    сам стенд, и она закрепляет НОВОЕ его умение. Механизм в ней не участвует вовсе.
    """
    registry_invariants.waive("реестр собран противоречивым нарочно — подопытный сам сторож")
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "брошенная", **open_claim("d:/первое", claimed_at=hours_ago(3)))
    put_claim(
        folder,
        "уехавшая",
        **open_claim(
            "d:/второе",
            state="released",
            claimed_at=hours_ago(2),
            taken_from="d:/первое",
            taken_at=hours_ago(2),
        ),
    )

    faults = registry_faults(folder)

    assert any("ведущей записи не осталось" in fault for fault in faults), (
        f"сторож промолчал на адресе, у которого не осталось ведущей записи: {faults}"
    )


@needs_pwsh
def test_the_memory_of_past_moves_is_capped_and_drops_the_oldest(tmp_path: Path) -> None:
    """Список прошлых переездов не растёт бесконечно: лишнее отбрасывается с самого старого.

    ‼️ Проверка закрепляет ОГРАНИЧЕНИЕ, а не чинимое поведение, но красной против кода без правки
    она всё равно выходит: списка там нет вовсе.
    """
    board = tmp_path / "board.jsonl"
    here = tmp_path / "папка"
    old_moves = [
        {
            "wave": "wave9",
            "stream": str(number),
            "taken_from": f"d:/папка-{number}",
            "taken_at": hours_ago(100 - number),
        }
        for number in range(1, 26)
    ]
    put_claim(
        registry_dir(board),
        "папка",
        **open_claim(
            str(here),
            wave="wave9",
            stream="26",
            state="released",
            claimed_at=hours_ago(80),
            past_takeovers=old_moves,
        ),
    )

    claim(board, here, "wave9", "30", "-StreamName", "Следующий")

    kept = claim_of(board, here, only_open=True).fields["past_takeovers"]
    assert isinstance(kept, list) and len(kept) == 20, (
        f"память прошлых переездов не ограничена двумя десятками: {kept}"
    )
    numbers = [str(move["stream"]) for move in kept]
    assert "25" in numbers and "1" not in numbers, (
        f"отброшены не самые старые переезды, а какие попало: {numbers}"
    )


@needs_pwsh
@needs_git
def test_the_older_copy_keeps_the_memory_of_past_moves_it_does_not_understand(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Старая копия комплекта из живого дерева незнакомый список не понимает — и не стирает его.

    Копий около двадцати, синхронизации между ними нет, и сторож доставки старой копии правит файл
    заявки ЦЕЛИКОМ на каждом ходу вкладки. Выбрось он незнакомое поле — память о переезде умерла бы
    на первом же ходу, а брошенная запись прежней папки снова стала бы ведущей.
    """
    registry_invariants.waive(
        "инвариант «у адреса есть ведущая запись»: поток переехал, сдался и папку заняли под "
        "следующий — проверяется, что старая копия память о переезде не стирает"
    )
    board = tmp_path / "board.jsonl"
    common = tmp_path / "общая"
    tree = tmp_path / "дерево"
    claim(board, common, "wave9", "3", "-StreamName", "Переезд")
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, tree).returncode == 0, "сдача переехавшего потока не прошла"
    claim(board, tree, "wave9", "8", "-StreamName", "Следующий")
    old = older_copy(tmp_path)

    walked = older_deliver(old, board, tree, "старая")

    assert walked.returncode == 0, f"старый сторож доставки сорвался: {walked.stderr!r}"
    kept = claim_of(board, tree, only_open=True).fields.get(PAST_TAKEOVERS_FIELD)
    assert isinstance(kept, list) and kept, (
        f"старая копия стёрла память о прошлых переездах: {kept!r}"
    )
    denied = tool(
        board, "-Mode", "Add", "-To", "wave9/3", "-Title", "после старой копии", cwd=common
    )
    assert denied.returncode != 0, (
        f"после хода старой копии призрак снова ведёт адрес: {denied.stdout!r}"
    )


@needs_pwsh
def test_the_invariant_never_calls_a_lawful_move_a_circle(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """«Погашены все записи адреса» кругом НЕ является — сторож обязан звать сцену своим именем.

    Папка забрала адрес, а потом взялась за следующий поток: её запись теперь про другой адрес, а у
    прежнего осталась одна погашенная. Прежнее условие («погашены все») раньше и вправду означало
    круг — погасить последнюю запись адреса мог только сосед по тому же адресу. С памятью переездов
    гасит запись ДРУГОГО адреса, и утверждение начало кричать «перенос ходит по кругу» на самой
    частой законной сцене, ради которой правка и делалась. Про эту сцену говорит пятое утверждение.
    """
    registry_invariants.waive(
        "реестр собран руками: у адреса и вправду не осталось ведущей записи — проверяется как раз "
        "то, каким именем сторож эту сцену называет"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "прежняя", **open_claim("d:/первое", wave="wave9", stream="3"))
    put_claim(
        folder,
        "забравшая",
        **open_claim(
            "d:/второе",
            wave="wave9",
            stream="9",
            past_takeovers=[
                {
                    "wave": "wave9",
                    "stream": "3",
                    TAKEN_FROM_FIELD: "d:/первое",
                    TAKEN_AT_FIELD: hours_ago(2),
                }
            ],
        ),
    )

    faults = registry_faults(folder)

    assert not [fault for fault in faults if "по кругу" in fault], (
        f"законный переезд назван кругом — сторож кричит на сцене, ради которой всё делалось: "
        f"{faults}"
    )
    assert any("ведущей записи не осталось" in fault for fault in faults), (
        f"сцена, в которой адрес остался без ведущей записи, прошла молча: {faults}"
    )


@needs_pwsh
def test_the_answer_names_the_folder_where_the_address_really_went(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Потерпевшей называют КОНЕЦ цепочки переездов, а не среднюю папку.

    Цепочка A→B→C законна, и средняя папка могла с тех пор взяться за следующий поток. Прежде ответ
    «куда делся адрес» обрывался на первом же звене, чья запись сменила адрес, — то есть ровно там,
    где память переездов и понадобилась. Человека посылали в папку, где про этот адрес нет ничего.
    """
    registry_invariants.waive(
        "инвариант «у адреса есть ведущая запись»: поток переехал по цепочке и там закончился — "
        "проверяется, какую папку при этом называют потерпевшей"
    )
    board = tmp_path / "board.jsonl"
    first = tmp_path / "первое"
    second = tmp_path / "второе"
    third = tmp_path / "третье"
    claim(board, first, "wave9", "3", "-StreamName", "Цепочка")
    patch_claim(board, first, claimed_at=hours_ago(5))
    claim(board, second, "wave9", "3", "-StreamName", "Цепочка", "-TakeOver")
    patch_claim(board, second, claimed_at=hours_ago(4), taken_at=hours_ago(4))
    claim(board, third, "wave9", "3", "-StreamName", "Цепочка", "-TakeOver")
    patch_claim(board, third, claimed_at=hours_ago(3), taken_at=hours_ago(3))
    # Средняя папка взялась за следующий поток, последняя — свой честно сдала.
    claim(board, second, "wave9", "9", "-StreamName", "Следующий")
    assert release(board, third).returncode == 0, "сдача последней папки цепочки не прошла"

    given = release(board, first).stdout

    assert folder_key(str(third)) in folder_key(given), (
        f"конец цепочки переездов не назван — идти разбираться некуда: {given!r}"
    )
    assert folder_key(str(second)) not in folder_key(given), (
        f"потерпевшую посылают в среднюю папку, где про этот адрес нет ничего: {given!r}"
    )


@needs_pwsh
def test_a_dead_end_is_never_printed_as_the_way_out(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Ключ переноса советуют только там, где ему есть у кого забрать адрес.

    Адрес забрала соседняя папка и там поток закончила — ведущей записи у адреса не осталось.
    Прежде и сдача, и сторож доставки советовали потерпевшей вернуть адрес ключом переноса, а ключ
    отвечал «не понадобился: адрес не ведёт заявка другой папки». Вкладка ходила по кругу, выполняя
    единственный напечатанный ей выход, — напечатанный выход обязан работать.
    """
    registry_invariants.waive(
        "инвариант «у адреса есть ведущая запись»: поток переехал и там закончился — проверяется "
        "как раз то, что об этом тупике говорят правду"
    )
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "моя"
    rival = tmp_path / "соперник"
    claim(board, mine, "wave9", "3", "-StreamName", "Переезд")
    patch_claim(board, mine, claimed_at=hours_ago(4))
    claim(board, rival, "wave9", "3", "-StreamName", "Переезд", "-TakeOver")
    patch_claim(board, rival, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, rival).returncode == 0, "сдача переехавшего потока не прошла"

    given = release(board, mine).stdout
    walked = context_text(run_deliver(board, mine, "Start", "потерпевшая"))

    assert "-TakeOver" not in given, (
        f"сдача советует ключ, которому не у кого забрать адрес: {given!r}"
    )
    assert "нечем" in given, f"о том, что забирать адрес не у кого, сдача молчит: {given!r}"
    assert "-TakeOver" not in walked, (
        f"сторож доставки советует ключ, которому не у кого забрать адрес: {walked!r}"
    )
    assert "свободным номером" in walked, (
        f"рабочего выхода вкладке сторож доставки не напечатал: {walked!r}"
    )


@needs_pwsh
def test_a_forgotten_move_is_named_aloud_when_the_memory_overflows(tmp_path: Path) -> None:
    """Переезд, забытый по пределу памяти, назван ВСЛУХ, а не потерян молча.

    С каждым отброшенным ребром брошенная запись прежней папки снова становится ведущей на том
    адресе, а показ, приём и сторож доставки об этом не скажут ни слова. Сцена практически
    недостижима (нужен двадцать первый перехват одной папкой), но недостижимость — не повод молчать.
    """
    board = tmp_path / "board.jsonl"
    here = tmp_path / "папка"
    old_moves = [
        {
            "wave": "wave9",
            "stream": str(number),
            TAKEN_FROM_FIELD: f"d:/папка-{number}",
            TAKEN_AT_FIELD: hours_ago(100 - number),
        }
        for number in range(1, 26)
    ]
    put_claim(
        registry_dir(board),
        "папка",
        **open_claim(
            str(here),
            wave="wave9",
            stream="26",
            state="released",
            claimed_at=hours_ago(80),
            past_takeovers=old_moves,
        ),
    )

    out = claim(board, here, "wave9", "30", "-StreamName", "Следующий")

    loud = [text for text in out.splitlines() if text.startswith("‼️")]
    assert any("Память переездов переполнена" in text for text in loud), (
        f"забытый переезд потерян молча — о потере не сказал никто: {out!r}"
    )
    assert "wave9/1" in out and "папка-1" in out, (
        f"не назван сам забытый переезд: ни адрес, ни папка, у которой его забирали: {out!r}"
    )
