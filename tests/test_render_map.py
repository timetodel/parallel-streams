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
