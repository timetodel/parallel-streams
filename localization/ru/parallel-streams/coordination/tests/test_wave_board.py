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

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Файл теперь лежит в .claude/skills/parallel-streams/coordination/tests/ — до корня репозитория
# на пять уровней выше (tests -> coordination -> parallel-streams -> skills -> .claude -> корень).
REPO_ROOT = Path(__file__).resolve().parents[5]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
COORDINATION_DIR = REPO_ROOT / ".claude" / "skills" / "parallel-streams" / "coordination"
HOOKS_DIR = COORDINATION_DIR / "hooks"
TOOL = COORDINATION_DIR / "wave-board.ps1"
DELIVER = HOOKS_DIR / "wave-board-deliver.ps1"
NUDGE = HOOKS_DIR / "pretooluse-wave-board-nudge.ps1"

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh не найден — запускать скрипты нечем")


def settings() -> dict:
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
    """Правит заявку названной вкладки — так тест задаёт то, чего сам вычислить не может.

    Список тронутых файлов инструмент берёт у git, а тестовые папки репозиториями не являются:
    подставляем список руками и ставим свежую отметку времени, чтобы сторож его не пересчитывал.
    """
    registry = board.parent / "streams"
    here = str(worktree).replace("\\", "/").rstrip("/")
    for path in registry.glob("*.json"):
        claim = json.loads(path.read_text(encoding="utf-8"))
        if str(claim.get("worktree", "")).replace("\\", "/").rstrip("/") != here:
            continue
        claim.update(fields)
        path.write_text(json.dumps(claim, ensure_ascii=False), encoding="utf-8")
        return
    raise AssertionError(f"заявки для {worktree} в реестре нет")


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
    """Убирает поле из заявки — так выглядит заявка, заведённая прежней версией инструмента."""
    registry = board.parent / "streams"
    here = str(worktree).replace("\\", "/").rstrip("/")
    for path in registry.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if str(record.get("worktree", "")).replace("\\", "/").rstrip("/") != here:
            continue
        record.pop(field, None)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return
    raise AssertionError(f"заявки для {worktree} в реестре нет")


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
    """Файл заявки названной вкладки в реестре."""
    registry = board.parent / "streams"
    here = str(worktree).replace("\\", "/").rstrip("/")
    for path in registry.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if str(record.get("worktree", "")).replace("\\", "/").rstrip("/") == here:
            return path
    raise AssertionError(f"заявки для {worktree} в реестре нет")


def address_of(board: Path, worktree: Path) -> str:
    """Адрес потока, который вкладка объявила за собой, — так, как его назовут соседи."""
    record = json.loads(claim_file_of(board, worktree).read_text(encoding="utf-8"))
    return f"{record['wave']}/{record['stream']}"


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
def test_a_number_from_the_plan_is_never_moved(tmp_path: Path) -> None:
    """Номер из плана — имя потока, им адресуют находки: двигать его нельзя, можно только сказать.

    Перехват вкладки бывает осознанным (первую закрыли, не сдав), и разрешает такой спор человек.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "wave9-первая"
    second = tmp_path / "wave9-вторая"
    claim(board, first, "wave9", "3")
    out = claim(board, second, "wave9", "3")

    assert address_of(board, second) == "wave9/3", (
        f"названный номер сдвинули — адрес из плана уехал сам собой: {out!r}"
    )
    assert "На этот же поток есть открытая заявка другого дерева" in out, (
        f"о двух заявках на один номер не сказано ничего: {out!r}"
    )


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
