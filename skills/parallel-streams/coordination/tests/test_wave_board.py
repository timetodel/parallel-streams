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

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# The file lives in skills/parallel-streams/coordination/tests/ — the repository root is four levels
# up (tests -> coordination -> parallel-streams -> skills -> root).
REPO_ROOT = Path(__file__).resolve().parents[4]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
COORDINATION_DIR = REPO_ROOT / "skills" / "parallel-streams" / "coordination"
HOOKS_DIR = COORDINATION_DIR / "hooks"
TOOL = COORDINATION_DIR / "wave-board.ps1"
DELIVER = HOOKS_DIR / "wave-board-deliver.ps1"
NUDGE = HOOKS_DIR / "pretooluse-wave-board-nudge.ps1"

pwsh = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not pwsh, reason="pwsh not found — nothing to run the scripts with")


def settings() -> dict:
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
    """Patches the named tab's claim — sets what the test can't compute on its own.

    The list of touched files comes from git in the real tool, and test folders aren't repositories:
    substitute the list by hand and set a fresh timestamp so the guard doesn't recompute it.
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
    raise AssertionError(f"no claim for {worktree} in the registry")


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
    """Removes a field from a claim — what a claim filed by an earlier version of the tool looks like."""
    registry = board.parent / "streams"
    here = str(worktree).replace("\\", "/").rstrip("/")
    for path in registry.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if str(record.get("worktree", "")).replace("\\", "/").rstrip("/") != here:
            continue
        record.pop(field, None)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return
    raise AssertionError(f"no claim for {worktree} in the registry")


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
    """The named tab's claim file in the registry.

    Skips a file it can't decode as UTF-8 or parse as JSON: a neighbouring claim in the registry may
    deliberately be written in an unusual encoding (a separate check for that), and looking up one
    particular tab's file must not choke on a file that isn't the one being looked for.
    """
    registry = board.parent / "streams"
    here = str(worktree).replace("\\", "/").rstrip("/")
    for path in registry.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if str(record.get("worktree", "")).replace("\\", "/").rstrip("/") == here:
            return path
    raise AssertionError(f"no claim for {worktree} in the registry")


def address_of(board: Path, worktree: Path) -> str:
    """The stream address the tab announced for itself — the way neighbours will call it."""
    record = json.loads(claim_file_of(board, worktree).read_text(encoding="utf-8"))
    return f"{record['wave']}/{record['stream']}"


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
def test_a_number_from_the_plan_is_never_moved(tmp_path: Path) -> None:
    """A number from the plan is the stream's name, findings are addressed by it: it can't be moved,
    only reported.

    A takeover of a tab does happen deliberately (the first one closed without releasing), and a
    human resolves that kind of dispute.
    """
    board = tmp_path / "board.jsonl"
    first = tmp_path / "wave9-first"
    second = tmp_path / "wave9-second"
    claim(board, first, "wave9", "3")
    out = claim(board, second, "wave9", "3")

    assert address_of(board, second) == "wave9/3", (
        f"a named number got shifted — the plan's address moved on its own: {out!r}"
    )
    assert "has an open claim on this same stream" in out, (
        f"nothing was said about two claims on one number: {out!r}"
    )


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
