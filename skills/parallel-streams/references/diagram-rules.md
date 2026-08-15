# The diagram: how to generate it and how it reads

## Why it is generated, not drawn

An ASCII diagram drifts by one character and the drift is invisible to whoever wrote it — it is
only ever caught by the person reading it, which is the person you were trying to help. The bundled
renderer computes every position and then verifies its own output.

Do not hand-draw the diagram. Do not "fix up" the generated one by hand either; change the input and
render again.

## Running the renderer

The renderer ships next to this skill, at `scripts/render_map.py` inside the skill directory.
It needs Python 3.9+ and nothing else — no packages, no network.

Write the dependencies to a scratch file, one stream per line:

```
1:              # waits for nobody
2:
3:
4: 1            # waits for stream 1
5: 1, 2
6: 1
7: 4
8: 4, 3
9: 7, 8, 6, 5
```

Then render and verify in one go:

```
python scripts/render_map.py streams.txt --check
```

Output:

```
   S1 ──┐
        │
   S2 ──┼─────► S5 ──────┬────────┬─┬─► S9
        │                │        │ │
   S3 ──┼──────────────┬─┼─► S8 ──┘ │
        │              │ │          │
        ├─────► S4 ────┤ │          │
        │              │ │          │
        └─────► S6 ────┼─┘          │
                       │            │
                       └───► S7 ────┘
```

Paste that verbatim into a fenced block. `--check` prints `check passed` on stderr, or lists what is
wrong and exits non-zero — if it fails, the diagram does not go to the user.

### Options

| Option | Purpose |
|---|---|
| `--check` | Verify the rendered diagram: one appearance per stream, columns aligned, no stray characters |
| `--prefix S` | Label prefix for numeric ids; `--prefix ""` gives bare numbers |
| `--format json` | Read `{"streams": [{"id": "auth", "label": "auth", "depends_on": []}]}` instead of the line notation |

Reading from stdin works too: `... | python scripts/render_map.py --check`.

### Errors it will give you

- `depends on unknown stream 'X'` — a typo, or a stream you forgot to declare.
- `dependencies form a cycle between: ...` — the split itself is wrong. See the cycle section of
  `dependency-analysis.md`.
- `stream 'X' is declared twice` — two lines for one stream.

These are findings about your split, not renderer problems. Fix the split.

## How the picture reads

- **A column is a start moment.** Everything in the leftmost column can be opened right now. The
  next column opens once its dependencies are merged, and so on. This is the whole point: the reader
  counts a column and knows how many sessions to launch today.
- **Every stream appears exactly once.** A stream drawn twice turns one picture into two.
- **Crossings are fine.** Column position wins over avoiding a crossing; a line with a distant
  dependency honestly crosses the ones in between.
- **Only numbers on the diagram** (S1, S2). Names live in the table.
- No headings, no column captions, no blank separator lines inside the picture — a caption turns one
  diagram into several.

## If Python is unavailable

Say so in the answer — do not pretend the diagram was verified. Then either:

1. give the table alone and note that the diagram was skipped because no Python runtime was
   available, or
2. draw it by hand, and say it was hand-drawn.

For the hand-drawn case the rules are: box-drawing characters only (`─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ ►`),
never dashes and pipes; every stream once; labels of one column start at the same character
position; a branch is placed on the line after the label (`S1 ──┬──►`), never under it.
