"""Tests for the diagram renderer.

Run with: python -m pytest tests
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "parallel-streams" / "scripts"))

from render_map import (  # noqa: E402
    DiagramError,
    build_diagram,
    check_diagram,
    parse_json,
    parse_table,
    parse_text,
)


def render(notation: str, prefix: str = "S") -> tuple[str, list]:
    streams = parse_text(notation)
    for stream in streams:
        stream.label = f"{prefix}{stream.key}"
    diagram = build_diagram(streams)
    return diagram, streams


def test_single_stream_renders_its_label():
    diagram, _ = render("1:")
    assert diagram.strip() == "S1"


def test_chain_stays_on_one_line():
    diagram, streams = render("1:\n2: 1\n3: 2\n")
    assert len(diagram.split("\n")) == 1
    assert check_diagram(streams, diagram) == []
    assert diagram.index("S1") < diagram.index("S2") < diagram.index("S3")


def test_every_stream_appears_exactly_once():
    diagram, streams = render("1:\n2:\n3:\n4: 1\n5: 1, 2\n6: 1\n7: 4\n8: 4, 3\n9: 7, 8, 6, 5\n")
    for stream in streams:
        assert diagram.count(stream.label) == 1
    assert check_diagram(streams, diagram) == []


def test_streams_of_one_column_start_at_the_same_position():
    diagram, streams = render("1:\n2:\n3: 1\n4: 2\n")
    lines = diagram.split("\n")
    positions = {}
    for stream in streams:
        for line in lines:
            index = line.find(stream.label)
            if index >= 0:
                positions.setdefault(stream.column, set()).add(index)
    for column, found in positions.items():
        assert len(found) == 1, f"column {column} is not aligned: {found}"


def test_arrow_points_at_every_dependent_stream():
    diagram, _ = render("1:\n2: 1\n3: 1\n")
    assert diagram.count("►") == 2


def test_diagram_is_deterministic():
    notation = "1:\n2:\n3: 1, 2\n4: 3\n5: 1\n"
    first, _ = render(notation)
    second, _ = render(notation)
    assert first == second


def test_only_box_drawing_characters_are_used():
    diagram, streams = render("1:\n2: 1\n3: 1\n4: 2, 3\n")
    allowed = set(" ─│┌┐└┘├┤┬┴┼►")
    for char in diagram.replace("\n", ""):
        assert char in allowed or char.isalnum(), f"unexpected character {char!r}"


def test_unknown_dependency_is_rejected():
    with pytest.raises(DiagramError, match="unknown stream"):
        render("1:\n2: 7\n")


def test_cycle_is_rejected():
    with pytest.raises(DiagramError, match="cycle"):
        render("1: 2\n2: 1\n")


def test_self_dependency_is_rejected():
    with pytest.raises(DiagramError, match="itself"):
        render("1: 1\n")


def test_duplicate_stream_is_rejected():
    with pytest.raises(DiagramError, match="twice"):
        render("1:\n1:\n")


def test_empty_input_is_rejected():
    with pytest.raises(DiagramError, match="no streams"):
        render("# only a comment\n")


def test_comments_and_blank_lines_are_ignored():
    diagram, streams = render("# plan\n\n1:\n\n2: 1  # after the first\n")
    assert len(streams) == 2
    assert check_diagram(streams, diagram) == []


def test_json_input_supports_custom_labels():
    streams = parse_json(
        '{"streams": [{"id": "a", "label": "auth"}, '
        '{"id": "b", "label": "billing", "depends_on": ["a"]}]}'
    )
    diagram = build_diagram(streams)
    assert "auth" in diagram and "billing" in diagram
    assert check_diagram(streams, diagram) == []


def test_invalid_json_is_rejected():
    with pytest.raises(DiagramError, match="invalid JSON"):
        parse_json("{not json")


@pytest.mark.parametrize("seed", range(40))
def test_random_dependency_graphs_render_cleanly(seed):
    """No overlap, no duplicate labels, columns aligned — on arbitrary plans."""
    rng = random.Random(seed)
    count = rng.randint(2, 12)
    lines = []
    for index in range(1, count + 1):
        candidates = list(range(1, index))
        rng.shuffle(candidates)
        deps = candidates[: rng.randint(0, min(3, len(candidates)))]
        lines.append(f"{index}: {', '.join(str(dep) for dep in deps)}")
    diagram, streams = render("\n".join(lines))
    assert check_diagram(streams, diagram) == [], f"seed {seed} produced a broken diagram"


# ─── The map's own table as the input ────────────────────────────────────────────────────────────
#
# Every check below was a checklist line answered by eye. On a table of nine streams the eye is
# exactly what fails: a dependency written on one side and not the other, a blank cell that reads
# as "nothing to say", a transitive edge repeated until the diagram is a mesh. Machine-checkable
# means the checklist need not ask.

GOOD_TABLE = """
| Stream | Name | Waits for | Blocks | Escalation | Review |
|---|---|---|---|---|---|
| 1 | Flag | nothing | 2, 3 | none | light |
| 2 | Reader | 1 | 4 | none | light |
| 3 | Writer | 1 | 4 | none | deep |
| 4 | Acceptance | 2, 3 | — | whole stream | deep |
"""

RUSSIAN_TABLE = """
| Поток | Название | Ждёт | Держит | Усиленный режим | Разбор |
|---|---|---|---|---|---|
| 1 | Признак | ничего | 2 | не нужен | обычный |
| 2 | Приёмка | 1 | — | весь поток | глубокий |
"""


def table_problems(text: str) -> list[str]:
    _, _, problems = parse_table(text)
    return problems


def test_a_sound_table_passes_and_draws_its_own_diagram():
    streams, rows, problems = parse_table(GOOD_TABLE)
    assert problems == [], problems
    assert [stream.key for stream in streams] == ["1", "2", "3", "4"]
    assert [row.key for row in rows] == ["1", "2", "3", "4"]
    # The diagram is built from the very table the reader sees — that is the whole point of reading
    # the table instead of a notation retyped beside it.
    for stream in streams:
        stream.label = f"S{stream.key}"
    diagram = build_diagram(streams)
    assert "S1" in diagram and "S4" in diagram
    assert check_diagram(streams, diagram) == []


def test_the_russian_copy_of_the_table_is_read_too():
    """Both language copies ship one script — one that knew only English headings would refuse the
    very table the copy beside it tells sessions to write."""
    streams, _, problems = parse_table(RUSSIAN_TABLE)
    assert problems == [], problems
    assert [stream.depends_on for stream in streams] == [[], ["1"]]


def test_a_dependency_written_on_one_side_only_is_caught():
    broken = GOOD_TABLE.replace("| 1 | Flag | nothing | 2, 3 |", "| 1 | Flag | nothing | 2 |")
    problems = table_problems(broken)
    assert any("3 waits for 1" in problem and "does not list" in problem for problem in problems), (
        f"the two columns disagree and nothing said so: {problems}"
    )


def test_a_blank_cell_is_not_an_answer():
    blank = GOOD_TABLE.replace("| 2 | Reader | 1 | 4 | none | light |", "| 2 | Reader | 1 | 4 |  | light |")
    problems = table_problems(blank)
    assert any("empty 'escalation'" in problem for problem in problems), (
        f"a blank cell passed for an answer: {problems}"
    )


def test_none_with_a_reason_is_an_answer():
    assert table_problems(GOOD_TABLE) == []


def test_a_transitive_edge_is_named():
    mesh = GOOD_TABLE.replace(
        "| 4 | Acceptance | 2, 3 | — | whole stream | deep |",
        "| 4 | Acceptance | 1, 2, 3 | — | whole stream | deep |",
    ).replace("| 1 | Flag | nothing | 2, 3 |", "| 1 | Flag | nothing | 2, 3, 4 |")
    problems = table_problems(mesh)
    assert any("transitive" in problem for problem in problems), (
        f"a transitive edge went unnoticed: {problems}"
    )


def test_a_stream_that_does_not_exist_is_named():
    dangling = GOOD_TABLE.replace("| 2 | Reader | 1 | 4 |", "| 2 | Reader | 1 | 7 |")
    problems = table_problems(dangling)
    assert any("no such stream" in problem for problem in problems), (
        f"a reference to a stream that is not in the table passed: {problems}"
    )


def test_a_stream_listed_twice_is_named():
    doubled = GOOD_TABLE + "| 2 | Reader again | 1 | 4 | none | light |\n"
    problems = table_problems(doubled)
    assert any("appears twice" in problem for problem in problems), (
        f"one stream on two rows passed: {problems}"
    )


@pytest.mark.parametrize(
    "table, expected",
    [
        (
            "| Stream | Name | Waits for | Blocks | Review |\n|---|---|---|---|---|\n| 1 | A | nothing | — | light |\n",
            "expected exactly 6",
        ),
        (
            "| Stream | Name | Blocks | Waits for | Escalation | Review |\n|---|---|---|---|---|---|\n| 1 | A | — | nothing | none | light |\n",
            "the six columns are fixed",
        ),
        ("nothing resembling a table here\n", "no markdown table"),
    ],
)
def test_a_table_of_the_wrong_shape_is_refused_out_loud(table: str, expected: str):
    """The shape is refused, not worked around: a five-column map is not a map with a column
    missing, it is a map whose reader cannot tell which column they are looking at."""
    with pytest.raises(DiagramError) as failure:
        parse_table(table)
    assert expected in str(failure.value)
