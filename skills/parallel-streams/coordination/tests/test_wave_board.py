"""Guard: the wave board delivers a finding to a neighbouring stream and stays quiet everywhere else.

The board exists for exactly one reason: to let an addition to the plan catch up with an ALREADY
RUNNING session — the plan is read once, at start, from its own worktree, and a later edit to it
never catches up with a live neighbour at all. The mechanism itself stays silent throughout — both
hooks swallow any surprise and exit with zero. So its breakage looks exactly like "the neighbour has
nothing to say" and shows up as nothing else. Hence checking behaviour, not file existence: real
scripts are run against their own board and their own state folder.

The properties everything rests on are checked by name:
  • the board lives in the repository's SHARED directory — otherwise a neighbour won't see it;
  • a stranger's entry doesn't reach the tab, its own reaches it exactly once — otherwise that's
    noise in context;
  • an open entry comes back after context is compacted — otherwise it's lost on long work;
  • no more than five are shown per turn, and the rest arrive on later turns — otherwise the sixth
    and beyond never arrive at all;
  • the addressee is checked against real worktrees — otherwise a finding lands in the void with a
    cheerful report;
  • a broken line doesn't swallow the next record;
  • the reminder on a plan edit names the tabs that checked in first, and says "unknown" about the
    rest — but never calls them closed.

Silence here is ambiguous: "nothing to show" and "the hook died" look the same. So wherever silence
is checked, the next thing checked is that the hook speaks up in the same environment.

The state of THIS machine doesn't affect the check: the trees liveness is checked against are set up
by the test itself, in its own temporary repository. The old check looked at the project's real live
trees and used to skip itself in exactly the state that exposed the defect.
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

# The toolkit doesn't live only here: there are five more copies, and each has its own path prefix
# down to the coordination folder (in the public repository, for one —
# localization/ru/parallel-streams/coordination and skills/parallel-streams/coordination, with no
# .claude/skills/ of this project at all). The old count of levels up (parents[4]) was a hardcoded
# prefix of this one project: in another copy it landed in the wrong folder, the tool wasn't found,
# and the whole suite went red without even starting — nobody was guarding the declared source of
# truth. The tests folder lives INSIDE coordination always and everywhere, so the toolkit's own
# folder is taken from the check's file — one level up, with no prefixes counted.
COORDINATION_DIR = Path(__file__).resolve().parent.parent


def _find_repo_root(start: Path) -> Path:
    """The repository root is the first folder up the tree that has a `.git` in it.

    It is needed not for finding the toolkit itself (that comes off the check's file, see
    COORDINATION_DIR above), but as the working folder for running the tool and for the checks tied
    to the make-up of THIS PARTICULAR project (Claude Code's settings, the waves profile). Copies of
    the toolkit sit at different depths from the root, so counting levels here would again be a
    hardcoded prefix — we look for the `.git` marker instead of counting folders. Not found (an
    archive with no history, say) — the fallback is the old count of levels from the check's file.
    """
    for candidate in start.parents:
        if (candidate / ".git").exists():
            return candidate
    return start.parents[4]


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
HOOKS_DIR = COORDINATION_DIR / "hooks"
TOOL = COORDINATION_DIR / "wave-board.ps1"
DELIVER = HOOKS_DIR / "wave-board-deliver.ps1"
NUDGE = HOOKS_DIR / "pretooluse-wave-board-nudge.ps1"

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh not found — nothing to run the scripts with")


def settings() -> dict:
    """Claude Code's settings for THIS PARTICULAR project — read by the checks of the guards wired here.

    In another copy of the toolkit there may be no such file at all (the installer hasn't wired the
    guards in there yet) — then there is nothing to check against, and that has to be said with a
    skip rather than by dropping the check on an uncaught filesystem exception.
    """
    if not SETTINGS.exists():
        pytest.skip(
            f"this copy has no {SETTINGS} — Claude Code's settings for this project aren't set up, "
            "there's nothing to check the guards' wiring against"
        )
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def hooks_for(event: str) -> list[dict]:
    return [
        hook
        for entry in settings().get("hooks", {}).get(event, [])
        for hook in entry.get("hooks", [])
    ]


# The stub project's plans folder — DELIBERATELY not the one used in this repository. The toolkit
# promises portability: match the checks to one project's folder, and they'd fail in the very first
# other repository, and that failure would read as "the toolkit is broken".
STUB_PLANS = "planning/waves/"

# The plans folder of ANOTHER project — the very one that used to be hardcoded into the code. The
# stub profile doesn't name it, so to the toolkit it's just an ordinary folder: the guard must not
# count it as plans, and a neighbour editing the same files there must still count as an overlap.
ALIEN_PLANS = "docs/superpowers/plans/"


def profile_text(plans: str | None = STUB_PLANS, header: str = "## Plans") -> str:
    """The stub project's profile: with a plans folder, or with no such section at all."""
    head = "# Stub project profile\n\n## Isolation\n\nA separate tree per stream.\n"
    if plans is None:
        return head
    return f"{head}\n{header}\n\nWhere the wave plans live: `{plans}`.\n"


def plans_folder() -> str:
    """This project's own wave-plans folder — from its profile, not hardcoded in the check."""
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
    """There are two gaps, and each is closed by its own event.

    Session start carries everything open (including after context compaction), an ordinary turn —
    only what's new. Drop either one and the mechanism goes half-silent, indistinguishable from
    silence.
    """
    for event, stage in (("SessionStart", "-Stage Start"), ("UserPromptSubmit", "-Stage Prompt")):
        commands = [hook.get("command", "") for hook in hooks_for(event)]
        wired = [cmd for cmd in commands if "wave-board-deliver.ps1" in cmd]
        assert wired, (
            f"the delivery guard isn't wired on {event} — a neighbour's finding won't reach the tab, "
            "and it will look exactly like \"the neighbour has nothing to say\""
        )
        assert any(stage in cmd for cmd in wired), (
            f"on {event} the delivery guard runs without {stage} — the event will be handled by the wrong branch"
        )


def test_plan_edit_guard_is_wired_for_both_tools() -> None:
    """Half the edits go through one tool, half through the other: missing either one means silence.

    The folder in the condition is checked against the one THIS project's profile names: hardcoded in
    the check, it would fail this test in any other repository even though the channel would work
    correctly there.
    """
    plans = plans_folder()
    if not plans:
        pytest.skip(
            "the project's profile doesn't name a wave-plans folder — the nudge guard has nothing to listen for"
        )
    filters = " ".join(
        hook.get("if", "")
        for hook in hooks_for("PreToolUse")
        if "pretooluse-wave-board-nudge.ps1" in hook.get("command", "")
    )
    assert filters, "the plan-edit guard isn't wired at all"
    for tool_name in ("Edit", "Write"):
        assert f"{tool_name}({plans}**)" in filters, (
            f"the plan-edit guard doesn't listen to {tool_name} — a plan edit will go through without the "
            "reminder that it won't reach a neighbour's live tab on its own"
        )


@needs_pwsh
def test_board_lives_in_the_shared_git_directory() -> None:
    """The board must live in the repository's shared directory, not in a worktree.

    That's the whole point of the location: the directory is the same for every tree (a neighbour sees
    the entry right away, no merge needed), it sits outside branches (won't get stuck inside someone
    else's claim), and it survives a tree being deleted along with the closed tab. Let the board move
    into a tree, and the mechanism becomes invisible to the very people it exists for.
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
    assert "/.git/wave-board/" in path, f"the board doesn't live in the repository's shared directory: {path}"
    assert "/.claude/worktrees/" not in path, (
        f"the board moved inside a worktree ({path}) — neighbouring tabs won't see it"
    )


def tool(
    board: Path, *args: str, cwd: Path = REPO_ROOT, known: bool = False
) -> subprocess.CompletedProcess[str]:
    """Runs the board tool against the test's own separate board.

    Addressees in the tests are made up, so checking them against reality is worked around with an
    explicit switch: otherwise the tests would have to be tied to this one machine's real worktrees.
    `known=True` — when it's the check itself being checked.
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
    """Places a finding and returns its id."""
    out = run_tool(board, "-Mode", "Add", "-To", to, "-Title", title, cwd=cwd)
    return out.split("id ")[1].split(")")[0].strip()


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
    """The text the hook puts into the tab's context: it leaves as a single line of JSON."""
    stripped = stdout.strip()
    if not stripped.startswith("{"):
        return stdout
    return json.loads(stripped)["hookSpecificOutput"]["additionalContext"]


def bullets(text: str) -> list[str]:
    """Bullet lines in the hook's context (one per record or per tree)."""
    return [line for line in context_text(text).splitlines() if line.strip().startswith("•")]


@needs_pwsh
def test_record_travels_to_its_stream_and_only_there(tmp_path: Path) -> None:
    board = tmp_path / "board.jsonl"
    add(board, "feat/wave9-clock", "count the clock both directions")
    add(board, "feat/wave9-truth", "the flag defaults to cleared")

    # A tab knows itself by the name of its working folder — that's why the folder is named after
    # the stream.
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    first = run_deliver(board, mine, "Start", "s1")
    assert "count the clock both directions" in first, "the tab's own finding didn't reach it"
    assert "the flag defaults to cleared" not in first, (
        "a finding for ANOTHER stream reached the tab — that's noise in context, paid for on every "
        "turn until the work ends"
    )

    # Second turn: nothing new, so the guard must stay silent.
    assert run_deliver(board, mine, "Prompt", "s1").strip() == "", (
        "the finding was shown a second time — a repeat piles up in context and gets resent on every turn"
    )

    # The silence of "nothing to show" and the silence of "the hook died" look the same: both exit
    # with zero and empty output. The only thing that tells them apart is a live guard speaking up
    # for a new record.
    add(board, "wave9-clock", "late finding")
    assert "late finding" in run_deliver(board, mine, "Prompt", "s1"), (
        "after the silence the guard didn't speak up for a new record — so the silence was a "
        "breakage, not an absence of findings"
    )


@needs_pwsh
def test_closed_record_stops_travelling(tmp_path: Path) -> None:
    board = tmp_path / "board.jsonl"
    mark = add(board, "wave9-clock", "handled finding")

    # Only the ADDRESSEE closes it: a finding with a named address can only be closed by it —
    # closing such an entry is shared, and a stranger's hand would clear it at the real recipient's.
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    # Look up by ID, not by title: closing spawns an "acknowledged: ..." notice to the author, and
    # its title repeats the original one. Checking by title would confuse the two.
    assert f"[{mark}]" not in run_tool(board, "-Mode", "Show"), (
        "the closed finding stayed in the board listing"
    )
    assert run_deliver(board, mine, "Start", "s2").strip() == "", (
        "the closed finding keeps arriving in the tab — closing it would then be pointless"
    )


@needs_pwsh
def test_open_record_returns_after_context_is_compacted(tmp_path: Path) -> None:
    """Context compaction raises session start again — an open finding must come back.

    Otherwise it's lost exactly where the mechanism matters most: on long work, where compactions
    are frequent.
    """
    board = tmp_path / "board.jsonl"
    add(board, "wave9-clock", "still unhandled finding")
    mine = tmp_path / "wave9-clock"
    mine.mkdir()

    assert "still unhandled finding" in run_deliver(board, mine, "Start", "s3")
    assert run_deliver(board, mine, "Prompt", "s3").strip() == ""
    assert "still unhandled finding" in run_deliver(board, mine, "Start", "s3"), (
        "after context compaction the open finding didn't come back — and the old context already "
        "scrolled away"
    )


def crowd_the_board(board: Path, count: int) -> None:
    for number in range(count):
        add(board, "wave9-clock", f"finding {number}")


@needs_pwsh
def test_one_turn_shows_no_more_than_five_records(tmp_path: Path) -> None:
    """The display cap is five records per turn: more than that is a wall of text nobody keeps reading."""
    board = tmp_path / "board.jsonl"
    crowd_the_board(board, 7)
    mine = tmp_path / "wave9-clock"
    mine.mkdir()

    assert len(bullets(run_deliver(board, mine, "Start", "s-batch"))) == 5, (
        "one turn didn't show exactly five records — the cap isn't honored, and the tab's context "
        "gets resent on every turn"
    )


@needs_pwsh
def test_records_over_the_limit_arrive_on_the_next_turns(tmp_path: Path) -> None:
    """The sixth record and beyond must arrive on later turns, not vanish.

    The shown-log marks records as shown — mark ALL matching ones while showing only the first five,
    and the rest never arrives: not on the next turn, not after context compaction either.
    """
    board = tmp_path / "board.jsonl"
    crowd_the_board(board, 7)
    mine = tmp_path / "wave9-clock"
    mine.mkdir()

    first = run_deliver(board, mine, "Start", "s-rest")
    second = run_deliver(board, mine, "Prompt", "s-rest")
    seen = {number for number in range(7) if f"finding {number}" in first + second}
    assert seen == set(range(7)), (
        f"not all findings arrived, only {sorted(seen)} — records over the cap vanished for good"
    )
    assert len(bullets(second)) == 2, "the remainder didn't arrive on the next turn"


@needs_pwsh
def test_broadcast_reaches_everyone_but_its_author(tmp_path: Path) -> None:
    """`*` — every live tab except the one that posted: the author already knows their own finding.

    The author is learned from the record's field, and it's filled in from git. Git doesn't answer
    (the tab isn't launched from a repository) — the field must fall back to the working folder's
    name, or the finding would boomerang back to the author.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-clock"
    author.mkdir()
    other = tmp_path / "wave9-truth"
    other.mkdir()

    add(board, "*", "wave-wide finding", cwd=author)
    assert "wave-wide finding" in run_deliver(board, other, "Start", "s-all"), (
        "the \"everyone\" finding didn't reach the neighbouring tab"
    )
    assert run_deliver(board, author, "Start", "s-mine").strip() == "", (
        "the \"everyone\" finding came back to whoever posted it — that's noise in the author's context"
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
    """One stream gets called three ways, and all three must fold to the same key.

    Let the forms drift apart, and a finding addressed by branch wouldn't find a tab that knows
    itself by folder name — and it would look exactly like "the neighbour has nothing to say".
    """
    board = tmp_path / "board.jsonl"
    add(board, addressed, "finding under a different name form")
    mine = tmp_path / folder
    mine.mkdir()
    assert "finding under a different name form" in run_deliver(board, mine, "Start", "s-name"), (
        f"addressee \"{addressed}\" didn't resolve to working folder \"{folder}\""
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

# Wave 7's trees: the first three have a fresh beacon, the rest have none or a stale one.
ALIVE = ("wave7-alpha", "wave7-beta", "wave7-gamma")
UNKNOWN = ("wave7-eta", "wave7-iota", "wave7-kappa", "wave7-lambda", "wave7-mu", "wave7-theta")


def set_beacon(tree: Path, hours_ago: float) -> None:
    """Sets a live tab's beacon to the needed freshness — the same way the delivery guard does."""
    mark = tree / BEACON
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text("2026-08-21T10:00:00 probe-session\n", encoding="utf-8")
    when = time.time() - hours_ago * 3600
    os.utime(mark, (when, when))


@pytest.fixture(scope="module")
def wave_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repository of its own with worktrees — the check doesn't depend on this machine's state.

    The reminder used to be checked against the project's live trees, and the test would skip itself
    in exactly the machine state that exposes the defect: when no "live" trees are visible at all.
    Here we set up the trees and the freshness of their beacons ourselves, so all three cases get
    checked — a tab that checked in, a tree with no mark, and a wave with no trees at all.

    The stub project's profile names ITS OWN plans folder and goes into the first commit: a worktree
    only sees what's committed, and the guard looks for the profile without stepping outside its own
    tree.
    """
    root = tmp_path_factory.mktemp("wave-repo")
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "probe")
    (root / "readme.md").write_text("probe\n", encoding="utf-8")
    (root / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "first")
    trees = ("wave7-here", *ALIVE, *UNKNOWN, "wave8-other", "wave6-lonely")
    for name in trees:
        git(root, "worktree", "add", "-b", f"feat/{name}", f".claude/worktrees/{name}")
    # A tree whose folder name and branch name diverge: their keys are different, and a stream that
    # knows itself by branch must be recognized by folder — and the other way around. The name
    # deliberately carries no wave number: it must not show up in the wave-scoped reminder lists.
    git(root, "worktree", "add", "-b", "feat/oddbranch-tab", ".claude/worktrees/oddfolder-tab")
    for name in ("wave7-here", *ALIVE, "wave8-other"):
        set_beacon(root / ".claude" / "worktrees" / name, 0.1)
    # A stale beacon means "unknown", not "alive": the tab was closed a day ago.
    set_beacon(root / ".claude" / "worktrees" / "wave7-theta", 13)
    return root


def here_of(repo: Path) -> Path:
    """The tree the check runs from: the reminder doesn't name itself in the list."""
    return repo / ".claude" / "worktrees" / "wave7-here"


def named_streams(text: str) -> list[str]:
    return re.findall(r"feat/wave\d+-[a-z]+", text)


@needs_pwsh
def test_tool_refuses_an_addressee_without_a_worktree(tmp_path: Path, wave_repo: Path) -> None:
    """The addressee is checked against real worktrees: otherwise a finding lands in the void with a
    cheerful report.

    It's easy to miss in three ways at once — name the stream in words, typo the branch, leave a
    stray trailing slash. All three look like success, because the entry really does get written.
    """
    board = tmp_path / "board.jsonl"
    here = here_of(wave_repo)
    for wrong in ("stream 3", "feat/wave7-alfa"):
        done = tool(
            board, "-Mode", "Add", "-To", wrong, "-Title", "misdirected finding", cwd=here, known=True
        )
        assert done.returncode != 0, (
            f"the tool accepted a nonexistent addressee \"{wrong}\" and reported success"
        )
        assert "Wave Loose Ends" in done.stderr, (
            "the refusal doesn't say where to put a finding for a closed stream"
        )
    # In the hint, names come in their canonical short form — the very one -To accepts.
    assert (
        "wave7-alpha"
        in tool(
            board, "-Mode", "Add", "-To", "feat/wave7-alfa", "-Title", "off-target", cwd=here, known=True
        ).stderr
    ), "the typo refusal didn't suggest the similar name, even though it's right there"
    assert not board.exists(), "an off-target entry still landed on the board"

    # A tree that's actually set up passes the check — otherwise the check would just forbid everything.
    add(board, "feat/wave7-alpha", "finding for a real tree", cwd=here)


@needs_pwsh
def test_tool_tells_whether_the_neighbour_tab_answered_recently(
    tmp_path: Path, wave_repo: Path
) -> None:
    """The report never promises delivery it can't know, and doesn't round in either direction.

    A fresh mark means "the tab worked recently", not "it's working right now": nothing clears the
    beacon on close, and a tab closed an hour ago still looks checked-in for half a day more. A firm
    "will get there on its own" is dangerous here — the finding's author would relax on it and never
    set up a task in the loose ends. A tree with no fresh mark is even more "unknown": the tab could
    have been closed, or never opened at all.
    """
    board = tmp_path / "board.jsonl"
    here = here_of(wave_repo)
    alive = run_tool(
        board, "-Mode", "Add", "-To", "feat/wave7-alpha", "-Title", "for the live one", cwd=here, known=True
    )
    assert "checked in recently" in alive, (
        "about a tab with a fresh mark the report doesn't say the main thing — that it worked recently"
    )
    assert "most likely" in alive, (
        "the report promises delivery as a firm fact — but a beacon only means \"worked in the last "
        "few hours\": the tab could have been closed an hour ago, nothing clears the mark on close"
    )
    unknown = run_tool(
        board, "-Mode", "Add", "-To", "feat/wave7-eta", "-Title", "for the silent one", cwd=here, known=True
    )
    assert "checked in recently" not in unknown, (
        "the report passes off a long-silent tab as one that just checked in"
    )
    assert "unknown" in unknown, "the report doesn't admit it knows nothing about the neighbouring tab"


@needs_pwsh
def test_tool_refuses_an_empty_addressee_even_with_the_bypass(tmp_path: Path) -> None:
    """An empty key (`feat/wave3-plan-clock/`) — always refused: such an entry would reach no one."""
    board = tmp_path / "board.jsonl"
    done = tool(
        board, "-Mode", "Add", "-To", "feat/wave3-plan-clock/", "-Title", "finding into the void"
    )
    assert done.returncode != 0, "the tool accepted an addressee whose key collapsed to empty"
    assert not board.exists(), "an entry with an empty addressee landed on the board"


@needs_pwsh
def test_a_broken_line_does_not_swallow_the_next_record(tmp_path: Path) -> None:
    """A broken record must not carry the next one down with it.

    Appending without a newline glues the new record onto the fragment: neither parses, and the tool
    still reports success — the finding is lost silently.
    """
    board = tmp_path / "board.jsonl"
    whole = json.dumps(
        {
            "id": "aaaa1111",
            "at": "2026-08-20T10:00:00",
            "to": "wave9-clock",
            "title": "first finding",
        },
        ensure_ascii=False,
    )
    board.write_text(whole + '\n{"id":"bbbb2222","at":"2026-08-2', encoding="utf-8")

    add(board, "wave9-clock", "second finding")
    show = run_tool(board, "-Mode", "Show")
    assert "first finding" in show, "the broken record carried the previous one off the board with it"
    assert "second finding" in show, (
        "the new record glued onto the fragment and didn't parse — yet the tool reported success"
    )


@needs_pwsh
def test_delivery_guard_stays_silent_on_a_wrong_stage(tmp_path: Path) -> None:
    """The hook's header promises "exits silently with zero on any surprise" — including its own launch.

    Parameter binding runs before the script body, so an invalid value used to return a nonzero code
    and dump the input JSON straight out.
    """
    assert pwsh
    board = tmp_path / "board.jsonl"
    add(board, "wave9-clock", "finding for this stream")
    # The working folder is the ADDRESSEE's folder: otherwise the hook would stay silent on any
    # stage, and the check would come back green even if stage parsing were gutted entirely.
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    call = json.dumps({"session_id": "s-stage", "tool_input": {"file_path": "secret"}})
    done = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(DELIVER), "-Stage", "Nonsense", "-BoardPath", str(board)],
        input=call,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(mine),
        timeout=60,
    )
    assert done.returncode == 0, "the hook returned a nonzero code — the harness will treat it as broken"
    assert done.stdout.strip() == "", f"the hook dumped extra output: {done.stdout!r}"

    # In the SAME environment the correct stage must speak up — only then does the silence above mean
    # "wrong stage", not "nothing to show" or "the hook died".
    assert "finding for this stream" in run_deliver(board, mine, "Start", "s-stage"), (
        "the hook stayed silent on the correct stage too — so the silence on the wrong one proves nothing"
    )


@needs_pwsh
def test_cleanup_spares_the_live_session_and_runs_without_records(tmp_path: Path) -> None:
    """Log cleanup must not touch a live session's log — and must always run.

    A log's write time used to change only from new findings, and a long session may go without one
    for days: its log fell under cleanup, and everything already shown arrived all over again. The
    other half: cleanup sat AFTER the early exit and didn't run at all when the board was empty.
    """
    board = tmp_path / "board.jsonl"
    mark = add(board, "wave9-clock", "finding")
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    run_deliver(board, mine, "Start", "s-live")

    cache = mine / ".claude" / ".cache"
    journal = cache / "wave-board-shown-s-live.txt"
    assert journal.exists(), "the shown-log wasn't created — nothing now holds back repeats"
    ancient = cache / "wave-board-shown-ancient.txt"
    ancient.write_text("deadbeef\n", encoding="utf-8")
    stale = time.time() - 3 * 24 * 3600
    os.utime(journal, (stale, stale))
    os.utime(ancient, (stale, stale))

    # The finding got closed: nothing to show, and the hook used to exit BEFORE cleanup. Only the
    # addressee closes it — a stranger's hand would clear a named finding at the real recipient's.
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    assert run_deliver(board, mine, "Prompt", "s-live").strip() == ""

    assert journal.exists(), (
        "cleanup carried off the live session's log — everything already shown will arrive again"
    )
    assert journal.stat().st_mtime > stale + 3600, (
        "the live session's mark isn't refreshed on the fly — in a day its log would fall under "
        "cleanup again"
    )
    assert not ancient.exists(), (
        "a long-closed session's log wasn't removed — cleanup doesn't run when there's nothing to show"
    )


@needs_pwsh
def test_board_can_be_compacted_and_warns_when_it_grows(tmp_path: Path) -> None:
    """The board is parsed whole on every turn of every tab — it must be possible to compact it.

    A closed record stays a line in the file forever: parsing gets more expensive, with nothing to
    show for it. So the display warns about a crowded board, and compaction keeps only what's open.
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
                    "title": f"trivia {number}",
                },
                ensure_ascii=False,
            )
        )
        lines.append(json.dumps({"id": mark, "at": "2026-08-01T11:00:00", "done": True}))
    # The open record is appended as TEXT, not through the tool: adding a record compacts a crowded
    # board on its own (a separate test below), and there'd be nothing left to check here.
    lines.append(
        json.dumps(
            {
                "id": "openone",
                "at": "2026-08-01T12:00:00",
                "to": "wave9-clock",
                "title": "open finding",
            },
            ensure_ascii=False,
        )
    )
    board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert "-Mode Compact" in run_tool(board, "-Mode", "Show"), (
        "the display doesn't warn about a crowded board — it gets parsed whole on every turn"
    )

    run_tool(board, "-Mode", "Compact")
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(left) == 1, f"after compaction the board has {len(left)} lines left instead of one"

    after = run_tool(board, "-Mode", "Show")
    assert "open finding" in after, "compaction carried off the open record"
    assert "-Mode Compact" not in after, "the warning stayed on the compacted board"


@needs_pwsh
def test_adding_a_record_compacts_a_crowded_board(tmp_path: Path) -> None:
    """A crowded board gets compacted by the act of adding a finding itself.

    A "please compact by hand" request is aimed at whoever came here for something else: they drop a
    finding for a neighbour and leave. The work is safe (it refuses on any doubt), so there's no
    reason to do it by hand — and every tab pays for a bloated board, parsing it whole on every turn.
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
                    "title": f"trivia {number}",
                },
                ensure_ascii=False,
            )
        )
        lines.append(json.dumps({"id": mark, "at": "2026-08-01T11:00:00", "done": True}))
    board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = run_tool(board, "-Mode", "Add", "-To", "wave9-clock", "-Title", "fresh finding")
    assert "compacted" in out, (
        "adding stayed silent about compaction — something erased silently is indistinguishable "
        "from something that was never there"
    )
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(left) == 1, f"after adding, the board has {len(left)} lines left instead of one"
    assert "fresh finding" in run_tool(board, "-Mode", "Show"), (
        "compaction carried off the very record the whole thing was for"
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
    """The reminder narrows the choice down to those who checked in, but doesn't bury the rest.

    The delivery guard sets the mark in its own tree on every turn. A fresh mark — the tab is
    definitely alive; no mark — unknown (an old tab, the hook never reached it, work going on
    quietly), and passing that off as "the stream is closed" is not allowed: the finding would go
    into "Wave Loose Ends" past a live neighbour. Along the way this also checks a relative path — the
    hook used to exit silently on one.
    """
    done = run_nudge(here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-nudge-alive")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), (
        "the reminder stays silent on a relative path — and plan edits arrive in that form too"
    )
    context = context_text(done.stdout)
    names = named_streams(context)
    assert names[:3] == [f"feat/{name}" for name in ALIVE], (
        f"the tabs that checked in aren't named first: {names}"
    )
    assert "feat/wave7-here" not in names, "the reminder suggests addressing a finding to itself"
    assert "feat/wave8-other" not in names, "the list has a neighbour from ANOTHER wave"
    assert "feat/wave6-lonely" not in names, "the list has a neighbour from ANOTHER wave"
    assert len(names) == 8, f"the cap of eight names isn't honored: {len(names)}"
    assert "and 1 more" in context, "the truncated list doesn't admit it left names out"
    assert "unknown" in context, (
        "a tree with no fresh mark is passed off as a live tab — the report promises what it doesn't know"
    )
    # Separately, about the freshness threshold: wave7-theta's mark is half a day old, and it no
    # longer counts as a live tab. Stop the threshold from working, and theta would land in the
    # fourth line's first spot.
    answered = [line for line in context.splitlines() if "marked themselves" in line]
    assert len(answered) == 1, "the line about tabs that checked in was lost or duplicated"
    assert named_streams(answered[0]) == [f"feat/{name}" for name in ALIVE], (
        f"the wrong tabs ended up counted as alive: {named_streams(answered[0])} — looks like the "
        "mark freshness threshold isn't working"
    )
    assert "Wave Loose Ends" in context, (
        "the reminder doesn't say where to put a finding for a closed stream — that's the rule's "
        "other half"
    )

    second = run_nudge(
        here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-nudge-alive"
    )
    assert second.stdout.strip() == "", (
        "the reminder repeats within the same session — a stream sees many plan edits, and the "
        "reminder is shown once"
    )


@needs_pwsh
def test_plan_edit_guard_does_not_bury_a_wave_without_beacons(wave_repo: Path) -> None:
    """A wave where not a single tab has checked in yet is "unknown", not "all closed".

    The old liveness signal (a worktree lock) made exactly this mistake: the mechanism silently
    declared every stream closed and steered the finding into "Wave Loose Ends" past the board.
    """
    done = run_nudge(here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave6-probe.md", "s-nudge-quiet")
    assert done.returncode == 0, done.stderr
    context = context_text(done.stdout)
    assert "feat/wave6-lonely" in context, (
        "a wave tree with no mark isn't named at all — the finding will go past a live neighbour"
    )
    assert "unknown" in context, "nothing is said about a silent tree — that nothing is known about it"
    assert "must be closed" not in context, (
        "a silent tree is declared a closed stream — that's exactly the claim the mechanism doesn't "
        "know to be true"
    )


@needs_pwsh
def test_plan_edit_guard_says_when_the_wave_has_no_worktrees(wave_repo: Path) -> None:
    """No trees for the wave at all — that IS "streams closed", and the finding's place is the loose ends."""
    done = run_nudge(here_of(wave_repo), f"{STUB_PLANS}2026-01-01-wave5-probe.md", "s-nudge-empty")
    assert done.returncode == 0, done.stderr
    context = context_text(done.stdout)
    assert named_streams(context) == [], (
        "for a wave with no trees the reminder still named someone's names"
    )
    assert "Wave Loose Ends" in context, "doesn't say where to put a finding when there's no one to address"


def stub_project(root: Path, name: str, profile: str) -> Path:
    """A stub project with its own profile: the plans folder is ITS OWN there, unlike this repository."""
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    git(project, "init", "-b", "main")
    (project / ".parallel-streams.md").write_text(profile, encoding="utf-8")
    return project


@needs_pwsh
def test_plan_edit_guard_takes_the_plans_folder_from_the_profile(wave_repo: Path) -> None:
    """The project's profile names the plans folder, not the guard's code.

    A folder hardcoded for one project is a silent breakage: in another project the condition never
    matches, the guard exits silently with zero, and it looks like "nothing to remind about".
    """
    here = here_of(wave_repo)
    mine = run_nudge(here, f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-profile-plans")
    assert mine.returncode == 0, mine.stderr
    assert mine.stdout.strip(), (
        "the guard stayed silent about a plan edit in the folder the project's profile named"
    )
    alien = run_nudge(here, f"{ALIEN_PLANS}2026-01-01-wave7-probe.md", "s-profile-alien")
    assert alien.stdout.strip() == "", (
        "the guard mistook a file in ANOTHER project's folder for a wave plan — the folder must be hardcoded"
    )


@needs_pwsh
def test_the_plans_section_is_found_whatever_the_case(tmp_path: Path) -> None:
    """The section heading is matched case-insensitively — a human writes the profile by hand.

    The section has two readers, the installer and the guard, and they must read it identically: let
    them diverge in case, and the installer would wire the guard to a folder the guard itself doesn't
    consider the plans folder — and the guard would stay silent forever.
    """
    project = stub_project(tmp_path, "lowercase-project", profile_text(header="## plans"))
    done = run_nudge(project, f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-lower-case")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), (
        "the lowercase \"## plans\" heading wasn't found — the guard thinks the project has no plans"
    )


@needs_pwsh
def test_without_a_plans_section_the_guard_has_no_folder_at_all(tmp_path: Path) -> None:
    """No folder named means there is none — and no default falls back to another project's path.

    Otherwise the guard would treat files in a folder this project doesn't even know about as wave plans.
    """
    project = stub_project(tmp_path, "no-plans-project", profile_text(plans=None))
    quiet = run_nudge(project, f"{ALIEN_PLANS}2026-01-01-wave7-probe.md", "s-no-section")
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout.strip() == "", (
        "the guard spoke up about a plans folder the profile never named — the path was taken by default"
    )

    # Silence is ambiguous: "no folder" and "the guard died" look the same. Name the folder — and in
    # the same project the guard must speak up.
    (project / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    named = run_nudge(project, f"{STUB_PLANS}2026-01-01-wave7-probe.md", "s-no-section-then")
    assert named.stdout.strip(), (
        "the guard stays silent even after the folder was named — so the silence above was a breakage"
    )


@needs_pwsh
def test_delivery_guard_marks_its_worktree_alive(tmp_path: Path) -> None:
    """The live-tab beacon gets set on every turn — even when there's nothing to show.

    A neighbour tells a working tab apart from an abandoned tree by it. Set it only alongside showing
    a finding, and only the tab that already got something delivered would count as alive.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    assert run_deliver(board, mine, "Prompt", "s-beacon").strip() == ""

    mark = mine / BEACON
    assert mark.exists(), "the beacon wasn't set when there's no board at all — yet there was a turn"
    assert "s-beacon" in mark.read_text(encoding="utf-8"), "the beacon doesn't show whose tab it is"

    stale = time.time() - 5 * 3600
    os.utime(mark, (stale, stale))
    run_deliver(board, mine, "Start", "s-beacon")
    assert mark.stat().st_mtime > stale + 3600, (
        "the beacon isn't refreshed on the fly — in half a day a live tab would count as silent"
    )


@needs_pwsh
def test_tool_refuses_a_blank_title(tmp_path: Path) -> None:
    """A title made of nothing but spaces would be seen by no one: display hides it, delivery skips it."""
    board = tmp_path / "board.jsonl"
    done = tool(board, "-Mode", "Add", "-To", "wave9-clock", "-Title", "   ")
    assert done.returncode != 0, "the blank title was accepted with a cheerful report"
    assert not board.exists(), "an invisible entry still landed on the board"


@needs_pwsh
@pytest.mark.parametrize(
    "args",
    [
        ("-Mode", "Add", "-Title", "finding with no addressee"),
        ("-Mode", "Add", "-To", "wave9-clock"),
        (
            "-Mode",
            "Done",
        ),
    ],
)
def test_tool_refuses_plainly(tmp_path: Path, args: tuple[str, ...]) -> None:
    """A human reads the refusal: PowerShell's exception frame tears up the text with line breaks and color."""
    board = tmp_path / "board.jsonl"
    done = tool(board, *args)
    assert done.returncode != 0, f"the tool accepted an incomplete call: {args}"
    assert "Exception" not in done.stderr, (
        f"the refusal came out wrapped in an exception frame instead of plain text: {done.stderr!r}"
    )
    assert "wave-board.ps1:" not in done.stderr, (
        "the refusal shows the script's innards — a human has to read it"
    )


def hold_the_board(board: Path, seconds: int = 30) -> subprocess.Popen[bytes]:
    """Holds the board with exclusive access — the way antivirus and a backup tool hold it for an instant.

    Returns the holder: it must be killed in a `finally`, or the file stays locked.
    """
    assert pwsh
    ready = board.parent / "holder-ready.txt"
    ready.unlink(missing_ok=True)
    script = (
        f"$s = [System.IO.File]::Open('{board}', 'Open', 'ReadWrite', 'None'); "
        f"Set-Content -LiteralPath '{ready}' -Value 'holding' -Encoding utf8; "
        f"Start-Sleep -Seconds {seconds}; $s.Dispose()"
    )
    holder = subprocess.Popen([pwsh, "-NoProfile", "-Command", script])
    deadline = time.time() + 30
    while time.time() < deadline:
        if ready.exists():
            return holder
        time.sleep(0.1)
    holder.kill()
    raise AssertionError("the board's holder never took the file — nothing to check")


@needs_pwsh
def test_compaction_refuses_when_the_board_cannot_be_read(tmp_path: Path) -> None:
    """A failed read must not look like an empty board — or compaction would wipe out every finding.

    Reading used to return an empty list both when the file is missing and when five attempts in a
    row failed to open it. Compaction only checked that the size hadn't changed — and it hadn't,
    nobody was appending — and replaced the board with an empty file, reporting "was 0 lines, now 0".
    """
    board = tmp_path / "board.jsonl"
    add(board, "wave9-clock", "a finding that must not be lost")
    before = board.read_text(encoding="utf-8")

    holder = hold_the_board(board)
    try:
        done = tool(board, "-Mode", "Compact")
        assert done.returncode != 0, "compaction ran blind, unable to read the board"
        assert "was 0 lines" not in done.stdout, "compaction reported an empty board"
        assert "couldn't read" in done.stderr, f"the refusal didn't name the reason: {done.stderr!r}"
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert board.read_text(encoding="utf-8") == before, (
        "the board changed even though it couldn't be read"
    )
    assert "a finding that must not be lost" in run_tool(board, "-Mode", "Show"), (
        "the finding vanished from the board"
    )


@needs_pwsh
def test_commands_report_a_locked_board_instead_of_calling_it_empty(tmp_path: Path) -> None:
    """A locked board is not an empty board, and not "already closed": the reason has to be named.

    A failed read used to turn into an empty list, and the tool would say "no open entries" or
    "maybe it's already closed" — exactly the opposite of the truth. Appending, after ten attempts,
    always said "board is busy", even when the disk had actually run out of space.
    """
    board = tmp_path / "board.jsonl"
    mark = add(board, "wave9-clock", "finding under lock")

    holder = hold_the_board(board)
    try:
        shown = tool(board, "-Mode", "Show")
        assert shown.returncode != 0, "the display passed off a locked board as one it had read"
        assert "No open entries on the wave board" not in shown.stdout, (
            "a locked board is shown as an empty one — that pushes toward filing a duplicate finding"
        )
        assert "couldn't read" in shown.stderr, f"the display didn't name the reason: {shown.stderr!r}"

        closing = tool(board, "-Mode", "Done", "-Id", mark)
        assert "already closed" not in closing.stdout, (
            "closing lied about \"already closed\" when the board simply couldn't be read"
        )
        assert closing.returncode != 0

        adding = tool(board, "-Mode", "Add", "-To", "wave9-clock", "-Title", "one more")
        assert adding.returncode != 0
        assert "Last reason:" in adding.stderr, (
            f"the append refusal didn't keep the real reason: {adding.stderr!r}"
        )
        # A human reads the reason, but system messages come in English.
        assert "the file is locked by another process" in adding.stderr, (
            f"the refusal reason stayed a raw system message: {adding.stderr!r}"
        )
        assert "being used by another process" not in adding.stderr
    finally:
        holder.kill()
        holder.wait(timeout=30)


@needs_pwsh
def test_compaction_refuses_a_board_that_parses_into_nothing(tmp_path: Path) -> None:
    """A non-empty file that parses into not a single record is a reason to stop, not to erase.

    That's what a garbled write, a foreign encoding, or a fragment all look like. Rewriting such a
    board means losing whatever might still be sitting in it.
    """
    board = tmp_path / "board.jsonl"
    board.write_text("something we don't understand\nand another line\n", encoding="utf-8")
    before = board.read_text(encoding="utf-8")

    done = tool(board, "-Mode", "Compact")
    assert done.returncode != 0, "compaction erased a board it parsed nothing out of"
    assert board.read_text(encoding="utf-8") == before, "the board was rewritten blind"


@needs_pwsh
def test_broadcast_closes_only_for_the_stream_that_closed_it(tmp_path: Path) -> None:
    """An "everyone" finding is handled by each addressee for themselves.

    A global close by id used to hide it from everyone at once: whoever handled it first, and the
    rest never saw it — especially the one with no turn or restart since it was added.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "wave-wide finding", cwd=author)
    assert "wave-wide finding" in run_deliver(board, first, "Start", "s-first")
    closing = run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    assert "for stream" in closing, (
        "closing the broadcast entry didn't say that it's personal"
    )

    assert run_deliver(board, first, "Start", "s-first-again").strip() == "", (
        "the stream that closed it keeps getting the handled finding"
    )
    assert "wave-wide finding" in run_deliver(board, second, "Start", "s-second"), (
        "the neighbour didn't see the \"everyone\" finding after another stream closed it — and it "
        "had had no turn at all since it was added"
    )

    board_view = run_tool(board, "-Mode", "Show")
    assert "wave-wide finding" in board_view, "the display hid a finding not everyone has handled yet"
    assert "wave9-clock" in board_view, "the display doesn't say who's already handled it"


@needs_pwsh
def test_show_for_a_stream_includes_what_is_addressed_to_everyone(tmp_path: Path) -> None:
    """A display scoped to a stream must include what's addressed to everyone: delivery will bring it.

    A false "no entries" pushes toward compacting the board or filing a duplicate finding.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    add(board, "*", "wave-wide finding", cwd=author)
    add(board, "wave9-truth", "someone else's finding")

    view = run_tool(board, "-Mode", "Show", "-To", "wave9-clock")
    assert "wave-wide finding" in view, (
        "the stream-scoped display hid the \"everyone\" entry, though delivery will bring it to that tab"
    )
    assert "someone else's finding" not in view, "the stream-scoped display dragged in an unrelated record"


@needs_pwsh
def test_compaction_keeps_personal_closings_of_surviving_records(tmp_path: Path) -> None:
    """Compaction must not hand a stream back something it already handled.

    An "everyone" entry survives compaction while it hasn't been handled by everyone — and its named
    closings must survive right along with it, or the stream that handled it gets the finding again.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "wave-wide finding", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    run_tool(board, "-Mode", "Compact")

    assert run_deliver(board, first, "Start", "s-after-compact").strip() == "", (
        "after compaction the handled finding came back to whoever closed it"
    )
    assert "wave-wide finding" in run_deliver(board, second, "Start", "s-other-after-compact"), (
        "compaction carried off an \"everyone\" entry that not everyone had handled yet"
    )


def now_minus(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def board_line(**fields: object) -> str:
    """A board line assembled by hand: the only way to fake an age that can't be set any other way."""
    record: dict[str, object] = {
        "id": "aaaa0001",
        "at": now_minus(0),
        "wave": "",
        "to": "*",
        "title": "finding",
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
    """An "everyone" finding must have a shared way out too: topic's closed — clear it for everyone at once.

    Without it the entry can't be removed from the board at all: a personal close only silences it
    for the one who closed it, compaction keeps it along with its named closings, and every NEW
    worktree gets it at start — even one with nothing to do with that wave.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "exhausted finding", cwd=author)
    closing = run_tool(board, "-Mode", "Done", "-Id", mark, "-ForAll", cwd=first)
    assert "for every addressee" in closing, "the shared close didn't say it cleared the entry for everyone"

    assert run_deliver(board, second, "Start", "s-after-forall").strip() == "", (
        "an entry cleared for everyone keeps arriving for neighbours"
    )
    assert "exhausted finding" not in run_tool(board, "-Mode", "Show"), (
        "an entry cleared for everyone stayed in the board listing"
    )
    run_tool(board, "-Mode", "Compact")
    assert "exhausted finding" not in board.read_text(encoding="utf-8"), (
        "compaction kept an entry that was cleared for everyone"
    )


@needs_pwsh
def test_stale_broadcast_stops_travelling(tmp_path: Path) -> None:
    """The shelf life is a fallback for when the global close was forgotten.

    A wave lives for weeks; a finding not handled in two weeks is as stale as the wave itself, and
    every tab in the project pays for it in context — including ones set up later with nothing to do
    with that wave.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="stale001", at=now_minus(20), title="long-past finding")
        + "\n"
        + closing_line("stale001", by="wave9-clock", days_ago=19)
        + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-truth"
    mine.mkdir()

    assert run_deliver(board, mine, "Start", "s-stale").strip() == "", (
        "an expired \"everyone\" finding still reaches tabs"
    )
    assert "long-past finding" not in run_tool(board, "-Mode", "Show"), (
        "an expired finding is shown as open"
    )

    run_tool(board, "-Mode", "Compact")
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert left == [], f"compaction kept the expired record or its named closings: {left}"


@needs_pwsh
def test_addressed_record_never_goes_stale(tmp_path: Path) -> None:
    """The shelf life only touches "everyone" entries: an addressed one closes globally, and has a way out."""
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(
            id="old00001", at=now_minus(40), to="wave9-clock", title="long-standing addressed finding"
        )
        + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-clock"
    mine.mkdir()
    assert "long-standing addressed finding" in run_deliver(board, mine, "Start", "s-old-addressed"), (
        "the shelf life silenced an addressed finding — and nobody had closed it"
    )


@needs_pwsh
def test_show_tells_apart_open_closed_and_stale(tmp_path: Path) -> None:
    """The display tells three states apart: open, closed for you, expired.

    Otherwise it's unclear why a record sits in the file yet shows up nowhere, and why compaction
    sometimes removes it and sometimes doesn't.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        "\n".join(
            [
                board_line(id="fresh001", title="fresh everyone finding"),
                board_line(id="mine0001", title="finding I handled"),
                closing_line("mine0001", by="wave9-clock"),
                board_line(id="stale002", at=now_minus(20), title="long-past finding"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    view = run_tool(board, "-Mode", "Show", "-To", "wave9-clock")
    assert "fresh everyone finding" in view, "the open record vanished from the display"
    assert "finding I handled" not in view, "a handled record is shown as open"
    assert "closed for you" in view.lower(), (
        "the display doesn't say the record is closed specifically for this stream"
    )
    assert "expired" in view.lower(), "the display stays silent about expired records"


@needs_pwsh
def test_show_does_not_advise_compaction_that_would_remove_nothing(tmp_path: Path) -> None:
    """Advising a compaction that would remove nothing is wasted work and false hope."""
    board = tmp_path / "board.jsonl"
    lines = [
        board_line(id=f"open{number:04d}", to="wave9-clock", title=f"finding {number}")
        for number in range(220)
    ]
    board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    view = run_tool(board, "-Mode", "Show")
    assert "-Mode Compact" not in view, (
        "the display recommends compaction even though there's nothing to remove — it would change nothing"
    )


@needs_pwsh
def test_compaction_reports_lines_it_could_not_parse(tmp_path: Path) -> None:
    """A dropped unreadable line must be named: something erased silently is indistinguishable from
    something that was never there."""
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="good0001", to="wave9-clock", title="live finding")
        + "\na record fragment that doesn't parse\n",
        encoding="utf-8",
    )
    out = run_tool(board, "-Mode", "Compact")
    assert "lines not parsed and dropped: 1" in out.lower(), (
        f"compaction silently dropped the unreadable line: {out!r}"
    )
    assert "live finding" in board.read_text(encoding="utf-8"), "compaction carried off the healthy record"


@needs_pwsh
def test_show_for_a_stream_knows_both_of_its_names(tmp_path: Path, wave_repo: Path) -> None:
    """A stream is called by both branch and folder, and they fold to DIFFERENT keys when the names diverge.

    Closing writes the key by branch, while display was asked by folder name — and it didn't
    recognize the stream's own closing, showing a handled finding as open.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    odd = wave_repo / ".claude" / "worktrees" / "oddfolder-tab"

    mark = add(board, "*", "finding for the two-named stream", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=odd)

    # The display runs FROM the repository that has these trees: git only names both forms of the
    # stream's name there.
    view = run_tool(board, "-Mode", "Show", "-To", "oddfolder-tab", cwd=odd)
    assert "finding for the two-named stream" not in view, (
        "the display by folder name didn't recognize the closing recorded by that same tree's branch name"
    )
    assert "closed for you" in view.lower(), "the display didn't count the record as closed for this stream"


@needs_pwsh
def test_the_stream_that_closed_it_personally_can_still_close_it_for_everyone(
    tmp_path: Path,
) -> None:
    """Realizing the topic is exhausted is easiest for whoever just handled it.

    And that's exactly who used to get locked out: their own personal close hides the record from
    themselves, and the shared close already answered "nothing open" — the only way off the board
    was closed right in front of the one who needed it most.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()
    second = tmp_path / "wave9-truth"
    second.mkdir()

    mark = add(board, "*", "exhausted topic", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    closing = run_tool(board, "-Mode", "Done", "-Id", mark, "-ForAll", cwd=first)
    assert "for every addressee" in closing, (
        "the stream that already handled the finding couldn't clear it for everyone — and it's the "
        "first to know the topic is exhausted"
    )
    assert run_deliver(board, second, "Start", "s-forall-after-personal").strip() == "", (
        "after clearing it for everyone the finding keeps arriving for neighbours"
    )


@needs_pwsh
def test_broadcast_with_a_broken_date_is_not_immortal(tmp_path: Path) -> None:
    """A corrupted date must not give an "everyone" entry immortality.

    A board line gets hand-edited, brought in by a different version of the tool — and the date ends
    up empty, numeric, or unparsable. The shelf life used to do nothing at all for such an entry: it
    was delivered to every NEW tree and survived compaction — that is, in a narrow case, it reopened
    exactly the hole the shelf life was built to close. The safe side is to treat it as expired: a
    finding with a corrupted date isn't fit to be acted on anyway.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        "\n".join(
            [
                board_line(id="broke001", at="", title="finding with no date"),
                board_line(id="broke002", at=1234567890, title="finding with a number instead of a date"),
                board_line(id="broke003", at="the day before yesterday", title="finding with a garbled date"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-truth"
    mine.mkdir()

    assert run_deliver(board, mine, "Start", "s-broken").strip() == "", (
        "an entry with a corrupted date still gets delivered — and there's no way to clear it off the board"
    )
    view = run_tool(board, "-Mode", "Show")
    assert "finding with no date" not in view, "an entry with a corrupted date is shown as open"
    assert "corrupted" in view.lower(), (
        "the display stays silent about entries with a corrupted date — a human won't understand "
        "where the finding went"
    )

    run_tool(board, "-Mode", "Compact")
    left = [line for line in board.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert left == [], f"compaction kept entries with a corrupted date: {left}"


@needs_pwsh
def test_a_ten_day_old_broadcast_is_still_alive(tmp_path: Path) -> None:
    """The live edge of the shelf life: the two weeks the rules promise must be real.

    Quietly shortening it (14 → 1) is easy, and no test caught it: only the expired side was checked.
    Then an "everyone" finding would vanish before anyone got a chance to handle it, while the wave
    rules kept promising two weeks.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="fresh010", at=now_minus(10), title="still-live everyone finding") + "\n",
        encoding="utf-8",
    )
    mine = tmp_path / "wave9-truth"
    mine.mkdir()

    assert "still-live everyone finding" in run_deliver(board, mine, "Start", "s-ten-days"), (
        "a ten-day-old finding wasn't delivered — the shelf life was shortened"
    )
    assert "still-live everyone finding" in run_tool(board, "-Mode", "Show"), (
        "a ten-day-old finding vanished from the display"
    )
    run_tool(board, "-Mode", "Compact")
    assert "still-live everyone finding" in board.read_text(encoding="utf-8"), (
        "compaction carried off a finding that's only ten days old"
    )


@needs_pwsh
def test_closing_the_same_record_twice_tells_the_truth(tmp_path: Path) -> None:
    """Closing your own record a second time must not lie about "cleared for everyone".

    A stream already silenced the "everyone" finding for itself; it closes again — and used to hear
    that it was cleared for everyone, or that it's expired, when neither happened. The answer must
    name the real state and point to the way out that actually exists.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    author.mkdir()
    first = tmp_path / "wave9-clock"
    first.mkdir()

    mark = add(board, "*", "finding for the double close", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    again = run_tool(board, "-Mode", "Done", "-Id", mark, cwd=first)

    assert "for yourself" in again, f"closing again didn't name the real state: {again!r}"
    assert "-ForAll" in again, "closing again didn't suggest how to clear the record for everyone"
    assert "expired" not in again, "closing again lied about the record being expired"


@needs_pwsh
def test_show_counts_stale_records_of_the_asked_wave_only(tmp_path: Path) -> None:
    """The wave filter is the same one for the list and for the state counts — or the "expired" line lies."""
    board = tmp_path / "board.jsonl"
    board.write_text(
        "\n".join(
            [
                board_line(id="w9stale1", at=now_minus(20), wave="9", title="expired, wave 9"),
                board_line(id="w8stale1", at=now_minus(20), wave="8", title="expired, wave 8"),
                board_line(id="w9open01", wave="9", title="live, wave 9"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    view = run_tool(board, "-Mode", "Show", "-Wave", "9")
    assert "live, wave 9" in view, "the open record of the wanted wave vanished"
    assert "expired (older than 14 days) — 1" in view, (
        f"the expired count counts another wave's records even though the list is filtered by wave: {view!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Claim registry: who is running a stream right now.
#
# The board answers "what was handed over", the registry answers "to whom, and is it alive". Without
# a registry, the address is inferred from a branch name, and names lie: in wave 6's plan two streams
# were assigned the same branch, while the tabs worked on different ones, and one tab had already
# repurposed a folder for other work.
# ─────────────────────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────────────────────
# The registry AS A WHOLE: one read of every claim and four assertions over them.
#
# The suite's helpers used to walk the directory and take the FIRST record that matched by worktree
# folder, silently assuming there was only one match. While every folder had exactly one claim in
# the registry, that added up. Once superseded records appeared — the ones whose address another
# folder took — the first one to turn up stopped being the one being asked about: the suite would
# start pinning down the behaviour of a silenced record and miss the live one, silently and
# differently from run to run (nobody ever promised an order for a directory listing). Hence the
# rule: a helper takes the record that is NOT closed, and on an ambiguity it fails out loud — a
# silent choice here is the very defect the suite is there to catch.
#
# Beyond that, the whole-registry invariants live here. Before them not a single check in the suite
# looked at the registry as a whole: an edit to a tab's key could leave a ghost in it and fail no
# test at all. They are taken in the TAIL of every check (the `registry_invariants` fixture below),
# not as a separate test: a separate test would pin down one artificial scene, while what's needed
# is a watch over every announcing and releasing scenario — including ones nobody has written yet.
# ─────────────────────────────────────────────────────────────────────────────────────────────

# The succession field: the address's new owner names, in ITS OWN claim, the worktree folder that
# address was taken from. The mechanism writes it — but only where a takeover really happened:
# previous-version claims don't carry it at all, and a missing field reads as "there was no
# takeover", not as corruption.
#
# Next to the field the mechanism writes the MOMENT of the takeover (`taken_at`): it, and it alone,
# decides whether a takeover edge applies. A claim that began LATER than that moment isn't silenced
# by the edge — otherwise the previous folder could never announce again on the freed address, and
# returning an address by the same key would silence both sides at once.
#
# ‼️ The field name here and in the mechanism must match. Let them drift and the invariants stop
# seeing takeovers and go quiet in the very place they were built for: two records of one address
# start counting as lawful, and a silenced record as live.
TAKEN_FROM_FIELD = "taken_from"

# The moment of the takeover — the second half of the same point of agreement: without it a takeover
# edge can't be told from an eternal one, and the suite would drift from the mechanism in exactly the
# place where the mechanism stopped silencing fresh claims.
TAKEN_AT_FIELD = "taken_at"

# The list of this claim's PAST takeovers — the third half of the same point of agreement. The memory
# of a takeover lives in the taking folder's claim, and a folder has ONE claim: the moment that same
# folder took on the next stream, its file was rewritten, the edge vanished — and the abandoned record
# of the previous folder became the leader again, silently at that. The list carries the folder's
# previous claim's takeovers over into the new one, and each of its entries carries ITS OWN address:
# a past takeover's is not the one the claim has now.
#
# Inside a list entry live the same two names as the current takeover has (folder, moment), plus the
# wave and number of the address it was taken at.
PAST_TAKEOVERS_FIELD = "past_takeovers"

# The "who released it" trace in a record closed BY ADDRESS. Releasing your own doesn't write it —
# there the releaser and the owner are one and the same tab; here an outsider closed the record, and
# without the trace releasing an orphan would be indistinguishable from an honest release by the tab.
RELEASED_FROM_FIELD = "released_from"


def registry_dir(board: Path) -> Path:
    """This board's claim directory — the same place the tool itself looks for it."""
    return board.parent / "streams"


def folder_key(path: object) -> str:
    """The worktree folder in one shape: forward slashes, no trailing one, letter case ignored."""
    return str(path or "").replace("\\", "/").rstrip("/").lower()


def moment_of(raw: object) -> datetime | None:
    """A time out of a claim field; nothing honestly means "we don't know", not "the dawn of time".

    The difference matters: the decision whether a takeover edge applies rests on it. Let the suite
    invent a value and it would answer differently from the mechanism, pinning down the wrong thing.
    """
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def read_claim_json(path: Path) -> dict[str, object] | None:
    """Parses a claim file; unreadable and unparseable give "no record", not a crash.

    The encodings tried are the ones the tool tolerates: a claim arrives in UTF-16 too, and with a
    byte order mark. Corruption (an empty file, one cut off halfway, a claim of another version) and
    a file held by a neighbour are lawful states of the suite — it sets them up deliberately; helpers
    must not fall over on them, and corruption has its own checks in the suite.
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
    """One takeover of an address: at WHICH address, from whose folder and when it was taken.

    ‼️ The address is kept in the takeover itself, not read off the claim as it stands now: the claim
    may have moved to another number since, or taken on the next stream, while the edge is still
    about the address it was taken at.
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
    """One parsed claim of the registry: its file and its fields."""

    file: Path
    fields: dict[str, object]

    @property
    def worktree(self) -> str:
        return folder_key(self.fields.get("worktree"))

    @property
    def address(self) -> str:
        """The stream address — the way neighbours will call it."""
        return f"{self.fields.get('wave', '')}/{self.fields.get('stream', '')}".lower()

    @property
    def addressed(self) -> bool:
        """Whether the record has an address at all: another version's claim may have neither."""
        return bool(self.fields.get("wave")) and bool(self.fields.get("stream"))

    @property
    def released(self) -> bool:
        return str(self.fields.get("state", "")) == "released"

    @property
    def taken_from(self) -> str:
        """The folder this claim took the address from; empty — there was no takeover."""
        return folder_key(self.fields.get(TAKEN_FROM_FIELD))

    @property
    def taken_at(self) -> datetime | None:
        """The moment of the takeover; empty — a previous-version claim, it carries no moment."""
        return moment_of(self.fields.get(TAKEN_AT_FIELD))

    @property
    def claimed_at(self) -> datetime | None:
        """The moment of announcing; empty — the claim was written by hand or its time won't parse."""
        return moment_of(self.fields.get("claimed_at"))

    @property
    def takeovers(self) -> list[Takeover]:
        """EVERY takeover of this record: the current one and each past one from the list.

        ‼️ The parsing must match the mechanism word for word. Duplicates of ONE takeover (the same
        address from the same folder) collapse, keeping the later one by time: an earlier moment
        silences less than it should — a victim's claim filed between two takeovers would slip out
        from under the edge. An unknown moment counts as the latest: an edge without a moment applies
        unconditionally. But takeovers of one address from DIFFERENT folders never collapse: those
        are different edges, and losing any of them resurrects its own victim.
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
            # A takeover with no address is not a takeover: there's nothing to silence by it, and
            # two addressless neighbours would meet on an "address" made of two blanks.
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
    """The whole registry at once, in a stable order of file names."""
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
    """Records on one line: by file name and folder the owner will find them in the registry."""
    return ", ".join(sorted(f"{record.file.name} (folder {record.worktree})" for record in records))


def supersessions(records: list[ClaimRecord]) -> tuple[set[int], list[str]]:
    """Who in the registry is silenced by a takeover — and everything that doesn't add up in them.

    ‼️ The parsing must match the mechanism word for word: let it drift and the suite starts pinning
    down behaviour the mechanism doesn't have, and staying quiet where the mechanism raises alarm.

    An edge runs from the claim that took the address to the claim it was taken from: the claim names
    another folder, and both share one address. The edge applies BY TIME: it fails to apply exactly
    when it is PROVEN that the victim's claim began LATER than the moment of the takeover. The
    takeover itself has no moment (a claim of an unreleased interim version doesn't carry one) — then
    the moment the taking record was ANNOUNCED is used, exactly as the mechanism does. If there's no
    such moment either, or the victim's announcing time is unknown, the edge applies: not knowing must
    not resurrect a ghost.

    Mutual edges (a return ring that fitted inside one second) are separated by the full ordering key
    — announcing time, and on a tie the tree's path: the edge of the SENIOR record survives. The same
    rule settles a dispute over a stream number, and it is the same for every tab.

    ‼️ Records with NO address take no part in takeovers at all — just as the mechanism skips them.
    Otherwise two addressless neighbours meet on an "address" made of two blanks, and the suite sees
    an edge that isn't there.

    ‼️ A RELEASED claim holds a takeover just as an open one does. The suite used to skip it ("the tab
    is gone, so there's nobody to take it from"), and that was its own mistake: the stream moved,
    honestly finished the work and released — while the abandoned record in the previous folder became
    the leader again and kept the address alive. The suite would then call such a scene a number
    issued twice, even though the records are linked by a takeover. A takeover is an event in an
    ADDRESS'S history, and releasing doesn't undo it.
    """
    order = [(record.claimed_at or datetime.max, record.worktree) for record in records]
    # ‼️ The moment is taken from THE TAKEOVER ITSELF, not from the claim's current fields: the same
    # record's past takeover happened at another time and was about another address.
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
            # Several takers — we call the last one the leader: the address is with them now. An
            # unknown moment counts as the latest, just as the mechanism has it.
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
        # ‼️ A CIRCLE is when an address's records silence EACH OTHER: every one of them was silenced
        # by a record of that same address, itself silenced. The condition used to be just "every
        # record of the address is silenced", and back then that did mean a circle: only a neighbour
        # on the same address could silence an address's last record. With the memory of takeovers
        # that stopped being true — a record of ANOTHER address silences too (the folder took the
        # address, then took on the next stream) — and the assertion started shouting "circle" at the
        # most common lawful scene, the very one the change was made for. The scene "the address has
        # no leader left" has its own, fifth assertion, and it calls it by its proper name.
        if any(taken_by[index][0] not in here for index in here):
            continue
        faults.append(
            f"the takeover of address {address} runs in a circle — the records silence each other, "
            f"and not one leader is left: {names_of([record for _, record in group])}"
        )
    for j in sorted(superseded):
        alive = [records[i] for i, loser in drawn if loser == j and i not in superseded]
        if len(alive) > 1:
            faults.append(
                f"address {records[j].address} was taken from folder {records[j].worktree} twice "
                f"over ({names_of(alive)}) — which of these records leads, the registry doesn't say"
            )
    return superseded, faults


def by_address(records: list[ClaimRecord]) -> dict[str, list[tuple[int, ClaimRecord]]]:
    """Records by address — only those that have an address at all."""
    grouped: dict[str, list[tuple[int, ClaimRecord]]] = {}
    for index, record in enumerate(records):
        if record.addressed:
            grouped.setdefault(record.address, []).append((index, record))
    return grouped


def succession_edges(records: list[ClaimRecord]) -> list[tuple[int, int, datetime | None]]:
    """Takeover edges: who took an address from whom and WHEN — one per takeover per record.

    ‼️ An edge is built for every takeover — the current one and each past one — and the address is
    taken from THE TAKEOVER ITSELF. Otherwise the memory of a takeover lives exactly until the day
    that same folder is taken for the next stream: a folder has one claim file, it gets rewritten,
    and the abandoned record of the previous folder becomes the leader again.

    ‼️ A takeover with no moment — we take the moment the taking record was ANNOUNCED, exactly as the
    mechanism does. An unconditional edge locked the address behind the victim forever: however many
    times it announced again, the edge silenced each of its fresh claims, and the way out printed for
    it didn't work. Only an unreleased interim version could write a succession field with no moment,
    and it wrote both fields at the very same instant of announcing — so nothing is lost by the
    substitution.
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
    """Addresses this record ever held: the current one and each one it took before.

    They're needed for an address's HISTORY, not for silencing. A folder gets reused: a record that
    once took an address may be running a different stream today — but it hasn't gone anywhere from
    that address's history, and without it a chain of takeovers snaps right in the middle.
    """
    found = {record.address} if record.addressed else set()
    return found | {move.address for move in record.takeovers}


def succession_links(records: list[ClaimRecord]) -> set[tuple[int, int]]:
    """Pairs "taker — victim": a claim names another worktree folder, and both share one address.

    ‼️ Time isn't asked about here at all, and that's no oversight. Whether an edge applies is a
    separate question (`supersessions` settles it), while the LINK between the records stays forever:
    it is what tells one stream's history apart from one number issued to two different streams.

    ‼️ And for the same reason the victim is recognized here BY ITS WHOLE HISTORY of addresses, not by
    its current one: the middle folder of an A→B→C chain may have taken on another stream since, and
    C's link to A would otherwise snap — the suite would call one number issued twice where this is
    one history.
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
    """Each record's succession group root. Groups are counted over the WHOLE registry.

    ‼️ Over the whole one, not over the records of a single address: two records of an address may be
    linked THROUGH a third that is running a different stream today (the middle folder of a chain of
    takeovers, having taken on the next job). Were we to count groups within an address, such a chain
    would fall into two — and the suite would call one number issued twice where this is one history
    of one stream.
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
    """Five assertions about the registry as a whole — one line per violation.

    1. No more than one LEADING record per address (a leader is one that is neither closed nor
       superseded). Two leaders mean who gets a finding is decided by the directory listing's order.
    2. No more than one unclosed claim per worktree folder: the second one either erased the first or
       doubles that folder's stream as seen from outside.
    3. Every superseded record has exactly one superseding record, and the takeover doesn't run in a
       circle: a circle is when every record of the address is silenced and each was silenced by a
       record of THAT SAME address.
       ‼️ The superseding record may itself be superseded: an A→B→C chain of takeovers is lawful, both
       A and B are silenced in it, and one C leads. And ‼️ "every record of the address is silenced" is
       NOT in itself a circle: a record of another address silences too — the folder took the address,
       then took on the next stream. The fifth assertion speaks about that scene, and by its own name.
    4. No number ever taken in a wave gets issued a second time — neither after a release nor after a
       takeover: every record of one address must be ONE stream, linked by takeovers.
    5. An address that has claims open in their own files has at least one LEADING record. Zero
       leaders with live files is a tab that doesn't exist from the outside: it believes it is running
       the stream, while accepting a finding for that address will refuse.

    Records with neither an address nor a worktree folder don't count: that's what a claim of another
    version looks like, and it isn't treated as corruption — that is settled separately in a decision.
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
                f"address {address} is led by {len(found)} records at once ({names_of(found)}) — "
                "which of them a finding reaches is decided by the directory listing's order"
            )
    # ‼️ The mirror trouble of the same kind: an address has records open in their own files, and not
    # one leader. That's what a tab that doesn't exist from the outside looks like: its file is open,
    # it believes it is running the stream, while accepting a finding for that address will refuse and
    # the delivery hook will bring nothing. Nobody guarded this with a watch of its own: a takeover
    # circle is only caught when ALL the address's records are silenced, and the moment one of them is
    # released the scene went through in silence.
    for address, group in sorted(by_address(records).items()):
        if address in leading:
            continue
        orphans = [record for _, record in group if not record.released]
        if orphans:
            faults.append(
                f"address {address} has no leading record left, and there are unclosed claims "
                f"({names_of(orphans)}) — those tabs don't exist from the outside"
            )
    for here, found in sorted(per_folder.items()):
        if len(found) > 1:
            faults.append(
                f"folder {here} has {len(found)} unclosed claims at once ({names_of(found)}) — "
                "that folder's stream is doubled as seen from outside"
            )
    # ‼️ A number taken in a wave doesn't get issued a second time. Records of one address linked by
    # succession are ONE stream's history: a move, a chain of moves, an address returned by the same
    # key, and the previous folder announcing on an honestly released address. The link is looked at
    # here WITHOUT time: whether the edge still applies is a separate question, while the link stays
    # forever. Two unlinked groups on one address are exactly two different streams with one name.
    roots = succession_roots(records, succession_links(records))
    for address, group in sorted(by_address(records).items()):
        if address in doubled:
            continue
        if len({roots[index] for index, _ in group}) > 1:
            faults.append(
                f"number {address} was issued twice ({names_of([record for _, record in group])}): "
                "the records aren't linked by a takeover, so these are two streams with one address"
            )
    return faults


def assert_registry_invariants(board: Path) -> None:
    """Takes this board's registry invariants. Lists ALL violations, not the first one it meets."""
    folder = registry_dir(board)
    faults = registry_faults(folder)
    assert not faults, "the claim registry contradicts itself ({}):\n  • {}".format(
        folder, "\n  • ".join(faults)
    )


class RegistryWatch:
    """The registry watch in a check's tail — and an explicit refusal of it, named by its reason."""

    def __init__(self) -> None:
        self.waived = ""

    def waive(self, reason: str) -> None:
        """Waives the watch for THIS check. The reason is spelled out: no silent skipping."""
        self.waived = reason


@pytest.fixture(autouse=True)
def registry_invariants(tmp_path: Path) -> Iterator[RegistryWatch]:
    """The registry invariants are taken in the tail of EVERY check, not as a separate test.

    A separate test would pin down one artificial scene. What's needed instead is a watch over every
    announcing and releasing scenario — including the ones that get written after this change — or a
    future change will leave a ghost in the registry and fail no test at all. So the check runs by
    itself, over every registry set up inside the test's temporary folder, and there's no need to add
    it to a new scenario.

    There is exactly one way to opt out: ask for this fixture and say the reason out loud —
    `registry_invariants.waive("why")`. There is no silent skip: a broken invariant must be either
    fixed or named.
    """
    watch = RegistryWatch()
    yield watch
    if watch.waived:
        return
    for folder in sorted(tmp_path.rglob("streams")):
        if folder.is_dir():
            assert_registry_invariants(folder.parent / "board.jsonl")


# ‼️ The ONE ledger of waivers of the registry watch: check name → why the guard is off.
#
# It exists because the previous ledger lived as a comment on one of the checks and was WRONG: the
# comment insisted this was "the only place in the suite where the invariant is deliberately off",
# and there were six such places. The whole discipline of waivers rests on that bookkeeping — wrong
# bookkeeping is worse than none: the reader believes the comment and doesn't go looking at the rest.
#
# There are three kinds of place, and they must not be confused.
#   • Checks of SCENARIOS where the registry is deliberately contradictory and the mechanism is what's
#     under test. All of them assemble a doubled address BY HAND (`put_claim`) — that's what the
#     legacy of defect 1 looks like in a registry the change is being rolled out onto. The mechanism
#     itself can no longer double an address: the folder rule refuses BEFORE writing. Nor does the
#     mechanism take the legacy apart silently — display shouts about it in a loud line, and it is the
#     take-over key that clears it, by a human's decision.
#   • Checks of THE GUARD ITSELF: they assemble registries by hand and ask whether it catches them.
#     Waiving the watch there is needed for good — otherwise the guard would fall over on its own
#     laboratory scenes.
#   • Scenes where an address ENDED with no leading record, and that is the right outcome: the stream
#     moved and in the new folder released or moved on further, while the abandoned record of the
#     previous folder stayed open. The invariant "an address has a leading record" is deliberately
#     waived there — it exists precisely so that such scenes can't be set up SILENTLY; what's checked
#     is exactly that they are spoken about out loud: display shouts, accepting a finding refuses,
#     announcing doesn't report plain success.
#
# The list is checked by machine (a check below), so it can no longer go stale in silence.
WAIVED_SCENES: dict[str, str] = {
    # Scenarios: the registry is contradictory on purpose, the mechanism is what's under test.
    "test_show_keeps_one_order_on_the_same_registry": (
        "the invariant \"one leading record per address\": two records of one address are assembled "
        "by hand as the legacy of a defect — the completeness of display's order is checked on them"
    ),
    "test_show_shouts_about_a_doubled_address": (
        "the invariant \"one leading record per address\": the doubling is assembled by hand — what's "
        "checked is that display shouts about it instead of staying quiet"
    ),
    "test_adding_a_finding_to_a_doubled_address_says_it_may_reach_the_wrong_tab": (
        "the invariant \"one leading record per address\": the doubling is assembled by hand as the "
        "legacy of a defect — what's checked is that accepting a finding shouts too, not just display"
    ),
    "test_a_reclaim_that_names_only_the_wave_keeps_its_seniority": (
        "the invariant \"one leading record per address\": the rival for the same number is assembled "
        "by hand, and what's checked is exactly that the tab does NOT give the address up. The folder "
        "rule deliberately doesn't separate this pair: the number here is INHERITED from the tab's own "
        "previous record, that is, it stays issued, and the folder rule doesn't extend to an issued "
        "number — there a number may move, and the yield ring settles the dispute"
    ),
    # Checks of the guard itself: the registries are assembled by hand, the guard is the subject.
    "test_registry_invariants_catch_a_doubled_address": (
        "the watch is off entirely: the registry is contradictory on purpose, the guard is the subject"
    ),
    "test_registry_invariants_catch_every_broken_shape": (
        "the watch is off entirely: the registries are contradictory on purpose, the guard is the subject"
    ),
    "test_registry_invariants_pass_the_registries_the_tool_really_makes": (
        "the watch is off entirely: the registries are assembled by hand, what's checked is the "
        "guard's silence on lawful ones"
    ),
    "test_registry_invariants_catch_an_address_without_a_leader": (
        "the watch is off entirely: the registry is contradictory on purpose, the guard is the subject"
    ),
    "test_show_shouts_about_an_address_left_without_a_leader": (
        "the watch is off entirely: the registry is assembled by hand as exactly that scene — what's "
        "checked is that display shouts about it"
    ),
    # Scenes of an address's lawful end: no leading record is left, and that is the outcome checked.
    "test_a_finding_for_a_released_stream_is_refused_even_after_the_address_moved": (
        "the invariant \"an address has a leading record\": the stream moved and honestly released in "
        "the new folder, while the abandoned record of the previous folder stayed open — what's "
        "checked is that a finding for such an address is refused"
    ),
    "test_a_move_outlives_the_folder_taken_by_the_next_stream": (
        "the invariant \"an address has a leading record\": the stream moved, released, and the folder "
        "was taken for the next one — what's checked is that the previous folder's ghost doesn't "
        "become the leader"
    ),
    "test_the_answer_names_the_folder_where_the_address_really_went": (
        "the invariant \"an address has a leading record\": the stream moved along a chain and ended "
        "in the last folder — what's checked is which folder gets named to the victim"
    ),
    "test_a_dead_end_is_never_printed_as_the_way_out": (
        "the invariant \"an address has a leading record\": the stream moved and ended there — what's "
        "checked is that the way out printed to the victim works, instead of advising a key with "
        "nothing left to take"
    ),
    "test_the_invariant_never_calls_a_lawful_move_a_circle": (
        "the watch is off entirely: the registry is assembled by hand as exactly that scene — the "
        "guard is the subject, and what's checked is by what NAME it calls the scene"
    ),
    "test_the_older_copy_keeps_the_memory_of_past_moves_it_does_not_understand": (
        "the invariant \"an address has a leading record\": the same scene of an address's lawful end "
        "— what's checked is that a move by an older copy of the toolkit doesn't erase the memory"
    ),
}


def test_every_waiver_of_the_registry_watch_is_listed_in_the_ledger() -> None:
    """The ledger of waivers is checked by machine: the list above must match the suite's code.

    It used to be a comment, and the comment lied: it named one place, and there were six. A comment
    goes stale in silence — the reader believes it and doesn't go recounting — and the whole
    discipline rests on that bookkeeping: waiving an invariant is allowed as long as it is named and
    explained.

    So the list became the only one, and the reconciliation became mechanical: add a waiver and fail
    to write it down (or write one down and then drop the waiver) — the check fails and names the
    discrepancy by name.
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
        "the ledger of waivers has drifted from the suite's code — bookkeeping you can't trust is "
        "worse than none.\n  waived but not listed: {}\n  listed but never waived: {}"
    ).format(sorted(waived - listed) or "none", sorted(listed - waived) or "none")


def put_claim(folder: Path, file_name: str, **fields: object) -> Path:
    """Puts a claim of the given shape into the registry — that's how a scene the tool won't make gets
    assembled.

    The FILE's name is given first and positionally: the claim itself also has a "stream name" field,
    and were they called the same, the second would become impossible to express.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_name}.json"
    path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    return path


def open_claim(
    worktree: str, wave: str = "wave9", stream: str = "3", **extra: object
) -> dict[str, object]:
    """The fields of an unclosed claim — a blank for artificial registry scenes."""
    return {"wave": wave, "stream": stream, "worktree": worktree, "state": "open", **extra}


def test_registry_invariants_catch_a_doubled_address(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Two leading records of one address — the guard must FAIL and name the address.

    This is the proof of the guard itself: without it, it could go green in silence on any registry,
    and the whole idea of invariants would come down to lines of code that guard nothing.
    """
    registry_invariants.waive("the registry is contradictory on purpose — the guard is under test")
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "first", **open_claim(str(tmp_path / "first")))
    put_claim(folder, "second", **open_claim(str(tmp_path / "second")))

    with pytest.raises(AssertionError) as fault:
        assert_registry_invariants(board)
    assert "wave9/3" in str(fault.value), (
        f"the guard failed but didn't name the doubled address — nowhere to look for it: {fault.value}"
    )


def test_registry_invariants_catch_every_broken_shape(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """The other three assertions guard too, instead of just being listed.

    The scenes are assembled by hand: the tool doesn't make registries like these today — that's the
    point, the guard is set up against a change that starts making them.
    """
    registry_invariants.waive("the registries are contradictory on purpose — the guard is under test")
    scenes: dict[str, dict[str, dict[str, object]]] = {
        "two unclosed claims of one folder": {
            "first": open_claim("d:/tree", stream="3"),
            "second": open_claim("d:/tree", stream="4"),
        },
        "the number was issued a second time after a release": {
            "released": open_claim("d:/first", state="released"),
            "new": open_claim("d:/second"),
        },
        "the address was taken from one folder twice": {
            "previous": open_claim("d:/first"),
            "one": open_claim("d:/second", taken_from="d:/first"),
            "another": open_claim("d:/third", taken_from="d:/first"),
        },
        # ‼️ A circle of THREE records, not of two: a mutual pair is separated by the time rule itself
        # (the senior record's edge survives), and it is lawful — that's the ring of an address being
        # returned. But a circle where each one took from the next leaves the address with no leading
        # record at all: findings for it reach nobody, and staying quiet about that is not allowed.
        "the takeover runs in a circle of three": {
            "one": open_claim("d:/first", taken_from="d:/third"),
            "another": open_claim("d:/second", taken_from="d:/first"),
            "third": open_claim("d:/third", taken_from="d:/second"),
        },
    }
    for number, (scene, claims) in enumerate(scenes.items(), start=1):
        # A scene's name won't do as a path: it has a colon in it, and Windows won't make that folder.
        board = tmp_path / f"scene-{number}" / "board.jsonl"
        for name, fields in claims.items():
            put_claim(registry_dir(board), name, **fields)
        assert registry_faults(registry_dir(board)), (
            f"the guard stayed quiet on the scene \"{scene}\" — the registry contradicts itself while "
            "the check goes green"
        )


def test_registry_invariants_pass_the_registries_the_tool_really_makes(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """And the other side: on lawful registries the guard stays quiet instead of failing the work.

    A false alarm here costs more than a miss: it would fire in the tail of somebody else's check, and
    whoever wrote that check — and has nothing to do with the registry — would have to sort it out.
    """
    registry_invariants.waive("the registries are assembled by hand — the guard is under test, not the mechanism")
    scenes: dict[str, dict[str, dict[str, object]]] = {
        "claims of the current shape, no succession field at all": {
            "one": open_claim("d:/first"),
            "another": open_claim("d:/second", stream="4"),
        },
        "the stream was released, the number stayed with it": {
            "released": open_claim("d:/first", state="released"),
        },
        "a move: the address was taken, the previous record silenced": {
            "previous": open_claim("d:/first"),
            "new": open_claim("d:/second", taken_from="d:/first"),
        },
        "the folder was taken for another stream after the move": {
            "previous": open_claim("d:/first", stream="7"),
            "new": open_claim("d:/second", taken_from="d:/first"),
        },
        # The ring of return: both records took the address from each other. Seniority separates them
        # — one leader is left, and this is a lawful scenario, printed by the mechanism itself.
        "the address was returned by the same key": {
            "one": open_claim("d:/first", taken_from="d:/second"),
            "another": open_claim("d:/second", taken_from="d:/first"),
        },
        # A chain of moves: both the first and the second are silenced, the third leads.
        "moved twice in a row": {
            "first": open_claim("d:/first"),
            "second": open_claim("d:/second", taken_from="d:/first"),
            "third": open_claim("d:/third", taken_from="d:/second"),
        },
        # The address was honestly released, and the PREVIOUS folder announced on it again: its claim
        # began later than the takeover, so the old edge doesn't apply to it and nothing silences it.
        "the previous folder announced on the freed address": {
            "moved": open_claim(
                "d:/second",
                state="released",
                taken_from="d:/first",
                taken_at=hours_ago(2),
            ),
            "new": open_claim("d:/first", claimed_at=hours_ago(1)),
        },
        "a claim of another version, with no folder and no address": {
            "stranger": {"state": "open"},
            "ours": open_claim("d:/first"),
        },
    }
    for number, (scene, claims) in enumerate(scenes.items(), start=1):
        board = tmp_path / f"scene-{number}" / "board.jsonl"
        for name, fields in claims.items():
            put_claim(registry_dir(board), name, **fields)
        faults = registry_faults(registry_dir(board))
        assert not faults, f"a false alarm on the lawful scene \"{scene}\": {faults}"


def claim_of(board: Path, worktree: Path, *, only_open: bool) -> ClaimRecord:
    """The named tab's claim — exactly the one the check is asking about.

    ‼️ We don't take the first record that happens to match by folder. There may be several matches,
    and a silent choice would pin down the behaviour of a silenced record without noticing the live
    one. An ambiguity is a failure out loud: it means the mechanism left a ghost in the registry, and
    that's a finding, not an obstacle to the check.

    `only_open` — for when the live record is exactly what's wanted: editing a claim's fields,
    stripping a field, reading the address. A closed one must not be handed over there: the check is
    asking about the stream being run right now. Without it (asking for the claim file) an unclosed
    one still wins, but failing that the single closed one is handed over — the suite deliberately
    goes to a RELEASED stream's file too.
    """
    records = read_registry(registry_dir(board))
    superseded, _ = supersessions(records)
    here = folder_key(worktree)
    mine = [(i, record) for i, record in enumerate(records) if record.worktree == here]
    live = [record for i, record in mine if not record.released and i not in superseded]
    if len(live) > 1:
        raise AssertionError(
            f"folder {here} has {len(live)} unclosed claims at once ({names_of(live)}) — "
            "which of them the check means is not for the suite to decide"
        )
    if live:
        return live[0]
    closed = [record for _, record in mine]
    if only_open:
        found = f"; there are closed records: {names_of(closed)}" if closed else ""
        raise AssertionError(f"no unclosed claim for {here} in the registry{found}")
    if not closed:
        raise AssertionError(f"no claim for {here} in the registry")
    if len(closed) > 1:
        raise AssertionError(
            f"folder {here} has {len(closed)} closed claims at once ({names_of(closed)}) — "
            "which of them the check means is not for the suite to decide"
        )
    return closed[0]


def write_claim(record: ClaimRecord) -> None:
    """Puts an edited claim back into the very file it was read from."""
    record.file.write_text(json.dumps(record.fields, ensure_ascii=False), encoding="utf-8")


def claim(board: Path, cwd: Path, wave: str, stream: str, *extra: str) -> str:
    """Announces a stream for the tab working in folder `cwd`.

    ‼️ The stream's name goes through `-StreamName`, not `-Name`, and must not be renamed back.
    On 2026-08-22, under the short name, the value arrived on the build box SWAPPED OUT: instead of
    the name that was passed, the claim ended up holding `GIT_ALTERNATE_OBJECT_DIRECTORIES` — an
    environment variable's name — and the same happened for both Cyrillic and Latin names alike.
    Neighboring parameters of the same call (`-Wave`, `-Stream`, `-Tasks`) arrived intact, and on the
    development machine everything worked correctly. So the cause was the short parameter name
    itself, not the encoding and not the value.
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
    """A finding's address is the stream's number in the plan, not its branch name.

    The branch name is already different by the middle of the wave: what the plan named becomes the
    FOLDER's name, the branch is set up differently, and the folder gets repurposed for other work.
    The stream's number in the plan never changes — it's the only stable name there is.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-dispatch"
    claim(board, mine, "wave9", "3", "-StreamName", "Dispatcher")

    added = run_tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "the contract changed")
    assert "is live" in added, f"the report didn't see the stream's live claim: {added!r}"

    shown = run_deliver(board, mine, "Start", "s-claim")
    assert "the contract changed" in context_text(shown), (
        "a finding addressed by the stream's number didn't reach the tab that announced it"
    )


@needs_pwsh
def test_record_for_a_stream_that_has_not_started_waits_for_its_claim(tmp_path: Path) -> None:
    """A finding can be left for a stream that hasn't been opened yet.

    Without this, such a case wouldn't exist at all: an addressee with no worktree gets refused, and
    the finding's only option left would be the "Wave Loose Ends" section. But that's ordinary wave
    business — a neighbour opens tomorrow.
    """
    board = tmp_path / "board.jsonl"
    added = run_tool(board, "-Mode", "Add", "-To", "wave9/7", "-Title", "open it and take a look")
    assert "hasn't announced itself yet" in added, (
        f"the report didn't say the stream hasn't been opened yet: {added!r}"
    )

    later = tmp_path / "wave9-late"
    claim(board, later, "9", "7")
    shown = run_deliver(board, later, "Start", "s-late")
    assert "open it and take a look" in context_text(shown), (
        "an entry left for a stream in advance didn't arrive once it announced itself — that's its only chance"
    )


@needs_pwsh
def test_tool_refuses_a_record_for_a_released_stream(tmp_path: Path) -> None:
    """A finding can't be placed for a released stream: there's no one left to receive it.

    Such an entry used to be accepted (the tree was still there, after all) and stayed on the board
    forever — exactly the defect the board was built to fix, just one floor down.

    The wave is named — so it has a plan, and the "Wave Loose Ends" advice is appropriate. The claim
    deliberately doesn't name a plan file here: the announce command doesn't always name one, and the
    rule about having a plan doesn't rest on the file. A project with no waves is checked separately —
    there the advice reads differently.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-done"
    claim(board, mine, "wave9", "5")
    assert release(board, mine).returncode == 0, "releasing an empty stream didn't go through"

    done = tool(board, "-Mode", "Add", "-To", "wave9/5", "-Title", "late finding")
    assert done.returncode != 0, "the tool accepted a finding for a released stream"
    assert "RELEASED" in done.stderr, f"the refusal didn't name the reason: {done.stderr!r}"
    assert "Wave Loose Ends" in done.stderr, "the refusal didn't say where to put the finding"


@needs_pwsh
def test_release_refuses_while_the_inbox_is_not_empty(tmp_path: Path) -> None:
    """Release is the one place that asks "is everything that arrived actually handled".

    Otherwise an entry placed ten minutes before the tab closes reaches no one, ever: the liveness
    mark holds for half a day more, and the sender has already been told it succeeded.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-busy"
    claim(board, mine, "wave9", "2")
    mark = add(board, "wave9/2", "unread")

    refused = release(board, mine)
    assert refused.returncode != 0, "the stream released with a non-empty inbox"
    assert "unread" in refused.stderr, "the refusal didn't name what's left"

    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    assert release(board, mine).returncode == 0, "release still didn't go through after the inbox was cleared"


@needs_pwsh
def test_release_is_not_blocked_by_a_self_closing_acknowledgement(tmp_path: Path) -> None:
    """An "acknowledged" notice is no obstacle to release: it self-closes and carries no work.

    Otherwise a neighbour who closed your finding a minute before release locks up your release — and
    suggests moving into "Wave Loose Ends" a confirmation nobody needs there.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-leaving"
    taker = tmp_path / "wave9-taker"
    claim(board, author, "wave9", "1")
    claim(board, taker, "wave9", "2")
    mark = add(board, "wave9/2", "fix the contract", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=taker)

    done = release(board, author)
    assert done.returncode == 0, (
        f"release was blocked by a self-closing \"acknowledged\" notice: {done.stderr!r}"
    )


@needs_pwsh
def test_project_wide_broadcast_crosses_waves_and_closes_personally(tmp_path: Path) -> None:
    """The "every tab in the project" address is the second broadcast form, and its rules are the same.

    Forget it where only the single asterisk is checked, and a record would be silenced for everyone
    at once by the first stream to handle it, and the author would get a confirmation that a
    many-addressee record must never produce.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    same = tmp_path / "wave9-mate"
    other = tmp_path / "wave8-stranger"
    claim(board, author, "wave9", "1")
    claim(board, same, "wave9", "2")
    claim(board, other, "wave8", "1")

    out = run_tool(board, "-Mode", "Add", "-To", "**", "-Title", "project-wide announcement", cwd=author)
    mark = out.split("id ")[1].split(")")[0].strip()
    assert "every session in the project" in out, f"the report didn't say where the record will go: {out!r}"

    assert "project-wide announcement" in run_deliver(board, other, "Start", "s-cross"), (
        "the \"every tab in the project\" record didn't reach a tab of another wave — that's the whole point of it"
    )

    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=same)
    lines = board.read_text(encoding="utf-8").splitlines()
    closings = [json.loads(line) for line in lines if line.strip() and json.loads(line).get("done")]
    assert closings and closings[-1].get("by"), (
        "closing a many-addressee record turned out global — the first to handle it silenced it for everyone"
    )
    assert not any("acknowledged" in line for line in lines), (
        "the author got a confirmation for a many-addressee record — there would be as many as there "
        "are addressees"
    )


@needs_pwsh
def test_project_wide_broadcast_goes_stale_like_the_wave_one(tmp_path: Path) -> None:
    """The "every tab in the project" address has a shelf life too — otherwise the record lives forever.

    It arrives at EVERY new worktree, survives compaction, and is paid for in context by tabs that
    have nothing to do with its work. That's exactly the hole the shelf life was built to close.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="wide0001", at=now_minus(40), to="**", title="ancient announcement") + "\n",
        encoding="utf-8",
    )
    view = run_tool(board, "-Mode", "Show")
    assert "ancient announcement" not in view, "an expired record is still shown as open"
    assert "expired" in view, f"the display stayed silent about the expired record: {view!r}"

    tab = tmp_path / "wave9-fresh"
    tab.mkdir()
    assert "ancient announcement" not in run_deliver(board, tab, "Start", "s-wide"), (
        "an expired \"every tab in the project\" record still travels to tabs"
    )

    forced = tmp_path / "wave9-forced"
    claim(board, forced, "wave9", "8")
    add(board, "wave9/8", "will stay unread")
    done = release(board, forced, "-Force")
    assert done.returncode == 0, "a deliberate forced release didn't go through"
    assert "no one will get them" in done.stdout, (
        "a forced release stayed silent about what's left behind — something abandoned silently is "
        "indistinguishable from something handled"
    )


@needs_pwsh
def test_broadcast_stays_inside_its_own_wave(tmp_path: Path) -> None:
    """`*` — every stream of ITS OWN wave, not all two dozen trees in the project.

    Trees of neighbouring waves pay for someone else's finding in context on every turn and have to
    close it personally — even though it has nothing to do with their work at all.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    same = tmp_path / "wave9-mate"
    other = tmp_path / "wave8-stranger"
    claim(board, author, "wave9", "1")
    claim(board, same, "wave9", "2")
    claim(board, other, "wave8", "1")

    run_tool(board, "-Mode", "Add", "-To", "*", "-Title", "wave-wide announcement", cwd=author)

    assert "wave-wide announcement" in context_text(run_deliver(board, same, "Start", "s-same")), (
        "the \"everyone\" record didn't reach a neighbouring stream of its own wave"
    )
    assert "wave-wide announcement" not in run_deliver(board, other, "Start", "s-other"), (
        "the \"everyone\" record reached a tab of another wave — it pays for it in context for nothing"
    )


@needs_pwsh
def test_streams_answers_whose_task_it_is(tmp_path: Path) -> None:
    """"Whose piece of work is this" is a question that today has no answer anywhere.

    A tab is tempted to take a neighbouring task, offers it to the owner, and the owner has no way of
    knowing it was planned for another stream, so they confirm it. The registry answers this mechanically.
    """
    board = tmp_path / "board.jsonl"
    claim(
        board,
        tmp_path / "wave9-dispatch",
        "wave9",
        "3",
        "-StreamName",
        "Dispatcher",
        "-Tasks",
        "10-13",
    )

    mine = run_tool(board, "-Mode", "Streams", "-Task", "12")
    assert "wave9/3" in mine and "Dispatcher" in mine, (
        f"the registry didn't name the stream running task 12: {mine!r}"
    )

    nobody = run_tool(board, "-Mode", "Streams", "-Task", "20")
    assert "isn't claimed by any stream" in nobody, (
        "about a task nobody owns, the registry stayed just as silent as about one it does own — the "
        "answer doesn't tell them apart"
    )


@needs_pwsh
def test_stream_address_is_not_confused_with_a_branch_name(tmp_path: Path) -> None:
    """`feat/wave6-compute` is a branch, `wave6/3` is a stream. They must not be confused.

    Address parsing settles it by shape: the right side of the slash must hold a stream NUMBER, not
    a word. Otherwise a branch `feat/...` would parse as stream `feat`, and the finding would go into
    the void. On the left, the wave name is matched WHOLE: `wave6-compute` is a folder name, not wave
    `wave6`, and it must not be usable as an address even when wave `wave6` exists in the registry.
    """
    board = tmp_path / "board.jsonl"
    accepted = run_tool(board, "-Mode", "Add", "-To", "wave6/3", "-Title", "for the stream")
    assert "hasn't announced itself yet" in accepted, "the \"wave/stream\" address wasn't parsed as a stream"

    branchy = tool(
        board, "-Mode", "Add", "-To", "feat/never-existed", "-Title", "for the branch", known=True
    )
    assert branchy.returncode != 0, (
        "a branch name that's in neither the registry nor the worktrees was accepted as a stream address"
    )

    claim(board, tmp_path / "wave6-compute", "wave6", "3")
    foldery = tool(board, "-Mode", "Add", "-To", "wave6-compute/3", "-Title", "for the folder", known=True)
    assert foldery.returncode != 0, (
        "the folder name \"wave6-compute/3\" was accepted as an address for stream wave6/3 — the "
        "finding would have gone to a stranger"
    )


def patch_claim(board: Path, worktree: Path, **fields: object) -> None:
    """Patches a tab's UNCLOSED claim — sets what the test can't compute on its own.

    The list of touched files comes from git in the real tool, and test folders aren't repositories:
    substitute the list by hand and set a fresh timestamp so the guard doesn't recompute it.

    ‼️ The record is picked by `claim_of`, not by the first match on the folder: patching a silenced
    record would pin down a ghost's behaviour, and the check would go green on a broken mechanism.
    """
    record = claim_of(board, worktree, only_open=True)
    record.fields.update(fields)
    write_claim(record)


@needs_pwsh
def test_tab_is_told_that_a_neighbour_edits_the_same_files(tmp_path: Path) -> None:
    """An overlap in edits is visible BEFORE the merge conflict — and before a neighbour's task gets taken.

    A tab is tempted to grab a neighbouring piece: it offers it to the owner, the owner has no way of
    knowing the piece was planned for another stream, and confirms it. Shared files are the only sign
    of this a machine can see on its own, without relying on discipline.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-mine"
    neighbour = tmp_path / "wave9-neighbour"
    claim(board, mine, "wave9", "1", "-StreamName", "Pipeline")
    claim(board, neighbour, "wave9", "5", "-StreamName", "Storefronts")
    now = datetime.now().isoformat(timespec="seconds")
    patch_claim(board, mine, files=["packages/core/pipe.py", "docs/readme.md"], files_at=now)
    patch_claim(board, neighbour, files=["packages/core/pipe.py"], files_at=now)

    first = context_text(run_deliver(board, mine, "Prompt", "s-overlap"))
    assert "editing the same files" in first, (
        f"the tab didn't learn about a neighbour editing the same files: {first!r}"
    )
    assert "wave9/5" in first and "Storefronts" in first, "the warning didn't name whose stream it is"
    assert "packages/core/pipe.py" in first, "the warning didn't name the shared file"

    second = run_deliver(board, mine, "Prompt", "s-overlap")
    assert "editing the same files" not in second, (
        "the warning repeats every turn — the tab's context gets resent on every turn"
    )


@needs_pwsh
def test_shared_plan_file_is_not_counted_as_an_overlap(tmp_path: Path) -> None:
    """Every stream edits the wave plan by how the work is set up — that's not an overlap, it's normal.

    A guard that shouts about every shared plan gets ignored along with everything else it says.

    The plans folder here is the STUB PROJECT'S OWN, from its profile: hardcoded into the code, it
    would make this the norm in exactly one repository, while in every other one the wave plan would
    land in the overlaps — meaning the warning would fire on every unrelated turn.
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

    assert "editing the same files" not in run_deliver(board, mine, "Prompt", "s-plan"), (
        "a shared wave plan is passed off as a work overlap — the guard would get noisy and get ignored"
    )


@needs_pwsh
def test_a_plan_folder_of_another_project_is_an_ordinary_overlap(tmp_path: Path) -> None:
    """The profile is what makes a place shared by design — not a folder name from a neighbouring project.

    Keep the folder hardcoded, and in another project the guard would stay silent about a real work
    overlap, just because it happened to land in a folder with the same name.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-three"
    neighbour = tmp_path / "wave9-four"
    claim(board, mine, "wave9", "3", "-StreamName", "Pipeline")
    claim(board, neighbour, "wave9", "4", "-StreamName", "Storefronts")
    for tab in (mine, neighbour):
        (tab / ".parallel-streams.md").write_text(profile_text(), encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    alien = f"{ALIEN_PLANS}2026-08-13-server-wave9.md"
    patch_claim(board, mine, files=[alien], files_at=now)
    patch_claim(board, neighbour, files=[alien], files_at=now)

    first = context_text(run_deliver(board, mine, "Prompt", "s-alien-plan"))
    assert "editing the same files" in first, (
        f"an overlap in a folder the profile never named as the plans folder is hidden: {first!r}"
    )


@needs_pwsh
def test_author_learns_that_the_finding_was_taken_into_account(tmp_path: Path) -> None:
    """The finding's author learns its fate — otherwise they never learn it at all.

    The finding's fate decides whether to set up a task for it in "Wave Loose Ends". The notice
    self-closes on display: it exists to take the question off the author's hands, not to add work
    closing records.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "wave9-author"
    taker = tmp_path / "wave9-taker"
    claim(board, author, "wave9", "1")
    claim(board, taker, "wave9", "2")

    mark = add(board, "wave9/2", "fix the contract", cwd=author)
    run_tool(board, "-Mode", "Done", "-Id", mark, cwd=taker)

    first = context_text(run_deliver(board, author, "Start", "s-ack"))
    assert "acknowledged" in first and "fix the contract" in first, (
        f"the author didn't learn their finding was handled: {first!r}"
    )

    second = run_deliver(board, author, "Start", "s-ack-again")
    assert "acknowledged" not in second, (
        "the notice arrived again — it must self-close, not require closing by hand"
    )


@needs_pwsh
def test_owner_sees_stuck_records_only_in_the_main_folder(tmp_path: Path, wave_repo: Path) -> None:
    """A stuck record surfaces for the owner — otherwise no one sees it at all.

    The addressee is released, silent, or doesn't exist: the record sits open, and the sender's
    already been told it succeeded. This is the one place where the mechanism admits delivery didn't happen.
    """
    board = tmp_path / "board.jsonl"
    board.write_text(
        board_line(id="lost0001", at=now_minus(3), to="wave9/44", title="no one to receive it") + "\n",
        encoding="utf-8",
    )

    owner = context_text(run_deliver(board, wave_repo, "Start", "s-owner"))
    assert "stuck" in owner, f"the owner didn't see the stuck record: {owner!r}"
    assert "no one to receive it" in owner, "the summary didn't name the finding itself"
    assert "Wave Loose Ends" in owner, "the summary didn't say what to do with the finding"

    inside = run_deliver(board, here_of(wave_repo), "Start", "s-inside")
    assert "stuck" not in inside, (
        "a summary of someone else's stuck records reached a worktree — that's noise in the stream's context"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# A project with no waves: the wave gets substituted by itself.
#
# The channel gets installed in projects with no waves at all too, and a wave may have no plan even
# where waves exist. A tab used to run into a dead end: it couldn't announce itself (the tool
# required a wave), and release advised writing a line into a plan section that doesn't exist.
#
# The texts a human sees were approved by the owner VERBATIM, so they're checked as whole lines, not
# by fragment: an accidentally rewritten wording fails the check right away. That's their protection —
# there's nowhere else to get it from.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def today_wave() -> str:
    """The wave name the tool substitutes itself when none was named — today's date."""
    return datetime.now().strftime("%Y-%m-%d")


def claim_bare(board: Path, cwd: Path, *extra: str) -> str:
    """An announcement with no wave and no stream number — how a tab announces in a project with no waves."""
    cwd.mkdir(parents=True, exist_ok=True)
    return run_tool(board, "-Mode", "Claim", *extra, cwd=cwd)


def said(text: str) -> list[str]:
    """Output lines with indentation stripped — approved wordings are checked against these whole."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def strip_claim_field(board: Path, worktree: Path, field: str) -> None:
    """Removes a field from an UNCLOSED claim — what a claim filed by an earlier version looks like."""
    record = claim_of(board, worktree, only_open=True)
    record.fields.pop(field, None)
    write_claim(record)


@needs_pwsh
def test_claim_without_a_wave_opens_one_by_todays_date(tmp_path: Path) -> None:
    """No wave was named and there's nowhere to take one from — the tool substitutes it itself, by today's date.

    A refusal here used to be a dead end: without announcing, a stream is indistinguishable from
    "never opened" from the outside, and a human has nowhere to take a wave number from in a project
    with no waves.
    """
    board = tmp_path / "board.jsonl"
    out = claim_bare(board, tmp_path / "solo")
    today = today_wave()

    assert f"Stream {today}/1 announced for this session" in out, (
        f"announcing with no wave didn't substitute one by date: {out!r}"
    )
    assert (
        f"Wave not named — taken from today's date. Address for neighbours: {today}/1."
        in said(out)
    ), f"the line about the substituted wave was rewritten or lost: {out!r}"
    assert "Stream number not named — issued the next free one: 1." in said(out), (
        f"the tab wasn't told its stream number was issued by the tool itself: {out!r}"
    )


@needs_pwsh
def test_second_tab_joins_the_work_that_is_already_running(tmp_path: Path) -> None:
    """A second tab joins the SAME wave, rather than starting its own.

    Start its own, and neighbours would never see each other — not in the stream map, not in
    addresses — and the whole coordination channel would fall apart into single-tab waves.
    """
    board = tmp_path / "board.jsonl"
    claim_bare(board, tmp_path / "first")
    out = claim_bare(board, tmp_path / "second")
    today = today_wave()

    assert f"Stream {today}/2 announced for this session" in out, (
        f"the second tab didn't join the work already under way: {out!r}"
    )
    assert (
        f"Wave not named — session joined work already under way, {today}, streams in it: 2."
        in said(out)
    ), f"the line about joining was rewritten or lost: {out!r}"
    assert "Stream number not named — issued the next free one: 2." in said(out), (
        f"the second tab wasn't issued the next free number: {out!r}"
    )


@needs_pwsh
def test_a_named_wave_is_never_joined_automatically(tmp_path: Path) -> None:
    """‼️ A tab never auto-joins a named wave.

    A wave from a plan has its stream numbers announced IN THE PLAN. Joining it, a tab would take
    someone else's number, and half the wave's findings would go to the wrong place — silently, with
    a cheerful report to both sides.
    """
    board = tmp_path / "board.jsonl"
    claim(board, tmp_path / "wave6-compute", "wave6", "4")
    out = claim_bare(board, tmp_path / "loner")

    assert "wave6" not in out, (
        f"the tab joined the plan's wave and took a number in it: {out!r}"
    )
    assert f"Stream {today_wave()}/1 announced for this session" in out, (
        f"its own date-named wave wasn't set up: {out!r}"
    )


@needs_pwsh
def test_an_old_claim_without_the_flag_is_treated_as_a_named_wave(tmp_path: Path) -> None:
    """A claim from an earlier version carries no "wave was self-supplied" flag — and can't be
    joined: it was made with a named wave, where numbers come from the plan.

    Along the way this also checks the address of a wave named by a word: it exists in the registry,
    and a finding can be addressed to it the same way as to a wave from a plan.
    """
    board = tmp_path / "board.jsonl"
    elder = tmp_path / "elder"
    claim(board, elder, "sprint-alpha", "1")
    strip_claim_field(board, elder, "wave_auto")

    addressed = run_tool(board, "-Mode", "Add", "-To", "sprint-alpha/1", "-Title", "by word")
    assert "is live" in addressed, (
        f"the address of a wave named by a word didn't parse, even though it's in the registry: {addressed!r}"
    )

    out = claim_bare(board, tmp_path / "newcomer")
    assert "sprint-alpha" not in out, (
        f"the tab joined an old-format claim that carries no flag at all: {out!r}"
    )
    assert f"Stream {today_wave()}/1 announced for this session" in out, (
        f"its own date-named wave wasn't set up: {out!r}"
    )


@needs_pwsh
def test_a_finding_reaches_a_stream_of_a_date_named_wave(tmp_path: Path) -> None:
    """A finding reaches a stream even in a wave named by date.

    Address parsing used to accept only `wave<number>` or a bare number on the left, so
    `2026-08-24/2` never parsed at all: the finding landed on the board and reached no one — silently,
    like everything this mechanism fixes.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "first"
    mate = tmp_path / "second"
    claim_bare(board, author)
    claim_bare(board, mate)
    today = today_wave()

    added = run_tool(
        board, "-Mode", "Add", "-To", f"{today}/2", "-Title", "the contract changed", cwd=author
    )
    assert "is live" in added, f"the report didn't see the neighbour's live claim: {added!r}"

    shown = run_deliver(board, mate, "Start", "s-date-wave")
    assert "the contract changed" in context_text(shown), (
        "a finding addressed to a date-named wave's stream didn't reach the tab running it"
    )


@needs_pwsh
def test_release_without_a_plan_sends_the_result_to_the_owner(tmp_path: Path) -> None:
    """No plan — release doesn't send you off to write a line into its section.

    Advising a line be written into a file that doesn't exist is a dead end: the tab can neither
    carry it out nor figure out what to do instead.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "no-plan"
    claim_bare(board, mine)

    done = release(board, mine)
    assert done.returncode == 0, done.stderr
    assert (
        "No wave plan — nowhere to write a stream line; the summary goes in your reply to the owner."
        in said(done.stdout)
    ), f"the line about having no plan was rewritten or lost: {done.stdout!r}"
    assert (
        'Last step — a line for your stream in the wave plan\'s "Stream status" section.'
        not in said(done.stdout)
    ), "release with no plan still sent the tab to write a line into the plan"
    assert f"Stream {today_wave()}/1 released. Findings will no longer be accepted for it." in said(
        done.stdout
    ), f"the release-with-no-plan line was rewritten or lost: {done.stdout!r}"


@needs_pwsh
def test_release_of_a_named_wave_keeps_the_line_in_the_plan(tmp_path: Path) -> None:
    """The wave is named — release texts are unchanged, word for word, and need no plan file for it.

    ‼️ The plan rule rests on exactly ONE thing: was the wave substituted by itself. Rest it on a
    plan file named in the claim instead, and this project's waves would see "no plan" texts, because
    the announce command doesn't always name a plan file. So the plan is deliberately left unnamed in
    the claim here.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "wave9-planned"
    claim(board, mine, "wave9", "1")

    done = release(board, mine)
    assert done.returncode == 0, done.stderr
    assert (
        'Last step — a line for your stream in the wave plan\'s "Stream status" section.'
        in said(done.stdout)
    ), f"a named wave's stream lost its previous final release line: {done.stdout!r}"
    assert (
        "Stream wave9/1 released. Findings will no longer be accepted for it — their place is now "
        "the Wave Loose Ends."
        in said(done.stdout)
    ), f"a named wave's stream had its release line rewritten: {done.stdout!r}"


@needs_pwsh
def test_a_wave_taken_from_the_plan_name_is_not_an_invented_one(tmp_path: Path) -> None:
    """A wave taken from the plan's file name is a NAMED one: its stream numbers come from the plan,
    texts unchanged.

    Mix it up with a substituted one, and a neighbour's tab would join the plan's wave, taking someone
    else's number in it.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "planned"
    mine.mkdir()
    plan = mine / "2026-08-24-wave9.md"
    plan.write_text("# wave 9 plan\n", encoding="utf-8")

    out = claim_bare(board, mine, "-Plan", str(plan))
    assert "Wave taken from the plan file name." in said(out), (
        f"the line about the wave taken from the plan name was rewritten or lost: {out!r}"
    )
    assert "Stream wave9/1 announced for this session" in out, (
        f"the wave wasn't taken from the plan's file name: {out!r}"
    )

    later = claim_bare(board, tmp_path / "loner")
    assert "wave9" not in later, f"the tab joined the wave taken from the plan's file name: {later!r}"

    done = release(board, mine)
    assert done.returncode == 0, done.stderr
    assert (
        'Last step — a line for your stream in the wave plan\'s "Stream status" section.'
        in said(done.stdout)
    ), f"a stream with a plan lost its previous final release line: {done.stdout!r}"


@needs_pwsh
def test_forced_release_without_a_plan_names_the_leftovers_to_the_owner(tmp_path: Path) -> None:
    """A forced release with no plan names what's left in the inbox to the owner, not "Wave Loose Ends"."""
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "forced-no-plan"
    claim_bare(board, mine)
    add(board, f"{today_wave()}/1", "unread")

    done = release(board, mine, "-Force")
    assert done.returncode == 0, done.stderr
    assert (
        "‼️ Released with a non-empty inbox: 1 entries left — no one will get them, name them in "
        "your reply to the owner." in said(done.stdout)
    ), f"the line about a forced release with no plan was rewritten or lost: {done.stdout!r}"
    assert "Wave Loose Ends" not in done.stdout, (
        "a forced release sent the tab to a plan section this project doesn't have"
    )


@needs_pwsh
def test_forced_release_of_a_named_wave_keeps_the_tails_line(tmp_path: Path) -> None:
    """A forced release in a wave with a plan keeps the previous text: what's left moves into "Wave Loose Ends"."""
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "forced-wave9"
    claim(board, mine, "wave9", "2")
    add(board, "wave9/2", "unread")

    done = release(board, mine, "-Force")
    assert done.returncode == 0, done.stderr
    assert (
        "‼️ Released with a non-empty inbox: 1 entries left — no one will get them, move them into "
        "the Wave Loose Ends." in said(done.stdout)
    ), f"the line about a forced release in a wave with a plan was rewritten: {done.stdout!r}"


@needs_pwsh
def test_stuck_summary_without_a_plan_points_at_the_owner(tmp_path: Path, wave_repo: Path) -> None:
    """The stuck summary for the owner follows the same rule as every other piece of advice.

    This is the one place where the mechanism admits delivery didn't happen. Pointing to a plan
    section that doesn't exist would leave the finding with no place at all.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "gone"
    claim_bare(board, mine)
    assert release(board, mine).returncode == 0, "releasing an empty stream didn't go through"
    board.write_text(
        board_line(id="lost0002", at=now_minus(3), to=f"{today_wave()}/1", title="no one to receive it")
        + "\n",
        encoding="utf-8",
    )

    owner = context_text(run_deliver(board, wave_repo, "Start", "s-owner-no-plan"))
    assert "stuck" in owner, f"the owner didn't see the stuck record: {owner!r}"
    assert "There is no wave plan — name the finding in your reply to the owner." in said(owner), (
        f"the summary sent the owner to a plan section that doesn't exist: {owner!r}"
    )
    assert "Wave Loose Ends" not in owner, "the summary still carried the advice about a plan section"


@needs_pwsh
def test_release_without_a_plan_names_the_leftovers_to_the_owner(tmp_path: Path) -> None:
    """A refusal over a non-empty inbox also doesn't send the tab to a plan section that doesn't exist."""
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "busy-no-plan"
    claim_bare(board, mine)
    add(board, f"{today_wave()}/1", "unread")

    refused = release(board, mine)
    assert refused.returncode != 0, "the stream released with a non-empty inbox"
    assert (
        "Not your work — name it in your reply to the owner and release with -Force."
        in said(refused.stderr)
    ), f"the line about unrelated work with no plan was rewritten or lost: {refused.stderr!r}"
    assert "Wave Loose Ends" not in refused.stderr, (
        "the refusal sent the tab to a plan section this project doesn't have"
    )


@needs_pwsh
def test_a_finding_for_a_released_stream_without_a_plan_goes_to_the_owner(tmp_path: Path) -> None:
    """A released stream with no plan: the finding is named to the owner, not carried into "Wave Loose Ends".

    A refusal must say what to do with the finding. A plan section in a project with no waves is a
    pointer into nothing, and after it the finding lands nowhere at all.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "no-plan-done"
    claim_bare(board, mine)
    assert release(board, mine).returncode == 0, "releasing an empty stream didn't go through"

    done = tool(board, "-Mode", "Add", "-To", f"{today_wave()}/1", "-Title", "late finding")
    assert done.returncode != 0, "the tool accepted a finding for a released stream"
    assert "There is no wave plan — name the finding in your reply to the owner." in said(done.stderr), (
        f"the line about a finding with no plan was rewritten or lost: {done.stderr!r}"
    )
    assert "Wave Loose Ends" not in done.stderr, (
        "the refusal sent the tab to a plan section this project doesn't have"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Dispute over a stream number: a wave's tabs open ALL AT ONCE.
#
# That's not a rare case, it's the ordinary course of work: a plan gets split into streams and tabs
# get opened one after another within a single minute. Before these checks, all of them announced as
# stream #1 of one wave — the registry snapshot was read once, BEFORE writing your own claim, and the
# next free number came out the same for everyone. A finding for such a stream reached all three of
# them, and "whose piece of work is this" answered incorrectly.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def claim_file_of(board: Path, worktree: Path) -> Path:
    """The named tab's claim file: the unclosed one, and failing that the single closed one.

    The closed one is handed over because the suite deliberately goes to a RELEASED stream's file
    too — holding it open, corrupting it, deleting it. But as long as the folder has a live record,
    it is always that one that's meant.
    """
    return claim_of(board, worktree, only_open=False).file


def address_of(board: Path, worktree: Path) -> str:
    """The address of the stream the tab is running NOW — the way neighbours will call it."""
    fields = claim_of(board, worktree, only_open=True).fields
    return f"{fields['wave']}/{fields['stream']}"


@needs_pwsh
def test_tabs_started_at_once_do_not_share_one_stream_number(tmp_path: Path) -> None:
    """Six tabs launched on a shared time signal must end up with six different addresses.

    That's exactly how they get opened: a plan gets split into streams — and the tabs are opened all
    at once. A shared address between them is silent: a finding goes to all of them at once, and
    "whose piece of work is this" gets the wrong answer, with neither side finding out.

    Six tabs, not three: with three the defect isn't caught on anywhere near every run, and a wave
    does get split into six streams — the more there are, the tighter the claims bunch up and the
    more often numbers collide.
    """
    assert pwsh
    board = tmp_path / "board.jsonl"
    tabs = [tmp_path / f"atonce-{number}" for number in range(6)]
    for tab in tabs:
        tab.mkdir()
    # A shared signal: everyone waits for the same moment and starts from it, not one after another.
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
        # Name the failure reason right away: without it a crash reads as "something went wrong",
        # and sorting it out means reproducing a rare race all over again.
        assert process.returncode == 0, (
            f"announcing failed: {err.decode('utf-8', 'replace')}{out.decode('utf-8', 'replace')}"
        )

    addresses = [address_of(board, tab) for tab in tabs]
    assert len(set(addresses)) == len(tabs), (
        f"tabs started at the same moment shared one address: {addresses}"
    )


# One process makes one entry into the critical section under the registry lock. Kept as a separate
# file, not an inline command string: the entry must look exactly like it does in the tool — the same
# shared toolkit file, the same two lock functions wrapping the work.
LOCK_STAND = """#Requires -Version 7
param([string]$Lib, [string]$Dir, [string]$Marks, [string]$StartAt, [int]$HoldMs)
. $Lib
# A shared time signal: without it the processes drift apart during shell warm-up, and on an
# implementation with NO lock at all they might never even meet — the guard would stay silent
# exactly where it's supposed to shout.
$moment = [datetime]::Parse($StartAt, [cultureinfo]::InvariantCulture)
while ((Get-Date) -lt $moment) { Start-Sleep -Milliseconds 2 }
$handle = Enter-RegistryLock -Dir $Dir
if (-not $handle) { exit 2 }
$mark = Join-Path $Marks "inside-$PID"
try {
    [System.IO.File]::WriteAllText($mark, "$PID")
    # How many of us are inside right now. More than one means mutual exclusion isn't working.
    $together = @(Get-ChildItem -LiteralPath $Marks -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'inside-*' })
    if ($together.Count -gt 1) {
        # A violator writes ITS OWN file: a shared log would need dividing up, and that's a lock
        # all over again.
        [System.IO.File]::WriteAllText((Join-Path $Marks "violation-$PID"),
            "seen inside: $($together.Count)")
    }
    Start-Sleep -Milliseconds $HoldMs
} finally {
    Remove-Item -LiteralPath $mark -Force -ErrorAction SilentlyContinue
    # Make sure the mark is GONE before releasing the lock: deleting a file antivirus happens to be
    # holding at that instant gets deferred by Windows — the name stays visible, and the next one in
    # would count two inside for no reason.
    $till = (Get-Date).AddSeconds(5)
    while ((Test-Path -LiteralPath $mark) -and (Get-Date) -lt $till) { Start-Sleep -Milliseconds 10 }
    Exit-RegistryLock -Handle $handle
}
"""

# A stand with two roles: one holds the lock, one tries to take it away. The wait limit is swapped
# for a short one — otherwise the "while held, the second one isn't let in" check would take half a
# minute. The swap works because the function name is resolved at call time, not when the file loads.
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
# The stand must not litter the error stream with a story about waiting.
function Get-RegistryLockSpeakAfterSeconds { return 3600 }
switch ($Role) {
    'hold' {
        # Took the lock and sits inside. The mark is written AFTER taking it: its appearance is how
        # the check learns the holder is already inside, without guessing by elapsed time.
        $handle = Enter-RegistryLock -Dir $Dir
        if (-not $handle) { exit 2 }
        [System.IO.File]::WriteAllText($Signal, "holding")
        if ($Until) {
            # Released on the check's SIGNAL, not by the clock: otherwise the check's safety margin
            # is the gap between "how long we hold it" and "how fast the neighbouring process starts
            # up", and that gap vanishes on a loaded machine. The clock stays only as a safeguard
            # against hanging.
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
        # Tries to get in. "no" in the file means "wasn't let in" — that's what we wait for while
        # the lock is held.
        $handle = Enter-RegistryLock -Dir $Dir
        [System.IO.File]::WriteAllText($Signal, $(if ($handle) { 'entered' } else { 'no' }))
        Exit-RegistryLock -Handle $handle
    }
}
"""


def lock_stand_args(stand: Path, registry: Path, **extra: object) -> list[str]:
    """The shared part of launching any lock stand."""
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
    """Waits for a non-empty file to appear — how a stand reports it's already inside."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.02)
    raise AssertionError(f"the stand never marked itself in {path} within {seconds}s")


@needs_pwsh
def test_two_tabs_are_never_inside_the_claim_at_once(tmp_path: Path) -> None:
    """Exactly one is ever inside the announcement's critical section — checked directly, not by outcome.

    The six-tab check next to this one guards the OUTCOME of the work (addresses came out different)
    and does so PROBABILISTICALLY: on an implementation with no lock it doesn't fail on every run —
    measured at four failures out of five. So a single green run on a build proves nothing about the
    absence of a race, and a wave can't be left under a guard like that.

    Here the actual cause is caught directly. Eight processes enter the critical section on a shared
    time signal; each one, on entry, writes its own mark and checks how many marks are sitting next to
    it. Two — means two are inside, and that stays as a file on disk that outlives the process exiting.
    A shared signal is mandatory: without it the processes drift apart during shell warm-up and might
    not even meet, even where there's no lock at all.

    ‼️ This same guard is responsible for portability. Mutual exclusion is held by the system (a file
    opened with no shared access); on Windows this is verified, on other systems .NET fakes it through
    advisory kernel locks. There's no way to test this on the development machine — but the guard runs
    on WHATEVER system the toolkit is actually launched on, and won't let a silent failure through.
    """
    assert pwsh
    registry = tmp_path / "streams"
    registry.mkdir()
    marks = tmp_path / "marks"
    marks.mkdir()
    stand = tmp_path / "lock-stand.ps1"
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
            # Read out the output instead of waiting silently: a full pipe would hang the process
            # solid, and the stand wouldn't crash, it would hang until the wait limit.
            assert process.returncode == 0, (
                f"a stand process never took the lock at all: {err.decode('utf-8', 'replace')}"
                f"{out.decode('utf-8', 'replace')}"
            )
        left = [path.name for path in marks.glob("inside-*")]
        assert not left, f"a process exited without clearing its own mark: {left}"

    breached = sorted(path.read_text(encoding="utf-8") for path in marks.glob("violation-*"))
    assert not breached, f"two processes entered the announcement's critical section at once: {breached}"


@needs_pwsh
def test_a_held_lock_keeps_the_neighbour_out(tmp_path: Path) -> None:
    """While the lock is held, a neighbour isn't let inside — and honestly walks away with nothing.

    The other side of the mutual-exclusion stand: that one catches two getting in, this one catches
    whether a refusal happens at all. Without it a "lock" could let everyone in and still look
    healthy: no mark of two being inside would ever appear, simply because the processes happened to
    miss each other in time.

    Along the way this also checks that the wait ends in a refusal, not a hang: the stand swaps the
    wait limit for a short one.
    """
    registry = tmp_path / "streams"
    registry.mkdir()
    stand = tmp_path / "role-stand.ps1"
    stand.write_text(LOCK_ROLES, encoding="utf-8")

    held = tmp_path / "held.txt"
    release_now = tmp_path / "release.txt"
    # The holder sits inside until the check allows it to exit: this way there's no time margin at
    # all, that a slow machine could eat up, between "a neighbour tries to get in" and "the lock is
    # still held".
    holder = subprocess.Popen(
        lock_stand_args(
            stand, registry, Role="hold", Signal=held, Until=release_now, HoldMs=120000
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for_file(held)
        answer = tmp_path / "answer.txt"
        taker = subprocess.run(
            lock_stand_args(stand, registry, Role="grab", Signal=answer, WaitSeconds=2),
            capture_output=True,
            timeout=120,
        )
        assert taker.returncode == 0, taker.stderr.decode("utf-8", "replace")
        assert answer.read_text(encoding="utf-8").strip() == "no", (
            "a neighbour entered the critical section while the lock was held"
        )
    finally:
        release_now.write_text("go", encoding="utf-8")
        kept, failed = holder.communicate(timeout=180)
        assert holder.returncode == 0, (
            f"the holder stand crashed: {failed.decode('utf-8', 'replace')}"
            f"{kept.decode('utf-8', 'replace')}"
        )


@needs_pwsh
def test_a_killed_holder_frees_the_lock_at_once(tmp_path: Path) -> None:
    """A killed tab frees the lock immediately — nothing to wait for and nothing to seize.

    This is exactly why the lock is held by a HANDLE, not by the file's existence. By file existence
    a crashed tab would lock up the board until the lock "goes stale", and seizing a stale one can't
    be made safe with off-the-shelf means: the decision "I'm taking it" is made based on one file
    state, while a DIFFERENT one gets taken away — a neighbour's fresh lock gets seized while that
    neighbour is still working inside it.

    The lock file STAYS on disk, and that's checked too: it must not be deleted — a second process
    would create the file anew and take the lock on the new one, while the first still holds the old one.
    """
    registry = tmp_path / "streams"
    registry.mkdir()
    stand = tmp_path / "role-stand.ps1"
    stand.write_text(LOCK_ROLES, encoding="utf-8")

    held = tmp_path / "held.txt"
    holder = subprocess.Popen(
        lock_stand_args(stand, registry, Role="hold", Signal=held, HoldMs=60000),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    wait_for_file(held)
    # Kill the holder without letting it close the handle: this is how a tab closed mid-word leaves.
    holder.kill()
    # Read out the output here too: the pipe could have filled before the kill, and then waiting for
    # the process to end would hang solid.
    holder.communicate(timeout=60)

    answer = tmp_path / "answer.txt"
    taker = subprocess.run(
        lock_stand_args(stand, registry, Role="grab", Signal=answer, WaitSeconds=5),
        capture_output=True,
        timeout=120,
    )
    assert taker.returncode == 0, taker.stderr.decode("utf-8", "replace")
    # ‼️ Judge by the ANSWER, not by the clock. The stand's wait limit is set short: had the killed
    # tab not released the lock, the one trying to take it would have come back with "no". Measuring
    # wall-clock time here would add nothing, and would instead tie the check to machine speed: on a
    # loaded build box a single shell launch already eats seconds, and the check would fail on
    # correct code.
    assert answer.read_text(encoding="utf-8").strip() == "entered", (
        "the killed tab's lock wasn't released — the board is locked until the wait limit"
    )
    assert (registry / ".claim.lock").exists(), (
        "the lock file was deleted — a second process would take the lock on a new one while the first still holds the old one"
    )


# Holds the named file, letting no one else in — the way antivirus, Windows Search, a backup tool,
# or a cloud-sync folder holds it for a fraction of a second.
HOLD_FILE_STAND = """#Requires -Version 7
param([string]$Path, [string]$Signal, [int]$HoldMs, [string]$UntilLockTaken = '', [int]$ExtraMs = 0)
$stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'None')
try {
    [System.IO.File]::WriteAllText($Signal, 'holding')
    if ($UntilLockTaken) {
        # Released on an EVENT, not by the clock: wait until the announcing tab takes the registry
        # lock, and only then sit out a short extra bit. Otherwise the check's safety margin is the
        # gap between "how long we hold it" and "how fast the tab gets around to reading", and on a
        # machine three times slower that gap vanishes: the hold ends before the file gets read, and
        # the check turns green for nothing.
        $till = (Get-Date).AddMilliseconds($HoldMs)
        while ((Get-Date) -lt $till) {
            try {
                $probe = [System.IO.File]::Open($UntilLockTaken, 'OpenOrCreate', 'Write', 'None')
                $probe.Dispose()
            } catch {
                # The lock is held — meaning the tab is already inside and about to read the claim.
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
    """Takes a file away from everyone else and returns the holder once it's already holding it.

    `until_lock_taken` — the registry lock's path: the hold then lasts until the announcing tab takes
    the lock, plus `extra_ms` on top. This way the check doesn't depend on machine speed.
    """
    assert pwsh
    stand = tmp_path / "file-holder.ps1"
    stand.write_text(HOLD_FILE_STAND, encoding="utf-8")
    signal = tmp_path / f"holding-{target.name}.txt"
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
    """A neighbour's claim that got taken away for an instant is WAITED OUT — not treated as nonexistent.

    A claim file gets taken away for a fraction of a second by antivirus, Windows Search, a backup
    tool, and a cloud-sync folder. It used to be read in a single attempt, and any such failure
    silently turned the neighbour into someone who doesn't exist: a second tab took its number, cheerfully
    reported success, and warned about nothing. The dispute-resolution circle that followed read the
    same corrupted snapshot and didn't see the rival either — the address collision stuck forever.

    The findings board has been protected from this same trouble by retries from the very start; the
    claim registry was left without them, and the excuse "there's only one writer for the file" answers
    a question about writing, not about reading.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    claim_bare(board, first)
    assert address_of(board, first).endswith("/1"), (
        "the check is set up wrong: the first number wasn't taken"
    )

    # The hold lasts until the announcing tab takes the registry lock, plus half a second on top —
    # it would hold that long on any machine, fast or three times slower.
    holder = hold_file(
        tmp_path,
        claim_file_of(board, first),
        20000,
        until_lock_taken=board.parent / "streams" / ".claim.lock",
        extra_ms=500,
    )
    try:
        second = tmp_path / "second"
        out = claim_bare(board, second)
        assert address_of(board, second).endswith("/2"), (
            f"a neighbour with a locked claim file was counted as nonexistent — its number was issued a second time: {out!r}"
        )
    finally:
        holder.communicate(timeout=60)


@needs_pwsh
def test_an_unreadable_claim_file_refuses_the_claim_instead_of_reusing_the_number(
    tmp_path: Path,
) -> None:
    """A neighbour's claim can't be read at all — announcing refuses out loud, instead of handing out
    its number again.

    Retries save you from a brief obstacle, but not a long one. What's left is one choice: refuse for
    a few seconds, asking to retry — or silently hand the neighbour's own number to someone else a
    second time and leave two streams sharing one address forever. A refusal is fixable, an address
    collision isn't.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    claim_bare(board, first)

    holder = hold_file(tmp_path, claim_file_of(board, first), 20000)
    try:
        second = tmp_path / "second"
        second.mkdir()
        started = time.monotonic()
        done = tool(board, "-Mode", "Claim", cwd=second)
        spent = time.monotonic() - started

        assert done.returncode != 0, (
            f"announcing went through without seeing the neighbour — the addresses collided silently: {done.stdout!r}"
        )
        assert "couldn't read" in done.stderr, f"the refusal didn't name the reason: {done.stderr!r}"
        assert spent < 15, f"the refusal took {spent:.1f}s — the tab was made to wait for nothing"
        # There's still only one claim in the registry — the first tab's. We don't read its content:
        # the stand is holding its file, and looking inside it is exactly what the check forbids.
        #
        # ‼️ This checks the FIRST registry read — the one that happens before writing your own claim.
        # For the second one, in the dispute-resolution circle, a refusal there leaves the claim in
        # the registry: it's already been written. The harm from that is small (the number is chosen
        # from a full snapshot, and announcing again would return the same one), but this check can't
        # promise "after a refusal there will be no claim" at all.
        assert len(list((board.parent / "streams").glob("*.json"))) == 1, (
            "the refusal came, yet the claim landed anyway — neighbours will see a ghost stream"
        )
    finally:
        holder.kill()
        holder.communicate(timeout=60)


# Kinds of claim-file corruption. All four are real, not invented: an empty file and one cut off
# midway are left behind by a write interrupted mid-word; a claim with no worktree is a claim from a
# DIFFERENT version of the toolkit; a file of nothing but spaces is the same interrupted write on a
# different filesystem.
BROKEN_CLAIMS = {
    "empty file": "",
    "spaces only": "   \n  \n",
    "cut off midway": '{"wave":"wave6","stream":"3","worktr',
    "another version's claim, no worktree": '{"wave":"wave6","stream":"3"}',
}


def spoil_claim(board: Path, worktree: Path, text: str) -> None:
    """Corrupts the named tab's claim file — the way an interrupted write corrupts it."""
    claim_file_of(board, worktree).write_text(text, encoding="utf-8")


@needs_pwsh
@pytest.mark.parametrize("porch", sorted(BROKEN_CLAIMS), ids=lambda name: name.split(",")[0])
def test_a_broken_claim_file_never_makes_its_stream_invisible(tmp_path: Path, porch: str) -> None:
    """A corrupted claim must not make its stream silently invisible — not to neighbours, not to its owner.

    Same root cause as a locked file: the file sits right there, and the stream vanishes from the
    list. The only difference is that a lock passes on its own and corruption doesn't; for whoever is
    choosing a number from the registry or looking up a task's owner, there's no difference at all.

    This outcome stayed silent until the last fix, and its price was two streams with one address: a
    second tab announced, got the neighbour's address, and warned about nothing. The owner of the
    corrupted claim was invisible to themselves too: release answered "no claim on this tab — nothing
    to release" and exited with success, and the stream listing said "no stream claims".
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    claim_bare(board, first)
    taken = address_of(board, first)
    spoil_claim(board, first, BROKEN_CLAIMS[porch])

    second = tmp_path / "second"
    second.mkdir()
    claimed = tool(board, "-Mode", "Claim", cwd=second)
    assert claimed.returncode != 0, (
        f"the second tab announced right over an invisible neighbour: {claimed.stdout!r}"
    )
    assert "broken and won't parse" in claimed.stderr, f"the refusal didn't name the reason: {claimed.stderr!r}"
    assert taken not in claimed.stdout, "the neighbour's address was issued a second time"

    # The owner of the corrupted claim must not hear "nothing to release": the stream is still theirs.
    given = tool(board, "-Mode", "Release", cwd=first)
    assert given.returncode != 0, f"release succeeded with a corrupted claim: {given.stdout!r}"
    assert "nothing to release" not in given.stdout, (
        f"the owner was told there's no claim — the stream will stay listed as theirs: {given.stdout!r}"
    )

    # And "who's running which stream" must not answer "none".
    listed = tool(board, "-Mode", "Streams", cwd=first)
    assert "No stream claims" not in listed.stdout, (
        f"the stream listing declared the registry empty even though a claim sits right there: {listed.stdout!r}"
    )


@needs_pwsh
def test_an_unusual_but_valid_claim_file_is_still_read(tmp_path: Path) -> None:
    """A legitimate but unusual claim still gets read — strictness must not catch its own.

    A refusal over a corrupted claim stops work for every tab in the project until the file is fixed.
    That cost can't be paid for a false positive, so the other side is checked too: a claim in
    UTF-16, in UTF-8 with a byte-order mark, with Windows line endings, and with an extra unknown
    field are all legitimate claims, and the stream behind them must stay visible.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    claim_bare(board, first)
    path = claim_file_of(board, first)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["unseen_field"] = "from a future version"
    text = json.dumps(record, ensure_ascii=False, indent=2).replace("\n", "\r\n")

    for encoding in ("utf-16", "utf-8-sig"):
        path.write_text(text, encoding=encoding)
        second = tmp_path / f"second-{encoding}"
        out = claim_bare(board, second)
        assert address_of(board, second).endswith("/2"), (
            f"a legitimate claim in {encoding} was mistaken for a corrupted one — the addresses collided: {out!r}"
        )
        claim_file_of(board, second).unlink()


@needs_pwsh
def test_the_task_owner_question_never_answers_nobody_when_a_claim_is_busy(tmp_path: Path) -> None:
    """"Whose piece of work is this" doesn't answer "no one's taken it" while a neighbour's claim can't be read.

    The project's rules require asking this question BEFORE offering the owner work outside their
    own tasks. An answer of "no one's taken this task" is a direct green light to take someone else's
    piece, and the owner will confirm it, because they have no way of knowing otherwise. The cost is
    two tabs doing the same work and colliding in a merge conflict — not the "invisible line in the
    listing" that's usually the excuse for tolerating this.
    """
    board = tmp_path / "board.jsonl"
    owner = tmp_path / "task-owner"
    claim(board, owner, "wave6", "3", "-Tasks", "10-13")

    asking = tmp_path / "asker"
    asking.mkdir()
    holder = hold_file(tmp_path, claim_file_of(board, owner), 20000)
    try:
        asked = tool(board, "-Mode", "Streams", "-Task", "11", cwd=asking)
        assert "isn't claimed by any stream" not in asked.stdout, (
            f"an unrelated task was declared unclaimed — a tab will take it with the owner's blessing: {asked.stdout!r}"
        )
        assert asked.returncode != 0, f"the answer came out confident even though the registry is incomplete: {asked.stdout!r}"
    finally:
        holder.kill()
        holder.communicate(timeout=60)


@needs_pwsh
def test_a_finding_for_a_released_stream_is_refused_even_when_its_claim_is_busy(
    tmp_path: Path,
) -> None:
    """A finding for a RELEASED stream is refused even when its claim can't be read at that instant.

    With an intact registry, the refusal arrives as designed. With that same stream's claim file
    locked, the entry used to be accepted with a report saying "the stream hasn't announced itself
    yet — it'll wait for its claim and arrive in the first minute of work". There will never be a
    claim: the stream is released. The cost is a lost finding plus a reassured author who, after a
    report like that, never sets up a fallback item in the plan.
    """
    board = tmp_path / "board.jsonl"
    gone = tmp_path / "released"
    claim(board, gone, "wave6", "4")
    given = release(board, gone)
    assert given.returncode == 0, given.stderr

    holder = hold_file(tmp_path, claim_file_of(board, gone), 20000)
    try:
        author = tmp_path / "author"
        author.mkdir()
        done = tool(board, "-Mode", "Add", "-To", "wave6/4", "-Title", "finding", cwd=author)
        assert done.returncode != 0, f"a finding was accepted for a released stream: {done.stdout!r}"
        assert "hasn't announced itself yet" not in done.stdout, (
            f"a released stream was passed off as one never opened — the author would relax, and "
            f"the finding would vanish: {done.stdout!r}"
        )
    finally:
        holder.kill()
        holder.communicate(timeout=60)


@needs_pwsh
def test_refusals_about_the_registry_directory_name_the_real_cause(tmp_path: Path) -> None:
    """A refusal about the claims folder names the real cause, not one canned line for every kind of trouble.

    It used to answer any trouble with "there's not a folder where the claims folder should be,
    remove it" — on a nonexistent drive and in a folder with no permissions alike, where there was
    nothing to remove. Then a branch with a real reason appeared, but the reason turned out empty:
    creating a folder over a blocked path silently reports success, having created nothing. And a
    nonexistent drive never even got that far: the tool used to crash earlier, while parsing the
    path, and leaked a raw system message.
    """
    blocked = tmp_path / "in-the-way"
    blocked.write_text("I'm a file, not a folder", encoding="utf-8")
    tab = tmp_path / "tab"
    tab.mkdir()

    denied = tool(blocked / "board.jsonl", "-Mode", "Claim", cwd=tab)
    assert denied.returncode != 0, "announcing went through right over a blocked path"
    assert "blocked by a file" in denied.stderr and str(blocked) in denied.stderr, (
        f"the culprit wasn't named: {denied.stderr!r}"
    )

    # A nonexistent drive: the refusal must be ours, in plain terms, not a raw system message.
    missing = tool(dead_board_path(), "-Mode", "Claim", cwd=tab)
    assert missing.returncode != 0, "announcing went through on a nonexistent drive"
    assert "claims folder" in missing.stderr, (
        f"the refusal didn't come from the tool but was a raw system message: {missing.stderr!r}"
    )


@needs_pwsh
def test_an_unopenable_lock_refuses_at_once_instead_of_waiting_for_a_neighbour(
    tmp_path: Path,
) -> None:
    """A lock that can't be opened at all refuses right away — it doesn't pass itself off as a
    neighbour holding it.

    A contested lock and an unfixable obstacle arrive the same way: a failed file open. Confuse them,
    and a tab honestly waits half a minute, prints a scary "another session is announcing right now",
    and then fails anyway one step later. Before the lock existed, the refusal here was instant and honest.

    The obstacle is chosen to reach exactly the point of TELLING THEM APART: the registry folder is
    real (otherwise everything would end earlier, on the folder check), while a folder sits where the
    lock FILE itself should be — it can't be opened as a file, not now and not in half a minute.
    """
    board = tmp_path / "board.jsonl"
    registry = board.parent / "streams"
    registry.mkdir(parents=True)
    (registry / ".claim.lock").mkdir()

    tab = tmp_path / "tab"
    tab.mkdir()
    started = time.monotonic()
    done = tool(board, "-Mode", "Claim", cwd=tab)
    spent = time.monotonic() - started

    assert done.returncode != 0, f"announcing went through right over a lock that can't be set up: {done.stdout!r}"
    assert "couldn't set up the claim registry lock" in done.stderr, (
        f"the refusal didn't name the reason: {done.stderr!r}"
    )
    assert spent < 15, f"the refusal took {spent:.1f}s — an obstacle was mistaken for a neighbour holding it"


@needs_pwsh
def test_a_blocked_registry_path_refuses_at_once_instead_of_waiting(tmp_path: Path) -> None:
    """An unfixable obstacle refuses right away and for real — it doesn't pass itself off as a
    neighbour holding a lock.

    A contested lock and an obstacle are easy to confuse: both arrive as a failed file open. Confuse
    them, and a tab honestly waits out the full limit, prints a scary "another session is announcing
    right now", and then fails anyway while writing its claim. Before the lock existed, the refusal
    here was instant and honest.

    The obstacle is the simplest one there is: a file sits where the claims folder should be.
    This checks both the refusal's text and its timing: waiting for the lock would give itself away in seconds.
    """
    board = tmp_path / "board.jsonl"
    # A file sits where the claims folder should be. No folder can be set up there, and waiting for
    # it makes no sense.
    (board.parent / "streams").write_text("not a folder", encoding="utf-8")

    tab = tmp_path / "tab"
    tab.mkdir()
    started = time.monotonic()
    done = tool(board, "-Mode", "Claim", cwd=tab)
    spent = time.monotonic() - started

    assert done.returncode != 0, f"announcing went through right over a broken registry: {done.stdout!r}"
    assert "not a folder" in done.stderr, f"the refusal didn't name the reason: {done.stderr!r}"
    assert spent < 15, f"the refusal took {spent:.1f}s — an obstacle was mistaken for a neighbour holding it"


needs_windows_acl = pytest.mark.skipif(
    os.name != "nt" or not shutil.which("icacls"),
    reason="nothing to close folder access with — Windows access rules aren't available",
)


def deny_listing(folder: Path) -> None:
    """Blocks reading the folder's CONTENTS while leaving access to the files themselves.

    That's how Windows folder protection and part of the endpoint-security tools behave, and on a
    network share it's the right to enter without the right to list. This is exactly the state in
    which a folder listing used to answer with emptiness.
    """
    who = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    done = subprocess.run(
        ["icacls", str(folder), "/deny", f"{who}:(RD)"], capture_output=True, timeout=60
    )
    assert done.returncode == 0, done.stdout.decode("utf-8", "replace")


def dead_board_path() -> Path:
    """A board path on a drive that does NOT exist on this machine.

    ‼️ The drive letter isn't hardcoded: on a build box or a machine with network drives, a hardcoded
    letter might turn out to be live, and the check would silently change meaning — a dead path would
    become an ordinary one. No free letter found — skip honestly, rather than turn green for nothing.
    """
    if os.name != "nt":
        pytest.skip("finding a nonexistent drive is written for Windows")
    for letter in "ZYXWVUT":
        if not os.path.exists(f"{letter}:\\"):
            return Path(f"{letter}:/no-such-drive/board.jsonl")
    pytest.skip("no free drive letter — nothing to build a dead path from")


def deny_listing_or_skip(folder: Path) -> None:
    """Denies access and MAKES SURE it's actually denied; if it didn't take, skips with a reason.

    ‼️ An access denial doesn't work under every account: some service accounts bypass it, and then
    the check silently changes meaning — there's no obstacle, and the check either turns green for
    nothing or fails on correct code. That's exactly what happened on the build box. The check must
    neither turn green silently nor fail where there's nothing to check.
    """
    deny_listing(folder)
    try:
        os.listdir(folder)
    except PermissionError:
        return
    allow_listing(folder)
    pytest.skip("folder access denial doesn't work here — nothing to check")


def allow_listing(folder: Path) -> None:
    """Restores permissions — otherwise the folder won't be deletable and temp-folder cleanup breaks."""
    who = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    subprocess.run(["icacls", str(folder), "/remove:d", who], capture_output=True, timeout=60)


@needs_pwsh
@needs_windows_acl
def test_an_unlistable_registry_is_never_taken_for_an_empty_one(tmp_path: Path) -> None:
    """A claims folder that can't be listed is not "no claims".

    The quietest hole in the whole mechanism, and it wasn't in reading files, it was in the VERY
    FIRST question: what's even in the folder. Listing through the shell with a name pattern, when
    access to the folder's contents is closed off, returns an empty list and REPORTS NO ERROR —
    nothing to catch, and the whole strict guard further up the code simply never got called at all.

    Out in the open it looked like this: a neighbour is running the first stream, a second tab
    announces and gets the same number with a success code; "whose piece is this" answers "nobody
    took it"; a finding for the live stream gets "the stream hasn't announced itself yet". All of it
    silent.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    claim(board, first, "wave6", "1", "-Tasks", "10-13")
    registry = board.parent / "streams"

    deny_listing_or_skip(registry)
    try:
        second = tmp_path / "second"
        second.mkdir()
        claimed = tool(board, "-Mode", "Claim", "-Wave", "wave6", cwd=second)
        assert claimed.returncode != 0, (
            f"a tab announced against an invisible registry — its number will collide with a neighbour's: {claimed.stdout!r}"
        )
        assert "couldn't list" in claimed.stderr, f"the reason wasn't named: {claimed.stderr!r}"

        asked = tool(board, "-Mode", "Streams", "-Task", "11", cwd=second)
        assert "isn't claimed by any stream" not in asked.stdout, (
            f"an unrelated task was declared unclaimed against an invisible registry: {asked.stdout!r}"
        )
        assert asked.returncode != 0, f"the answer came out confident: {asked.stdout!r}"

        offered = tool(board, "-Mode", "Add", "-To", "wave6/1", "-Title", "finding", cwd=second)
        assert "hasn't announced itself yet" not in offered.stdout, (
            f"a live stream was passed off as one never opened: {offered.stdout!r}"
        )
        assert offered.returncode != 0, f"a finding was accepted against an invisible registry: {offered.stdout!r}"
    finally:
        allow_listing(registry)


@needs_pwsh
@needs_windows_acl
def test_handing_over_a_stream_does_not_pass_on_an_unreadable_registry(tmp_path: Path) -> None:
    """A stream can't be released while the registry can't be read: otherwise a finding stays unclaimed forever.

    A subtle seam: parsing a "wave/stream" address relies on the list of announced wave names when
    the wave is named BY A WORD. That list is derived from the registry, and used to be built once,
    tolerantly. An incomplete list means the address doesn't parse, the finding no longer counts as
    this stream's finding, the inbox looks empty — and release succeeds, leaving the entry on the
    board forever. The finding's author, meanwhile, has already been told "a session is running this
    stream, it'll most likely get there on its own".

    The wave is deliberately named by a word: for waves named by number or date, address parsing
    never goes to the registry, and this obstacle changes nothing there.
    """
    board = tmp_path / "board.jsonl"
    owner = tmp_path / "owner"
    claim(board, owner, "sprint-alpha", "1")
    neighbour = tmp_path / "neighbour"
    claim(board, neighbour, "sprint-alpha", "2")
    add(board, "sprint-alpha/1", "important finding", cwd=neighbour)

    # Control: with an intact registry, release is refused — an unread finding sits in the inbox.
    control = release(board, owner)
    assert control.returncode != 0, "the check is set up wrong: the inbox came out empty"
    assert "still has" in control.stderr, control.stderr

    registry = board.parent / "streams"
    deny_listing_or_skip(registry)
    try:
        given = release(board, owner)
        assert given.returncode != 0, (
            f"a stream released against an invisible registry — the finding stays unclaimed on the board: {given.stdout!r}"
        )
        assert "released" not in given.stdout, f"the tab was told the stream was released: {given.stdout!r}"
    finally:
        allow_listing(registry)


needs_git = pytest.mark.skipif(
    not shutil.which("git"), reason="git not found — nothing to set up trees with"
)


def real_worktrees(root: Path, tabs: dict[str, str]) -> None:
    """A real repository with worktrees: {folder name: branch name}.

    It's real because part of a stream's names comes from git, and on plain stub folders such names
    simply never appear — the check would silently weaken.
    """
    main = root / "repo"

    def git(*args: str, cwd: Path = main) -> None:
        done = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120
        )
        assert done.returncode == 0, done.stderr

    main.mkdir(parents=True)
    git("init", "-q", "-b", "main", ".")
    git("config", "user.email", "check@stand")
    git("config", "user.name", "check")
    (main / "readme.md").write_text("probe", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "start")
    for folder, branch in tabs.items():
        git("worktree", "add", "-q", "-b", branch, str(root / folder))


def rename_branch(tab: Path, name: str) -> None:
    """Renames a worktree's branch — the same way it gets renamed by hand by the middle of a wave."""
    done = subprocess.run(
        ["git", "branch", "-m", name], cwd=str(tab), capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr


@needs_pwsh
@needs_git
def test_a_reused_branch_name_belongs_to_the_one_who_carries_it_now(tmp_path: Path) -> None:
    """A name a stream only REMEMBERS goes to whoever carries it now — and only to them.

    Branch names get reused in a live repository. Let name memory exist without this rule, and a
    name could legitimately point at two streams at once, while closing a finding with a named
    address is SHARED: whoever closed it first silenced it for everyone. Reproduced: the finding went
    to both tabs, both were told "handled — close it", and the one that only remembers the name
    silenced the other's finding. The real addressee saw nothing at all and released green with an
    empty inbox, while the author got "acknowledged" from a stream they never named.

    Hence the rule: no more than one stream answers to any given name, and whoever carries the name
    now wins over whoever only remembers it.
    """
    real_worktrees(tmp_path, {"first": "alpha", "second": "spare", "third": "third-branch"})
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    claim(board, first, "wave9", "1")
    rename_branch(first, "gamma")
    # The first tab remembers "alpha"; announcing again, it keeps that memory.
    claim(board, first, "wave9", "1")
    # The second tab takes the freed-up name — ordinary business in a live repository.
    rename_branch(second, "alpha")
    claim(board, second, "wave9", "2")

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "alpha",
        "-Title",
        "for the current alpha",
        cwd=tmp_path / "third",
        known=True,
    )
    assert offered.returncode == 0, f"the finding wasn't accepted: {offered.stderr!r}"

    to_carrier = run_deliver(board, second, "Start", "carrier")
    assert "for the current alpha" in to_carrier, (
        f"the finding didn't reach whoever carries the name now: {to_carrier!r}"
    )
    to_rememberer = run_deliver(board, first, "Start", "rememberer")
    assert "for the current alpha" not in to_rememberer, (
        "the finding also went to whoever only remembers the name — it would silence it at the real "
        f"addressee's: {to_rememberer!r}"
    )

    # And it must not be able to close someone else's named finding even knowing the id.
    mark = offered.stdout.split("id ")[1].split(")")[0].strip()
    denied = tool(board, "-Mode", "Done", "-Id", mark, cwd=first)
    assert denied.returncode != 0, "someone else's named finding was closed — the addressee will never see it"
    assert "not you" in denied.stderr, f"the refusal didn't name the reason: {denied.stderr!r}"

    # A human can see that the name was taken away from the stream.
    listed = run_tool(board, "-Mode", "Streams", cwd=first)
    assert "names taken away: alpha" in listed, (
        f"the taken-away name isn't shown anywhere — a human won't notice the discrepancy: {listed!r}"
    )


@needs_pwsh
@needs_windows_acl
def test_a_released_stream_gets_nothing_even_when_the_registry_is_unreadable(
    tmp_path: Path,
) -> None:
    """A released stream gets no findings even when its claim is missing from the registry snapshot.

    Normally a released stream stays quiet because it doesn't answer to any name — that's decided
    against the whole registry at once. But the registry snapshot can turn out incomplete: the claims
    folder didn't list, while its own file is still read directly. Then names fall back to the ones
    the stream carries — and a released stream would get the finding again, along with an instruction
    to close it.

    The guard against this case lives inside the delivery guard itself. The check sets up exactly
    that state: folder listing blocked, its own file accessible.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "departing"
    claim_bare(board, mine)
    add(board, "departing", "leftover finding", cwd=tmp_path)
    given = release(board, mine, "-Force")
    assert given.returncode == 0, given.stderr

    registry = board.parent / "streams"
    deny_listing_or_skip(registry)
    try:
        brought = run_deliver(board, mine, "Start", "released-no-registry")
        assert "leftover finding" not in brought, (
            f"a released stream got a finding through fallback names: {brought!r}"
        )
    finally:
        allow_listing(registry)


@needs_pwsh
@needs_git
def test_a_name_two_streams_remember_is_refused_with_the_real_reason(tmp_path: Path) -> None:
    """A name remembered by two streams and carried by neither is refused with the REAL reason.

    Such a name deliberately goes to no one: otherwise the finding would reach both, and closing a
    named address is shared — either side could clear it for the other. The refusal used to say the
    addressee wasn't among the worktrees and offer three explanations, none of them correct; the
    truth was visible only in the stream listing, which the refusal didn't even point to. We kept
    catching defects circle after circle over misleading refusals like this.
    """
    real_worktrees(tmp_path, {"first": "shared", "second": "spare", "third": "third-branch"})
    board = tmp_path / "board.jsonl"
    first, second = tmp_path / "first", tmp_path / "second"
    claim(board, first, "wave9", "1")
    rename_branch(first, "first-new")
    claim(board, first, "wave9", "1")
    # The second tab takes the same name and also moves off it: now "shared" is remembered by two and
    # carried by neither.
    rename_branch(second, "shared")
    claim(board, second, "wave9", "2")
    rename_branch(second, "second-new")
    claim(board, second, "wave9", "2")

    denied = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "shared",
        "-Title",
        "for whom?",
        cwd=tmp_path / "third",
        known=True,
    )
    assert denied.returncode != 0, f"a finding was accepted by a name that belongs to no one: {denied.stdout!r}"
    assert "is remembered by two streams" in denied.stderr, (
        f"the real reason wasn't named: {denied.stderr!r}"
    )
    assert "not among the worktrees" not in denied.stderr, (
        f"the refusal still explains the trouble incorrectly: {denied.stderr!r}"
    )
    assert "-Mode Streams" in denied.stderr, (
        f"the human wasn't pointed to where the discrepancy is visible: {denied.stderr!r}"
    )


@needs_pwsh
def test_a_released_stream_neither_gets_findings_nor_kills_them(tmp_path: Path) -> None:
    """A released stream neither gets delivery nor gets to close a finding.

    It was just told "findings will no longer be accepted", while delivery kept carrying them — and
    the delivery text tells the reader outright to close the record if it doesn't apply to their
    work. Closing a named address is SHARED, so a released stream would silence the finding for good:
    its author would get "acknowledged" while there was no one left to handle it. A released stream's
    inbox is non-empty exactly when it released with -Force — what's left then gets moved into "Wave
    Loose Ends" by hand, not closed by a tab that no longer exists.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "departing"
    claim_bare(board, mine)
    mark = add(board, "departing", "leftover finding", cwd=tmp_path)

    given = release(board, mine, "-Force")
    assert given.returncode == 0, given.stderr
    assert "released" in given.stdout, given.stdout

    brought = run_deliver(board, mine, "Start", "after-release")
    assert "leftover finding" not in brought, (
        f"a released stream got the finding delivered — it has nothing to do with it: {brought!r}"
    )

    denied = tool(board, "-Mode", "Done", "-Id", mark, cwd=mine)
    assert denied.returncode != 0, "a released stream closed a finding that no one is left to handle"


@needs_pwsh
@needs_git
def test_a_finding_by_the_current_branch_name_reaches_the_stream(tmp_path: Path) -> None:
    """A finding addressed by the CURRENT branch name is recognized by all three, even though the
    stream announced under a different one.

    The third side of the agreement is accepting a finding. It checks the finding against the
    stream's names, and taking only the ones recorded in the claim means that after a branch rename,
    the stream isn't recognized under its new name: the report to the author gets more cautious than
    the truth ("whether the tab is alive is unknown") in a case where it's actually known the stream
    is being run. The finding isn't lost here, but an unnoticed regression of a fix like this is
    exactly the mechanism by which defects survived circle after circle.

    The trees are real: the branch's current name comes from git, and it doesn't exist at all on stub
    folders.
    """
    real_worktrees(tmp_path, {"tab": "alpha", "neighbour": "neighbour-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave6", "1")

    renamed = subprocess.run(
        ["git", "branch", "-m", "beta"], cwd=str(tab), capture_output=True, text=True, timeout=120
    )
    assert renamed.returncode == 0, renamed.stderr

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "beta",
        "-Title",
        "by the new name",
        cwd=tmp_path / "neighbour",
        known=True,
    )
    assert offered.returncode == 0, (
        f"acceptance didn't recognize the stream by its new branch name: {offered.stderr!r}"
    )
    assert "is live" in offered.stdout, (
        f"the stream was recognized by something other than its claim — the report to the author is "
        f"more cautious than the truth: {offered.stdout!r}"
    )

    brought = run_deliver(board, tab, "Start", "new-name")
    assert "by the new name" in brought, f"delivery didn't bring the finding: {brought!r}"

    given = release(board, tab)
    assert given.returncode != 0 and "still has" in given.stderr, (
        f"release didn't see the finding: {given.stdout!r} {given.stderr!r}"
    )


@needs_pwsh
@needs_git
def test_a_finding_by_a_former_branch_name_still_reaches_the_stream(tmp_path: Path) -> None:
    """A finding by a FORMER branch name still arrives even after the tab announced itself again.

    A tab can announce a second time, and announcing silently rewrites the claim. Rename the branch
    and announce again, and the old name would vanish from everywhere — a finding sent under it
    wouldn't arrive and wouldn't hold up release either: acceptance would take it in (the tree's still
    there, after all) and promise the author delivery.

    So announcing carries the branch's earlier names into the new claim, and they remain the stream's
    names.
    """
    real_worktrees(tmp_path, {"tab": "alpha", "neighbour": "neighbour-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave6", "1")

    subprocess.run(["git", "branch", "-m", "beta"], cwd=str(tab), capture_output=True, timeout=120)
    # A second announcement: the claim is rewritten wholesale, and the former name must survive in it.
    claim(board, tab, "wave6", "1")

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "alpha",
        "-Title",
        "by the former name",
        cwd=tmp_path / "neighbour",
        known=True,
    )
    assert offered.returncode == 0, (
        f"the stream forgot its former name — a finding by it wasn't accepted: {offered.stderr!r}"
    )

    brought = run_deliver(board, tab, "Start", "former-name")
    assert "by the former name" in brought, (
        f"a finding by the former name wasn't delivered — it will never arrive: {brought!r}"
    )

    given = release(board, tab)
    assert given.returncode != 0 and "still has" in given.stderr, (
        f"release didn't see the finding by the former name: {given.stdout!r} {given.stderr!r}"
    )


@needs_pwsh
def test_all_three_sides_know_the_stream_by_the_same_names(tmp_path: Path) -> None:
    """Acceptance, delivery, and release must recognize a stream by the SAME set of names.

    The hole here is a different kind than a silent read: the file system doesn't lie at all, the two
    sides of the mechanism just agreed on different things. Acceptance checked names AS OF
    ANNOUNCEMENT TIME, delivery only against those found out RIGHT NOW. A branch gets renamed or
    switched (ordinary business by the middle of a wave), a neighbour posts a finding by the branch
    name the stream is called in the plan: acceptance takes it in and promises "a session is running
    this stream — it'll most likely get there on its own", and delivery never brings it, not this
    turn, not the next day either.

    So it's not the three sides checked separately, but their AGREEMENT: the same name passes through
    acceptance, delivery, and release. Let any pair drift apart and it fails.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "own-folder"
    claim_bare(board, mine)
    # The stream's branch is named DIFFERENTLY from its folder: that exact mismatch is where the loss lived.
    patch_claim(board, mine, branch="feat/stream-branch")

    taken = tool(
        board, "-Mode", "Add", "-To", "feat/stream-branch", "-Title", "neighbour's finding", cwd=tmp_path
    )
    assert taken.returncode == 0, f"acceptance didn't recognize the stream by its branch name: {taken.stderr!r}"

    brought = run_deliver(board, mine, "Start", "by-branch-name")
    assert "neighbour's finding" in brought, (
        f"acceptance took the finding in, but delivery doesn't know it — it will never arrive: {brought!r}"
    )

    given = release(board, mine)
    assert given.returncode != 0, (
        f"the stream released along with a finding addressed by its branch name: {given.stdout!r}"
    )
    assert "still has" in given.stderr, (
        f"the finding by branch name never made it into the inbox: {given.stderr!r}"
    )


@needs_pwsh
def test_the_owner_of_a_broken_claim_is_told_which_file_to_remove(tmp_path: Path) -> None:
    """The owner of a corrupted claim must learn the file's PATH and a way out that actually works.

    A refusal with no path is a dead end for no reason: "fix the file" doesn't say which one, and
    "announce again" is impossible — announcing reads the whole registry strictly, hits the same
    file, and refuses too. A human reads their own refusal and finds no way out of it.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "mine"
    claim_bare(board, mine)
    path = claim_file_of(board, mine)
    path.write_text("", encoding="utf-8")

    given = tool(board, "-Mode", "Release", cwd=mine)
    assert given.returncode != 0, f"release went through with a corrupted claim: {given.stdout!r}"
    assert str(path) in given.stderr, f"the path to its own file wasn't named: {given.stderr!r}"
    assert "Remove this file" in given.stderr, f"a working way out wasn't named: {given.stderr!r}"


@needs_pwsh
def test_a_dead_path_is_never_taken_for_an_empty_registry(tmp_path: Path) -> None:
    """A dropped drive or share is not "no claims yet", and the two must not be confused.

    Listing a folder turns one kind of failure into the legitimate answer "no registry yet": the
    first tab to announce is the one that creates it. But the system answers with that exact same
    failure for a DEAD PATH too — a dropped drive, a vanished network share. While the two weren't
    told apart, on a dead path "whose piece is this" answered "no one's taken the task", and release
    answered "no claim on this tab", both with a success code: the same consequence every hole of this
    class has.

    The difference is simple: is there at least one live folder further up the path.
    """
    board = dead_board_path()
    tab = tmp_path / "tab"
    tab.mkdir()

    asked = tool(board, "-Mode", "Streams", "-Task", "11", cwd=tab)
    assert "isn't claimed by any stream" not in asked.stdout, (
        f"on a dead path an unrelated task was declared unclaimed: {asked.stdout!r}"
    )
    assert asked.returncode != 0, f"the answer came out confident on a dead path: {asked.stdout!r}"

    given = tool(board, "-Mode", "Release", cwd=tab)
    assert "nothing to release" not in given.stdout, (
        f"on a dead path it said there's no claim: {given.stdout!r}"
    )
    assert given.returncode != 0, f"release went through on a dead path: {given.stdout!r}"


@needs_pwsh
def test_a_finding_on_a_missing_drive_is_refused_in_our_own_words(tmp_path: Path) -> None:
    """On a broken path the tool refuses, not the system with its raw message.

    Parsing a path through the shell asks it about the drive, and on a nonexistent drive (or a
    dropped network share alike) it crashes outright. This was fixed for announcing a while back, and
    accepting a finding failed the exact same way — leaking a raw system message that tells a human
    neither what happened nor what to do.
    """
    tab = tmp_path / "tab"
    tab.mkdir()
    done = tool(
        dead_board_path(),
        "-Mode",
        "Add",
        "-To",
        "wave6/3",
        "-Title",
        "finding",
        cwd=tab,
    )
    assert done.returncode != 0, "a finding landed on a nonexistent drive"
    # Any link in the chain could refuse (listing the registry, writing to the board) — what matters
    # is that the TOOL refuses in its own words, not the system with a raw message.
    assert "claim" in done.stderr or "board" in done.stderr, (
        f"the refusal didn't come from the tool: {done.stderr!r}"
    )
    assert not done.stderr.strip().startswith("Cannot find drive"), (
        f"a raw system message leaked out: {done.stderr!r}"
    )


@needs_pwsh
def test_a_leftover_lock_file_is_neither_a_barrier_nor_a_claim(tmp_path: Path) -> None:
    """A leftover lock file neither blocks announcing nor counts as a stream's claim.

    The lock file lives forever, deliberately: the lock is held by an open handle, not by the file,
    and it must not be deleted — a second process would create the file anew and take the lock on the
    new one, while the first still holds the old one. Hence two properties that must hold on every
    announcement.

    First: the file being there doesn't mean "taken". It's left behind by every past tab, and if
    announcing got stuck on it, the board would lock up after the very first closed tab.

    Second: it sits INSIDE the claims registry, and the registry gets read whole. Let a listing pick
    it up, and a ghost stream would show up in the neighbour list, and the next number would skip past it.
    """
    board = tmp_path / "board.jsonl"
    registry = board.parent / "streams"
    registry.mkdir(parents=True)
    lock = registry / ".claim.lock"
    lock.write_text("process 31337@machine, taken 2026-01-01T10:00:00", encoding="utf-8")

    tab = tmp_path / "after-a-crash"
    out = claim_bare(board, tab)
    assert "announced for this session" in out, f"a leftover lock file blocked announcing: {out!r}"
    assert "wasn't handed over" not in out, (
        f"the tab waited for a lock that no one is holding: {out!r}"
    )
    assert address_of(board, tab).endswith("/1"), (
        "the lock was mistaken for someone else's claim, and the stream number skipped ahead"
    )

    assert lock.exists(), "the lock file was deleted — a second process would take the lock on a new one"
    streams = run_tool(board, "-Mode", "Streams")
    assert "Stream claims: 1" in streams, f"the lock file ended up in the claims listing: {streams!r}"


@needs_pwsh
def test_a_number_taken_by_a_neighbour_is_given_up_after_the_claim_is_written(
    tmp_path: Path,
) -> None:
    """A shared number must not survive announcing silently.

    We set up exactly what a simultaneous start produces: a neighbour's claim appears in the registry
    AFTER a tab has already chosen its own number. Whoever announced later yields — the order is the
    same on both sides, or they'd trade numbers back and forth forever.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    claim_bare(board, first)
    # Hide the neighbour's claim: this way a tab picks a number without seeing it yet — and takes the same one.
    hidden = claim_file_of(board, first)
    kept = hidden.read_text(encoding="utf-8")
    hidden.unlink()
    claim_bare(board, second)
    assert address_of(board, second).endswith("/1"), "the check is set up wrong: the numbers didn't collide"

    hidden.write_text(kept, encoding="utf-8")
    patch_claim(board, first, claimed_at="2026-01-01T10:00:00")
    out = claim_bare(board, second)

    assert address_of(board, second) == f"{today_wave()}/2", (
        f"the tab stayed on someone else's number — the finding will reach both: {out!r}"
    )
    assert "shifted to the next free one" in out, (
        f"not a word was said about the shift — neighbours will be told the old address: {out!r}"
    )


@needs_pwsh
def test_a_number_from_the_plan_is_never_moved_and_never_doubled(tmp_path: Path) -> None:
    """A number from the plan is the stream's name, findings are addressed by it: it can't be moved,
    and doubling it is worse still.

    This check used to pin down TODAY'S behaviour — the second tab got the same address, the warning
    was printed AFTER the write, and the address was led by two live claims. That was defect 1
    exactly: who gets a finding was decided by the directory listing's order. Now there is a refusal
    BEFORE the write in its place, with one explicit way out — the take-over key; the behaviour
    pinned down before is overturned deliberately.

    The number still can't be moved: it is named in the plan and findings are addressed by it — so
    the second tab doesn't "move on to the next free one", it gets refused, and a human settles the
    dispute.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "wave9-first"
    second = tmp_path / "wave9-second"
    second.mkdir()
    claim(board, first, "wave9", "3")
    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=second)

    assert denied.returncode != 0, (
        f"the second tab got the same address — the address is led by two again: {denied.stdout!r}"
    )
    assert "Another worktree has an open claim on this same stream" not in denied.stdout, (
        f"the warning AFTER the write came back instead of a refusal BEFORE it: {denied.stdout!r}"
    )
    assert not list(registry_dir(board).glob("*wave9-second*")), (
        "the second tab's claim got written after all — the refusal doesn't come before the write"
    )
    assert address_of(board, first) == "wave9/3", (
        "a named number got shifted — the plan's address moved on its own"
    )
    # The way out of the refusal is one, explicit and named: a takeover does happen deliberately (the
    # first tab was closed without releasing), and a human settles that dispute, not the mechanism.
    taken = claim(board, second, "wave9", "3", "-TakeOver")
    assert address_of(board, second) == "wave9/3", f"the take-over key didn't hand the address over: {taken!r}"


@needs_pwsh
def test_claim_warns_about_a_number_that_cannot_be_addressed(tmp_path: Path) -> None:
    """A stream number given as a word makes the stream unaddressable — that has to be said right away.

    Address parsing requires a number on the right: `incidents/incidentchannel` doesn't parse at all,
    and no finding can be sent to such a stream the main way. A refusal isn't an option — the work is
    already under way; but staying silent means leaving the tab with an address that doesn't exist,
    and that gets discovered weeks later.
    """
    board = tmp_path / "board.jsonl"
    worded = claim(board, tmp_path / "incidents-channel", "incidents", "incidentchannel")
    assert "can't be used to address the stream" in worded, (
        f"a word-form number was accepted silently — the stream is left with no address: {worded!r}"
    )

    numbered = claim(board, tmp_path / "incidents-second", "incidents", "2")
    assert "can't be used to address the stream" not in numbered, (
        f"the warning fired on an ordinary stream number: {numbered!r}"
    )


@needs_pwsh
def test_a_finding_for_a_silent_stream_without_a_plan_goes_to_the_owner(tmp_path: Path) -> None:
    """The advice to duplicate a finding in a plan section applies only where a plan exists.

    The "stream is claimed, but the tab hasn't checked in for a while" branch used to bypass the
    shared check, even though the recipient's claim had already been found by that point. In a
    project with no waves it sent the reader to a plan section that doesn't exist — and after advice
    like that, the finding landed nowhere at all.
    """
    board = tmp_path / "board.jsonl"
    author = tmp_path / "author"
    silent = tmp_path / "silent-one"
    claim_bare(board, author)
    claim_bare(board, silent)
    long_ago = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    patch_claim(board, silent, seen_at=long_ago)

    out = run_tool(
        board, "-Mode", "Add", "-To", f"{today_wave()}/2", "-Title", "fix the contract", cwd=author
    )
    assert "hasn't checked in for a while" in out, f"the report isn't about the silent tab: {out!r}"
    assert "Name the finding in your reply to the owner." in out, (
        f"with no wave plan the finding was sent to a plan section: {out!r}"
    )
    assert "Loose Ends" not in out, "in a project with no waves the plan-section advice is still there"

    # Second branch: a wave with a plan keeps the previous text, word for word.
    planned = tmp_path / "wave9-author"
    quiet = tmp_path / "wave9-quiet"
    claim(board, planned, "wave9", "1")
    claim(board, quiet, "wave9", "2")
    patch_claim(board, quiet, seen_at=long_ago)
    planned_out = run_tool(
        board, "-Mode", "Add", "-To", "wave9/2", "-Title", "fix the contract", cwd=planned
    )
    assert "Duplicate the finding as an item in the Wave Loose Ends." in planned_out, (
        f"a wave with a plan had its previous advice rewritten: {planned_out!r}"
    )


@needs_pwsh
def test_an_old_claim_outside_a_wave_is_told_that_it_has_no_plan(tmp_path: Path) -> None:
    """A claim from an earlier version carries no flag — it's judged by its wave's name.

    Such claims already sit in registries, and their wave is sometimes named by a word even though it
    has no wave plan behind it. The old rule "no flag means there's a plan" used to send them to write
    a line into a file section that doesn't exist — a dead end exactly where a tab is finishing its work.
    """
    board = tmp_path / "board.jsonl"
    outside = tmp_path / "outside-a-wave"
    claim(board, outside, "incidents", "1")
    strip_claim_field(board, outside, "wave_auto")

    done = release(board, outside)
    assert done.returncode == 0, done.stderr
    assert (
        "No wave plan — nowhere to write a stream line; the summary goes in your reply to the owner."
        in said(done.stdout)
    ), f"a claim with no flag and no plan was advised to use a plan section: {done.stdout!r}"

    # Second branch: the same old-format claim, but with a PLAN's wave, keeps the same texts.
    planned = tmp_path / "wave9-old"
    claim(board, planned, "wave9", "1")
    strip_claim_field(board, planned, "wave_auto")
    kept = release(board, planned)
    assert kept.returncode == 0, kept.stderr
    assert (
        'Last step — a line for your stream in the wave plan\'s "Stream status" section.'
        in said(kept.stdout)
    ), f"an old-format claim with a plan's wave lost its previous release line: {kept.stdout!r}"


@needs_pwsh
def test_a_wave_with_mixed_claims_is_judged_by_its_first_claim(
    tmp_path: Path, wave_repo: Path
) -> None:
    """Whoever announced first founds the wave — its claim decides whether the wave has a plan.

    The old rule "if even one claim is self-substituted" made a whole planned wave plan-less the
    moment a single tab announced into it without naming a wave: the plan-section texts vanished for
    all its streams at once. The answer must be the same for every tab and must not change from run to run.
    """
    board = tmp_path / "board.jsonl"
    invented = tmp_path / "self-started"
    named = tmp_path / "explicitly-named"
    claim_bare(board, invented)
    claim(board, named, today_wave(), "2")
    board.write_text(
        board_line(id="lost0003", at=now_minus(3), to=f"{today_wave()}/9", title="no one to receive it")
        + "\n",
        encoding="utf-8",
    )

    # The tab that made up the wave itself announced first — the wave has no plan.
    patch_claim(board, invented, claimed_at="2026-01-01T10:00:00")
    patch_claim(board, named, claimed_at="2026-01-02T10:00:00")
    first = context_text(run_deliver(board, wave_repo, "Start", "s-mixed-invented"))
    assert "There is no wave plan — name the finding in your reply to the owner." in said(first), (
        f"a wave founded with no plan was treated as a planned wave: {first!r}"
    )

    # Only the announcement times were swapped — the answer must flip right along with them.
    patch_claim(board, invented, claimed_at="2026-01-02T10:00:00")
    patch_claim(board, named, claimed_at="2026-01-01T10:00:00")
    second = context_text(run_deliver(board, wave_repo, "Start", "s-mixed-named"))
    assert "Wave Loose Ends" in second, (
        f"a wave founded by a named claim was declared plan-less because of a neighbouring claim: {second!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# A session's key is the ROOT of its worktree, not whatever folder it happened to be started in.


def subfolder_of(tab: Path) -> Path:
    """A subfolder of a worktree — a session is very often started from exactly such a place."""
    deep = tab / "docs" / "deep"
    deep.mkdir(parents=True, exist_ok=True)
    return deep


@needs_pwsh
@needs_git
def test_a_claim_and_its_release_meet_whatever_subfolder_the_tab_started_in(tmp_path: Path) -> None:
    """Claimed from the tree root, released from a subfolder — it is one and the same claim.

    A session's key used to be the current folder, and these two commands then diverged by key:
    release didn't find its own claim and answered "nothing to release" WITH A SUCCESS CODE. The
    session closed, and the neighbours went on addressing findings to a stream they believed alive —
    the most expensive loss this mechanism has, because the sender is told it succeeded.

    The reverse is checked too: a claim filed from a subfolder must also land in the ROOT's claim,
    or the registry would gain a second file on the same tree and the stream would double outside.
    """
    real_worktrees(tmp_path, {"tab": "tab-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)

    claim(board, tab, "wave9", "3", "-StreamName", "Session key")
    given = release(board, deep)

    assert given.returncode == 0, f"release from a subfolder fell over: {given.stderr!r}"
    assert "Stream wave9/3 released." in given.stdout, (
        f"release from a subfolder didn't find the claim filed from the root: {given.stdout!r}"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, (
        f"claim files started on one tree: {len(records)} — from outside the stream is doubled"
    )
    assert records[0].released, "the claim stayed open even though release reported success"
    assert records[0].worktree == folder_key(tab), (
        f"the claim records not the tree root but {records[0].worktree}: under that key neither "
        "release nor the neighbours will find it"
    )

    # The reverse: claiming from a subfolder and releasing from the root. A different number — the
    # previous one is taken in this wave already, and a taken number can't be issued twice.
    claim(board, deep, "wave9", "7", "-StreamName", "Reverse")
    back = release(board, tab)
    assert back.returncode == 0, f"release from the root fell over: {back.stderr!r}"
    assert "Stream wave9/7 released." in back.stdout, (
        f"release from the root didn't find the claim filed from a subfolder: {back.stdout!r}"
    )
    assert len(read_registry(registry_dir(board))) == 1, (
        "a claim filed from a subfolder started a second claim on the same tree"
    )


@needs_pwsh
@needs_git
def test_a_tab_started_in_a_subfolder_is_seen_alive_by_its_neighbours(tmp_path: Path) -> None:
    """A session works from a subfolder — the neighbours still see it as alive.

    The liveness beacon is written by the delivery hook, and it is looked for along the paths git
    names. Were the writer to address the current folder, the two would part whenever work runs
    from a subfolder: a live session looks abandoned, and a finding goes into "Wave Loose Ends"
    past the very human sitting at the screen.
    """
    real_worktrees(tmp_path, {"tab": "alpha", "neighbour": "beta"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)

    assert run_deliver(board, deep, "Prompt", "s-subfolder").strip() == "", (
        "the delivery hook spoke up where there is nothing to show"
    )
    assert (tab / BEACON).exists(), (
        "the beacon didn't land in the tree root — worktree parsing looks for it exactly there"
    )
    assert not (deep / BEACON).exists(), "the beacon stayed in the subfolder, where no one reads it"

    seen = run_tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "alpha",
        "-Title",
        "to a live neighbour",
        cwd=tmp_path / "neighbour",
        known=True,
    )
    assert "checked in recently" in seen, (
        f"the neighbour thinks a session working from a subfolder is abandoned: {seen!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_filed_by_the_older_version_from_a_subfolder_is_still_released(
    tmp_path: Path,
) -> None:
    """A claim filed by the older version from a subfolder is found by release's second route.

    A claim's file name is derived from the path, so such claims are keyed by the subfolder, while
    today's key is the tree root. By file name one's own claim isn't found, and without a second
    route the change would orphan exactly the streams it is made for.

    The file name here is deliberately not the canonical one: release must search by the worktree
    folder recorded IN the claim, not by the file name. This is reading, not a move — the record is
    edited in place, and not a single file is created or deleted.
    """
    real_worktrees(tmp_path, {"tab": "orphan-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "older-version-claim",
        **open_claim(str(deep), wave="wave9", stream="4"),
    )

    given = release(board, deep)

    assert given.returncode == 0, f"release fell over: {given.stderr!r}"
    assert "Stream wave9/4 released." in given.stdout, (
        f"release didn't find the older version's claim and orphaned the stream: {given.stdout!r}"
    )
    fields = read_claim_json(orphan)
    assert fields is not None and fields.get("state") == "released", (
        f"release wrote into the wrong file — the older version's claim stayed open: {fields!r}"
    )
    assert len(read_registry(registry_dir(board))) == 1, (
        "release started a new claim file instead of writing into the one it found"
    )


@needs_pwsh
def test_when_git_cannot_name_the_tree_root_claim_and_release_refuse_aloud(tmp_path: Path) -> None:
    """git doesn't answer — claim and release refuse aloud, the delivery hook works on.

    A silent fallback to the current folder would change the session's IDENTITY, and the blow would
    land in the worst place: release would stop finding its own claim and would exit with success.
    For a tolerant reader the same fallback is harmless — a miss there costs one invisible line,
    not a stream.

    The scene: the repository marker is in place, but git can say nothing about it. That is exactly
    the fork where a fallback is dangerous; where there is no repository at all there is no tree
    either — the current folder is then the session's only identity, and there is nothing for the
    keys to diverge from.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    tab.mkdir()
    (tab / ".git").write_text("gitdir: Q:/no-such-path/.git", encoding="utf-8")

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "5", cwd=tab)
    assert denied.returncode != 0, (
        f"the claim went through with an unknown session key: {denied.stdout!r}"
    )
    assert "couldn't work out the worktree root" in denied.stderr, (
        f"the refusal didn't name a reason: {denied.stderr!r}"
    )
    assert not list(registry_dir(board).glob("*.json")), (
        "the claim landed after all — under a key by which nobody will find it later"
    )

    refused = release(board, tab)
    assert refused.returncode != 0, f"release reported success blindly: {refused.stdout!r}"
    assert "couldn't work out the worktree root" in refused.stderr, (
        f"release didn't name the reason for its refusal: {refused.stderr!r}"
    )

    assert run_deliver(board, tab, "Prompt", "s-silent-git").strip() == "", (
        "the delivery hook spoke up where there is nothing to show"
    )
    assert (tab / BEACON).exists(), (
        "the delivery hook fell silent because of git — it is told to work off the current folder, "
        "and to do it quietly"
    )


@needs_pwsh
@needs_git
def test_release_closes_the_claim_of_the_very_folder_it_was_run_from(tmp_path: Path) -> None:
    """Stand in exactly a record's folder — THAT record is released, not the tree root's one.

    The reviewer's scene in full. Two records in the registry: a live one filed from the tree root,
    and a ghost whose worktree folder is a subfolder of that same tree (that is how the older
    version's claims lie). Display prints the folder FROM THE RECORD and advises "release the spare
    one by standing in exactly its folder" — and the human does just that.

    While release first resolved the folder up to the tree ROOT, that single printed way out was
    not merely unworkable but harmful: from the ghost's folder the LIVE record got closed, the
    ghost stayed open and went on holding the address, and the human was told it had succeeded.
    Reproduced on a live scene.

    The reverse (a session in a subfolder, its own record at the root) must meanwhile keep working
    as before — it is checked separately, in the same place where a claim from the root meets a
    release from a subfolder.
    """
    real_worktrees(tmp_path, {"tab": "tab-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)

    claim(board, tab, "wave9", "3", "-StreamName", "Live stream")
    live = claim_of(board, tab, only_open=True)
    live_before = live.file.read_bytes()
    ghost = put_claim(
        registry_dir(board),
        "ghost-from-subfolder",
        **open_claim(str(deep), wave="wave9", stream="4", name="Ghost", seen_at=now_minus(3)),
    )

    given = release(board, deep)

    assert given.returncode == 0, f"release from the ghost's folder fell over: {given.stderr!r}"
    assert "Stream wave9/4 released." in given.stdout, (
        "release closed the wrong record: the human stood in the ghost's folder, yet the root's "
        f"stream was released — the ghost went on holding the address: {given.stdout!r}"
    )
    assert (read_claim_json(ghost) or {}).get("state") == "released", (
        f"the ghost stayed open: {read_claim_json(ghost)!r}"
    )
    assert live.file.read_bytes() == live_before, (
        "the root's live record changed — it was released instead of the ghost, and the session "
        "will never learn of it"
    )

    # ‼️ Release must name WHAT it closed: the address, the name and the worktree folder. While only
    # the address was printed, a swapped record was not visible to the human at all.
    assert "Ghost" in given.stdout, (
        f"release didn't name the closed claim — a swapped record isn't visible: {given.stdout!r}"
    )
    assert folder_key(deep) in folder_key(given.stdout), (
        f"release didn't name the worktree folder of the claim it closed: {given.stdout!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# "Not visible" is not "not there". The probe answering "is there a repository here at all" knows
# THREE answers, and the third ("couldn't tell") is a refusal in strict mode, not a quiet fallback.

MARKER_STAND = """param([string]$Lib, [string]$StartDir, [string]$Marker)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. $Lib
if ($StartDir) {
    (Get-RepoMarkerState -StartDir $StartDir).Kind
    exit 0
}
# We substitute the probe's answer. The scene "the drive dropped out from under a running process"
# can't be built on a live machine: a process can't have its current folder taken away, and it
# can't be started in one that doesn't exist. The probe itself is checked by the neighbouring test,
# on a genuinely dead path; what is checked here is the FORK that rests on its answer.
function Get-RepoMarkerState {
    param([string]$StartDir)
    return [pscustomobject]@{ Kind = $Marker; Reason = 'substituted by the check' }
}
try { "strict: $(Get-TreeRoot -Strict)" } catch { "strict refused: $($_.Exception.Message)" }
"tolerant: $(Get-TreeRoot)"
"""


def dead_folder_path() -> Path:
    """A folder on a drive this machine does NOT have — an unreachable path all the way through.

    The letter is picked the same way as for the dead board: a hard-wired one may turn out to be
    live, and the scene would silently change its meaning.
    """
    return dead_board_path().parent / "tab"


def marker_stand(tmp_path: Path) -> Path:
    """A stand that dot-sources the library: there is no other way to reach the probe itself."""
    stand = tmp_path / "marker-stand.ps1"
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
    """What the strict and tolerant readers of the session key answer to a substituted probe."""
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
    """Three answers instead of two: marker found, marker definitely absent, couldn't tell.

    There used to be two answers, and an unreachable path (a dropped drive, a vanished network
    share) was passed off as "there is no repository here". The difference between them is
    load-bearing: "no" lets a session work by the current folder, because there is no tree and
    nothing for the keys to diverge from, while "not visible" grants no such licence — a tree may
    be there, and a silent fallback would change the session's IDENTITY.
    """
    real_worktrees(tmp_path, {"tab": "tab-branch"})
    stand = marker_stand(tmp_path)
    plain = tmp_path / "no-repo"
    plain.mkdir()

    assert ask_marker(stand, plain) == "none", "a folder outside a repository wasn't called 'no'"
    assert ask_marker(stand, tmp_path / "tab") == "found", (
        "the repository marker wasn't found inside a worktree"
    )
    assert ask_marker(stand, dead_folder_path()) == "unknown", (
        "an unreachable path was passed off as 'there is no repository here' — and that is a "
        "licence for a session to change its own identity silently"
    )


@needs_pwsh
def test_an_unreadable_marker_refuses_aloud_to_the_strict_and_stays_silent_for_the_rest(
    tmp_path: Path,
) -> None:
    """A "couldn't tell" answer refuses aloud where a stream's fate hangs on the key.

    Claim and release read a session's key strictly: their silent fallback to the current folder
    hits the most expensive place — release stops finding its own claim and exits with SUCCESS. For
    the hook and display the same fallback is harmless, a miss there costs one invisible line, and
    refusing them is not allowed.

    The answer "there is no repository here at all" must meanwhile stay quiet for both: there is no
    tree, so there is nothing for the keys to diverge from — otherwise the toolkit would stop
    working everywhere a plain folder stands in place of a repository.
    """
    stand = marker_stand(tmp_path)
    plain = tmp_path / "no-repo"
    plain.mkdir()

    unknown = ask_tree_root(stand, plain, "unknown")
    assert "strict refused" in unknown, (
        f"on 'couldn't tell' the strict reader changed the session's identity silently: {unknown!r}"
    )
    assert "not seeing something is not the same" in unknown and "substituted by the check" in unknown, (
        "the refusal named neither the substance nor the reason — the human has nothing to go and "
        f"sort it out with: {unknown!r}"
    )
    assert "tolerant: " in unknown, (
        f"the tolerant reader refused too — the delivery hook is required to be mute: {unknown!r}"
    )

    none = ask_tree_root(stand, plain, "none")
    assert "strict refused" not in none and "strict: " in none, (
        f"where there is no repository at all the strict reader refused for nothing: {none!r}"
    )


@needs_pwsh
@needs_git
def test_a_worktree_written_in_another_case_is_still_the_same_folder(tmp_path: Path) -> None:
    """The worktree folder recorded in a claim is compared with the session key WITHOUT case.

    ‼️ Honestly: against the code from before this change the check is GREEN — the shell compares
    strings case-insensitively by itself, and all five comparisons silently inherited that. It is
    added not as proof of a fix but as a guard for a property that stopped resting on the shell's
    default: normalizing the worktree folder is now ONE for the whole toolkit and folds case
    EXPLICITLY, because the same key is used by ordering (which compares strings byte by byte) and
    by sets, where the shell's convention doesn't hold.

    Let the session key part from the recorded folder by so much as the case of the drive letter,
    and the session would count its own claim as a rival, lose its memory of former branch names,
    and fail to recognize itself in the display.
    """
    real_worktrees(tmp_path, {"tab": "tab-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"

    claim(board, tab, "wave9", "3", "-StreamName", "Letter case")

    def spell_loudly() -> None:
        """Rewrites the claim's worktree folder in another case — as another source might have."""
        record = claim_of(board, tab, only_open=True)
        record.fields["worktree"] = str(record.fields["worktree"]).upper()
        write_claim(record)

    spell_loudly()
    again = claim(board, tab, "wave9", "3", "-StreamName", "Letter case")
    assert "has an open claim on this same stream" not in again, (
        f"the session counted its own claim as a rival: {again!r}"
    )
    assert len(read_registry(registry_dir(board))) == 1, (
        "the claim started a second one — the recorded folder wasn't recognized as its own"
    )

    spell_loudly()
    listed = run_tool(board, "-Mode", "Streams", cwd=tab)
    assert "this is you" in listed, f"the session didn't see itself in the display: {listed!r}"

    spell_loudly()
    given = release(board, tab)
    assert "Stream wave9/3 released." in given.stdout, (
        f"release didn't find its own claim because of case: {given.stdout!r} / {given.stderr!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Visibility: the worktree folder in the display, the "this is you" mark, a full ordering of lines,
# a loud line about a doubled address. Without it both of the coming refusals ("folder taken",
# "address taken") are unworkable: they name SOMEONE ELSE'S worktree folder to the human, and today
# there is nowhere for them to see it.


def shown_streams(text: str) -> list[str]:
    """The stream lines of the display — in the order they were printed.

    A stream line starts with two spaces; the note under a loud line starts with three, and it
    doesn't belong to the ordering.
    """
    return [
        line for line in text.splitlines() if line.startswith("  ") and not line.startswith("   ")
    ]


def stream_line_of(text: str, folder: Path) -> str:
    """The display line belonging to EXACTLY this worktree folder.

    Picking it out by the path occurring anywhere won't do: a superseded record names on the same
    line the folder the address WENT TO — and a neighbour's line would be found along with one's
    own. So we check exactly the folder the line calls its own.
    """
    wanted = folder_key(folder)
    found = [
        line
        for line in shown_streams(text)
        if (named := re.search(r", folder ([^,)]+)", line)) and folder_key(named.group(1)) == wanted
    ]
    assert len(found) == 1, f"display lines for folder {folder}: {len(found)}, not one: {text!r}"
    return found[0]


def shown_folders(text: str) -> list[str]:
    """The worktree folders named by the stream lines — in the same order."""
    found: list[str] = []
    for line in shown_streams(text):
        folder = re.search(r", folder ([^,)]+)", line)
        if folder:
            found.append(folder_key(folder.group(1)))
    return found


@needs_pwsh
def test_show_keeps_one_order_on_the_same_registry(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Two runs of the display on one registry give ONE order of lines, and that order is full.

    Display used to sort only by wave and stream number. Two records on one address don't happen in
    a sound registry — but the change ships onto a dirty one, where such pairs already lie as a
    legacy of the "a takeover doubles the address" defect. On them the sort key ran out, and the
    order was decided by the directory listing: a human couldn't compare two runs by eye, while
    decisions about such a pair are made by accepting a finding.

    The scene is built so that the directory listing ARGUES with the right order: the later claim's
    file is named earlier alphabetically than the earlier claim's. Had they agreed, the check would
    have gone green silently on an unsorted display too.
    """
    registry_invariants.waive(
        "two open records on one address are assembled deliberately: this is the legacy of the "
        "defect for whose sake the ordering is being made full"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    late = tmp_path / "late"
    early = tmp_path / "early"
    put_claim(
        folder,
        "aa-late",
        **open_claim(str(late), wave="wave9", stream="3", claimed_at="2026-09-02T18:00:00"),
    )
    put_claim(
        folder,
        "zz-early",
        **open_claim(str(early), wave="wave9", stream="3", claimed_at="2026-09-01T09:00:00"),
    )
    put_claim(
        folder,
        "mm-other-number",
        **open_claim(str(tmp_path / "other"), wave="wave9", stream="1"),
    )

    first = run_tool(board, "-Mode", "Streams")
    second = run_tool(board, "-Mode", "Streams")

    assert shown_streams(first) == shown_streams(second), (
        "two runs of the display on one registry gave a different order of lines — they can't be "
        "compared by eye"
    )
    assert shown_folders(first) == [
        folder_key(tmp_path / "other"),
        folder_key(early),
        folder_key(late),
    ], (
        "the display doesn't go by the full key (wave, number, time of claim, path): the order of "
        f"two records on one address is decided by the directory listing — {shown_streams(first)!r}"
    )


@needs_pwsh
@needs_git
def test_show_prints_the_worktree_and_marks_your_own(tmp_path: Path) -> None:
    """A stream line names the worktree folder and marks your own record.

    Both refusals name someone else's folder to the human. While the display printed only the
    address, the name and the branch, there was nowhere to find that folder — that is, the refusal
    was unworkable: a human could neither recognize themselves in it nor see whether it is alive.
    """
    real_worktrees(tmp_path, {"tab": "alpha"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    gone = tmp_path / "vanished"
    claim(board, tab, "wave9", "3", "-StreamName", "Visibility")
    put_claim(registry_dir(board), "theirs", **open_claim(str(gone), wave="wave9", stream="8"))

    listed = run_tool(board, "-Mode", "Streams", cwd=tab)

    mine = [line for line in shown_streams(listed) if line.startswith("  wave9/3")]
    theirs = [line for line in shown_streams(listed) if line.startswith("  wave9/8")]
    assert len(mine) == 1 and len(theirs) == 1, f"the display didn't name both streams: {listed!r}"
    assert folder_key(tab) in folder_key(mine[0]), (
        f"your own line didn't name the worktree folder — the refusal is unworkable: {mine[0]!r}"
    )
    assert "this is you" in mine[0], (
        f"your own record isn't marked — a human won't tell it from someone else's: {mine[0]!r}"
    )
    assert folder_key(gone) in folder_key(theirs[0]), (
        f"someone else's line didn't name the worktree folder: {theirs[0]!r}"
    )
    assert "this is you" not in theirs[0], f"someone else's record is marked yours: {theirs[0]!r}"
    assert "folder is gone" in theirs[0], (
        f"the record's folder isn't on disk, and the evidence for it isn't printed: {theirs[0]!r}"
    )
    assert "folder is gone" not in mine[0], f"a live folder was declared vanished: {mine[0]!r}"


@needs_pwsh
def test_show_shouts_about_a_doubled_address(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """A doubled address is said aloud on a line of its own, with both worktree folders.

    Two similar-looking lines in a list a human will scroll straight past; decisions about such a
    pair, meanwhile, are made by accepting a finding, and made by the order of a directory listing.
    So the doubling is spoken of loudly and with evidence: both folders are named, and there is
    somewhere to go and sort it out.
    """
    registry_invariants.waive(
        "a doubled address is assembled deliberately — we check that the display shouts about it "
        "rather than staying quiet"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    first = tmp_path / "first"
    second = tmp_path / "second"
    put_claim(folder, "one", **open_claim(str(first), wave="wave9", stream="3"))
    put_claim(folder, "another", **open_claim(str(second), wave="wave9", stream="3"))

    listed = run_tool(board, "-Mode", "Streams")

    loud = [line for line in listed.splitlines() if line.startswith("‼️")]
    assert len(loud) == 1, f"the display didn't say the doubled address aloud: {listed!r}"
    assert "wave9/3" in loud[0], f"the loud line didn't name the doubled address: {loud[0]!r}"
    assert folder_key(first) in folder_key(loud[0]) and folder_key(second) in folder_key(loud[0]), (
        f"the loud line didn't name both worktree folders — nowhere to go and look: {loud[0]!r}"
    )

    # ‼️ And the main thing: the loud line must not vanish on an EMPTY filter by task. It was counted
    # before the filter even earlier, but printed after it — that is, it disappeared in exactly the
    # most dangerous answer. "No one has taken the task" reads as licence to take that piece up,
    # while at that very moment two records are running it at once, and who gets a finding is
    # decided by the order of the directory listing.
    empty = run_tool(board, "-Mode", "Streams", "-Task", "42")
    assert "isn't claimed by any stream" in empty, (
        f"the scene is built wrong — the filter by task found something: {empty!r}"
    )
    still_loud = [line for line in empty.splitlines() if line.startswith("‼️")]
    assert len(still_loud) == 1 and "wave9/3" in still_loud[0], (
        "the answer 'no one has taken the task' says nothing at all about the doubled address — "
        f"and that is exactly a licence to take up a piece that two are running: {empty!r}"
    )

    # And the other side: on a sound registry the display is quiet, not shouting for nothing.
    calm = tmp_path / "calm" / "board.jsonl"
    put_claim(registry_dir(calm), "one", **open_claim(str(first), wave="wave9", stream="3"))
    quiet = [
        line for line in run_tool(calm, "-Mode", "Streams").splitlines() if line.startswith("‼️")
    ]
    assert not quiet, "the display shouts about doubling where one record falls on the address"


@needs_pwsh
@needs_git
def test_a_claim_filed_by_the_older_version_from_a_subfolder_still_gets_its_mail(
    tmp_path: Path,
) -> None:
    """An older version's claim from a subfolder is found not only by release but by the hook too.

    A session's key is now the worktree root, while such claims lie under a subfolder's key.
    Release looks for them by a second route (the worktree folder recorded in the claim), whereas
    the delivery hook read only the canonical key — and out came the worst of all possible things:
    accepting a finding takes it with a cheerful "a session is running that stream, it will get
    there on its own", and it never reaches the session. The hook takes the stream address FROM its
    own claim: without it, it doesn't know what the stream is called.

    ‼️ The expectation about the file staying unchanged is INVERTED here deliberately. The check used
    to pin down that the hook doesn't touch a claim found by the second route by a single byte —
    and along with that it pinned down its eternal silence: the liveness mark and the list of
    touched files were set by the hook only under the canonical key, and such a claim's key is a
    different one. A day later it landed in the owner's summary of what is stuck, and the
    neighbours stopped counting the session alive — that is, a finding would go into "Wave Loose
    Ends" past the very human sitting at the screen.

    It is safe for exactly this reason: the second route searches by an EXACT match of the worktree
    folder, which means the record it finds belongs to this very session and the file gains no
    second writer. The general ban on writing into SOMEONE ELSE'S claim file stands, and it is
    checked here as well that the hook still doesn't CREATE files: the file name stays the same,
    and no second one shows up in the registry.
    """
    real_worktrees(tmp_path, {"tab": "orphan-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "older-version-claim",
        **open_claim(str(deep), wave="wave9", stream="4", seen_at=now_minus(3)),
    )
    before = read_claim_json(orphan) or {}

    add(board, "wave9/4", "a finding for the address", cwd=tmp_path)
    brought = run_deliver(board, deep, "Start", "s-orphan-from-subfolder")

    assert "a finding for the address" in brought, (
        "a finding taken with the report 'it will get there on its own' never reached the session: "
        f"the delivery hook didn't find the older version's claim — {brought!r}"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, (
        "the delivery hook started a second file in the registry — creating files stays forbidden "
        "to it after the change too"
    )
    assert records[0].file == orphan, (
        f"the hook moved the claim into another file ({records[0].file.name}) — it is only allowed "
        "to write into the one it found"
    )
    after = read_claim_json(orphan) or {}
    assert str(after.get("seen_at", "")) > str(before.get("seen_at", "")), (
        "the liveness mark wasn't updated: the older version's claim gets its mail but from outside "
        f"looks silent — in a day it goes to the summary of what's stuck ({before=}, {after=})"
    )
    assert after.get("state") == "open" and after.get("wave") == "wave9", (
        f"the hook rewrote the claim instead of marking it: {after!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# The folder rule: one worktree folder holds no more than ONE unclosed claim.
#
# There is physically one claim per folder — its name is derived from the path. So announcing another
# stream from a taken folder does not "argue", it ERASES the previous entry silently, with a success
# code: the previous stream's tasks look untaken, no one addresses findings to it, and its own release
# at the end closes someone else's entry. That is how stream 9 of wave 5 vanished without a trace in a
# neighbouring project on 31.08.2026.
#
# Hence the place of the refusal — BEFORE writing. A warning AFTER is pointless here: there is nothing
# left to erase.


def tool_source() -> str:
    """The tool's own text — the ABSENCE of a switch is checked against it, not just behaviour."""
    return TOOL.read_text(encoding="utf-8")


def tool_switches() -> set[str]:
    """The tool's switches, as declared in its own parameter block."""
    source = tool_source()
    start = source.index("\nparam(")
    return set(re.findall(r"\[switch\]\$(\w+)", source[start : source.index("\n)", start)]))


def claim_block() -> str:
    """The text of the announcing block — from its own heading to the release heading."""
    source = tool_source()
    start = source.index("\n    'Claim' {")
    return source[start : source.index("\n    'Release' {", start)]


def folder_taken_refusal(
    board: Path, tab: Path, *, address: str, name: str, tasks: str, branch: str, state: str
) -> list[str]:
    """The folder rule's refusal WHOLE — in the very lines the decision promises.

    Checked WHOLE, and not by the address occurring in it: half the refusal's work is three ways out
    with the values already filled in. Had we checked the address alone, the refusal could have stayed
    a dead end (the session is told "you may not" and not told what to do), and the check would not
    have noticed.
    """
    kept = claim_of(board, tab, only_open=True)
    wave = str(kept.fields.get("wave", ""))
    stream = str(kept.fields.get("stream", ""))
    stamp = datetime.fromisoformat(str(kept.fields["claimed_at"])).strftime("%Y-%m-%d %H:%M")
    return [
        f'this worktree folder already holds a different stream: {wave}/{stream} "{name}", '
        f"tasks {tasks} — {state}, branch {branch}, claimed {stamp}.",
        f"There is ONE claim per folder: announcing stream {address} would erase it silently — the "
        "previous stream's tasks would look untaken, no one would address findings to it, and its "
        "release at the end would close someone else's entry.",
        "The previous stream is finished — release it right here: pwsh scripts/wave-board.ps1 "
        "-Mode Release",
        "This is that same stream and you're announcing it again — name its address: pwsh "
        f"scripts/wave-board.ps1 -Mode Claim -Wave {wave} -Stream {stream}",
        "The work is new — set up a separate worktree and announce from there.",
    ]


@needs_pwsh
@needs_git
def test_another_address_from_a_taken_folder_is_refused_before_a_single_byte_is_written(
    tmp_path: Path,
) -> None:
    """Another stream from a taken folder — refused, and the previous claim intact BYTE FOR BYTE.

    Byte for byte, because "the stream was not lost" and "the file was rewritten with the same fields"
    look alike from outside and cost differently: a rewritten claim loses the moment it was claimed
    (that is, its seniority in the dispute over the number) and the memory of former branch names, the
    ones findings still travel to the stream by.

    The refusal has to be actionable: it names the previous stream whole (address, name, tasks, state,
    branch and the moment it was claimed) and prints THREE ways out as ready-made lines. Otherwise the
    session hits a non-zero code on its very first action, decides the tool is broken and goes off to
    work with no claim — and a session invisible from outside is worse than any of the defects the
    rule fixes.
    """
    real_worktrees(tmp_path, {"tab": "taken-folder-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "8", "-StreamName", "Occupier", "-Tasks", "10-13")
    kept = claim_of(board, tab, only_open=True)
    before = kept.file.read_bytes()
    expected = folder_taken_refusal(
        board,
        tab,
        address="wave9/3",
        name="Occupier",
        tasks="10-13",
        branch="taken-folder-branch",
        state="live",
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab)

    assert denied.returncode != 0, (
        f"announcing another stream from a taken folder went through: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, (
        "the previous claim's file was touched — and with it went the stream's seniority and the "
        "memory of the branch names findings still travel to it by"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"a line of the refusal was reworded or lost: {line!r} — {denied.stderr!r}"
        )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, f"a second entry appeared in the registry: {names_of(records)}"
    assert records[0].address == "wave9/8" and not records[0].released, (
        f"the previous stream vanished from the registry or closed itself: {records[0].fields!r}"
    )


@needs_pwsh
@needs_git
def test_a_different_wave_with_the_same_stream_number_is_refused_before_a_single_byte_is_written(
    tmp_path: Path,
) -> None:
    """The same stream NUMBER but from a DIFFERENT wave — also refused, previous claim intact byte for byte.

    The neighbouring check above takes the edge "same wave, different number". This one is its mirror:
    the number is the very same, the wave is different. A stream's address is the pair (wave, number)
    WHOLE, and either of the two halves can fail to match on its own. Were the rule to check the number
    alone, forgetting the wave, it would decide that announcing wave B with wave A's number is that
    same stream announcing again, and it would erase A's claim silently: the same defect of 31.08.2026
    (see the neighbouring check), only from the other side of the pair.
    """
    real_worktrees(tmp_path, {"tab": "taken-folder-other-wave-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "8", "-StreamName", "Occupier", "-Tasks", "10-13")
    kept = claim_of(board, tab, only_open=True)
    before = kept.file.read_bytes()
    expected = folder_taken_refusal(
        board,
        tab,
        address="wave10/8",
        name="Occupier",
        tasks="10-13",
        branch="taken-folder-other-wave-branch",
        state="live",
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave10", "-Stream", "8", cwd=tab)

    assert denied.returncode != 0, (
        f"announcing a different wave with the same stream number from a taken folder went through: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, (
        "the previous claim's file was touched — and with it went the stream's seniority and the "
        "memory of the branch names findings still travel to it by"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"a line of the refusal was reworded or lost: {line!r} — {denied.stderr!r}"
        )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, f"a second entry appeared in the registry: {names_of(records)}"
    assert records[0].address == "wave9/8" and not records[0].released, (
        f"the previous stream vanished from the registry or closed itself: {records[0].fields!r}"
    )


@needs_pwsh
@needs_git
def test_the_same_claim_goes_through_once_the_previous_stream_is_released(tmp_path: Path) -> None:
    """Release the previous stream, and the same announcement goes through. That is the first way out.

    Without this half the folder rule would be a dead end: the refusal advises releasing the previous
    stream right here, and if the announcement still does not go through after the release, the advice
    is a lie. A released claim counts as no obstacle: the session that ran that stream is gone, and
    the next stream in the same folder is the normal course of work.
    """
    real_worktrees(tmp_path, {"tab": "round-trip-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "8", "-StreamName", "Previous", "-Tasks", "10-13")
    assert (
        tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab).returncode != 0
    ), "the check is built wrong: announcing another stream from a taken folder did not refuse"

    assert release(board, tab).returncode == 0, "releasing the previous stream did not go through"
    passed = claim(board, tab, "wave9", "3", "-StreamName", "Next")

    assert "Stream wave9/3 announced for this session" in passed, (
        f"after the release the announcement still failed — the way out of the refusal lies: {passed!r}"
    )
    assert claim_of(board, tab, only_open=True).address == "wave9/3", (
        "the new stream did not take the place of the released one"
    )


@needs_pwsh
@needs_git
def test_no_key_at_all_lets_a_claim_overwrite_the_stream_that_holds_the_folder(
    tmp_path: Path,
) -> None:
    """There is NO force-overwrite switch in the tool AT ALL — and none must ever appear.

    A neighbouring copy has such a switch, and it is overloaded with a second, harmless meaning
    (releasing with a non-empty inbox) that the tool itself recommends — hence the habit of hitting it
    without looking. And destructive it is without any need: releasing the previous stream happens in
    that very same folder, loses nothing and gives the same outcome. So the whole class is cut out,
    not the one case.

    It is exactly the ABSENCE that is checked, in four ways at once: the tool has exactly four known
    switches (a new bypass would have to be declared as a fifth), the announcing block does not
    mention a force switch (a second meaning cannot be fitted to it unnoticed), the folder rule's
    refusal prints no switch at all — otherwise the session would take the single printed way out —
    and the address MOVE switch does not bypass the folder rule either.

    ‼️ That last one is the real protection today. The move switch is the only switch added to
    announcing after the class of bypasses was cut out, and the temptation to fit it with a second
    meaning ("erase whatever was lying here while you are at it") is exactly the one that made a
    neighbour's stream vanish silently. A move takes the ADDRESS away from another folder and writes
    not one byte into the other file; it does not touch YOUR OWN folder's claim and does not cancel
    the folder rule.
    """
    real_worktrees(tmp_path, {"tab": "no-bypass-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "8", "-StreamName", "Holds the folder", "-Tasks", "10-13")
    kept = claim_of(board, tab, only_open=True)
    before = kept.file.read_bytes()

    forced = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", "-Force", cwd=tab)

    assert forced.returncode != 0, (
        f"the release-with-a-non-empty-inbox switch worked as a bypass of the folder rule: {forced.stdout!r}"
    )
    assert kept.file.read_bytes() == before, "the previous stream's claim was overwritten by a switch"
    assert tool_switches() == {"AllowUnknownStream", "ForAll", "Force", "TakeOver"}, (
        f"a new switch appeared in the tool: {sorted(tool_switches())} — if it is a bypass of the "
        "folder rule, streams will start vanishing silently again"
    )
    assert "$Force" not in claim_block(), (
        "the announcing block mentions the force switch again — a second meaning turns it into a "
        "switch people hit without looking"
    )
    offered = [line for line in said(forced.stderr) if "-Force" in line]
    assert not offered, f"the refusal offers a switch instead of three harmless ways out: {offered!r}"

    taken = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", "-TakeOver", cwd=tab)

    assert taken.returncode != 0, (
        f"the move switch worked as a bypass of the folder rule: {taken.stdout!r}"
    )
    assert "this worktree folder already holds a different stream" in taken.stderr, (
        f"the wrong guard refused — the folder rule was bypassed: {taken.stderr!r}"
    )
    assert kept.file.read_bytes() == before, "the previous claim was overwritten by the move switch"


@needs_pwsh
@needs_git
def test_the_registry_lock_is_free_the_moment_the_folder_rule_refuses(tmp_path: Path) -> None:
    """After a refusal the registry lock is let go: a neighbouring session announces at once, no wait.

    The refusal comes from under the lock taken for picking a number. Were the tool to leave without
    letting it go, every neighbouring session would pay half a minute of waiting for someone else's
    refusal on its very first action — and after the wait it would announce WITHOUT the lock, that is,
    on a number that could collide with a neighbour's.
    """
    real_worktrees(tmp_path, {"tab": "refusal-branch", "neighbour": "neighbour-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    mate = tmp_path / "neighbour"
    claim(board, tab, "wave9", "8", "-StreamName", "Occupier", "-Tasks", "10-13")
    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab)
    assert denied.returncode != 0, "the check is built wrong: there was no refusal"

    started = time.monotonic()
    neighbour = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "4", cwd=mate)
    elapsed = time.monotonic() - started

    assert neighbour.returncode == 0, f"the neighbouring session did not announce: {neighbour.stderr!r}"
    assert "Waiting for the claim registry lock" not in neighbour.stdout, (
        f"the neighbour waited on a lock abandoned by the refusal: {neighbour.stdout!r}"
    )
    assert "The claim registry lock wasn't handed over" not in neighbour.stdout, (
        "the neighbour gave up waiting for the lock and picked a number without it — the very case "
        f"where two streams get one address: {neighbour.stdout!r}"
    )
    assert elapsed < 15, (
        f"the neighbour's announcement took {elapsed:.1f}s against a 30s lock wait limit — it looks "
        "as if the lock stayed held after the refusal"
    )


@needs_pwsh
@needs_git
def test_a_claim_from_a_subfolder_of_a_neighbours_tree_never_erases_their_stream(
    tmp_path: Path,
) -> None:
    """Announcing from a subfolder of SOMEONE ELSE'S tree does not erase the neighbour's claim.

    A session's key is now the worktree root, so announcing from any folder of a neighbour's tree
    lands on THE SAME key as the neighbour's claim and would overwrite it silently, with a success
    code. Before, such an announcement made a file of its own and harmed no one — that is, without the
    folder rule the change of key by itself creates the very defect it fixes.

    The case is not invented: all the project's worktrees lie in one folder, from any of them to any
    other is exactly one hop, and the rules plainly tell a session to go and look whose piece this is.
    """
    real_worktrees(tmp_path, {"neighbour": "neighbour-branch", "tab": "own-branch"})
    board = tmp_path / "board.jsonl"
    mate = tmp_path / "neighbour"
    claim(board, mate, "wave9", "5", "-StreamName", "Neighbour", "-Tasks", "20-22")
    kept = claim_of(board, mate, only_open=True)
    before = kept.file.read_bytes()

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "6", cwd=subfolder_of(mate))

    assert denied.returncode != 0, (
        f"announcing from someone else's subfolder went through — the neighbour's claim is erased: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, "the neighbour's claim was overwritten"
    assert 'wave9/5 "Neighbour", tasks 20-22' in denied.stderr, (
        f"the refusal did not name the stream that holds this folder: {denied.stderr!r}"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1 and records[0].address == "wave9/5", (
        f"the registry changed although the announcement was refused: {names_of(records)}"
    )


@needs_pwsh
@needs_git
def test_a_claim_of_the_older_version_from_a_subfolder_is_written_back_in_place(
    tmp_path: Path,
) -> None:
    """An older version's claim from a subfolder plus an announcement on the same address give ONE entry.

    A claim's file name is derived from the path, and for such claims the key is the subfolder, while
    the canonical key today is the tree root. Without a second route the announcement made a SECOND
    open entry of the same stream alongside it: the tool printed the session a warning that it was
    arguing with itself, and wrote the second entry anyway, with a success code. Which of them a
    finding would then reach was decided by the directory listing — and that is the very defect the
    whole change was undertaken for.

    The second route is the one release and the delivery hook already use: an exact match on the
    worktree folder recorded in the claim. We write back into the FOUND file: not one file is created,
    deleted or renamed.
    """
    real_worktrees(tmp_path, {"tab": "orphan-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "older-version-claim",
        **open_claim(str(deep), wave="wave9", stream="4"),
    )

    out = claim(board, deep, "wave9", "4", "-StreamName", "The same stream")

    records = read_registry(registry_dir(board))
    assert len(records) == 1, (
        f"the announcement made a second entry of the same stream: {names_of(records)} — which of "
        "them a finding reaches is decided by the directory listing"
    )
    assert records[0].file == orphan, (
        f"the entry landed in a new file instead of the found one: {records[0].file.name}"
    )
    assert records[0].fields.get("name") == "The same stream", (
        f"the announcement never reached the found claim: {records[0].fields!r}"
    )
    assert "Another worktree has an open claim" not in out, (
        f"the session was told that it is arguing with itself: {out!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_of_the_older_version_from_a_subfolder_holds_the_folder_too(tmp_path: Path) -> None:
    """An older version's claim from a subfolder is your own, and the folder rule sees it.

    Otherwise the second route would itself become a hole: were the rule not to recognize such a claim
    as THIS folder's claim, an announcement on a different address would write the new stream straight
    into its file — that is, would erase the previous stream the same way, only more quietly.
    """
    real_worktrees(tmp_path, {"tab": "orphan-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    deep = subfolder_of(tab)
    orphan = put_claim(
        registry_dir(board),
        "older-version-claim",
        **open_claim(str(deep), wave="wave9", stream="4"),
    )
    before = orphan.read_bytes()

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "9", cwd=deep)

    assert denied.returncode != 0, (
        f"another stream was written over an older version's claim: {denied.stdout!r}"
    )
    assert orphan.read_bytes() == before, "the older version's claim was overwritten"
    assert "wave9/4" in denied.stderr, f"the refusal did not name the folder's holder: {denied.stderr!r}"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# The short re-announcement: a session announces again from ITS OWN folder, naming neither the wave
# nor the number.
#
# The wave-substitution ladder had no "your own previous claim" step, so the stream drifted off into
# the wave named by today's date and lost, along with the address, its name, its tasks, the plan path,
# the "is there a plan" flag and its seniority in the dispute over the number. Neighbours meanwhile
# went on addressing findings to the old address, and it was no longer in the registry. With the
# folder rule the case became VISIBLE (the announcement got refused), but no more correct: the session
# hit a refusal where it should simply carry on with its own stream.
#
# The entry is checked WHOLE, and not by a string occurring in it. Half of what was lost (the moment
# of the claim, the "is there a plan" flag, the plan path) is not visible from outside at all, and a
# piecewise check would miss exactly that half — and on the "is there a plan" flag hangs the whole
# "Wave Loose Ends or a reply to the owner" fork.


def claim_stamp(fields: dict[str, object]) -> str:
    """The moment a claim was made — in the very shape the tool prints it."""
    return datetime.fromisoformat(str(fields["claimed_at"])).strftime("%Y-%m-%d %H:%M")


def reclaimed_the_same_stream(before: dict[str, object], after: dict[str, object]) -> None:
    """After a short re-announcement the entry must match the previous one WHOLE.

    Except for the liveness mark: that is what says "the session is on the move", and it is supposed
    to be refreshed. Everything else — the address, the name, the tasks, the plan path, the "is there
    a plan" flag, the moment of the claim, the memory of branch names, the worktree folder, the state
    — belongs to the STREAM and not to the call, and a re-announcement does not touch it.
    """
    was = {key: value for key, value in before.items() if key != "seen_at"}
    now = {key: value for key, value in after.items() if key != "seen_at"}
    assert now == was, (
        "the short re-announcement changed the stream's entry — so the stream lost part of itself:\n"
        f"  was: {was}\n"
        f"  now: {now}"
    )


@needs_pwsh
def test_a_short_reclaim_continues_a_stream_of_a_named_wave(tmp_path: Path) -> None:
    """The wave was named in the first announcement — a short reclaim continues THAT SAME stream.

    The costliest of the three cases: in a wave that comes from a plan the stream numbers are declared
    IN THE PLAN, and a stream that has drifted into the wave named by today's date becomes invisible
    to neighbours under exactly the address the plan calls it by. Findings go off into the void, with
    a cheerful report back to the sender.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "3", "-StreamName", "Stream identity", "-Tasks", "10-13")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert before["wave_auto"] is False, (
        f'the check is built wrong: a named wave has the wrong "is there a plan" flag: {before!r}'
    )

    out = claim_bare(board, tab)

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        "Wave not named — inherited from your previous entry. You're continuing stream wave9/3 "
        f'"Stream identity", claimed {claim_stamp(before)}.' in said(out)
    ), f"the fourth source of the wave is not named, or its line was reworded: {out!r}"
    assert (
        "The work is different — release the previous stream right here (-Mode Release) and announce again."
        in said(out)
    ), f"the way out of an inherited address is not named: {out!r}"
    assert "issued the next free one" not in out, (
        f"an inherited number was passed off as freshly issued — the session was lied to: {out!r}"
    )

    # The "is there a plan" flag is checked in earnest too: the whole release fork hangs on it.
    done = release(board, tab)
    assert done.returncode == 0, done.stderr
    assert (
        'Last step — a line for your stream in the wave plan\'s "Stream status" section.'
        in said(done.stdout)
    ), f"after the reclaim a stream of a planned wave was left with no plan: {done.stdout!r}"


@needs_pwsh
def test_a_short_reclaim_continues_a_stream_whose_wave_came_from_the_plan_name(
    tmp_path: Path,
) -> None:
    """The wave came from the plan's file name — a short reclaim keeps both it and the plan path.

    The plan path sits in the claim as a field of its own, and losing it is the quietest loss of all:
    from outside it is not visible at all, yet it is needed where the session is told which section of
    the plan to write its summary into.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    tab.mkdir()
    plan = tab / "2026-08-24-wave9.md"
    plan.write_text("# wave 9 plan\n", encoding="utf-8")
    claim_bare(board, tab, "-Plan", str(plan), "-StreamName", "From the plan name", "-Tasks", "1-4")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert before["plan"] == str(plan) and before["wave_auto"] is False, (
        f"the check is built wrong: the wave did not come from the plan's file name: {before!r}"
    )

    out = claim_bare(board, tab)

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        "Wave not named — inherited from your previous entry. You're continuing stream wave9/1 "
        f'"From the plan name", claimed {claim_stamp(before)}.' in said(out)
    ), f"the fourth source of the wave is not named, or its line was reworded: {out!r}"

    done = release(board, tab)
    assert done.returncode == 0, done.stderr
    assert (
        'Last step — a line for your stream in the wave plan\'s "Stream status" section.'
        in said(done.stdout)
    ), f"after the reclaim the stream lost the plan taken from the file name: {done.stdout!r}"


@needs_pwsh
def test_a_short_reclaim_continues_a_stream_of_a_wave_the_tool_invented(tmp_path: Path) -> None:
    """The tool supplied the wave itself — a reclaim starts no second one and loses no seniority.

    Here the address did not change even before the fix (the tool gave this folder back its previous
    number), which is why the loss was the least noticeable of all: away went the name, the tasks and
    the MOMENT OF THE CLAIM — that is, the seniority in the dispute over the number. A session that
    announced itself again began yielding its number to neighbours who came into the wave LATER than
    it did.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim_bare(board, tab, "-StreamName", "No plan", "-Tasks", "5-7")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert before["wave_auto"] is True, (
        f"the check is built wrong: the wave was not supplied by the tool itself: {before!r}"
    )

    out = claim_bare(board, tab)

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        "Wave not named — inherited from your previous entry. You're continuing stream "
        f'{today_wave()}/1 "No plan", claimed {claim_stamp(before)}.' in said(out)
    ), f"the fourth source of the wave is not named, or its line was reworded: {out!r}"

    done = release(board, tab)
    assert done.returncode == 0, done.stderr
    assert "No wave plan — nowhere to write a stream line; the summary goes in your reply to the owner." in said(
        done.stdout
    ), f"after the reclaim a planless stream acquired a plan: {done.stdout!r}"


@needs_pwsh
def test_a_short_reclaim_of_an_old_claim_does_not_invent_the_flag_it_never_had(
    tmp_path: Path,
) -> None:
    """An older version's claim has no "the wave was supplied itself" flag — and none may be invented.

    Every reader judges such a claim by the name of the wave: `waveN` is a plan's wave, a word or a
    date is a wave of its own. Let a reclaim invent a value for it, and a wave named by a WORD would
    suddenly become "supplied by the tool itself" — and a neighbour has every right to join such a
    wave and take in it a number the plan has already declared. Half the wave's findings would go
    astray, silently.

    So what is inherited is not a computed value but the field exactly as it lay — together with its
    absence.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "sprint-alpha", "1", "-StreamName", "An older version's claim")
    strip_claim_field(board, tab, "wave_auto")
    before = dict(claim_of(board, tab, only_open=True).fields)
    assert "wave_auto" not in before, "the check is built wrong: the flag was not removed from the claim"

    claim_bare(board, tab)

    after = dict(claim_of(board, tab, only_open=True).fields)
    reclaimed_the_same_stream(before, after)
    assert "wave_auto" not in after, (
        f"the reclaim invented a flag the claim never had: {after!r}"
    )

    # A check in earnest: the neighbour's right to join this wave must not come into being.
    newcomer = claim_bare(board, tmp_path / "neighbour")
    assert "sprint-alpha" not in newcomer, (
        f"the neighbour joined a wave named by a word and took a number in it: {newcomer!r}"
    )


@needs_pwsh
@needs_git
def test_a_short_reclaim_keeps_the_names_the_stream_is_remembered_by(tmp_path: Path) -> None:
    """A short reclaim — and the memory of the stream's former branch names stayed with it.

    Name inheritance is tied to the ADDRESS, and a short announcement names no address. So these two
    changes hold each other up: were they to drift apart, a session announcing itself again would get
    a new address and lose with it the names findings still travel to it by. The loss is silent:
    accepting takes such a finding (the tree is in place) and promises the author delivery, and there
    is no delivery.

    The trees are real: the branch name is taken from git, and on stub folders there is none at all.
    """
    real_worktrees(tmp_path, {"tab": "alpha", "neighbour": "neighbour-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    neighbour = tmp_path / "neighbour"
    claim(board, tab, "wave6", "1", "-StreamName", "The same stream")
    rename_branch(tab, "beta")

    claim_bare(board, tab)

    record = claim_of(board, tab, only_open=True)
    assert record.address == "wave6/1", (
        f"the short reclaim moved the stream onto a different address: {record.fields!r}"
    )
    assert record.fields["former_branches"] == ["alpha"], (
        f"the stream forgot the name it was known by before the branch was renamed: {record.fields!r}"
    )

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "alpha",
        "-Title",
        "by the branch's former name",
        cwd=neighbour,
        known=True,
    )
    assert offered.returncode == 0, (
        f"accepting did not recognize the stream by its branch's former name: {offered.stderr!r}"
    )

    brought = run_deliver(board, tab, "Start", "short-reclaim")
    assert "by the branch's former name" in brought, (
        f"the finding sent by the former name was not delivered — it will never arrive: {brought!r}"
    )


@needs_pwsh
@needs_git
def test_a_new_stream_over_a_released_one_does_not_inherit_its_names(tmp_path: Path) -> None:
    """A stream released, the next announced from the same folder — the released one's names stay behind.

    Before, former branch names were carried across on a match of the FOLDER alone. And folders get
    reused all the time: a stream is released, the next one is announced in the same folder — and the
    new one began answering to the released one's name and receiving the findings addressed to it. The
    author who sent a finding by the released stream's branch name read "noted" from a stream they had
    never named, while the stream itself never saw the finding at all.

    A branch name is a way of addressing a finding to a STREAM, so it is inherited by address (wave
    and number), and not by folder. That inheritance stayed as it was where the address matches is
    guarded by the neighbouring check about the short re-announcement.
    """
    real_worktrees(tmp_path, {"tab": "alpha", "neighbour": "neighbour-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    neighbour = tmp_path / "neighbour"
    claim(board, tab, "wave6", "1")
    # The branch was renamed and re-announced on the same address — the stream now REMEMBERS the old name.
    rename_branch(tab, "beta")
    claim(board, tab, "wave6", "1")
    assert claim_of(board, tab, only_open=True).fields["former_branches"] == ["alpha"], (
        "the check is built wrong: the stream did not remember its branch's former name"
    )

    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "alpha",
        "-Title",
        "to the released one by its former name",
        cwd=neighbour,
        known=True,
    )
    assert offered.returncode == 0, f"the finding by the former name was not accepted: {offered.stderr!r}"

    given = release(board, tab, "-Force")
    assert given.returncode == 0, f"releasing the stream did not go through: {given.stdout!r} {given.stderr!r}"

    claim(board, tab, "wave6", "2", "-StreamName", "Next")
    record = claim_of(board, tab, only_open=True)
    assert record.fields["former_branches"] == [], (
        f"the new stream took the released one's names for itself: {record.fields!r}"
    )

    # A positive check IN THE SAME environment: what is its own does reach the new stream. Without it
    # the silence of the check below would be indistinguishable from a dead delivery hook — and it
    # would go green whatever the code did.
    mine = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "wave6/2",
        "-Title",
        "to the new stream by address",
        cwd=neighbour,
        known=True,
    )
    assert mine.returncode == 0, f"the finding for the new stream was not accepted: {mine.stderr!r}"

    brought = run_deliver(board, tab, "Start", "folder-reuse")
    assert "to the new stream by address" in brought, (
        f"the delivery hook is silent entirely — the check below would be green at any code: {brought!r}"
    )
    assert "to the released one by its former name" not in brought, (
        f"the released stream's finding reached the one who merely sat down in its folder: {brought!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Stream identity: who exactly is being continued, and who has merely sat down in the same folder.
#
# The checks below close four findings of the independent review of this change. They have one
# thing in common: "the same stream" was recognized by signs that DON'T belong to the STREAM — by
# the folder, by a number that happened to match, by silence about the wave — and identity leaked
# now to the folder's new tenant, now to a released neighbour, and now was lost altogether.


@needs_pwsh
@needs_git
def test_a_stream_claimed_after_a_release_gets_a_free_number_and_none_of_the_names(
    tmp_path: Path,
) -> None:
    """A stream is honestly released, the next announces from the same folder — FREE number, no names.

    The memory of branch names passed to the folder's new tenant all by itself, without a single
    switch, and the root here runs deeper than inheritance: picking a number returned the number of
    THIS folder's OWN previous claim without looking at its state. A released one counted as its
    own too, so the next stream in the reused folder got the same address — and along with the
    address it inherited both the released one's memory of names and its post. And at release time
    the tool prints a promise that findings for a released stream will no longer be accepted.

    The decision speaks about this case plainly: the folder rule doesn't apply, the announcement is
    an ordinary one, the number is FREE, the memory of names is NOT inherited — it passes only
    between claims of one and the same address.

    The trees are real: a stream's names are taken from git, and on stub folders they never appear.
    """
    real_worktrees(tmp_path, {"tab": "alpha", "neighbour": "neighbour-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    neighbour = tmp_path / "neighbour"
    claim(board, tab, "wave6", "1", "-StreamName", "Releasing")
    # The branch was renamed and the same address announced again — the stream now REMEMBERS the
    # former name.
    rename_branch(tab, "beta")
    claim(board, tab, "wave6", "1")
    assert claim_of(board, tab, only_open=True).fields["former_branches"] == ["alpha"], (
        "the check is built wrong: the stream didn't remember the former name of its branch"
    )
    offered = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        "alpha",
        "-Title",
        "for the released one by its old name",
        cwd=neighbour,
        known=True,
    )
    assert offered.returncode == 0, f"a finding by the old name wasn't accepted: {offered.stderr!r}"
    assert release(board, tab, "-Force").returncode == 0, "releasing the stream failed"

    claim_bare(board, tab, "-Wave", "wave6", "-StreamName", "Next")

    record = claim_of(board, tab, only_open=True)
    assert record.address == "wave6/2", (
        f"the next stream of the same folder got the released one's number: {record.fields!r} — "
        "neighbours will go on sending findings to that address that it never awaited"
    )
    assert record.fields["former_branches"] == [], (
        f"the new stream took the released one's names for itself: {record.fields!r}"
    )

    # A positive check IN THE VERY SAME setup: what belongs to the new stream does reach it. Without
    # it, the silence of the check below would be indistinguishable from a dead delivery hook.
    mine = tool(
        board,
        "-Mode",
        "Add",
        "-To",
        record.address,
        "-Title",
        "for the new stream by address",
        cwd=neighbour,
        known=True,
    )
    assert mine.returncode == 0, f"the finding for the new stream wasn't accepted: {mine.stderr!r}"

    brought = run_deliver(board, tab, "Start", "after-the-release")
    assert "for the new stream by address" in brought, (
        f"the delivery hook says nothing at all — the check below would be green on any code: {brought!r}"
    )
    assert "for the released one by its old name" not in brought, (
        f"a released stream's finding reached whoever merely sat down in its folder: {brought!r}"
    )


@needs_pwsh
def test_a_reclaim_that_names_only_the_wave_keeps_the_rest_of_the_stream(tmp_path: Path) -> None:
    """The wave was named, the number wasn't — the stream continues WHOLE, not by halves.

    The entire inheritance step hung on the condition "the wave wasn't named". Yet a tab is fully
    entitled to announce itself afresh, naming its own wave in words (or passing a plan path) and
    not naming a number: the address didn't change from that — the number was still picked as its
    own. But the name, the tasks and the plan path were erased, the claim time was reset, and on
    top of that the tool printed an untruth, as if the number had been issued as the next free one.

    Continuing a stream is never partial: unnamed fields are taken from one's own unclosed claim of
    the SAME ADDRESS, wherever the wave itself came from. The whole record is checked — half of
    what is lost (the claim time, the plan path) is not visible from the outside at all.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    tab.mkdir()
    plan = tab / "2026-08-24-wave9.md"
    plan.write_text("# wave 9 plan\n", encoding="utf-8")
    claim(
        board,
        tab,
        "wave9",
        "3",
        "-StreamName",
        "Stream identity",
        "-Tasks",
        "10-13",
        "-Plan",
        str(plan),
    )
    before = dict(claim_of(board, tab, only_open=True).fields)

    out = claim_bare(board, tab, "-Wave", "wave9")

    reclaimed_the_same_stream(before, dict(claim_of(board, tab, only_open=True).fields))
    assert (
        f"You're continuing stream wave9/3 \"Stream identity\", claimed {claim_stamp(before)}."
        in said(out)
    ), f"the tab wasn't told it is continuing its own stream, or the line was rewritten: {out!r}"
    assert (
        "The work is different — release the previous stream right here (-Mode Release) and "
        "announce again." in said(out)
    ), f"the way out of the inherited address isn't named: {out!r}"
    assert "issued the next free one" not in out, (
        f"an inherited number was passed off as freshly issued — the tab was told an untruth about "
        f"its own address: {out!r}"
    )


@needs_pwsh
def test_a_reclaim_that_names_only_the_wave_keeps_its_seniority(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """And seniority survives with it: the yield ring won't hand the address to a later neighbour.

    Of everything lost in a partial re-announcement, the claim time is the most dangerous: it, and
    it alone, measures seniority in a dispute over a number. Reset it, and the tab yields its
    address to anyone who announced themselves between its two announcements — that is, it moves
    silently to a number its neighbours know nothing about, while at its former address findings
    are accepted by somebody else.
    """
    registry_invariants.waive(
        "the invariant \"one leading record per address\": a rival for the same number is built by "
        "hand here, and what is being checked is precisely that the tab does NOT give the address "
        "up. The address rule deliberately doesn't part this pair: the number here is INHERITED "
        "from the tab's own previous record, that is, it stays issued, and the address rule doesn't "
        "cover an issued number — there the number may be moved, and the dispute is settled by the "
        "yield ring"
    )
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "3", "-StreamName", "Senior", "-Tasks", "10-13")
    # The stream has been running since yesterday. Otherwise there is nothing to check: a claim made
    # a second ago stays the senior one even with the time reset — the reset shifts it by that very
    # second.
    patch_claim(
        board,
        tab,
        claimed_at=(datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),
    )
    put_claim(
        registry_dir(board),
        "neighbour-who-came-later",
        **open_claim(
            str(tmp_path / "neighbour"),
            wave="wave9",
            stream="3",
            claimed_at=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        ),
    )

    out = claim_bare(board, tab, "-Wave", "wave9")

    assert claim_of(board, tab, only_open=True).address == "wave9/3", (
        "the tab gave its address up to a neighbour that announced itself LATER than it did — so "
        f"the re-announcement reset the claim time, and seniority along with it: {out!r}"
    )
    assert "shifted to the next free one" not in out, (
        f"the stream was moved off its own address while its seniority was alive: {out!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_never_writes_over_the_live_record_of_a_dirty_folder(tmp_path: Path) -> None:
    """A released and an open record in one folder — an announcement mustn't overwrite the LIVE one.

    There were two choices of "this folder's previous claim", and they diverged: the folder rule
    took the FIRST record in the registry listing (a released one doesn't count, so no refusal was
    raised), while the file write went to the one found under the canonical key — that is, to the
    live one. Announcing another stream went through with a success code and erased a working one:
    exactly the incident this whole change was started for, only coming in from the other side.

    Such a folder is not invented: an announcement writes an older version's claim from a subfolder
    in place, so its file stays lying under the old name next to the new one.
    """
    real_worktrees(tmp_path, {"tab": "dirty-folder-branch"})
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim(board, tab, "wave9", "8", "-StreamName", "Live one", "-Tasks", "10-13")
    live = claim_of(board, tab, only_open=True)
    before = live.file.read_bytes()
    # A released record of THE SAME folder under its own file name. Its number is lower — so it
    # comes first in the registry listing, and the former choice "the first in order" fell to it.
    put_claim(
        registry_dir(board),
        "released-of-the-older-version",
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
        name="Live one",
        tasks="10-13",
        branch="dirty-folder-branch",
        state="live",
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tab)

    assert denied.returncode != 0, (
        f"announcing another stream went through over the folder's live record: {denied.stdout!r}"
    )
    assert live.file.read_bytes() == before, (
        "the live stream was overwritten silently — the folder rule looked at the released record "
        "while the write went over the working one"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"the refusal named the wrong stream or the line was rewritten: {line!r} — "
            f"{denied.stderr!r}"
        )
    records = read_registry(registry_dir(board))
    assert len(records) == 2, (
        f"the registry changed though the announcement was refused: {names_of(records)}"
    )


@needs_pwsh
def test_the_continued_stream_is_named_by_the_address_it_really_had(tmp_path: Path) -> None:
    """The "you're continuing stream …" line names the address the stream HAD, not the one just issued.

    The line is printed after the yield ring. A senior neighbour holds the number, the tab is
    shifted to the next free one — and the tab was told it is continuing a stream at an address it
    never had, and "claimed yesterday" on top of that. On the next line the tool honestly speaks
    about the shift, but the first line is believed before the second one is read, and it is the
    address from that first line the tab passes on to its neighbours.
    """
    board = tmp_path / "board.jsonl"
    tab = tmp_path / "tab"
    claim_bare(board, tab, "-StreamName", "Shifted", "-Tasks", "5-7")
    mine = claim_of(board, tab, only_open=True)
    before = dict(mine.fields)
    earlier = datetime.fromisoformat(str(mine.fields["claimed_at"])) - timedelta(hours=1)
    put_claim(
        registry_dir(board),
        "senior-neighbour",
        **open_claim(
            str(tmp_path / "neighbour"),
            wave=today_wave(),
            stream="1",
            wave_auto=True,
            claimed_at=earlier.isoformat(timespec="seconds"),
        ),
    )

    out = claim_bare(board, tab)

    assert claim_of(board, tab, only_open=True).address == f"{today_wave()}/2", (
        f"the check is built wrong: the yield ring didn't shift the stream — {out!r}"
    )
    assert (
        "Wave not named — inherited from your previous entry. You're continuing stream "
        f"{today_wave()}/1 \"Shifted\", claimed {claim_stamp(before)} — the number was yielded to a "
        "neighbour, the new address is named below." in said(out)
    ), f"the line about continuing the stream names a foreign address or was rewritten: {out!r}"
    assert f"You're continuing stream {today_wave()}/2" not in out, (
        f"the tab was told it continues an address its stream never had: {out!r}"
    )
    assert (
        f"‼️ A neighbouring session announced number {today_wave()}/1 at the very same moment — "
        f"your stream was shifted to the next free one: {today_wave()}/2." in said(out)
    ), f"the tab wasn't told about the shift itself: {out!r}"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# DEFECT 1: a move doubles the address.
#
# A tab announced itself from a shared folder, set up a worktree and announced itself from there
# under the same address — the registry ended up with two open records of one address, and who got
# a finding was decided by the directory listing order. It is cured by two changes at once, and the
# order between them is strict:
#   • the address rule — a refusal BEFORE the write, with the clues and one explicit take-over key;
#   • one single sign of "the record is closed" — otherwise the losing tab goes on receiving the
#     new owner's post and silencing it for them.
#
# ‼️ A move is written as a field IN ONE'S OWN claim, not by editing someone else's file: another
# folder's claim has a second writer (that folder's delivery hook), which rewrites it whole on every
# turn and takes no lock. This same thing buys compatibility with an older copy of the toolkit in
# two dozen live trees.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def address_taken_refusal(
    board: Path, rival: Path, *, address: str, state: str, disk: str, distinct: str
) -> list[str]:
    """The address rule's refusal in full — in the very lines the decision promises.

    It is checked IN FULL, not by the address occurring in it: half the refusal's work is the clues
    (the other folder, its state, whether it is on disk) and three ways out with the values already
    substituted. The order of the ways out is part of the promise too: the HARMLESS one first, the
    destructive one last, because a tab uses the first thing printed.
    """
    fields = claim_of(board, rival, only_open=False).fields
    stamp = datetime.fromisoformat(str(fields["seen_at"])).strftime("%Y-%m-%d %H:%M")
    where = str(fields["worktree"])
    wave, stream = address.split("/")
    return [
        f"address {address} is already run by an unclosed claim of a DIFFERENT worktree folder: "
        f"{where} — {state}, checked in {stamp}, {disk}.",
        "There is ONE leading entry per address: announce as a second one, and which of you got a "
        "finding would be decided by the directory listing order — half of what is addressed would "
        "vanish with a cheerful report of success.",
        "A different split of the same wave — announce under your own number: pwsh "
        f"scripts/wave-board.ps1 -Mode Claim -Wave {wave} -Stream {distinct}",
        f"That stream is finished — release it, standing in exactly its folder {where}: "
        "pwsh scripts/wave-board.ps1 -Mode Release",
        "This is your stream and you moved here (or are picking up an abandoned session) — take "
        f"the address for yourself, folder {where} will lose it: pwsh scripts/wave-board.ps1 "
        f"-Mode Claim -Wave {wave} -Stream {stream} -TakeOver",
    ]


@needs_pwsh
def test_a_move_with_a_named_address_is_refused_before_a_single_byte_is_written(
    tmp_path: Path,
) -> None:
    """A move without the take-over key — refused BEFORE the write, the old claim untouched byte for byte.

    This is the main form of defect 1: a tab announced itself from a shared folder, set up a
    worktree and announces itself from there under the same, explicitly named address. Today that
    left two open records of one address in the registry — the refusal is placed where the doubling
    hasn't happened yet.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    tree.mkdir()
    claim(board, common, "wave9", "3", "-StreamName", "Move", "-Tasks", "10-13")
    kept = claim_of(board, common, only_open=True)
    before = kept.file.read_bytes()

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tree)

    assert denied.returncode != 0, (
        f"the move went through silently — the address is run by two: {denied.stdout!r}"
    )
    assert kept.file.read_bytes() == before, (
        "the old claim's file was touched — and \"not a byte into someone else's file\" is what the "
        "whole compatibility with an older copy of the toolkit in live worktrees rests on"
    )
    records = read_registry(registry_dir(board))
    assert len(records) == 1, f"a second record appeared in the registry: {names_of(records)}"
    expected = address_taken_refusal(
        board, common, address="wave9/3", state="live", disk="folder still there", distinct="3k"
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"a line of the refusal was rewritten or went missing: {line!r} — {denied.stderr!r}"
        )
    # ‼️ The order of the ways out: the harmless one FIRST, the destructive one LAST. A tab uses the
    # first thing printed, and had we swapped them round, the refusal itself would be nudging people
    # into taking someone else's address away.
    order = [said_lines.index(line) for line in expected[2:]]
    assert order == sorted(order), (
        f"the ways out printed in the wrong order — the destructive one isn't last: {said_lines!r}"
    )


@needs_pwsh
def test_the_refusal_names_a_folder_that_is_gone_and_frees_the_registry_lock(
    tmp_path: Path,
) -> None:
    """Picking up an abandoned tab: the clue says plainly "the folder is no longer on disk".

    A pick-up and a move end the same way, so the mechanism is one. The only difference is how loud
    the clues are — and that is enough for a human to decide in a second.

    The second half: after the refusal the registry lock is released. Were the tool to leave without
    releasing it, every neighbouring tab would pay half a minute of waiting on its own first action
    for someone else's refusal.
    """
    board = tmp_path / "board.jsonl"
    gone = tmp_path / "vanished"
    tree = tmp_path / "tree"
    tree.mkdir()
    put_claim(
        registry_dir(board),
        "abandoned",
        **open_claim(str(gone), wave="wave9", stream="3", seen_at=now_minus(3)),
    )

    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tree)

    assert denied.returncode != 0, f"an abandoned tab's address taken silently: {denied.stdout!r}"
    expected = address_taken_refusal(
        board,
        gone,
        address="wave9/3",
        state="silent",
        disk="folder is no longer on disk",
        distinct="3k",
    )
    said_lines = said(denied.stderr)
    for line in expected:
        assert line in said_lines, (
            f"the clue about the vanished folder was rewritten or went missing: {line!r} — "
            f"{denied.stderr!r}"
        )

    started = time.monotonic()
    neighbour = claim(board, tmp_path / "neighbour", "wave9", "8")
    assert time.monotonic() - started < 20, (
        "a neighbouring tab waited on a lock left over from someone else's refusal"
    )
    assert "The claim registry lock wasn't handed over" not in neighbour, (
        f"the lock wasn't freed after the refusal — the neighbour got by without it: {neighbour!r}"
    )


@needs_pwsh
@needs_git
def test_the_take_over_key_moves_the_address_the_inbox_and_the_branch_names(
    tmp_path: Path,
) -> None:
    """The take-over key: one leading record per address, inbox and branch names move to the new folder.

    The inbox is work, not bookkeeping: moving the address without moving the inbox would be the
    same loss, only from the other side. The branch names too: a finding ALREADY SENT under the
    stream's former name must reach the folder the stream moved to.
    """
    real_worktrees(tmp_path, {"common": "branch-before-the-move", "tree": "branch-after-the-move"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move", "-Tasks", "10-13")
    mark = add(board, "wave9/3", "a finding for the address")
    by_branch = add(board, "branch-before-the-move", "a finding by the former branch name")
    lost = str(claim_of(board, common, only_open=False).fields["worktree"])

    moved = claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")

    assert address_of(board, tree) == "wave9/3", "the address didn't move"
    assert f"Address wave9/3 taken from folder {lost}" in moved, (
        f"the move of the address wasn't said out loud: {moved!r}"
    )
    # A loud warning: the rival checked in just now, so it looks like it is working.
    assert (
        "‼️ That session checked in just now — it looks like it is working. The address was taken "
        "from a working neighbour: make sure this is your move and not a dispute between two live "
        "sessions." in said(moved)
    ), f"the address was taken from a working neighbour silently: {moved!r}"

    won = str(claim_of(board, tree, only_open=True).fields["worktree"])
    listed = run_tool(board, "-Mode", "Streams")
    ghost = [line for line in shown_streams(listed) if folder_key(lost) in folder_key(line)]
    assert len(ghost) == 1 and f"handed on to {won}" in ghost[0], (
        f"the old record isn't shown as handed on, nor is it named where the address went: {listed!r}"
    )
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"the display shouts about doubling where the address moved by an explicit key: {listed!r}"
    )

    arrived = bullets(run_deliver(board, tree, "Start", "new"))
    assert any(mark in line for line in arrived), (
        f"the finding from the moved address didn't reach the new folder: {arrived!r}"
    )
    assert any(by_branch in line for line in arrived), (
        f"the former stream's branch name doesn't answer to the new claim: {arrived!r}"
    )


@needs_pwsh
@needs_git
def test_the_losing_tab_is_disarmed_the_moment_its_address_moves(tmp_path: Path) -> None:
    """The losing tab is disarmed entirely — that is the single sign "the record is closed".

    Exactly this was missing from the original design: silencing lived only in the parsed registry,
    while delivery, release, closing a finding and both liveness marks read the state straight out
    of THEIR OWN file. So the loser would go on receiving the new owner's post and silencing it for
    them — the same trouble that was being cured at the neighbour's, only mirrored.
    """
    real_worktrees(tmp_path, {"common": "loser-branch", "tree": "winner-branch"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    mark = add(board, "wave9/3", "the new owner's finding")
    loser = claim_of(board, common, only_open=False)
    before = loser.file.read_bytes()
    # We name the folder EXACTLY as the claim recorded it: the tool prints it out of the record
    # rather than assembling it from the system path, and a whole-line check doesn't forgive that.
    won = str(claim_of(board, tree, only_open=True).fields["worktree"])

    # 1. The delivery hook brings it no findings and doesn't refresh its liveness mark.
    #
    # ‼️ The assertion is narrowed deliberately: it used to demand FULL silence from the hook, and
    # that cemented the very flaw because of which the losing side went quiet. Now the hook must
    # bring it no FINDINGS — and must say why it no longer brings them.
    walked = run_deliver(board, common, "Start", "loser")
    assert not bullets(walked), (
        f"the losing tab receives the post of the address's new owner: {bullets(walked)!r}"
    )
    assert mark not in context_text(walked), (
        f"the new owner's finding reached the losing tab: {context_text(walked)!r}"
    )
    assert f"‼️ Your stream wave9/3 was taken over into {won}" in context_text(walked), (
        f"the tab was disarmed silently — it wasn't told why the hook went quiet: {walked!r}"
    )
    assert loser.file.read_bytes() == before, (
        "the hook refreshed the liveness mark of a handed-on record — any new session in the old "
        "folder would resurrect a ghost on its very first turn"
    )

    # 2. Its release says "handed on", not "already released".
    given = release(board, common)
    assert given.returncode == 0, given.stderr
    assert said(given.stdout) == [
        f"Stream wave9/3 handed on to {won} — there is nothing to release here: that session runs "
        "the address.",
        "This session is no longer addressable: findings for the address arrive there, and they "
        "can't be closed from here.",
        "This is your stream and it was moved by mistake — take the address back: "
        "pwsh scripts/wave-board.ps1 -Mode Claim -Wave wave9 -Stream 3 -TakeOver",
    ], f"the release answer was rewritten or passes a move off as a release: {given.stdout!r}"
    assert loser.file.read_bytes() == before, "release closed a handed-on record as its own"

    # 3. Its attempt to close the new owner's finding is refused.
    closing = tool(board, "-Mode", "Done", "-Id", mark, cwd=common)
    assert closing.returncode != 0, (
        f"the loser silenced the new owner's finding — closing a name-based address is SHARED, and "
        f"they would have seen it neither in delivery nor on the board: {closing.stdout!r}"
    )
    assert "is addressed to stream" in closing.stderr, f"the wrong guard refused: {closing.stderr!r}"
    assert any(mark in line for line in bullets(run_deliver(board, tree, "Start", "owner"))), (
        "the new owner's finding was silenced by someone else's hand"
    )


@needs_pwsh
def test_the_succession_survives_a_short_reclaim_of_the_new_owner(tmp_path: Path) -> None:
    """The succession field is inherited by a short re-announcement — the ghost doesn't rise again.

    Otherwise the new owner's very first re-announcement would erase the mark, and the silenced
    record would come back leading, together with its address and its post.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move", "-Tasks", "10-13")
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    taken = claim_of(board, tree, only_open=True)
    assert taken.taken_from == folder_key(common), (
        "the move wasn't written into ONE'S OWN claim — there is nothing to silence the ghost with"
    )

    out = claim_bare(board, tree)

    assert claim_of(board, tree, only_open=True).taken_from == folder_key(common), (
        f"the short re-announcement erased the succession field — the ghost rose again: {out!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"after the re-announcement the address is doubled again: {listed!r}"
    )


@needs_pwsh
def test_a_claim_of_the_older_version_without_the_succession_field_is_not_a_corruption(
    tmp_path: Path,
) -> None:
    """An older version's claim has no succession field — rules work on it, it isn't a corruption.

    The mechanism lives in five copies with no synchronization between them, and claims of different
    ages are the norm, not the exception. A missing field reads as "there was no move".
    """
    board = tmp_path / "board.jsonl"
    old = tmp_path / "older-version"
    tree = tmp_path / "tree"
    old.mkdir()
    tree.mkdir()
    put_claim(
        registry_dir(board),
        "claim-of-the-older-version",
        wave="wave9",
        stream="3",
        name="Older version",
        worktree=str(old),
        state="open",
        seen_at=now_minus(0.1),
    )
    assert TAKEN_FROM_FIELD not in claim_of(board, old, only_open=True).fields, (
        "the check is built wrong: the older version's claim turned out to have a succession field"
    )

    # The address rule works on it: a refusal, not silent doubling.
    denied = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "3", cwd=tree)
    assert denied.returncode != 0, (
        f"the older version's claim wasn't counted a rival — two run the address: {denied.stdout!r}"
    )
    # And the folder rule: another stream from ITS folder doesn't get through.
    other = tool(board, "-Mode", "Claim", "-Wave", "wave9", "-Stream", "8", cwd=old)
    assert other.returncode != 0, (
        f"the older version's claim didn't hold its folder — a stream would be erased silently: "
        f"{other.stdout!r}"
    )
    # And the take-over key works on it — otherwise the way out of the refusal would be a deception.
    taken = claim(board, tree, "wave9", "3", "-TakeOver")
    assert address_of(board, tree) == "wave9/3", (
        f"the take-over key didn't give the address up: {taken!r}"
    )
    assert claim_of(board, tree, only_open=True).taken_from == folder_key(old), (
        "the move wasn't recorded for the older version's claim"
    )


@needs_pwsh
def test_a_finding_for_a_released_stream_is_refused_even_after_the_address_moved(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """A regression of defect 1 from the other side: the real stream is honestly released — intake REFUSES.

    This is the defect's costliest consequence: an abandoned record kept the address alive, a
    finding for it was accepted with a cheerful report of success, the sender was reassured and set
    up no fallback for it — and it reached nobody at all.
    """
    registry_invariants.waive(
        "the invariant \"an address has a leading record\": the address ends here DELIBERATELY — the "
        "stream moved and in the new folder honestly released itself, while the abandoned record of "
        "the old folder stayed open. That is exactly what is checked: the address has no leading "
        "record, and a finding for it is not accepted"
    )
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    assert release(board, tree).returncode == 0, "releasing the real stream failed"

    denied = tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "a late finding", cwd=common)

    assert denied.returncode != 0, (
        f"a finding for a released address was accepted — an abandoned record keeps it alive: "
        f"{denied.stdout!r}"
    )
    assert "stream \"wave9/3\" was RELEASED" in denied.stderr, (
        f"the refusal doesn't speak of a release — so a ghost holds the address: {denied.stderr!r}"
    )


def older_copy(root: Path) -> Path:
    """The toolkit from main BEFORE this work — exactly the copy standing in live trees today.

    There are about twenty copies, no synchronization between them, and BOTH sides of a move meet
    the old code: the winning one at the neighbour's, the losing one at its own place. So two checks
    set this copy up, and one helper sets it up for them.
    """
    old = root / "older-copy"
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
        pytest.skip("no history before this work (shallow clone) — nowhere to get the older copy")
    inside = ".claude/skills/parallel-streams/coordination"
    # Every file the older copy is made of: the library pulls in two more of its own, and without
    # them it would fail on the very first line — that is, the check would silently measure the
    # wrong thing.
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
    """A turn of the OLD delivery hook from the named worktree folder — as at a live neighbour's today."""
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
    """An older copy of the toolkit from a neighbouring tree neither breaks nor spoils anything.

    There are about twenty of them right now, and nothing keeps them in sync. The older copy sees
    the unfamiliar succession field and quietly ignores it; the losing record it reads as open —
    that is, exactly as it read it yesterday. No new breakage, no resurrection.

    ‼️ The point here is that the older copy SURVIVES the field rather than wiping it. Its liveness
    check-in rewrites the whole claim file, and were it to lose the succession field while doing so,
    the silenced record would come back to life for everyone at once, without a single warning.
    """
    old = older_copy(tmp_path)
    real_worktrees(tmp_path, {"common": "loser-branch", "tree": "winner-branch"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
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
    assert listed.returncode == 0, f"the older copy broke on the new registry: {listed.stderr!r}"
    assert "Stream claims: 2" in listed.stdout, (
        f"the older copy reads the new registry differently than yesterday: {listed.stdout!r}"
    )

    # Its delivery hook rewrites THIS folder's claim in full — the field has to survive that.
    walked = older_deliver(old, board, tree, "older")
    assert walked.returncode == 0, f"the older delivery hook failed: {walked.stderr!r}"
    after = read_claim_json(winner.file)
    assert after is not None and folder_key(after.get(TAKEN_FROM_FIELD)) == folder_key(common), (
        f"the older copy wiped the succession field — the silenced record would revive: {after!r}"
    )
    assert loser_file.read_bytes() == loser_before, "the older copy touched the losing claim file"


@needs_pwsh
@needs_git
def test_the_older_copy_in_the_losing_folder_still_carries_away_the_new_owners_mail(
    tmp_path: Path,
) -> None:
    """Takeover's other side: the older copy is the LOSER's — the registry holds, mail still comes.

    ‼️ The limitation is named out loud and CANNOT BE FIXED from its own folder by anything: the
    code the losing tab moves with lives in ITS worktree, and that code is old — it doesn't know
    about the takeover and cannot know until the change reaches its copy (and that takes days). So
    the test pins down exactly what we were promised and what we do hold: the registry stays intact,
    the succession field survives, the silenced record doesn't come back. And the new owner's
    finding the older hook does bring it all the same, telling it to close it — that is the price of
    copies of differing ages, named out loud.
    """
    old = older_copy(tmp_path)
    real_worktrees(tmp_path, {"common": "loser-branch", "tree": "winner-branch"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    mark = add(board, "wave9/3", "a finding for the new owner")
    winner = claim_of(board, tree, only_open=True)

    walked = older_deliver(old, board, common, "loser-with-an-older-copy")

    assert walked.returncode == 0, f"the older hook failed in the loser's folder: {walked.stderr!r}"
    assert mark in walked.stdout, (
        "the scene is assembled wrongly: the older hook brought the loser nothing — then there is "
        f"no limitation to name at all: {walked.stdout!r}"
    )
    after = read_claim_json(winner.file)
    assert after is not None and folder_key(after.get(TAKEN_FROM_FIELD)) == folder_key(common), (
        f"the older copy's turn in the loser's folder wiped the new owner's succession: {after!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"after the older copy's turn in the loser's folder the silenced record revived: {listed!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Notifying the losing tab, and releasing an orphan by address.
#
# Both changes close one hole from two sides. A tab whose address was taken has to LEARN about it:
# otherwise it works blind on an address it no longer holds, and tells its neighbours and the owner
# that it is running the stream. And a record that has not even a worktree folder left has to be
# releasable: otherwise it holds the address alive forever and accepts findings that will reach
# nobody.
#
# ‼️ Release by address is the toolkit's ONLY operation that writes into someone else's claim file,
# and its condition was chosen not by silence but by the absence of a writer: in a folder that does
# not exist the delivery hook cannot start. Silence proves exactly one thing — the tab made no move.
# ─────────────────────────────────────────────────────────────────────────────────────────────


@needs_pwsh
def test_the_losing_tab_learns_on_its_next_turn_that_its_address_was_taken(
    tmp_path: Path,
) -> None:
    """The losing tab learns of the takeover on its own next turn — a separate line from the hook.

    Without it the losing side goes quiet SILENTLY: findings stop arriving, release answers "handed
    on", an attempt to close a finding is refused — and why, the tab does not know. Its claim on
    disk still looks open (a takeover doesn't touch someone else's file by a single byte), and from
    its own file it will never learn about the takeover.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")

    before = context_text(run_deliver(board, common, "Prompt", "loser"))
    assert "taken over" not in before, f"the takeover was announced before it happened: {before!r}"

    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    won = str(claim_of(board, tree, only_open=True).fields["worktree"])

    after = said(context_text(run_deliver(board, common, "Prompt", "loser")))
    assert (
        f"‼️ Your stream wave9/3 was taken over into {won} — this session is no longer "
        "addressable: findings for that address arrive there, and they can't be closed from here."
    ) in after, f"the losing tab went quiet without a word: {after!r}"
    # The way out is printed as a ready-made line with the values filled in: a takeover is
    # reversible, the loser's file is untouched, and it may take the address back with the same key.
    assert (
        "This is your stream and it was taken over by mistake — take the address back: "
        "pwsh scripts/wave-board.ps1 -Mode Claim -Wave wave9 -Stream 3 -TakeOver"
    ) in after, f"the way out wasn't named as a ready-made line: {after!r}"


@needs_pwsh
def test_release_by_address_closes_an_orphan_whose_folder_is_gone(tmp_path: Path) -> None:
    """Release by address closes a record whose worktree folder is gone, and leaves a trace.

    This is the toolkit's only operation that writes into SOMEONE ELSE'S claim file. It is allowed
    because that file has no writer: a delivery hook cannot start in a folder that doesn't exist.
    The "who released it and when" trace is mandatory — without it a release by an outsider is
    indistinguishable from an honest release by the tab itself.
    """
    board = tmp_path / "board.jsonl"
    gone = tmp_path / "vanished"
    tab = tmp_path / "tab"
    tab.mkdir()
    put_claim(
        registry_dir(board),
        "orphan",
        **open_claim(str(gone), wave="wave9", stream="3", name="Orphan", seen_at=now_minus(5)),
    )

    given = release(board, tab, "-Wave", "wave9", "-Stream", "3")

    assert given.returncode == 0, given.stderr
    closed = claim_of(board, gone, only_open=False)
    assert closed.released, f"the orphan's record wasn't released: {closed.fields!r}"
    assert folder_key(closed.fields.get(RELEASED_FROM_FIELD)) == folder_key(tab), (
        'no "who released it" trace in the record — an outsider release is indistinguishable from '
        f"an honest one: {closed.fields!r}"
    )
    assert closed.fields.get("released_at"), (
        f'no "when it was released" trace in the record: {closed.fields!r}'
    )
    assert (
        f"Entry wave9/3 released by address: worktree folder {gone} is not on disk, and its "
        "claim has no writer."
    ) in said(given.stdout), f"what was done wasn't said out loud: {given.stdout!r}"

    # The address is no longer held by a ghost: a finding for it now gets a refusal, not a cheerful
    # report of success.
    denied = tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "a late finding", cwd=tab)
    assert denied.returncode != 0, (
        f"the entry released by address still holds the address alive: {denied.stdout!r}"
    )
    assert 'stream "wave9/3" was RELEASED' in denied.stderr, (
        "the refusal doesn't talk about a release — so a ghost is holding the address: "
        f"{denied.stderr!r}"
    )


@needs_pwsh
def test_release_by_address_refuses_while_the_folder_is_still_on_disk(tmp_path: Path) -> None:
    """Folder is there — refusal, even when the record is silent for five days. No bypass switch.

    Silence proves exactly one thing: the tab made no moves. Running subagents, waiting for a build
    and an overnight pause all look the same. And where the folder exists, the claim has a second
    writer as well — that tab's delivery hook: it rewrites the whole document on every turn and
    takes no lock, so our release mark would be wiped by its next liveness check-in, AFTER we had
    been told it succeeded.
    """
    board = tmp_path / "board.jsonl"
    silent = tmp_path / "silent"
    tab = tmp_path / "tab"
    silent.mkdir()
    tab.mkdir()
    kept = put_claim(
        registry_dir(board),
        "silent-for-five-days",
        **open_claim(str(silent), wave="wave9", stream="3", name="Silent", seen_at=now_minus(5)),
    )
    before = kept.read_bytes()

    denied = release(board, tab, "-Wave", "wave9", "-Stream", "3")

    assert denied.returncode != 0, (
        f"someone else's live folder was released on silence alone: {denied.stdout!r}"
    )
    assert kept.read_bytes() == before, "someone else's claim file was touched during the refusal"
    said_lines = said(denied.stderr)
    assert (
        f"stream wave9/3's worktree folder is still there: {silent} — go into it and release the "
        "stream from there: pwsh scripts/wave-board.ps1 -Mode Release"
    ) in said_lines, f"the refusal named neither the folder nor the way out: {denied.stderr!r}"
    # ‼️ There is no bypass switch here at all: print even one switch in the refusal and the tab
    # would take the only way out it was shown, and a live neighbour's record would vanish with a
    # success code.
    offered = [line for line in said_lines if "-Force" in line or "-TakeOver" in line]
    assert not offered, f"the refusal offers a bypass switch: {offered!r}"


@needs_pwsh
def test_release_by_address_tells_an_unreachable_path_from_a_missing_folder(
    tmp_path: Path,
) -> None:
    """An unreachable path means "unknown", not "gone", and one must not pass for the other.

    A dropped drive and a vanished network share answer with the same refusal as a deleted folder.
    Let the tool take one for the other, and it would write into someone else's claim at a path
    where a tab is living and working at that very moment, and that tab's delivery hook would wipe
    our mark straight away.
    """
    board = tmp_path / "board.jsonl"
    unreachable = dead_board_path().parent / "tree"
    tab = tmp_path / "tab"
    tab.mkdir()
    kept = put_claim(
        registry_dir(board),
        "on-a-dead-path",
        **open_claim(
            str(unreachable), wave="wave9", stream="3", name="Orphan", seen_at=now_minus(5)
        ),
    )
    before = kept.read_bytes()

    denied = release(board, tab, "-Wave", "wave9", "-Stream", "3")

    assert denied.returncode != 0, (
        f"a record on an unreachable path was released: {denied.stdout!r}"
    )
    assert kept.read_bytes() == before, "someone else's claim file was touched during the refusal"
    said_lines = said(denied.stderr)
    assert (
        f"the path to stream wave9/3's worktree folder is unreachable entirely: {unreachable} — "
        "whether it is alive is unknown."
    ) in said_lines, f"the refusal didn't name the path as unreachable: {denied.stderr!r}"
    guessed = [line for line in said_lines if "not on disk" in line or "folder is gone" in line]
    assert not guessed, f'"I cannot see it" was passed off as "it is not there": {guessed!r}'


# ─────────────────────────────────────────────────────────────────────────────────────────────
# A takeover edge acts by TIME, not by topology.
#
# The way out "take the address back with the same key" is printed by the tool itself — so mutual
# takeover edges aren't an invention of the test suite but a promised scenario. While edges were
# resolved topologically, such a pair gave no record a zero waiting count: the ready queue was
# empty, NOBODY was silenced, and the address was run by two again — the same defect 1, only
# quieter than before. The first record in a chain of moves A→B→C revived in exactly the same way.
#
# The time rule closes both cases at once: the edge "i took the address from folder j" does NOT act
# only when it is proven that j's claim began later than the moment of i's takeover. Hence the third
# part of the rule: the moment of announcement isn't inherited if your own earlier record was
# silenced by a takeover. Otherwise the returning tab would look as if it had announced itself
# before its address was taken — and the two records would silence each other.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def hours_ago(hours: float) -> str:
    """A past moment, in hours: the test sets the event order itself, not the machine's speed.

    Times in a claim are kept to the second, and three runs in a row fit into one second easily.
    Then the test would be checking not the rule but how fast the machine is.
    """
    return (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")


@needs_pwsh
@needs_git
def test_the_address_returned_by_the_same_key_leaves_exactly_one_leader(tmp_path: Path) -> None:
    """A takeover circle A↔B: the address returned by the printed command — ONE leader is left.

    The promise that a takeover is reversible rests exactly on this: the tool itself prints the
    losing tab the command to take it back. While edges were resolved topologically, the two records
    referred to each other and neither was silenced — a finding arrived at both, and whichever
    closed it first cleared it for the other.
    """
    real_worktrees(tmp_path, {"common": "first-branch", "tree": "second-branch"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Circle", "-Tasks", "10-13")
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Circle", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    back = claim(board, common, "wave9", "3", "-TakeOver")

    assert address_of(board, common) == "wave9/3", (
        f"the tab that took its address back didn't get it: {back!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"after the return two run the address again — the circle wasn't untangled: {listed!r}"
    )
    won = str(claim_of(board, common, only_open=True).fields["worktree"])
    assert f"handed on to {won}" in stream_line_of(listed, tree), (
        f"the taking record wasn't silenced by the return: {listed!r}"
    )

    mark = add(board, "wave9/3", "a finding after the return")
    arrived = bullets(run_deliver(board, common, "Start", "returned"))
    assert any(mark in line for line in arrived), (
        f"the finding didn't reach the tab that took its address back: {arrived!r}"
    )
    lost = context_text(run_deliver(board, tree, "Start", "giver"))
    assert mark not in lost, f"the finding went to both sides of the circle at once: {lost!r}"
    assert (
        f"‼️ Your stream wave9/3 was taken over into {won} — this session is no longer addressable"
    ) in lost, f"the second side of the circle went quiet without a word: {lost!r}"


@needs_pwsh
def test_the_returning_tab_gets_its_stream_back_but_not_its_seniority(tmp_path: Path) -> None:
    """Taking the address back brings the stream too — name, tasks, plan. Seniority it does not.

    Everything is inherited except the moment of announcement: seniority at this address was lost
    together with the address itself. And it isn't only about fairness: an inherited moment would
    fall EARLIER than the moment the address was taken — the rival's edge would start acting again,
    and the two records would silence each other. Today the tab comes back nameless altogether, that
    is, it loses the stream entirely.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    plan = "docs/superpowers/plans/2026-09-02-circle.md"
    claim(board, common, "wave9", "3", "-StreamName", "Circle", "-Tasks", "10-13", "-Plan", plan)
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Taker", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    back = claim(board, common, "wave9", "3", "-TakeOver")

    returned = claim_of(board, common, only_open=True).fields
    assert returned["name"] == "Circle" and returned["tasks"] == "10-13", (
        f"the returning tab announced itself nameless — the stream is lost with its tasks: {back!r}"
    )
    assert returned["plan"] == plan, f"the plan path was lost on the address's return: {returned!r}"
    assert str(returned["claimed_at"]) > hours_ago(1), (
        "the moment of announcement was inherited from the silenced record — the tab looks as if "
        "it announced itself before its address was taken, and that pits the two records against "
        f"each other again: {returned!r}"
    )


@needs_pwsh
def test_a_chain_of_moves_leaves_one_leader_and_no_resurrected_ghost(tmp_path: Path) -> None:
    """A chain of moves A→B→C silences A and B: only C runs it, no doubling, display is quiet.

    The rule "a takeover from an already taken-over record doesn't act" was a crutch against cycles
    and cost exactly this: on two moves in a row record A became the leader again, the address
    counted as doubled, and a human was invited to sort it out. That fork is closed — the ghost no
    longer revives.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    claim(board, first, "wave9", "3", "-StreamName", "Chain")
    patch_claim(board, first, claimed_at=hours_ago(3))
    claim(board, second, "wave9", "3", "-StreamName", "Chain", "-TakeOver")
    patch_claim(board, second, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    last = claim(board, third, "wave9", "3", "-StreamName", "Chain", "-TakeOver")

    assert address_of(board, third) == "wave9/3", f"the last move didn't get the address: {last!r}"
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"the chain of moves revived the first record — the address is doubled: {listed!r}"
    )
    for lost in (first, second):
        assert "handed on" in stream_line_of(listed, lost), (
            f"after the chain of moves folder {lost}'s record stayed the leader: {listed!r}"
        )


@needs_pwsh
def test_a_fresh_claim_in_the_old_folder_is_not_quenched_by_the_old_edge(tmp_path: Path) -> None:
    """The address taken, the stream moved and released honestly — the old folder claims it anew.

    The take-over key isn't needed here: the address has no open claims. And the old takeover edge,
    were it to act forever, would silence the fresh claim quietly — the folder would announce itself
    with a success code and stay invisible to neighbours and to findings alike. That is a hidden
    defect: from the outside it shows up nowhere but in the registry.
    """
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "First")
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "First", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, tree).returncode == 0, "releasing the moved stream didn't go through"

    again = claim(board, common, "wave9", "3", "-StreamName", "Second")

    assert address_of(board, common) == "wave9/3", (
        f"the old folder's fresh claim was silenced by the old takeover edge: {again!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    assert not [line for line in listed.splitlines() if line.startswith("‼️")], (
        f"the display counts the address as doubled after a lawful re-announcement: {listed!r}"
    )
    mark = add(board, "wave9/3", "a finding for the new stream")
    arrived = bullets(run_deliver(board, common, "Start", "fresh"))
    assert any(mark in line for line in arrived), (
        f"the finding didn't reach the tab that claimed the freed address: {arrived!r}"
    )


@needs_pwsh
def test_the_yield_ring_never_leaves_a_succession_of_another_address(tmp_path: Path) -> None:
    """The yield ring shifted the number — the succession field neither travels nor disappears.

    The field names the folder from which THIS VERY address was taken. Let it stay through the shift
    and the claim asserts that it took the address from a folder that never ran it: the real
    takeover edge disappears, and the silenced record comes back to life together with its inbox and
    its names.

    ‼️ Throwing it away isn't allowed either: the move DID happen, and the old folder's record is
    silenced by it. So it goes into the list of past ones TOGETHER WITH ITS OWN address — the one
    the stream ran before the shift.
    """
    board = tmp_path / "board.jsonl"
    tree = tmp_path / "tree"
    tree.mkdir()
    put_claim(
        registry_dir(board),
        "moved",
        **open_claim(
            str(tree),
            wave="wave9",
            stream="3",
            name="Moved",
            claimed_at=hours_ago(1),
            seen_at=now_minus(0),
            taken_from=str(tmp_path / "common"),
            taken_at=hours_ago(1),
        ),
    )
    put_claim(
        registry_dir(board),
        "older-neighbour",
        **open_claim(
            str(tmp_path / "neighbour"),
            wave="wave9",
            stream="3",
            claimed_at=hours_ago(3),
            seen_at=now_minus(0),
        ),
    )

    out = claim_bare(board, tree)

    assert "shifted to the next free one" in out, (
        f"the scene is assembled wrongly — the yield ring didn't shift the number: {out!r}"
    )
    moved = claim_of(board, tree, only_open=True)
    assert moved.address != "wave9/3", f"the number stayed the same: {moved.fields!r}"
    assert TAKEN_FROM_FIELD not in moved.fields and "taken_at" not in moved.fields, (
        "after the shift the claim asserts it took an address it doesn't hold — and the real "
        f"takeover edge disappeared with it: {moved.fields!r}"
    )
    remembered = moved.fields.get(PAST_TAKEOVERS_FIELD)
    assert isinstance(remembered, list) and len(remembered) == 1, (
        "the move was simply thrown away on the number shift — the record it silenced comes back: "
        f"{moved.fields!r}"
    )
    assert remembered[0]["stream"] == "3" and folder_key(
        remembered[0][TAKEN_FROM_FIELD]
    ) == folder_key(tmp_path / "common"), (
        f"the past move was recorded under the wrong address and the wrong folder: {remembered!r}"
    )


@needs_pwsh
@needs_git
def test_a_claim_of_the_older_version_from_a_subfolder_never_closes_the_new_owners_finding(
    tmp_path: Path,
) -> None:
    """Closing a finding looks for its claim by both routes — else the loser clears another's mail.

    Release and the delivery hook already got a second route to their own claim; closing a finding
    did not. A claim filed by an older version from a subfolder lies under that subfolder's key, is
    not found by the canonical key at all, and the closed-check never fires. So the tab whose
    address was TAKEN clears the new owner's finding — and closing a named address is SHARED, so he
    will see it neither in delivery nor on the board, while the author gets an "acknowledged".
    """
    real_worktrees(tmp_path, {"common": "loser-branch", "tree": "winner-branch"})
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    deep = subfolder_of(common)
    put_claim(
        registry_dir(board),
        "claim-of-the-older-version",
        **open_claim(
            str(deep),
            wave="wave9",
            stream="3",
            name="Loser",
            branch="loser-branch",
            seen_at=now_minus(0),
        ),
    )
    claim(board, tree, "wave9", "3", "-StreamName", "Winner", "-TakeOver")
    mark = add(board, "loser-branch", "a finding for the new owner", cwd=tmp_path)

    closing = tool(board, "-Mode", "Done", "-Id", mark, cwd=deep)

    assert closing.returncode != 0, (
        f"the tab whose address was taken cleared the new owner's finding: {closing.stdout!r}"
    )
    assert "not you" in closing.stderr, f"the wrong guard refused: {closing.stderr!r}"
    assert any(mark in line for line in bullets(run_deliver(board, tree, "Start", "owner"))), (
        "the new owner's finding was cleared by someone else's hand"
    )


@needs_pwsh
def test_adding_a_finding_to_a_doubled_address_says_it_may_reach_the_wrong_tab(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """A finding for a doubled address shouts like the display — else the author is calmed in vain.

    The display speaks about doubling in a loud line, while accepting answered "a tab runs the
    stream — most likely it will get there by itself". It is accepting that reassures the author:
    after a cheerful report he gives the finding no fallback item, and it may reach the wrong tab —
    which one exactly is decided by the order of the folder listing.
    """
    registry_invariants.waive(
        'the "one leading record per address" invariant: the doubling is assembled by hand as a '
        "legacy of the defect — what's checked is that accepting a finding shouts about it too, "
        "not the display alone"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    first = tmp_path / "first"
    second = tmp_path / "second"
    put_claim(
        folder, "one", **open_claim(str(first), wave="wave9", stream="3", seen_at=now_minus(0))
    )
    put_claim(
        folder, "other", **open_claim(str(second), wave="wave9", stream="3", seen_at=now_minus(0))
    )

    out = run_tool(
        board, "-Mode", "Add", "-To", "wave9/3", "-Title", "a finding for a doubled address"
    )

    loud = [line for line in out.splitlines() if line.startswith("‼️")]
    assert len(loud) == 1, f"accepting the finding stayed quiet about the doubled address: {out!r}"
    assert "wave9/3" in loud[0], f"the loud line didn't name the doubled address: {loud[0]!r}"
    assert folder_key(first) in folder_key(out) and folder_key(second) in folder_key(out), (
        f"the loud line named neither folder — there is nowhere to go and sort it out: {out!r}"
    )


def test_the_stand_reads_supersessions_the_way_the_tool_does(tmp_path: Path) -> None:
    """The stand's takeover parsing drops records WITH NO address — just as the tool drops them.

    A claim of a stranger's version may have no wave and no number at all. The tool doesn't count
    such a record among takeovers (no address — nothing to take), while the stand did: two
    address-less neighbours met on an "address" made of two voids, and the stand saw an edge the
    tool doesn't have. A divergence between the stand and the tool costs more than a defect in the
    stand itself: the stand starts pinning down the wrong behaviour.
    """
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "stranger-taker", state="open", worktree="d:/second", taken_from="d:/first")
    put_claim(folder, "stranger-former", state="open", worktree="d:/first")

    superseded, faults = supersessions(read_registry(folder))

    assert not superseded, "the stand silenced an address-less record — the tool sees no such edge"
    assert not faults, f"the stand found a fault where the tool sees no takeover at all: {faults}"

@needs_pwsh
def test_a_move_outlives_the_folder_taken_by_the_next_stream(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """The memory of a takeover outlives the REUSE of a folder, not just a release.

    The succession field lives in the taking folder's claim, and a folder has exactly ONE claim: the
    moment that same folder took on the next stream, its file was rewritten, the edge vanished — and
    the abandoned record of the old folder became the leader again. Silently: the display didn't
    shout, intake reported "it will get there on its own", and the delivery hook carried the finding
    to an abandoned tab. That is precisely the costliest consequence of defect 1.
    """
    registry_invariants.waive(
        "the invariant 'an address has a leading record': the address ends here DELIBERATELY — the "
        "stream moved, released and the folder was taken for the next one. What is checked is "
        "exactly that the ghost of the old folder does not become the leader"
    )
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")
    patch_claim(board, common, claimed_at=hours_ago(3))
    ghost_file = claim_of(board, common, only_open=True).file
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, tree).returncode == 0, "releasing the moved stream failed"
    before = ghost_file.read_bytes()

    claim(board, tree, "wave9", "8", "-StreamName", "Next")

    assert ghost_file.read_bytes() == before, "someone else's claim was touched — two writers on it"
    denied = tool(board, "-Mode", "Add", "-To", "wave9/3", "-Title", "a late finding", cwd=common)
    assert denied.returncode != 0, (
        f"a finding for an abandoned address was accepted — the ghost runs the stream again: "
        f"{denied.stdout!r}"
    )
    listed = run_tool(board, "-Mode", "Streams")
    line = stream_line_of(listed, common)
    assert "took the address" in line and "wave9/8" in line, (
        f"the display passes an abandoned record off as a handover to a live tab on it: {line!r}"
    )
    loud = [text for text in listed.splitlines() if text.startswith("‼️")]
    assert any("wave9/3" in text for text in loud), (
        f"the display stayed silent about an address left with no leading record: {listed!r}"
    )


@needs_pwsh
def test_a_chain_of_moves_outlives_a_reclaim_of_its_middle_folder(tmp_path: Path) -> None:
    """The chain A→B→C outlives a reclaim of its MIDDLE folder: the ghost does not come back.

    While only a folder's current claim remembered the takeover, announcing the next stream in the
    middle folder erased its edge — and the first record came alive beside the last. Two of them ran
    the address again.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    claim(board, first, "wave9", "3", "-StreamName", "Chain")
    patch_claim(board, first, claimed_at=hours_ago(4))
    claim(board, second, "wave9", "3", "-StreamName", "Chain", "-TakeOver")
    patch_claim(board, second, claimed_at=hours_ago(3), taken_at=hours_ago(3))
    claim(board, third, "wave9", "3", "-StreamName", "Chain", "-TakeOver")
    patch_claim(board, third, claimed_at=hours_ago(2), taken_at=hours_ago(2))

    claim(board, second, "wave9", "9", "-StreamName", "Next")

    assert address_of(board, third) == "wave9/3", "the last record of the chain lost the address"
    listed = run_tool(board, "-Mode", "Streams")
    assert not [text for text in listed.splitlines() if text.startswith("‼️")], (
        f"reclaiming the middle folder resurrected the first record — address doubled: {listed!r}"
    )
    assert "handed on to" in stream_line_of(listed, first), (
        f"the first record of the chain became the leader again: {listed!r}"
    )
    mark = add(board, "wave9/3", "a finding after the middle folder was reclaimed")
    arrived = bullets(run_deliver(board, third, "Start", "last"))
    assert any(mark in text for text in arrived), f"the finding missed the leader: {arrived!r}"
    lost = context_text(run_deliver(board, first, "Start", "first"))
    assert mark not in lost, f"the finding went to the abandoned tab as well: {lost!r}"


@needs_pwsh
def test_a_claim_quenched_the_moment_it_is_written_never_reports_plain_success(
    tmp_path: Path,
) -> None:
    """Your own record came out superseded at once — announcing shouts and REFUSES, not reports.

    The claim landed in its own file, but whether it leads or is already superseded is decided by
    the registry as a whole: a neighbouring tab took the address at the very moment ours was reading
    the registry — and as written down, our claim turned out to be moved away. A zero exit code
    would be read by the tab as "announced, working", and it would go off running a stream that does
    not exist from the outside.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "mine"
    rival = tmp_path / "rival"
    mine.mkdir(parents=True, exist_ok=True)
    # ‼️ The moment of the other side's takeover is a minute AHEAD: that is what the race this scene
    # was written for looks like (the neighbour took the address while our tab was reading the
    # registry). It also makes the outcome repeatable: had we put "now", a fraction of a second
    # would decide it.
    put_claim(
        registry_dir(board),
        "rival",
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
        "Fresh",
        "-TakeOver",
        cwd=mine,
    )

    assert done.returncode != 0, (
        f"announcing reported success where the record was superseded at once: {done.stdout!r}"
    )
    loud = [text for text in done.stdout.splitlines() if text.startswith("‼️")]
    assert any("SUPERSEDED" in text for text in loud), (
        f"announcing stayed silent about the record being superseded at once: {done.stdout!r}"
    )
    assert "Address wave9/3 taken from folder" in done.stdout, (
        f"the report about the takeover wasn't printed at all: {done.stdout!r}"
    )
    assert folder_key(str(rival)) in folder_key(done.stdout), (
        f"the folder the address stayed with isn't named — nowhere to go and sort it out: "
        f"{done.stdout!r}"
    )
    assert "-TakeOver" in done.stdout, (
        f"a live record runs the address, yet no working way out was printed: {done.stdout!r}"
    )
    # ‼️ Whether the claim was written we ask across the CLOSED ones too: a superseded record does
    # not count as open, while the refusal says outright that the file is in place and the command
    # need not be repeated.
    assert claim_of(board, mine, only_open=False).address == "wave9/3", (
        "the claim file wasn't written, and the refusal says otherwise"
    )


@needs_pwsh
def test_an_edge_without_a_moment_no_longer_locks_the_address_forever(tmp_path: Path) -> None:
    """A takeover edge without a moment no longer silences whoever announced LATER.

    The succession field without a moment was written only by an unreleased interim version, and it
    wrote both fields at the very same instant of announcing. While such an edge held
    unconditionally, it locked the address behind the victim forever: however many times she
    announced anew, the edge silenced every fresh claim of hers, and the way out printed for her did
    not work — there was no one left to take the address from.
    """
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "mine"
    rival = tmp_path / "rival"
    put_claim(
        registry_dir(board),
        "rival",
        **open_claim(
            str(rival),
            wave="wave9",
            stream="3",
            claimed_at=hours_ago(3),
            seen_at=hours_ago(3),
            taken_from=str(mine),
        ),
    )

    out = claim(board, mine, "wave9", "3", "-StreamName", "Reclaim", "-TakeOver")

    assert "Address wave9/3 taken from folder" in out, "the takeover wasn't named at all"
    assert "SUPERSEDED" not in out, (
        f"a fresh claim was silenced by an edge known only to be older: {out!r}"
    )
    assert address_of(board, mine) == "wave9/3", "the tab that reclaimed the address didn't get it"
    mark = add(board, "wave9/3", "a finding after the address was reclaimed")
    arrived = bullets(run_deliver(board, mine, "Start", "returned"))
    assert any(mark in text for text in arrived), (
        f"the finding for the address missed the tab that reclaimed it: {arrived!r}"
    )


@needs_pwsh
def test_show_shouts_about_an_address_left_without_a_leader(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """The display shouts about the opposite trouble too: open claims on an address, no leader.

    That is what a tab that does not exist from the outside looks like: its claim file is open, it
    believes it is running the stream, yet intake will not accept a finding for the address and the
    delivery hook will not bring one. About a doubled address the display shouted; about this one it
    stayed silent.
    """
    registry_invariants.waive(
        "the watch is waived entirely: the registry is assembled by hand as exactly this scene — "
        "what is checked is that the display shouts about it"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    lost = tmp_path / "abandoned"
    gone = tmp_path / "moved-away"
    put_claim(
        folder, "abandoned", **open_claim(str(lost), claimed_at=hours_ago(3), seen_at=hours_ago(0))
    )
    put_claim(
        folder,
        "moved-away",
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
        f"the display stayed silent about an address with no leading record: {listed!r}"
    )
    assert folder_key(str(lost)) in folder_key(listed), (
        f"the loud line didn't name the abandoned tab's folder: {listed!r}"
    )


def test_registry_invariants_catch_an_address_without_a_leader(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """The same question, put to the stand's watch: a leaderless registry it must call a fault.

    ‼️ This test cannot be red against the code without the fix: what is under test here is not the
    mechanism but the stand itself, and it pins down a NEW skill of its. The mechanism takes no part
    in it at all.
    """
    registry_invariants.waive("the registry is inconsistent on purpose — the watch is the subject")
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "abandoned", **open_claim("d:/first", claimed_at=hours_ago(3)))
    put_claim(
        folder,
        "moved-away",
        **open_claim(
            "d:/second",
            state="released",
            claimed_at=hours_ago(2),
            taken_from="d:/first",
            taken_at=hours_ago(2),
        ),
    )

    faults = registry_faults(folder)

    assert any("has no leading record left" in fault for fault in faults), (
        f"the watch stayed silent on an address that has no leader left: {faults}"
    )


@needs_pwsh
def test_the_memory_of_past_moves_is_capped_and_drops_the_oldest(tmp_path: Path) -> None:
    """The list of past takeovers does not grow forever: the excess goes from the oldest end.

    ‼️ This test pins down a LIMIT rather than the behaviour being fixed, yet it still comes out red
    against the code without the fix: there is no list there at all.
    """
    board = tmp_path / "board.jsonl"
    here = tmp_path / "folder"
    old_moves = [
        {
            "wave": "wave9",
            "stream": str(number),
            "taken_from": f"d:/folder-{number}",
            "taken_at": hours_ago(100 - number),
        }
        for number in range(1, 26)
    ]
    put_claim(
        registry_dir(board),
        "folder",
        **open_claim(
            str(here),
            wave="wave9",
            stream="26",
            state="released",
            claimed_at=hours_ago(80),
            past_takeovers=old_moves,
        ),
    )

    claim(board, here, "wave9", "30", "-StreamName", "Next")

    kept = claim_of(board, here, only_open=True).fields["past_takeovers"]
    assert isinstance(kept, list) and len(kept) == 20, (
        f"the memory of past takeovers isn't capped at two dozen: {kept}"
    )
    numbers = [str(move["stream"]) for move in kept]
    assert "25" in numbers and "1" not in numbers, (
        f"what was dropped isn't the oldest takeovers but whichever came to hand: {numbers}"
    )


@needs_pwsh
@needs_git
def test_the_older_copy_keeps_the_memory_of_past_moves_it_does_not_understand(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """An older copy of the toolkit from a live tree doesn't understand the list — nor erase it.

    There are about twenty copies, nothing synchronizes them, and the older copy's delivery hook
    rewrites the claim file IN FULL on every turn of the tab. Were it to throw the unfamiliar field
    away, the memory of the takeover would die on the very first turn, and the abandoned record of
    the old folder would become the leader again.
    """
    registry_invariants.waive(
        "the invariant 'an address has a leading record': the stream moved, released and the "
        "folder was taken for the next one — what is checked is that an older copy's turn does "
        "not erase the memory of the takeover"
    )
    board = tmp_path / "board.jsonl"
    common = tmp_path / "common"
    tree = tmp_path / "tree"
    claim(board, common, "wave9", "3", "-StreamName", "Move")
    patch_claim(board, common, claimed_at=hours_ago(3))
    claim(board, tree, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    patch_claim(board, tree, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, tree).returncode == 0, "releasing the moved stream failed"
    claim(board, tree, "wave9", "8", "-StreamName", "Next")
    old = older_copy(tmp_path)

    walked = older_deliver(old, board, tree, "old")

    assert walked.returncode == 0, f"the old delivery hook fell over: {walked.stderr!r}"
    kept = claim_of(board, tree, only_open=True).fields.get(PAST_TAKEOVERS_FIELD)
    assert isinstance(kept, list) and kept, (
        f"the older copy erased the memory of past takeovers: {kept!r}"
    )
    denied = tool(
        board, "-Mode", "Add", "-To", "wave9/3", "-Title", "after the older copy", cwd=common
    )
    assert denied.returncode != 0, (
        f"after the older copy's turn the ghost runs the address again: {denied.stdout!r}"
    )


@needs_pwsh
def test_the_invariant_never_calls_a_lawful_move_a_circle(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """Silencing every record of an address is NOT a circle — the watch must name the scene right.

    A folder took the address and then took on the next stream: its record is about a different
    address now, and the old one is left with a single silenced record. The old condition
    ("everything is silenced") did once really mean a circle — only a neighbour on the same address
    could silence an address's last record. With the memory of takeovers a record of ANOTHER address
    silences it, and the assertion started shouting "the takeover goes in a circle" at the most
    common lawful scene, the very one the fix was made for. The fifth assertion speaks about it.
    """
    registry_invariants.waive(
        "the registry is assembled by hand: the address really does have no leader left — what is "
        "checked is exactly the name the watch gives this scene"
    )
    board = tmp_path / "board.jsonl"
    folder = registry_dir(board)
    put_claim(folder, "former", **open_claim("d:/first", wave="wave9", stream="3"))
    put_claim(
        folder,
        "taker",
        **open_claim(
            "d:/second",
            wave="wave9",
            stream="9",
            past_takeovers=[
                {
                    "wave": "wave9",
                    "stream": "3",
                    TAKEN_FROM_FIELD: "d:/first",
                    TAKEN_AT_FIELD: hours_ago(2),
                }
            ],
        ),
    )

    faults = registry_faults(folder)

    assert not [fault for fault in faults if "in a circle" in fault], (
        f"a lawful takeover was called a circle — the watch shouts at the scene it was made for: "
        f"{faults}"
    )
    assert any("has no leading record left" in fault for fault in faults), (
        f"the scene where the address was left with no leader went by in silence: {faults}"
    )


@needs_pwsh
def test_the_answer_names_the_folder_where_the_address_really_went(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """The victim is told the END of the chain of takeovers, not the middle folder.

    The chain A→B→C is lawful, and the middle folder may since have taken on the next stream. The
    answer to "where did the address go" used to break off at the very first link whose record
    changed address — that is, exactly where the memory of takeovers was needed. A human was sent to
    a folder that holds nothing about that address.
    """
    registry_invariants.waive(
        "the invariant 'an address has a leading record': the stream moved along a chain and ended "
        "there — what is checked is which folder the victim is told about"
    )
    board = tmp_path / "board.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    claim(board, first, "wave9", "3", "-StreamName", "Chain")
    patch_claim(board, first, claimed_at=hours_ago(5))
    claim(board, second, "wave9", "3", "-StreamName", "Chain", "-TakeOver")
    patch_claim(board, second, claimed_at=hours_ago(4), taken_at=hours_ago(4))
    claim(board, third, "wave9", "3", "-StreamName", "Chain", "-TakeOver")
    patch_claim(board, third, claimed_at=hours_ago(3), taken_at=hours_ago(3))
    # The middle folder took on the next stream; the last one honestly released its own.
    claim(board, second, "wave9", "9", "-StreamName", "Next")
    assert release(board, third).returncode == 0, "releasing the chain's last folder failed"

    given = release(board, first).stdout

    assert folder_key(str(third)) in folder_key(given), (
        f"the end of the chain of takeovers isn't named — nowhere to go and sort it out: {given!r}"
    )
    assert folder_key(str(second)) not in folder_key(given), (
        f"the victim is sent to the middle folder, which holds nothing about the address: {given!r}"
    )


@needs_pwsh
def test_a_dead_end_is_never_printed_as_the_way_out(
    tmp_path: Path, registry_invariants: RegistryWatch
) -> None:
    """The take-over key is advised only where it has someone to take the address from.

    A neighbouring folder took the address and finished the stream there — the address has no
    leading record left. Both release and the delivery hook used to advise the victim to take the
    address back with the take-over key, and the key answered "wasn't needed: no other folder's
    claim runs the address". The tab went round in circles carrying out the one way out printed for
    it — and a printed way out has to work.
    """
    registry_invariants.waive(
        "the invariant 'an address has a leading record': the stream moved and ended there — what "
        "is checked is exactly that the truth is told about this dead end"
    )
    board = tmp_path / "board.jsonl"
    mine = tmp_path / "mine"
    rival = tmp_path / "rival"
    claim(board, mine, "wave9", "3", "-StreamName", "Move")
    patch_claim(board, mine, claimed_at=hours_ago(4))
    claim(board, rival, "wave9", "3", "-StreamName", "Move", "-TakeOver")
    patch_claim(board, rival, claimed_at=hours_ago(2), taken_at=hours_ago(2))
    assert release(board, rival).returncode == 0, "releasing the moved stream failed"

    given = release(board, mine).stdout
    walked = context_text(run_deliver(board, mine, "Start", "victim"))

    assert "-TakeOver" not in given, (
        f"release advises a key that has no one to take the address from: {given!r}"
    )
    assert "nothing to take the address back from" in given, (
        f"release stays silent about there being no one to take the address from: {given!r}"
    )
    assert "-TakeOver" not in walked, (
        f"the delivery hook advises a key with no one to take the address from: {walked!r}"
    )
    assert "under a free number" in walked, (
        f"the delivery hook printed no working way out for the tab: {walked!r}"
    )


@needs_pwsh
def test_a_forgotten_move_is_named_aloud_when_the_memory_overflows(tmp_path: Path) -> None:
    """A takeover forgotten to the memory limit is named ALOUD, not lost in silence.

    With every dropped edge the abandoned record of the old folder becomes the leader on that
    address again, and neither the display, nor intake, nor the delivery hook will say a word about
    it. The scene is practically unreachable (it takes a twenty-first takeover by one folder), but
    being unreachable is no reason to stay silent.
    """
    board = tmp_path / "board.jsonl"
    here = tmp_path / "folder"
    old_moves = [
        {
            "wave": "wave9",
            "stream": str(number),
            TAKEN_FROM_FIELD: f"d:/folder-{number}",
            TAKEN_AT_FIELD: hours_ago(100 - number),
        }
        for number in range(1, 26)
    ]
    put_claim(
        registry_dir(board),
        "folder",
        **open_claim(
            str(here),
            wave="wave9",
            stream="26",
            state="released",
            claimed_at=hours_ago(80),
            past_takeovers=old_moves,
        ),
    )

    out = claim(board, here, "wave9", "30", "-StreamName", "Next")

    loud = [text for text in out.splitlines() if text.startswith("‼️")]
    assert any("The takeover memory is full" in text for text in loud), (
        f"the forgotten takeover was lost in silence — nobody said a word about it: {out!r}"
    )
    assert "wave9/1" in out and "folder-1" in out, (
        f"the forgotten takeover isn't named: neither address nor the folder it left: {out!r}"
    )
