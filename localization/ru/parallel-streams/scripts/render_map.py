#!/usr/bin/env python3
"""Render a stream dependency map as an aligned box-drawing diagram.

Input is a tiny dependency notation, one stream per line:

    1:              # stream 1 depends on nothing
    4: 1            # stream 4 waits for stream 1
    9: 5, 6, 7, 8   # stream 9 waits for four streams

Output is a single connected diagram where a column is a start moment:
every stream in the leftmost column can be opened right now, the next
column opens once its dependencies land, and so on.

Alignment is computed from character positions, never typed by hand.
Run with --check to verify the result instead of eyeballing it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict

# Direction bits of a line segment leaving a cell.
UP, DOWN, LEFT, RIGHT = 1, 2, 4, 8

# Box-drawing character for every combination of directions.
GLYPH = {
    LEFT | RIGHT: "─",
    UP | DOWN: "│",
    DOWN | RIGHT: "┌",
    DOWN | LEFT: "┐",
    UP | RIGHT: "└",
    UP | LEFT: "┘",
    UP | DOWN | RIGHT: "├",
    UP | DOWN | LEFT: "┤",
    LEFT | RIGHT | DOWN: "┬",
    LEFT | RIGHT | UP: "┴",
    UP | DOWN | LEFT | RIGHT: "┼",
    LEFT: "─",
    RIGHT: "─",
    UP: "│",
    DOWN: "│",
}

ARROW = "►"

# Layout constants, in character cells.
INDENT = 3          # left margin before the first column
CHANNEL_GAP = 3     # from the right edge of a label to its vertical channel
CHANNEL_STEP = 2    # between two neighbouring channels
ARROW_RUN = 4       # from the last channel of a zone to the next column
ROW_STEP = 2        # blank row between two stream rows, for readability


class DiagramError(ValueError):
    """Raised when the input cannot be turned into a diagram."""


class Canvas:
    """Two layers: line segments (merged into box glyphs) and plain text."""

    def __init__(self) -> None:
        self.segments: dict[tuple[int, int], int] = defaultdict(int)
        self.text: dict[tuple[int, int], str] = {}

    def draw_horizontal(self, y: int, x_from: int, x_to: int) -> None:
        if x_to < x_from:
            x_from, x_to = x_to, x_from
        for x in range(x_from, x_to + 1):
            mask = 0
            if x > x_from:
                mask |= LEFT
            if x < x_to:
                mask |= RIGHT
            self.segments[(y, x)] |= mask

    def draw_vertical(self, x: int, y_from: int, y_to: int) -> None:
        if y_to < y_from:
            y_from, y_to = y_to, y_from
        for y in range(y_from, y_to + 1):
            mask = 0
            if y > y_from:
                mask |= UP
            if y < y_to:
                mask |= DOWN
            self.segments[(y, x)] |= mask

    def put_text(self, y: int, x: int, value: str) -> None:
        for offset, char in enumerate(value):
            self.text[(y, x + offset)] = char

    def put_arrow(self, y: int, x: int) -> None:
        self.text[(y, x)] = ARROW
        # The arrow head replaces a line cell, so keep the run continuous.
        self.segments.pop((y, x), None)

    def collisions(self) -> list[tuple[int, int]]:
        """Cells where a line runs through a label — always a layout bug."""
        return sorted(
            cell
            for cell, mask in self.segments.items()
            if mask and cell in self.text and self.text[cell] != ARROW
        )

    def render(self) -> str:
        cells = set(self.segments) | set(self.text)
        if not cells:
            return ""
        height = max(y for y, _ in cells) + 1
        width = max(x for _, x in cells) + 1
        lines = []
        for y in range(height):
            row = []
            for x in range(width):
                if (y, x) in self.text:
                    row.append(self.text[(y, x)])
                else:
                    mask = self.segments.get((y, x), 0)
                    row.append(GLYPH[mask] if mask else " ")
            lines.append("".join(row).rstrip())
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)


class Stream:
    def __init__(self, key: str, label: str, depends_on: list[str]) -> None:
        self.key = key
        self.label = label
        self.depends_on = depends_on
        self.column = 0
        self.row = 0


def parse_text(source: str) -> list[Stream]:
    """Parse the `id: dep, dep` notation. Blank lines and # comments ignored."""
    streams: list[Stream] = []
    seen: set[str] = set()
    for number, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, tail = line.partition(":")
        key = key.strip()
        if not key:
            raise DiagramError(f"line {number}: stream id is missing before ':'")
        if key in seen:
            raise DiagramError(f"line {number}: stream '{key}' is declared twice")
        seen.add(key)
        deps = [part.strip() for part in tail.replace(";", ",").split(",")]
        streams.append(Stream(key, key, [dep for dep in deps if dep and dep != "-"]))
    if not streams:
        raise DiagramError("no streams found in the input")
    return streams


def parse_json(source: str) -> list[Stream]:
    """Parse {"streams": [{"id": "1", "label": "S1", "depends_on": ["2"]}]}."""
    try:
        payload = json.loads(source)
    except json.JSONDecodeError as error:
        raise DiagramError(f"invalid JSON: {error}") from error
    entries = payload.get("streams") if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise DiagramError("expected a non-empty 'streams' array")
    streams: list[Stream] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DiagramError(f"stream #{index + 1} is not an object")
        key = str(entry.get("id", "")).strip()
        if not key:
            raise DiagramError(f"stream #{index + 1} has no 'id'")
        if key in seen:
            raise DiagramError(f"stream '{key}' is declared twice")
        seen.add(key)
        deps = entry.get("depends_on") or entry.get("dependsOn") or []
        if isinstance(deps, str):
            deps = [deps]
        label = str(entry.get("label") or key)
        streams.append(Stream(key, label, [str(dep).strip() for dep in deps]))
    return streams


def assign_columns(streams: list[Stream]) -> None:
    """A column is a start moment: 0 for streams that wait for nobody."""
    index = {stream.key: stream for stream in streams}
    for stream in streams:
        for dep in stream.depends_on:
            if dep not in index:
                raise DiagramError(
                    f"stream '{stream.key}' depends on unknown stream '{dep}'"
                )
            if dep == stream.key:
                raise DiagramError(f"stream '{stream.key}' depends on itself")

    resolved: dict[str, int] = {}
    pending = list(streams)
    while pending:
        progressed = False
        still_pending = []
        for stream in pending:
            if all(dep in resolved for dep in stream.depends_on):
                resolved[stream.key] = (
                    0
                    if not stream.depends_on
                    else 1 + max(resolved[dep] for dep in stream.depends_on)
                )
                progressed = True
            else:
                still_pending.append(stream)
        if not progressed:
            cycle = ", ".join(sorted(stream.key for stream in still_pending))
            raise DiagramError(f"dependencies form a cycle between: {cycle}")
        pending = still_pending

    for stream in streams:
        stream.column = resolved[stream.key]


def assign_rows(streams: list[Stream], forced_own_row: set[str]) -> None:
    """Continue a parent's row where possible, otherwise take a fresh one.

    Sharing a row keeps the diagram compact: a chain parent → child → grandchild
    reads as one straight line. Streams listed in `forced_own_row` always get a
    fresh row — that is how the caller resolves a collision found after routing.
    """
    index = {stream.key: stream for stream in streams}
    order = sorted(streams, key=lambda s: (s.column, _natural_key(s.key)))
    rows_taken_in_column: dict[int, set[int]] = defaultdict(set)
    continued_parents: set[str] = set()
    next_free_row = 0

    for stream in order:
        row = None
        if stream.key not in forced_own_row:
            parents = sorted(
                (index[dep] for dep in stream.depends_on),
                key=lambda s: (s.row, _natural_key(s.key)),
            )
            for parent in parents:
                # One parent hands its row to one child, so a chain reads as a
                # single straight line; later children drop to their own rows.
                if parent.key in continued_parents:
                    continue
                if parent.row in rows_taken_in_column[stream.column]:
                    continue
                row = parent.row
                continued_parents.add(parent.key)
                break
        if row is None:
            row = next_free_row
            next_free_row += 1
        stream.row = row
        rows_taken_in_column[stream.column].add(row)
        next_free_row = max(next_free_row, row + 1)


def _natural_key(key: str) -> tuple[int, str]:
    """Sort '2' before '10' while still accepting non-numeric ids."""
    return (0, f"{int(key):020d}") if key.isdigit() else (1, key)


def _compute_geometry(streams: list[Stream]) -> tuple[dict[int, int], dict[str, int]]:
    """X of every column, and X of the vertical channel that feeds each stream.

    A channel belongs to the *target*: everything that stream 8 waits for arrives
    on one vertical line just left of stream 8. Lines therefore converge where
    the reader is looking, instead of fanning out from their sources.
    """
    by_column: dict[int, list[Stream]] = defaultdict(list)
    for stream in streams:
        by_column[stream.column].append(stream)

    column_x: dict[int, int] = {}
    channel_x: dict[str, int] = {}
    x = INDENT
    for column in sorted(by_column):
        if column > 0:
            waiting = sorted(
                (s for s in by_column[column] if s.depends_on),
                key=lambda s: (s.row, _natural_key(s.key)),
            )
            zone = x
            for offset, stream in enumerate(waiting):
                channel_x[stream.key] = zone + CHANNEL_GAP + offset * CHANNEL_STEP
            if waiting:
                x = max(channel_x[stream.key] for stream in waiting) + ARROW_RUN
        column_x[column] = x
        x += max(len(stream.label) for stream in by_column[column])
    return column_x, channel_x


def _route(streams: list[Stream]) -> tuple[Canvas, set[str]]:
    """Draw the diagram; report streams whose incoming line hits a label.

    A line to a target runs along the target's row, so the fix for such a hit
    is always to move that target to a row of its own — see build_diagram.
    """
    index = {stream.key: stream for stream in streams}
    column_x, channel_x = _compute_geometry(streams)

    canvas = Canvas()
    label_cells: dict[tuple[int, int], str] = {}
    for stream in streams:
        y = stream.row * ROW_STEP
        x = column_x[stream.column]
        canvas.put_text(y, x, stream.label)
        for offset in range(len(stream.label)):
            label_cells[(y, x + offset)] = stream.key

    blocked: set[str] = set()

    def scan(y: int, x_from: int, x_to: int, owners: set[str]) -> None:
        """Record any stream whose label sits under the segment about to be drawn."""
        for x in range(min(x_from, x_to), max(x_from, x_to) + 1):
            owner = label_cells.get((y, x))
            if owner is not None and owner not in owners:
                blocked.add(owner)

    for stream in streams:
        if not stream.depends_on:
            continue
        channel = channel_x[stream.key]
        target_y = stream.row * ROW_STEP
        arrow_x = column_x[stream.column] - 2
        parents = sorted(
            (index[dep] for dep in stream.depends_on),
            key=lambda s: (s.row, _natural_key(s.key)),
        )
        for parent in parents:
            parent_y = parent.row * ROW_STEP
            label_end = column_x[parent.column] + len(parent.label)
            scan(parent_y, label_end + 1, channel, {parent.key, stream.key})
            canvas.draw_horizontal(parent_y, label_end + 1, channel)
        rows = [parent.row * ROW_STEP for parent in parents] + [target_y]
        canvas.draw_vertical(channel, min(rows), max(rows))
        scan(target_y, channel, arrow_x, {stream.key})
        canvas.draw_horizontal(target_y, channel, arrow_x)
        canvas.put_arrow(target_y, arrow_x)
    return canvas, blocked


def build_diagram(streams: list[Stream]) -> str:
    """Lay out, route, and resolve overlaps by giving a stream its own row.

    Compact first: rows are shared so an unbranched chain reads as one straight
    line. Every stream whose incoming line would cross a label is then pinned to
    its own row and the layout is retried. Giving every stream its own row always
    works, so the loop terminates with a correct diagram in the worst case.
    """
    assign_columns(streams)
    forced_own_row: set[str] = set()
    for _ in range(len(streams) + 2):
        assign_rows(streams, forced_own_row)
        canvas, blocked = _route(streams)
        if not blocked and not canvas.collisions():
            return canvas.render()
        if blocked - forced_own_row:
            forced_own_row |= blocked
            continue
        forced_own_row = {stream.key for stream in streams}
    assign_rows(streams, {stream.key for stream in streams})
    canvas, _ = _route(streams)
    collisions = canvas.collisions()
    if collisions:
        raise DiagramError(
            "could not lay out these dependencies without overlapping labels; "
            "please report this input at "
            "https://github.com/timetodel/parallel-streams/issues"
        )
    return canvas.render()


def check_diagram(streams: list[Stream], diagram: str) -> list[str]:
    """Verify the rendered diagram instead of trusting the eye."""
    problems: list[str] = []
    lines = diagram.split("\n")
    column_x, _ = _compute_geometry(streams)

    for stream in streams:
        # Whole-label match only: plain counting would find "S1" inside "S10".
        pattern = re.compile(
            rf"(?<![0-9A-Za-z_]){re.escape(stream.label)}(?![0-9A-Za-z_])"
        )
        occurrences = sum(len(pattern.findall(line)) for line in lines)
        if occurrences != 1:
            problems.append(
                f"stream {stream.label} appears {occurrences} times, expected once"
            )

    by_column: dict[int, list[Stream]] = defaultdict(list)
    for stream in streams:
        by_column[stream.column].append(stream)
    for column, members in sorted(by_column.items()):
        expected = column_x[column]
        for stream in members:
            line = lines[stream.row * ROW_STEP]
            if not line.startswith(stream.label, expected):
                problems.append(
                    f"stream {stream.label} starts at a different position than "
                    f"the rest of column {column + 1}"
                )

    for number, line in enumerate(lines):
        for char in line:
            if char not in " ─│┌┐└┘├┤┬┴┼►" and not char.isalnum():
                problems.append(f"line {number + 1} contains unexpected character {char!r}")
                break
    return problems


def _force_utf8_output() -> None:
    """Box-drawing characters must survive a legacy console code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="render_map.py",
        description="Render an aligned stream dependency diagram.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="file with the dependency notation, or - for stdin (default)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="input format (default: text)",
    )
    parser.add_argument(
        "--prefix",
        default="S",
        help="label prefix for numeric ids (default: S, giving S1, S2, ...)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the rendered diagram and report problems on stderr",
    )
    args = parser.parse_args(argv)

    try:
        source = sys.stdin.read() if args.input == "-" else _read_file(args.input)
        streams = parse_json(source) if args.format == "json" else parse_text(source)
        for stream in streams:
            if stream.label == stream.key and args.prefix:
                stream.label = f"{args.prefix}{stream.key}"
        diagram = build_diagram(streams)
    except DiagramError as error:
        print(f"render_map: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"render_map: cannot read input: {error}", file=sys.stderr)
        return 2

    print(diagram)
    if args.check:
        problems = check_diagram(streams, diagram)
        if problems:
            for problem in problems:
                print(f"render_map: check failed: {problem}", file=sys.stderr)
            return 1
        print("render_map: check passed", file=sys.stderr)
    return 0


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


if __name__ == "__main__":
    raise SystemExit(main())
