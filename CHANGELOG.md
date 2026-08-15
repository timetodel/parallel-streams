# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-08-15

First public release.

### Added

- **The skill** — splits an approved plan into parallel work streams: dependency map plus one
  self-contained brief per stream.
- **Generated diagrams** — `render_map.py` computes every character position and verifies its own
  output with `--check`. Standard library only, Python 3.9+.
- **Dependency analysis reference** — what counts as a cross-stream dependency, what only looks
  like one, the traps that read as independent, and how to cut a cycle.
- **Brief template** — fixed block order, with escalation and review mandatory in every brief.
- **Project profiles** — `.parallel-streams.md` carries your isolation model, test command, review
  gate, and merge policy into every brief. Template and a real-world profile included.
- **Three install paths** — ask your agent (see `INSTALL.md`), the plugin marketplace, or a
  directory copy.
- **Worked example** — an approved plan and the full split produced from it.
