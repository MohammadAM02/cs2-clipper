# Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A background app that turns every FACEIT Demo landing in Downloads into Reels of its top 5 Highlights, from both Perspectives, with no per-match input.

**Architecture:** A Python package `clipper/` with one job per module. A single-threaded worker moves each Demo through a state machine persisted in SQLite (`data/clipper.sqlite`). CS:DM's CLI analyzes and renders; `csdm_db` is the only reader of CS:DM's Postgres; the Gate decides when CS2 may launch. Tests come first, and no automated test needs CS2: a fake `csdm` script stands in.

**Tech Stack:** Python 3.13 (uv), psycopg 3, psutil, zstandard, pytest; CS Demo Manager 3.20.1 CLI; FFmpeg/ffprobe 8.1; SQLite (stdlib); PowerShell for notifications and Task Scheduler.

**Spec:** `.scratch/orchestrator/spec.md` — read it first; this plan argues from it. Background: `CONTEXT.md` (glossary), `docs/adr/0001`–`0003`, `spike/ACCEPTANCE.md` (why the render rules exist).

## Global Constraints

- Python ≥ 3.13 via uv. Runtime dependencies are only `psycopg[binary]`, `psutil`, `zstandard`; `pytest` is the only dev dependency.
- Windows only. Every path handed to CS:DM or FFmpeg is a native Windows path (`str(Path)`), never an MSYS path like `/c/...`.
- csdm child processes run with `USERPROFILE=<repo>/home`, `ELECTRON_RUN_AS_NODE=1`, the portable Postgres `bin` first on `PATH`, and no `PGPASSWORD`. The app itself runs with the user's real profile.
- csdm's exit code is never trusted: success is judged from its output and the files it wrote.
- The **hooked CS2** is a `cs2.exe` whose command line contains `-insecure`. Never stop csdm while a hooked CS2 runs — close the hooked CS2 instead. Never close a CS2 the user started.
- A hooked CS2 never runs while FACEIT AC runs (`FACEITService` not stopped, or any process whose name starts with `faceit`).
- `clipper/csdm_db.py` is the only code that reads CS:DM's database, and it never writes. `clipper/index.py` is the only code that writes our SQLite.
- Only `E:\cs2clips\library\` is ever shared. Reels go to `library\videos\<match checksum>\r<round>-<perspective>.mp4`.
- Use the glossary's words (Demo, Tick, Highlight, Frag, Kill, Clip, Reel, Sequence, Perspective, Render Job, Gate) in names, messages and tests.
- Modules pass Ticks, never seconds (64 Ticks per second); seconds appear only as durations and as the padding handed to csdm.
- Defaults: top 5 by Score (ties go to the earlier round), padding 4 s before / 2 s after, stall 180 s, csdm-never-launched 300 s, 5 GB minimum free, 30 s heads-up, 5 s poll, 3 attempts per step and per Render Job, pause after 3 failed render attempts in a row.
- One deliberate simplification: the Gate is checked on every tick (every 5 s), more often than the spec's "every 15 s", which is harmless.
- Run tests with `uv run pytest -q`. Tests marked `integration` skip when CS:DM's Postgres is unreachable; `ffmpeg` tests skip when FFmpeg is missing.
- **Tasks 1 and 13 launch CS2, and Task 7 Step 6 needs the FACEIT client opened for a minute — all three need the user at the PC.** An executing agent stops at them and hands over.
- Work on the current branch (`master`, no remote). Commit at the end of every task.

## File map

```
pyproject.toml  .gitattributes  clipper.example.toml  uv.lock
clipper/
  __init__.py  __main__.py
  config.py      settings and derived folders                  Task 2
  windows.py     Downloads folder, keep-awake                  Task 2
  model.py       RoundFacts, Highlight, MatchInfo, ClipFile    Tasks 3–5
  scoring.py     rules → Highlights; top N                     Task 3
  csdm_db.py     the only reader of CS:DM's Postgres           Task 4
  index.py       the only writer of our SQLite                 Task 5
  intake.py      Downloads → demos\                            Task 6
  unpack.py      .zst / .gz → .dem                             Task 6
  procs.py       processes and services (psutil)               Task 7
  gate.py        may CS2 launch now?                           Task 7
  csdm_cli.py    csdm command + environment; analyze           Task 8
  media.py       ffprobe durations                             Task 8
  render.py      one csdm video call, watched                  Task 8
  join.py        Clips → Highlights; concatenation             Task 9
  worker.py      the state machine                             Task 10
  notify.py      Windows notifications                         Task 11
  postgres.py    start the portable Postgres                   Task 11
  cli.py         run / status / retry / resume / highlights    Tasks 11–12
  install.py     the sign-in task                              Task 12
tests/
  __init__.py  fixtures.py  fake_csdm.py  test_*.py
```

Removed along the way: `db/highlights.sql` (Task 4) and `scripts/render_reel.sh` (Task 13).

---

### Task 1: Check the padding flags on the real Demo (user — launches CS2)

**Who:** the user. This launches CS2, so an executing agent stops and asks. Only the padding defaults depend on it, so Tasks 2–12 can go ahead meanwhile.

**Files:**
- Modify: `spike/ACCEPTANCE.md` (Run log table)

- [ ] **Step 1: Clear the Gate by hand**

Close CS2, the FACEIT client and CS Demo Manager. In PowerShell, `Get-Service FACEITService` must show `Stopped`. Then check Postgres:

```bash
python scripts/setup_local_postgres.py --status
```

Expected: `running            : True`. If it says `False`, run `python scripts/setup_local_postgres.py` and check again.

- [ ] **Step 2: Render round 12 with 4 s / 2 s padding into a fresh folder** (Git Bash, repo root)

```bash
REEL_OUT_DIR=E:/cs2clips/_padding_test scripts/render_reel.sh spike/demos/1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.dem player 12 --start-seconds-before 4 --end-seconds-after 2
```

Expected: CS2 opens and closes by itself, then `OK at …`.

- [ ] **Step 3: Check the Sequences**

```bash
for f in /e/cs2clips/_padding_test/player/sequence-*.mp4; do printf '%s  ' "$(basename "$f")"; ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"; done
```

Expected — the subject's Kills are at Ticks 74135, 76199, 76431 and 78361; 4 s = 256 Ticks before, 2 s = 128 after; overlapping Sequences merge:

```
sequence-1-tick-73879-to-74263.mp4  6.0 (±0.1)
sequence-2-tick-75943-to-76559.mp4  9.6 (±0.1)
sequence-3-tick-78105-to-78489.mp4  6.0 (±0.1)
```

Watch `sequence-1`: the Frag lands about 4 s in. If the names still read `74007-to-74263` (2 s padding), the flags are ignored — record ❌ in Step 4 and stop: the spec's padding needs rethinking before Task 13.

- [ ] **Step 4: Record the run**

Add a row to the Run log table in `spike/ACCEPTANCE.md` with the date, wall-clock time and total size you saw, for example:

```
| 4 | 2026-09-23 | `video <demo> --mode player --steamids … --event kills --rounds 12 --perspective player --start-seconds-before 4 --end-seconds-after 2` | 3 | ~90 s | 25 MB | ✅ padding flags honoured |
```

- [ ] **Step 5: Commit**

```bash
git add spike/ACCEPTANCE.md
git commit -m "docs: record the padding-flag check (orchestrator plan, task 1)"
```

---

### Task 2: Project scaffolding, settings and Windows helpers

**Files:**
- Create: `.gitattributes`, `pyproject.toml`, `clipper.example.toml`, `clipper/__init__.py`, `clipper/windows.py`, `clipper/config.py`, `tests/__init__.py`, `tests/test_windows.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `clipper.windows.downloads_dir() -> Path`; `clipper.windows.keep_awake()` (context manager); `clipper.config.REPO_ROOT: Path`; `clipper.config.Config` (frozen dataclass — fields `subject_steamid, downloads_dir, data_root, index_path, csdm_home, csdm_app_dir, pg_bin, pg_data, ffmpeg, ffprobe, top_n, padding_before_s, padding_after_s, stall_seconds, launch_timeout_seconds, min_free_gb, heads_up_seconds, poll_seconds`; properties `demos_dir, renders_dir, library_dir, logs_dir, csdm_exe, csdm_cli_js`; method `database_conninfo() -> dict[str, object]`); `clipper.config.load_config(path: Path | None = None) -> Config`.

- [ ] **Step 1: Pin line endings**

Git for Windows otherwise checks text files out with CRLF, which breaks `.sh` scripts. Create `.gitattributes`:

```
# LF everywhere except Windows batch and PowerShell files.
* text=auto eol=lf
*.cmd text eol=crlf
*.bat text eol=crlf
*.ps1 text eol=crlf
*.dem binary
*.zst binary
*.mp4 binary
```

Run:

```bash
git add .gitattributes && git add --renormalize . && git status --short
```

Expected: `A  .gitattributes` and nothing else (the repository already stores LF).

- [ ] **Step 2: Create the project files**

`pyproject.toml`:

```toml
[project]
name = "cs2-clipper"
version = "0.1.0"
description = "Hands-off CS2 highlight clipper: a FACEIT Demo in, Reels out."
requires-python = ">=3.13"
dependencies = [
    "psycopg[binary]>=3.2",
    "psutil>=6.0",
    "zstandard>=0.23",
]

[project.scripts]
clipper = "clipper.cli:main"

[dependency-groups]
dev = ["pytest>=8.3"]

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["clipper"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: reads the local CS:DM Postgres (skipped when it is not reachable)",
    "ffmpeg: needs ffmpeg and ffprobe on PATH (skipped when missing)",
]
```

`clipper/__init__.py`:

```python
"""Hands-off CS2 highlight clipper: a FACEIT Demo in, Reels out."""
```

`tests/__init__.py`: an empty file.

`clipper.example.toml`:

```toml
# Copy to clipper.toml to override any default. Every setting is optional.
# subject_steamid = "76561198192858303"
# downloads_dir = "C:/Users/AMG/Downloads"   # default: Windows' Downloads folder
# data_root = "E:/cs2clips"
# top_n = 5
# padding_before_s = 4.0
# padding_after_s = 2.0
# stall_seconds = 180.0
# launch_timeout_seconds = 300.0
# min_free_gb = 5.0
# heads_up_seconds = 30.0
# poll_seconds = 5.0
# ffmpeg = "ffmpeg"
# ffprobe = "ffprobe"
```

- [ ] **Step 3: Install the environment**

```bash
uv sync
uv run python -c "import psycopg, psutil, zstandard; print('deps ok')"
```

Expected: `uv sync` installs psycopg, psutil, zstandard and pytest, and removes the unused packages already in `.venv` (demoparser2, numpy, pandas, polars, pyarrow, …). The second command prints `deps ok`.

- [ ] **Step 4: Write the failing tests**

`tests/test_windows.py`:

```python
from clipper.windows import downloads_dir, keep_awake


def test_downloads_dir_is_an_existing_absolute_folder():
    path = downloads_dir()
    assert path.is_absolute()
    assert path.is_dir()


def test_keep_awake_enters_and_leaves_cleanly():
    with keep_awake():
        pass
```

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from clipper.config import Config, load_config


def test_defaults_match_the_spec(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.top_n == 5
    assert (cfg.padding_before_s, cfg.padding_after_s) == (4.0, 2.0)
    assert cfg.stall_seconds == 180.0
    assert cfg.library_dir == Path("E:/cs2clips/library")
    assert cfg.csdm_cli_js.name == "cli.js"


def test_toml_overrides_and_converts_paths(tmp_path):
    toml = tmp_path / "clipper.toml"
    toml.write_text('top_n = 3\ndata_root = "D:/clips"\n', encoding="utf-8")
    cfg = load_config(toml)
    assert cfg.top_n == 3
    assert cfg.data_root == Path("D:/clips")
    assert cfg.demos_dir == Path("D:/clips/demos")


def test_an_unknown_setting_is_rejected(tmp_path):
    toml = tmp_path / "clipper.toml"
    toml.write_text("topn = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="topn"):
        load_config(toml)


def test_database_conninfo_comes_from_csdm_settings(tmp_path):
    settings = tmp_path / ".csdm" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        '{"database": {"hostname": "127.0.0.1", "port": 5432, "username": "postgres",'
        ' "password": "pw", "database": "csdm"}}',
        encoding="utf-8",
    )
    cfg = Config(downloads_dir=tmp_path, csdm_home=tmp_path)
    assert cfg.database_conninfo() == {
        "host": "127.0.0.1", "port": 5432, "user": "postgres", "password": "pw", "dbname": "csdm",
    }
```

- [ ] **Step 5: Run them to see them fail**

Run: `uv run pytest tests/test_windows.py tests/test_config.py -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'clipper.windows'` and `'clipper.config'`.

- [ ] **Step 6: Write `clipper/windows.py`**

```python
"""Windows helpers: the real Downloads folder, and keeping the PC awake during a render."""

from __future__ import annotations

import ctypes
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# FOLDERID_Downloads, from KnownFolders.h
_FOLDERID_DOWNLOADS = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def downloads_dir() -> Path:
    """The user's Downloads folder as Windows knows it. Never derived from USERPROFILE, which the
    csdm child processes override."""
    guid = ctypes.create_string_buffer(_FOLDERID_DOWNLOADS.bytes_le, 16)
    path_ptr = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path_ptr))
    if result != 0:
        raise OSError(f"SHGetKnownFolderPath failed with HRESULT {result & 0xFFFFFFFF:#010x}")
    try:
        return Path(path_ptr.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)


@contextmanager
def keep_awake() -> Iterator[None]:
    """Ask Windows not to sleep until the block ends."""
    set_state = ctypes.windll.kernel32.SetThreadExecutionState
    set_state.argtypes = [ctypes.c_uint32]
    set_state.restype = ctypes.c_uint32
    set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        set_state(_ES_CONTINUOUS)
```

- [ ] **Step 7: Write `clipper/config.py`**

```python
"""Settings. Defaults fit this machine; any of them can be overridden in clipper.toml at the repo root."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from clipper.windows import downloads_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
_PATH_SETTINGS = frozenset(
    {"downloads_dir", "data_root", "index_path", "csdm_home", "csdm_app_dir", "pg_bin", "pg_data"}
)


@dataclass(frozen=True)
class Config:
    subject_steamid: str = "76561198192858303"
    downloads_dir: Path = field(default_factory=downloads_dir)
    data_root: Path = Path("E:/cs2clips")
    index_path: Path = REPO_ROOT / "data" / "clipper.sqlite"
    csdm_home: Path = REPO_ROOT / "home"
    csdm_app_dir: Path = _LOCALAPPDATA / "Programs" / "cs-demo-manager"
    pg_bin: Path = _LOCALAPPDATA / "pg17" / "pgsql" / "bin"
    pg_data: Path = _LOCALAPPDATA / "pg17" / "data"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    top_n: int = 5
    padding_before_s: float = 4.0
    padding_after_s: float = 2.0
    stall_seconds: float = 180.0
    launch_timeout_seconds: float = 300.0
    min_free_gb: float = 5.0
    heads_up_seconds: float = 30.0
    poll_seconds: float = 5.0

    @property
    def demos_dir(self) -> Path:
        return self.data_root / "demos"

    @property
    def renders_dir(self) -> Path:
        return self.data_root / "renders"

    @property
    def library_dir(self) -> Path:
        return self.data_root / "library"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def csdm_exe(self) -> Path:
        return self.csdm_app_dir / "cs-demo-manager.exe"

    @property
    def csdm_cli_js(self) -> Path:
        return self.csdm_app_dir / "resources" / "app.asar" / "cli.js"

    def database_conninfo(self) -> dict[str, object]:
        """CS:DM's own connection settings. The password lives only in CS:DM's settings file."""
        settings = json.loads((self.csdm_home / ".csdm" / "settings.json").read_text(encoding="utf-8"))
        db = settings["database"]
        return {
            "host": db["hostname"],
            "port": db["port"],
            "user": db["username"],
            "password": db["password"],
            "dbname": db["database"],
        }


def load_config(path: Path | None = None) -> Config:
    """Defaults, overridden by the TOML file at `path` (default: <repo>/clipper.toml) if it exists."""
    path = path or REPO_ROOT / "clipper.toml"
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(raw) - {f.name for f in fields(Config)})
    if unknown:
        raise ValueError(f"unknown setting(s) in {path}: {', '.join(unknown)}")
    return Config(**{key: Path(value) if key in _PATH_SETTINGS else value for key, value in raw.items()})
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_windows.py tests/test_config.py -q`
Expected: `6 passed`.

- [ ] **Step 9: Commit**

```bash
git add .gitattributes pyproject.toml uv.lock clipper.example.toml clipper/__init__.py clipper/windows.py clipper/config.py tests/__init__.py tests/test_windows.py tests/test_config.py
git commit -m "feat: project scaffolding, settings and Windows helpers"
```

---

### Task 3: Scoring rules

**Files:**
- Create: `clipper/model.py`, `clipper/scoring.py`, `tests/fixtures.py`, `tests/test_scoring.py`

**Interfaces:**
- Consumes: the package from Task 2.
- Produces: `clipper.model.RoundFacts(round: int, frag_ticks: tuple[int, ...], headshots=0, blind_frags=0, knife_frags=0, noscope_frags=0, smoke_frags=0, bot_frags=0, round_won=False, round_start_tick=0, round_end_tick=0)` with property `frags -> int`; `clipper.model.Highlight(round: int, type: str, score: int, reasons: tuple[str, ...], frag_ticks: tuple[int, ...], round_start_tick: int, round_end_tick: int)`; `clipper.scoring.score_round(facts) -> Highlight | None`, `score_match(facts: Iterable[RoundFacts]) -> list[Highlight]` (round order), `select(highlights, n: int) -> list[Highlight]`; `tests.fixtures.SUBJECT`, `MATCH_CHECKSUM`, `DEMO_NAME`, `MATCH_FACTS: tuple[RoundFacts, ...]`.

- [ ] **Step 1: Write the fixture of the analyzed match**

`tests/fixtures.py` — these are the exact values in CS:DM's database (Task 4 checks the live database still says this):

```python
"""The analyzed FACEIT match (aea4e59ccfc6c962, de_inferno, 13-5) as the round facts CS:DM's
database holds for the subject. tests/test_csdm_db.py checks the live database still says exactly this."""

from clipper.model import RoundFacts

SUBJECT = "76561198192858303"
MATCH_CHECKSUM = "aea4e59ccfc6c962"
DEMO_NAME = "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1"

MATCH_FACTS: tuple[RoundFacts, ...] = (
    RoundFacts(round=1, frag_ticks=(10363, 10520), headshots=2, round_won=True,
               round_start_tick=7538, round_end_tick=11223),
    RoundFacts(round=2, frag_ticks=(14512,), round_won=True,
               round_start_tick=11671, round_end_tick=14811),
    RoundFacts(round=3, frag_ticks=(19960, 20957, 21195), headshots=1, round_won=True,
               round_start_tick=15259, round_end_tick=21195),
    RoundFacts(round=4, frag_ticks=(25053, 26147, 28841), headshots=1, bot_frags=3, round_won=True,
               round_start_tick=21643, round_end_tick=29836),
    RoundFacts(round=5, frag_ticks=(33049, 33192), headshots=1, round_won=False,
               round_start_tick=30284, round_end_tick=37288),
    RoundFacts(round=6, frag_ticks=(39688, 41950), headshots=1, bot_frags=1, round_won=True,
               round_start_tick=37736, round_end_tick=42028),
    RoundFacts(round=7, frag_ticks=(45542,), smoke_frags=1, bot_frags=1, round_won=False,
               round_start_tick=42476, round_end_tick=47662),
    RoundFacts(round=8, frag_ticks=(52636, 52951, 53020), headshots=2, round_won=True,
               round_start_tick=48110, round_end_tick=53020),
    RoundFacts(round=9, frag_ticks=(56581,), headshots=1, round_won=True,
               round_start_tick=53468, round_end_tick=58107),
    RoundFacts(round=10, frag_ticks=(61489, 64547), bot_frags=1, round_won=True,
               round_start_tick=58555, round_end_tick=67211),
    RoundFacts(round=11, frag_ticks=(69972,), round_won=True,
               round_start_tick=67659, round_end_tick=71583),
    RoundFacts(round=12, frag_ticks=(74135, 76199, 76431, 78361), blind_frags=1, round_won=False,
               round_start_tick=72031, round_end_tick=81477),
    RoundFacts(round=14, frag_ticks=(92231, 92308, 93894), headshots=1, round_won=True,
               round_start_tick=90164, round_end_tick=93894),
    RoundFacts(round=15, frag_ticks=(97314, 97633, 100465), headshots=1, bot_frags=1, round_won=True,
               round_start_tick=94342, round_end_tick=100465),
    RoundFacts(round=18, frag_ticks=(118270, 120879, 123968), headshots=1, round_won=True,
               round_start_tick=116005, round_end_tick=123968),
)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_scoring.py`:

```python
from clipper.model import RoundFacts
from clipper.scoring import score_match, score_round, select
from tests.fixtures import MATCH_FACTS


def by_round(highlights):
    return {h.round: h for h in highlights}


def test_the_analyzed_match_scores_as_the_old_query_did():
    scored = by_round(score_match(MATCH_FACTS))
    assert len(scored) == 15
    assert sum(len(h.frag_ticks) for h in scored.values()) == 34
    assert (scored[12].type, scored[12].score, scored[12].reasons) == ("4K", 80, ("4k", "blind kill"))
    assert (scored[1].type, scored[1].score, scored[1].reasons) == ("2K", 25, ("2k", "all headshots"))
    assert (scored[3].type, scored[3].score) == ("3K", 40)
    assert (scored[2].type, scored[2].score, scored[2].reasons) == ("FRAG", 5, ("frag",))


def test_bonuses_other_than_the_knife_need_two_frags():
    scored = by_round(score_match(MATCH_FACTS))
    assert (scored[7].score, scored[7].reasons) == (5, ("frag",))   # one Frag, through smoke
    assert scored[9].score == 5                                      # one Frag, a headshot


def test_a_knife_frag_earns_thirty():   # issue 09
    highlight = score_round(RoundFacts(round=7, frag_ticks=(100,), knife_frags=1))
    assert highlight.score == 35
    assert highlight.reasons == ("frag", "knife kill")


def test_an_ace_is_the_top_base_rule():
    highlight = score_round(RoundFacts(round=1, frag_ticks=(1, 2, 3, 4, 5), headshots=5))
    assert (highlight.type, highlight.score) == ("ACE", 110)


def test_a_round_without_frags_is_not_a_highlight():
    assert score_round(RoundFacts(round=1, frag_ticks=())) is None


def test_the_top_five_break_ties_by_the_earlier_round():
    assert [h.round for h in select(score_match(MATCH_FACTS), 5)] == [12, 3, 4, 8, 14]


def test_fewer_highlights_than_n_returns_them_all():
    assert len(select(score_match(MATCH_FACTS[:2]), 5)) == 2
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_scoring.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'clipper.model'`.

- [ ] **Step 4: Write `clipper/model.py`**

```python
"""Plain data passed between modules: no behaviour beyond simple derived values, and no I/O."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundFacts:
    """What the subject did in one round, straight from CS:DM's tables."""

    round: int
    frag_ticks: tuple[int, ...]
    headshots: int = 0
    blind_frags: int = 0
    knife_frags: int = 0
    noscope_frags: int = 0
    smoke_frags: int = 0
    bot_frags: int = 0
    round_won: bool = False
    round_start_tick: int = 0
    round_end_tick: int = 0

    @property
    def frags(self) -> int:
        return len(self.frag_ticks)


@dataclass(frozen=True)
class Highlight:
    """A scored round: it becomes one Reel per Perspective if selected."""

    round: int
    type: str
    score: int
    reasons: tuple[str, ...]
    frag_ticks: tuple[int, ...]
    round_start_tick: int
    round_end_tick: int
```

- [ ] **Step 5: Write `clipper/scoring.py`**

```python
"""Scoring: a round's facts become a Highlight with a Score and reasons. Pure — no I/O.

Base rules name the Highlight Type; bonus rules add to the Score and appear only as reasons.
"""

from __future__ import annotations

from collections.abc import Iterable

from clipper.model import Highlight, RoundFacts

# Frags in the round -> (Highlight Type, base Score).
KILL_RULES: dict[int, tuple[str, int]] = {
    1: ("FRAG", 5),
    2: ("2K", 15),
    3: ("3K", 40),
    4: ("4K", 70),
    5: ("ACE", 100),
}


def _base_rules(facts: RoundFacts) -> list[tuple[str, int]]:
    """Every base rule that matches; the highest-scoring one names the Type. Clutch rules join
    here once issue 07 validates CS:DM's clutches table."""
    return [KILL_RULES[min(facts.frags, 5)]]


def _bonus_rules(facts: RoundFacts) -> list[tuple[str, int]]:
    multi = facts.frags >= 2
    candidates = [
        ("all headshots", 10, multi and facts.headshots == facts.frags),
        ("knife kill", 30, facts.knife_frags > 0),
        ("no-scope", 10, multi and facts.noscope_frags > 0),
        ("through smoke", 5, multi and facts.smoke_frags > 0),
        ("blind kill", 10, multi and facts.blind_frags > 0),
    ]
    return [(reason, points) for reason, points, matched in candidates if matched]


def score_round(facts: RoundFacts) -> Highlight | None:
    """The round as a Highlight, or None when the subject has no Frag in it."""
    if facts.frags == 0:
        return None
    bases = sorted(_base_rules(facts), key=lambda rule: rule[1], reverse=True)
    highlight_type, base_score = bases[0]
    bonuses = _bonus_rules(facts)
    return Highlight(
        round=facts.round,
        type=highlight_type,
        score=base_score + sum(points for _, points in bonuses),
        reasons=tuple(name.lower() for name, _ in bases) + tuple(reason for reason, _ in bonuses),
        frag_ticks=facts.frag_ticks,
        round_start_tick=facts.round_start_tick,
        round_end_tick=facts.round_end_tick,
    )


def score_match(facts: Iterable[RoundFacts]) -> list[Highlight]:
    """Every round with a Frag, as Highlights, in round order."""
    return [h for f in sorted(facts, key=lambda f: f.round) if (h := score_round(f)) is not None]


def select(highlights: Iterable[Highlight], n: int) -> list[Highlight]:
    """The n best Highlights by Score; a tie goes to the earlier round."""
    return sorted(highlights, key=lambda h: (-h.score, h.round))[:n]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_scoring.py -q`
Expected: `7 passed`.

- [ ] **Step 7: Commit**

```bash
git add clipper/model.py clipper/scoring.py tests/fixtures.py tests/test_scoring.py
git commit -m "feat: scoring rules as plain Python (fixes the knife bonus, issue 09)"
```

---

### Task 4: Read CS:DM's database (fixes issues 08 and 09)

**Files:**
- Modify: `clipper/model.py` (add `MatchInfo`)
- Create: `clipper/csdm_db.py`, `tests/test_csdm_db.py`
- Delete: `db/highlights.sql`
- Modify: `.scratch/v1-pipeline/issues/08-highlight-query-mixes-matches.md`, `.scratch/v1-pipeline/issues/09-knife-bonus-never-fires.md`

**Interfaces:**
- Consumes: `RoundFacts` (Task 3), `tests.fixtures` (Task 3), `Config.database_conninfo()` (Task 2).
- Produces: `clipper.model.MatchInfo(checksum: str, map_name: str, played_at: datetime, team_score: int, opponent_score: int)` with property `result -> "win" | "loss" | "tie"`; `clipper.csdm_db.connect(conninfo) -> psycopg.Connection`; `find_checksum(conn, demo_name: str) -> str | None`; `match_info(conn, checksum: str, steamid: str) -> MatchInfo | None`; `round_facts(conn, checksum: str, steamid: str) -> list[RoundFacts]`; `CsdmFacts(conninfo)` with methods `find_checksum(demo_name)`, `match_info(checksum, steamid)`, `round_facts(checksum, steamid)` (each on its own short connection).

These tests read the real database. Postgres must be running (`python scripts/setup_local_postgres.py`). In this task they must actually run, not skip.

- [ ] **Step 1: Add `MatchInfo` to `clipper/model.py`**

Replace the whole file with:

```python
"""Plain data passed between modules: no behaviour beyond simple derived values, and no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RoundFacts:
    """What the subject did in one round, straight from CS:DM's tables."""

    round: int
    frag_ticks: tuple[int, ...]
    headshots: int = 0
    blind_frags: int = 0
    knife_frags: int = 0
    noscope_frags: int = 0
    smoke_frags: int = 0
    bot_frags: int = 0
    round_won: bool = False
    round_start_tick: int = 0
    round_end_tick: int = 0

    @property
    def frags(self) -> int:
        return len(self.frag_ticks)


@dataclass(frozen=True)
class Highlight:
    """A scored round: it becomes one Reel per Perspective if selected."""

    round: int
    type: str
    score: int
    reasons: tuple[str, ...]
    frag_ticks: tuple[int, ...]
    round_start_tick: int
    round_end_tick: int


@dataclass(frozen=True)
class MatchInfo:
    """A match as seen from the subject's team."""

    checksum: str
    map_name: str
    played_at: datetime
    team_score: int
    opponent_score: int

    @property
    def result(self) -> str:
        if self.team_score > self.opponent_score:
            return "win"
        if self.team_score < self.opponent_score:
            return "loss"
        return "tie"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_csdm_db.py`:

```python
from datetime import datetime, timezone

import psycopg
import pytest

from clipper import csdm_db
from clipper.config import load_config
from tests.fixtures import DEMO_NAME, MATCH_CHECKSUM, MATCH_FACTS, SUBJECT

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    try:
        connection = psycopg.connect(**load_config().database_conninfo(), connect_timeout=5, autocommit=True)
    except (OSError, KeyError, psycopg.OperationalError) as exc:
        pytest.skip(f"CS:DM's Postgres is not reachable: {exc}")
    with connection:
        yield connection


def test_the_checksum_is_found_by_demo_name(conn):
    assert csdm_db.find_checksum(conn, DEMO_NAME) == MATCH_CHECKSUM
    assert csdm_db.find_checksum(conn, "1-00000000-0000-0000-0000-000000000000-1-1") is None


def test_match_info_is_from_the_subjects_side(conn):
    info = csdm_db.match_info(conn, MATCH_CHECKSUM, SUBJECT)
    assert info.map_name == "de_inferno"
    assert (info.team_score, info.opponent_score, info.result) == (13, 5, "win")
    assert info.played_at == datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc)


def test_match_info_is_none_when_the_subject_did_not_play(conn):
    assert csdm_db.match_info(conn, MATCH_CHECKSUM, "76561190000000000") is None


def test_round_facts_match_the_fixture(conn):
    assert csdm_db.round_facts(conn, MATCH_CHECKSUM, SUBJECT) == list(MATCH_FACTS)


def test_round_facts_stay_within_one_match(conn):   # issue 08
    """A second match with the same round numbers must not leak into the first match's facts.
    Temp tables shadow the real ones for this transaction only; the rollback removes them."""
    with conn.transaction(force_rollback=True):
        conn.execute("SET LOCAL search_path = pg_temp, public")
        for table in ("kills", "rounds", "players"):
            conn.execute(f"CREATE TEMP TABLE {table} ON COMMIT DROP AS SELECT * FROM public.{table}")
            conn.execute(f"UPDATE {table} SET match_checksum = 'second-match'")
            conn.execute(f"INSERT INTO {table} SELECT * FROM public.{table}")
        assert csdm_db.round_facts(conn, MATCH_CHECKSUM, SUBJECT) == list(MATCH_FACTS)
        assert csdm_db.round_facts(conn, "second-match", SUBJECT) == list(MATCH_FACTS)
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_csdm_db.py -q`
Expected: collection error — `ImportError: cannot import name 'csdm_db' from 'clipper'`. (If you instead see skips, Postgres is down: start it and re-run.)

- [ ] **Step 4: Write `clipper/csdm_db.py`**

```python
"""The only code that reads CS:DM's database (ADR 0003). If CS:DM renames a table or a column,
this file is what changes. It only reads: nothing here writes to CS:DM's tables."""

from __future__ import annotations

from collections.abc import Mapping

import psycopg
from psycopg.rows import dict_row

from clipper.model import MatchInfo, RoundFacts

_FIND_CHECKSUM = """
SELECT checksum FROM demos WHERE name = %(name)s ORDER BY date DESC LIMIT 1
"""

_MATCH_INFO = """
SELECT d.checksum, d.map_name, d.date, us.score AS team_score, them.score AS opponent_score
FROM demos d
JOIN players p  ON p.match_checksum = d.checksum AND p.steam_id = %(steamid)s
JOIN teams us   ON us.match_checksum = d.checksum AND us.name = p.team_name
JOIN teams them ON them.match_checksum = d.checksum AND them.name <> p.team_name
WHERE d.checksum = %(checksum)s
"""

# Every Kill credited to the subject, per round, without team kills and suicides (same side).
# rounds and players are joined on match_checksum as well as on number / steam_id (issue 08).
_ROUND_FACTS = """
SELECT k.round_number                                      AS round,
       array_agg(k.tick ORDER BY k.tick)                   AS frag_ticks,
       count(*) FILTER (WHERE k.is_headshot)               AS headshots,
       count(*) FILTER (WHERE k.is_killer_blinded)         AS blind_frags,
       count(*) FILTER (WHERE k.weapon_type = 'melee')     AS knife_frags,
       count(*) FILTER (WHERE k.is_no_scope)               AS noscope_frags,
       count(*) FILTER (WHERE k.is_through_smoke)          AS smoke_frags,
       count(*) FILTER (WHERE k.is_killer_controlling_bot) AS bot_frags,
       bool_or(r.winner_name = p.team_name)                AS round_won,
       min(r.start_tick)                                   AS round_start_tick,
       min(r.end_tick)                                     AS round_end_tick
FROM kills k
JOIN rounds r  ON r.match_checksum = k.match_checksum AND r.number = k.round_number
JOIN players p ON p.match_checksum = k.match_checksum AND p.steam_id = k.killer_steam_id
WHERE k.match_checksum = %(checksum)s
  AND k.killer_steam_id = %(steamid)s
  AND k.killer_side <> k.victim_side
GROUP BY k.round_number
ORDER BY k.round_number
"""


def connect(conninfo: Mapping[str, object]) -> psycopg.Connection:
    return psycopg.connect(**conninfo, connect_timeout=10)


def find_checksum(conn: psycopg.Connection, demo_name: str) -> str | None:
    """The match checksum CS:DM gave the Demo named `demo_name` (its file name without `.dem`).
    Looked up by name, not path: `csdm analyze` skips a Demo it already knows, so the stored path
    can point at an older copy of the same file."""
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(_FIND_CHECKSUM, {"name": demo_name}).fetchone()
    return row["checksum"] if row else None


def match_info(conn: psycopg.Connection, checksum: str, steamid: str) -> MatchInfo | None:
    """Map, date and final score from the subject's side, or None if the subject is not in the match."""
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(_MATCH_INFO, {"checksum": checksum, "steamid": steamid}).fetchone()
    if row is None:
        return None
    return MatchInfo(
        checksum=row["checksum"],
        map_name=row["map_name"],
        played_at=row["date"],
        team_score=row["team_score"],
        opponent_score=row["opponent_score"],
    )


def round_facts(conn: psycopg.Connection, checksum: str, steamid: str) -> list[RoundFacts]:
    """What the subject did in each round where they got at least one Frag."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(_ROUND_FACTS, {"checksum": checksum, "steamid": steamid}).fetchall()
    return [
        RoundFacts(
            round=row["round"],
            frag_ticks=tuple(row["frag_ticks"]),
            headshots=row["headshots"],
            blind_frags=row["blind_frags"],
            knife_frags=row["knife_frags"],
            noscope_frags=row["noscope_frags"],
            smoke_frags=row["smoke_frags"],
            bot_frags=row["bot_frags"],
            round_won=bool(row["round_won"]),
            round_start_tick=row["round_start_tick"],
            round_end_tick=row["round_end_tick"],
        )
        for row in rows
    ]


class CsdmFacts:
    """The three lookups the worker needs, each on its own short-lived connection."""

    def __init__(self, conninfo: Mapping[str, object]):
        self._conninfo = dict(conninfo)

    def find_checksum(self, demo_name: str) -> str | None:
        with connect(self._conninfo) as conn:
            return find_checksum(conn, demo_name)

    def match_info(self, checksum: str, steamid: str) -> MatchInfo | None:
        with connect(self._conninfo) as conn:
            return match_info(conn, checksum, steamid)

    def round_facts(self, checksum: str, steamid: str) -> list[RoundFacts]:
        with connect(self._conninfo) as conn:
            return round_facts(conn, checksum, steamid)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_csdm_db.py -q -rs`
Expected: `5 passed` and no `SKIPPED` lines.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (18 tests).

- [ ] **Step 7: Retire the old query and close the tickets**

```bash
git rm db/highlights.sql
```

In `.scratch/v1-pipeline/issues/08-highlight-query-mixes-matches.md`, change `Status: ready-for-agent` to `Status: resolved`, and append at the end of the file:

```markdown

## Answer

Fixed by `clipper/csdm_db.py`, which replaced `db/highlights.sql`: the per-round facts group and
join on `match_checksum` as well as on the round number and SteamID.
`tests/test_csdm_db.py::test_round_facts_stay_within_one_match` clones the match under a second
checksum and checks both keep exactly their own facts.
```

In `.scratch/v1-pipeline/issues/09-knife-bonus-never-fires.md`, change `Status: ready-for-agent` to `Status: resolved`, and append:

```markdown

## Answer

Fixed: `clipper/csdm_db.py` counts knife Frags with `weapon_type = 'melee'`, and
`clipper/scoring.py` gives them the +30 bonus
(`tests/test_scoring.py::test_a_knife_frag_earns_thirty`).
```

- [ ] **Step 8: Commit**

```bash
git add clipper/model.py clipper/csdm_db.py tests/test_csdm_db.py .scratch/v1-pipeline/issues/08-highlight-query-mixes-matches.md .scratch/v1-pipeline/issues/09-knife-bonus-never-fires.md
git commit -m "feat: read CS:DM's database from one module (fixes issues 08 and 09)"
```

---

### Task 5: The index

**Files:**
- Modify: `clipper/model.py` (add `ClipFile`)
- Create: `clipper/index.py`, `tests/test_index.py`

**Interfaces:**
- Consumes: `Highlight`, `MatchInfo` (Tasks 3–4).
- Produces: `clipper.model.ClipFile(sequence: int, start_tick: int, end_tick: int, path: Path, duration_s: float)`; `clipper.index.Index(path: Path)` with methods — Demos: `add_demo(file_name, sha256, archive_path) -> int`, `has_demo(file_name, sha256) -> bool`, `demo(demo_id) -> Row | None`, `find_demo(key: str) -> Row | None` (id or file name), `demos_in(states) -> list[Row]`, `all_demos() -> list[Row]`, `advance(demo_id, state, **fields)` (fields: `dem_path`, `match_checksum`, `last_error`; resets attempts), `record_failure(demo_id, error) -> int`, `fail(demo_id, error)`, `retry(demo_id) -> str`; Matches and Highlights: `save_match(info)`, `match(checksum) -> Row | None`, `save_highlights(checksum, highlights, selected_rounds: set[int])`, `selected_highlights(checksum) -> list[Row]` (round order); Render Jobs: `queue_render(demo_id, perspective, attempt) -> int`, `latest_render(demo_id, perspective) -> Row | None`, `start_render(job_id, output_dir, log_path)`, `finish_render(job_id, state, failure=None)`; Clips and Reels: `add_clip(job_id, highlight_id, clip)`, `clips_for(highlight_id, perspective) -> list[Row]` (done jobs only, Tick order), `save_reel(highlight_id, perspective, path, duration_s)`, `reel_count(checksum) -> int`; app state: `get_flag(key, default=None) -> str | None`, `set_flag(key, value)`; `close()`. Rows are `sqlite3.Row` (index by column name).

- [ ] **Step 1: Add `ClipFile` to `clipper/model.py`**

Add `from pathlib import Path` to the imports (below `from datetime import datetime`), and append:

```python


@dataclass(frozen=True)
class ClipFile:
    """One rendered Sequence. CS:DM names it sequence-<n>-tick-<start>-to-<end>.mp4."""

    sequence: int
    start_tick: int
    end_tick: int
    path: Path
    duration_s: float
```

- [ ] **Step 2: Write the failing tests**

`tests/test_index.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper.index import Index
from clipper.model import ClipFile, Highlight, MatchInfo

MATCH = MatchInfo(checksum="aea4e59ccfc6c962", map_name="de_inferno",
                  played_at=datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc),
                  team_score=13, opponent_score=5)


def highlight(round_, score, start):
    return Highlight(round=round_, type="3K", score=score, reasons=("3k",), frag_ticks=(start + 100,),
                     round_start_tick=start, round_end_tick=start + 5000)


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    yield idx
    idx.close()


def add(index, name="1-a.dem.zst", sha="a" * 64):
    return index.add_demo(name, sha, Path("E:/cs2clips/demos") / name)


def test_a_new_demo_is_spotted_and_found_by_id_or_name(index):
    demo_id = add(index)
    assert index.demo(demo_id)["state"] == "spotted"
    assert index.find_demo(str(demo_id))["id"] == demo_id
    assert index.find_demo("1-a.dem.zst")["id"] == demo_id
    assert index.find_demo("nope") is None


def test_has_demo_matches_the_name_or_the_hash(index):
    add(index)
    assert index.has_demo("1-a.dem.zst", "b" * 64)
    assert index.has_demo("other.dem.zst", "a" * 64)
    assert not index.has_demo("other.dem.zst", "b" * 64)


def test_advance_resets_attempts_and_sets_fields(index):
    demo_id = add(index)
    assert index.record_failure(demo_id, "boom") == 1
    index.advance(demo_id, "unpacked", dem_path=Path("E:/cs2clips/demos/1-a.dem"))
    demo = index.demo(demo_id)
    assert (demo["state"], demo["attempts"]) == ("unpacked", 0)
    assert demo["dem_path"] == str(Path("E:/cs2clips/demos/1-a.dem"))
    with pytest.raises(ValueError):
        index.advance(demo_id, "analyzed", colour="red")


def test_fail_remembers_the_step_and_retry_returns_to_it(index):
    demo_id = add(index)
    index.advance(demo_id, "rendering")
    job = index.queue_render(demo_id, "player", attempt=3)
    index.finish_render(job, "failed", "csdm reported: Game error")
    index.fail(demo_id, "player render failed 3 times")
    demo = index.demo(demo_id)
    assert (demo["state"], demo["resume_state"]) == ("failed", "rendering")
    assert index.retry(demo_id) == "rendering"
    assert index.demo(demo_id)["last_error"] is None
    latest = index.latest_render(demo_id, "player")
    assert (latest["state"], latest["attempt"]) == ("queued", 1)
    with pytest.raises(ValueError):
        index.retry(demo_id)


def test_saving_highlights_again_updates_them_in_place(index):
    both = [highlight(3, 40, 15259), highlight(12, 80, 72031)]
    index.save_highlights(MATCH.checksum, both, {3, 12})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    index.save_highlights(MATCH.checksum, both, {12})
    rows = index.selected_highlights(MATCH.checksum)
    assert [(row["round"], row["id"]) for row in rows] == [(12, ids[12])]
    assert json.loads(rows[0]["reasons"]) == ["3k"]


def test_render_jobs_track_the_latest_attempt(index):
    demo_id = add(index)
    first = index.queue_render(demo_id, "player", attempt=1)
    index.start_render(first, Path("E:/out"), Path("E:/log.txt"))
    assert index.latest_render(demo_id, "player")["state"] == "running"
    index.finish_render(first, "failed", "stalled")
    second = index.queue_render(demo_id, "player", attempt=2)
    latest = index.latest_render(demo_id, "player")
    assert (latest["id"], latest["attempt"], latest["state"]) == (second, 2, "queued")
    assert index.latest_render(demo_id, "enemy") is None


def test_clips_come_from_finished_renders_in_tick_order(index):
    demo_id = add(index)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    failed = index.queue_render(demo_id, "player", attempt=1)
    index.add_clip(failed, highlight_id, ClipFile(1, 74007, 74263, Path("old.mp4"), 4.0))
    index.finish_render(failed, "failed", "stalled")
    done = index.queue_render(demo_id, "player", attempt=2)
    index.add_clip(done, highlight_id, ClipFile(10, 78233, 78489, Path("c.mp4"), 4.0))
    index.add_clip(done, highlight_id, ClipFile(2, 76071, 76559, Path("b.mp4"), 7.6))
    index.finish_render(done, "done")
    assert [row["sequence"] for row in index.clips_for(highlight_id, "player")] == [2, 10]
    assert index.clips_for(highlight_id, "enemy") == []


def test_one_reel_per_highlight_and_perspective(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.7)
    index.save_reel(highlight_id, "enemy", Path("E:/r12-enemy.mp4"), 15.6)
    assert index.reel_count(MATCH.checksum) == 2


def test_matches_and_flags_round_trip(index):
    index.save_match(MATCH)
    row = index.match(MATCH.checksum)
    assert (row["map"], row["team_score"], row["opponent_score"], row["result"]) == ("de_inferno", 13, 5, "win")
    assert index.get_flag("paused", "0") == "0"
    index.set_flag("paused", "1")
    assert index.get_flag("paused") == "1"
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_index.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'clipper.index'`.

- [ ] **Step 4: Write `clipper/index.py`**

```python
"""Our SQLite index — the only code that writes it (spec: Index). The Clip library will read what
this stores; CS:DM's own database is never written."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from clipper.model import ClipFile, Highlight, MatchInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS demos (
    id             INTEGER PRIMARY KEY,
    file_name      TEXT NOT NULL UNIQUE,
    sha256         TEXT NOT NULL UNIQUE,
    archive_path   TEXT NOT NULL,
    dem_path       TEXT,
    match_checksum TEXT,
    state          TEXT NOT NULL,
    resume_state   TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    checksum       TEXT PRIMARY KEY,
    map            TEXT NOT NULL,
    played_at      TEXT NOT NULL,
    team_score     INTEGER NOT NULL,
    opponent_score INTEGER NOT NULL,
    result         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS highlights (
    id               INTEGER PRIMARY KEY,
    match_checksum   TEXT NOT NULL,
    round            INTEGER NOT NULL,
    type             TEXT NOT NULL,
    score            INTEGER NOT NULL,
    reasons          TEXT NOT NULL,
    frag_ticks       TEXT NOT NULL,
    round_start_tick INTEGER NOT NULL,
    round_end_tick   INTEGER NOT NULL,
    selected         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (match_checksum, round)
);
CREATE TABLE IF NOT EXISTS render_jobs (
    id          INTEGER PRIMARY KEY,
    demo_id     INTEGER NOT NULL REFERENCES demos (id),
    perspective TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    state       TEXT NOT NULL,
    output_dir  TEXT,
    log_path    TEXT,
    started_at  TEXT,
    finished_at TEXT,
    failure     TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY,
    render_job_id INTEGER NOT NULL REFERENCES render_jobs (id),
    highlight_id  INTEGER NOT NULL REFERENCES highlights (id),
    sequence      INTEGER NOT NULL,
    start_tick    INTEGER NOT NULL,
    end_tick      INTEGER NOT NULL,
    path          TEXT NOT NULL,
    duration_s    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reels (
    id           INTEGER PRIMARY KEY,
    highlight_id INTEGER NOT NULL REFERENCES highlights (id),
    perspective  TEXT NOT NULL,
    path         TEXT NOT NULL,
    duration_s   REAL NOT NULL,
    UNIQUE (highlight_id, perspective)
);
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DEMO_FIELDS = frozenset({"dem_path", "match_checksum", "last_error"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Index:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)   # autocommit: one statement, one transaction
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._db.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._db.execute(sql, params).fetchall()

    # --- Demos ---------------------------------------------------------------------------------

    def add_demo(self, file_name: str, sha256: str, archive_path: Path) -> int:
        now = _now()
        cursor = self._db.execute(
            "INSERT INTO demos (file_name, sha256, archive_path, state, created_at, updated_at)"
            " VALUES (?, ?, ?, 'spotted', ?, ?)",
            (file_name, sha256, str(archive_path), now, now),
        )
        return cursor.lastrowid

    def has_demo(self, file_name: str, sha256: str) -> bool:
        row = self._one("SELECT 1 FROM demos WHERE file_name = ? OR sha256 = ?", (file_name, sha256))
        return row is not None

    def demo(self, demo_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM demos WHERE id = ?", (demo_id,))

    def find_demo(self, key: str) -> sqlite3.Row | None:
        """By index id or by file name."""
        if key.isdigit() and (row := self.demo(int(key))) is not None:
            return row
        return self._one("SELECT * FROM demos WHERE file_name = ?", (key,))

    def demos_in(self, states: Iterable[str]) -> list[sqlite3.Row]:
        states = tuple(states)
        marks = ", ".join("?" * len(states))
        return self._all(f"SELECT * FROM demos WHERE state IN ({marks}) ORDER BY id", states)

    def all_demos(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM demos ORDER BY id")

    def advance(self, demo_id: int, state: str, **fields: object) -> None:
        """Move a Demo to `state`; the new step's attempts start from zero."""
        unknown = set(fields) - _DEMO_FIELDS
        if unknown:
            raise ValueError(f"unknown demo field(s): {', '.join(sorted(unknown))}")
        values = [str(value) if isinstance(value, Path) else value for value in fields.values()]
        extra = "".join(f", {name} = ?" for name in fields)
        self._db.execute(
            f"UPDATE demos SET state = ?, attempts = 0, updated_at = ?{extra} WHERE id = ?",
            (state, _now(), *values, demo_id),
        )

    def record_failure(self, demo_id: int, error: str) -> int:
        """Count a failed attempt at the current step; returns the attempts so far."""
        self._db.execute(
            "UPDATE demos SET attempts = attempts + 1, last_error = ?, updated_at = ? WHERE id = ?",
            (error, _now(), demo_id),
        )
        return self.demo(demo_id)["attempts"]

    def fail(self, demo_id: int, error: str) -> None:
        """Give up on a Demo, remembering the step to resume at."""
        self._db.execute(
            "UPDATE demos SET resume_state = state, state = 'failed', last_error = ?, updated_at = ?"
            " WHERE id = ?",
            (error, _now(), demo_id),
        )

    def retry(self, demo_id: int) -> str:
        """Send a failed Demo back to the step that failed; returns that step's state."""
        demo = self.demo(demo_id)
        if demo is None or demo["state"] != "failed":
            raise ValueError(f"demo {demo_id} has not failed")
        state = demo["resume_state"] or "spotted"
        self.advance(demo_id, state, last_error=None)
        if state == "rendering":
            for perspective in ("player", "enemy"):
                job = self.latest_render(demo_id, perspective)
                if job is not None and job["state"] == "failed":
                    self.queue_render(demo_id, perspective, attempt=1)
        return state

    # --- Matches and Highlights ------------------------------------------------------------------

    def save_match(self, info: MatchInfo) -> None:
        self._db.execute(
            "INSERT INTO matches (checksum, map, played_at, team_score, opponent_score, result)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (checksum) DO UPDATE SET map = excluded.map, played_at = excluded.played_at,"
            " team_score = excluded.team_score, opponent_score = excluded.opponent_score,"
            " result = excluded.result",
            (info.checksum, info.map_name, info.played_at.isoformat(), info.team_score,
             info.opponent_score, info.result),
        )

    def match(self, checksum: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM matches WHERE checksum = ?", (checksum,))

    def save_highlights(self, checksum: str, highlights: Iterable[Highlight], selected_rounds: set[int]) -> None:
        for h in highlights:
            self._db.execute(
                "INSERT INTO highlights (match_checksum, round, type, score, reasons, frag_ticks,"
                " round_start_tick, round_end_tick, selected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (match_checksum, round) DO UPDATE SET type = excluded.type,"
                " score = excluded.score, reasons = excluded.reasons, frag_ticks = excluded.frag_ticks,"
                " round_start_tick = excluded.round_start_tick, round_end_tick = excluded.round_end_tick,"
                " selected = excluded.selected",
                (checksum, h.round, h.type, h.score, json.dumps(list(h.reasons)),
                 json.dumps(list(h.frag_ticks)), h.round_start_tick, h.round_end_tick,
                 int(h.round in selected_rounds)),
            )

    def selected_highlights(self, checksum: str) -> list[sqlite3.Row]:
        return self._all(
            "SELECT * FROM highlights WHERE match_checksum = ? AND selected = 1 ORDER BY round", (checksum,)
        )

    # --- Render Jobs, Clips and Reels --------------------------------------------------------------

    def queue_render(self, demo_id: int, perspective: str, attempt: int) -> int:
        cursor = self._db.execute(
            "INSERT INTO render_jobs (demo_id, perspective, attempt, state) VALUES (?, ?, ?, 'queued')",
            (demo_id, perspective, attempt),
        )
        return cursor.lastrowid

    def latest_render(self, demo_id: int, perspective: str) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM render_jobs WHERE demo_id = ? AND perspective = ? ORDER BY id DESC LIMIT 1",
            (demo_id, perspective),
        )

    def start_render(self, job_id: int, output_dir: Path, log_path: Path) -> None:
        self._db.execute(
            "UPDATE render_jobs SET state = 'running', output_dir = ?, log_path = ?, started_at = ?"
            " WHERE id = ?",
            (str(output_dir), str(log_path), _now(), job_id),
        )

    def finish_render(self, job_id: int, state: str, failure: str | None = None) -> None:
        self._db.execute(
            "UPDATE render_jobs SET state = ?, failure = ?, finished_at = ? WHERE id = ?",
            (state, failure, _now(), job_id),
        )

    def add_clip(self, job_id: int, highlight_id: int, clip: ClipFile) -> None:
        self._db.execute(
            "INSERT INTO clips (render_job_id, highlight_id, sequence, start_tick, end_tick, path, duration_s)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, highlight_id, clip.sequence, clip.start_tick, clip.end_tick, str(clip.path),
             clip.duration_s),
        )

    def clips_for(self, highlight_id: int, perspective: str) -> list[sqlite3.Row]:
        """The Clips of a finished render for one Highlight and Perspective, in Tick order."""
        return self._all(
            "SELECT c.* FROM clips c JOIN render_jobs j ON j.id = c.render_job_id"
            " WHERE c.highlight_id = ? AND j.perspective = ? AND j.state = 'done' ORDER BY c.start_tick",
            (highlight_id, perspective),
        )

    def save_reel(self, highlight_id: int, perspective: str, path: Path, duration_s: float) -> None:
        self._db.execute(
            "INSERT INTO reels (highlight_id, perspective, path, duration_s) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (highlight_id, perspective) DO UPDATE SET path = excluded.path,"
            " duration_s = excluded.duration_s",
            (highlight_id, perspective, str(path), duration_s),
        )

    def reel_count(self, checksum: str) -> int:
        row = self._one(
            "SELECT count(*) FROM reels r JOIN highlights h ON h.id = r.highlight_id"
            " WHERE h.match_checksum = ?",
            (checksum,),
        )
        return row[0]

    # --- App state -----------------------------------------------------------------------------------

    def get_flag(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM app_state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_index.py -q`
Expected: `9 passed`.

- [ ] **Step 6: Commit**

```bash
git add clipper/model.py clipper/index.py tests/test_index.py
git commit -m "feat: SQLite index for Demos, Highlights, Render Jobs, Clips and Reels"
```

---

### Task 6: Intake and unpacking

**Files:**
- Create: `clipper/intake.py`, `clipper/unpack.py`, `tests/test_intake.py`, `tests/test_unpack.py`

**Interfaces:**
- Consumes: `Index` (Task 5).
- Produces: `clipper.intake.Intake(downloads: Path, demos_dir: Path, stable_seconds: float = 10.0, clock: Callable[[], float] = time.monotonic)` with `ready() -> list[Path]` and `take(path: Path, index: Index) -> int | None`; `clipper.intake.FACEIT_DEMO` (regex); `clipper.intake.sha256_of(path) -> str`; `clipper.unpack.unpack(src: Path, out_dir: Path) -> Path`; `clipper.unpack.UnpackError`; `clipper.unpack.DEMO_MAGIC`.

- [ ] **Step 1: Write the failing tests**

`tests/test_intake.py`:

```python
from pathlib import Path

import pytest

from clipper.index import Index
from clipper.intake import Intake

NAME = "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.dem.zst"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path):
    downloads, demos = tmp_path / "Downloads", tmp_path / "demos"
    downloads.mkdir()
    clock = Clock()
    index = Index(tmp_path / "clipper.sqlite")
    yield downloads, demos, clock, Intake(downloads, demos, clock=clock), index
    index.close()


def test_a_demo_is_ready_once_its_size_holds_for_ten_seconds(setup):
    downloads, _, clock, intake, _ = setup
    demo = downloads / NAME
    demo.write_bytes(b"abc")
    assert intake.ready() == []        # first sighting
    clock.now += 5
    assert intake.ready() == []        # not stable for long enough
    demo.write_bytes(b"abcdef")        # still growing
    clock.now += 10
    assert intake.ready() == []        # the size changed, so the wait starts again
    clock.now += 10
    assert intake.ready() == [demo]


def test_other_files_are_ignored(setup):
    downloads, _, clock, intake, _ = setup
    for name in ("notes.txt", "match.dem", "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.zip"):
        (downloads / name).write_bytes(b"x")
    intake.ready()
    clock.now += 60
    assert intake.ready() == []


def test_a_partial_download_holds_the_demo_back(setup):
    downloads, _, clock, intake, _ = setup
    (downloads / NAME).write_bytes(b"abc")
    (downloads / (NAME + ".part")).write_bytes(b"")
    intake.ready()
    clock.now += 60
    assert intake.ready() == []


def test_take_moves_the_demo_and_records_it(setup):
    downloads, demos, _, intake, index = setup
    (downloads / NAME).write_bytes(b"abc")
    demo_id = intake.take(downloads / NAME, index)
    row = index.demo(demo_id)
    assert (row["file_name"], row["state"]) == (NAME, "spotted")
    assert Path(row["archive_path"]) == demos / NAME
    assert (demos / NAME).read_bytes() == b"abc"
    assert not (downloads / NAME).exists()


def test_a_duplicate_is_moved_aside_without_a_new_row(setup):
    downloads, demos, _, intake, index = setup
    (downloads / NAME).write_bytes(b"abc")
    intake.take(downloads / NAME, index)
    (downloads / NAME).write_bytes(b"abc")
    assert intake.take(downloads / NAME, index) is None
    assert (demos / "duplicates" / NAME).exists()
    assert len(index.all_demos()) == 1
```

`tests/test_unpack.py`:

```python
import gzip

import pytest
import zstandard

from clipper.unpack import UnpackError, unpack

DEMO = b"PBDEMS2\x00" + bytes(range(256)) * 64
NAME = "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1"


def test_zst_is_decompressed(tmp_path):
    src = tmp_path / f"{NAME}.dem.zst"
    src.write_bytes(zstandard.ZstdCompressor().compress(DEMO))
    out = unpack(src, tmp_path / "demos")
    assert out.name == f"{NAME}.dem"
    assert out.read_bytes() == DEMO


def test_gz_is_decompressed(tmp_path):
    src = tmp_path / f"{NAME}.dem.gz"
    src.write_bytes(gzip.compress(DEMO))
    assert unpack(src, tmp_path / "demos").read_bytes() == DEMO


def test_a_plain_dem_already_in_place_is_returned_as_is(tmp_path):
    src = tmp_path / f"{NAME}.dem"
    src.write_bytes(DEMO)
    assert unpack(src, tmp_path) == src


def test_a_plain_dem_elsewhere_is_copied(tmp_path):
    src = tmp_path / f"{NAME}.dem"
    src.write_bytes(DEMO)
    out = unpack(src, tmp_path / "demos")
    assert out == tmp_path / "demos" / f"{NAME}.dem"
    assert out.read_bytes() == DEMO
    assert src.exists()


def test_a_file_without_the_demo_header_is_rejected_and_leaves_nothing(tmp_path):
    src = tmp_path / f"{NAME}.dem.zst"
    src.write_bytes(zstandard.ZstdCompressor().compress(b"not a demo"))
    with pytest.raises(UnpackError, match="PBDEMS2"):
        unpack(src, tmp_path / "demos")
    assert list((tmp_path / "demos").iterdir()) == []


def test_a_corrupt_zst_is_rejected(tmp_path):
    src = tmp_path / f"{NAME}.dem.zst"
    src.write_bytes(b"definitely not zstd")
    with pytest.raises(UnpackError):
        unpack(src, tmp_path / "demos")


def test_other_extensions_are_rejected(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_bytes(b"x")
    with pytest.raises(UnpackError, match="not a Demo"):
        unpack(src, tmp_path)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_intake.py tests/test_unpack.py -q`
Expected: collection errors — `No module named 'clipper.intake'` and `'clipper.unpack'`.

- [ ] **Step 3: Write `clipper/intake.py`**

```python
"""Intake: finished FACEIT Demos in Downloads move into the pipeline's own folder (spec: Flow, step 1)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipper.index import Index

log = logging.getLogger(__name__)

FACEIT_DEMO = re.compile(
    r"^1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-\d+-\d+\.dem(\.zst|\.gz)?$",
    re.IGNORECASE,
)
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")


@dataclass
class _Sighting:
    size: int
    since: float


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _free_name(path: Path) -> Path:
    """`path`, or the same name with ' (2)', ' (3)', … before its extensions if it is taken."""
    if not path.exists():
        return path
    base, extensions = path.name.split(".", 1)
    n = 2
    while (candidate := path.with_name(f"{base} ({n}).{extensions}")).exists():
        n += 1
    return candidate


def _copy_then_rename(src: Path, dest: Path) -> None:
    """Copy across drives without ever leaving a half-written file under the final name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(src, partial)
    os.replace(partial, dest)


class Intake:
    def __init__(self, downloads: Path, demos_dir: Path, stable_seconds: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self._downloads = downloads
        self._demos_dir = demos_dir
        self._stable_seconds = stable_seconds
        self._clock = clock
        self._sightings: dict[str, _Sighting] = {}

    def ready(self) -> list[Path]:
        """FACEIT Demos whose size has held for stable_seconds, with no partial-download sibling."""
        if not self._downloads.is_dir():
            return []
        now = self._clock()
        entries = {entry.name.lower(): entry for entry in self._downloads.iterdir()}
        ready = []
        for name, path in entries.items():
            if not FACEIT_DEMO.match(path.name) or not path.is_file():
                continue
            if any(name + suffix in entries for suffix in PARTIAL_SUFFIXES):
                continue
            size = path.stat().st_size
            seen = self._sightings.get(name)
            if seen is None or seen.size != size:
                self._sightings[name] = _Sighting(size, now)
            elif now - seen.since >= self._stable_seconds:
                ready.append(path)
        return sorted(ready)

    def take(self, path: Path, index: Index) -> int | None:
        """Move a ready Demo into demos_dir and record it. A Demo already in the index (same file
        name or SHA-256) goes to demos_dir/duplicates instead. Returns the new Demo's id, or None."""
        self._sightings.pop(path.name.lower(), None)
        digest = sha256_of(path)
        if index.has_demo(path.name, digest):
            dest = _free_name(self._demos_dir / "duplicates" / path.name)
            _copy_then_rename(path, dest)
            path.unlink()
            log.info("%s is already in the index; moved it to %s", path.name, dest)
            return None
        dest = self._demos_dir / path.name
        _copy_then_rename(path, dest)
        demo_id = index.add_demo(path.name, digest, dest)
        path.unlink()
        log.info("took %s as demo #%s", path.name, demo_id)
        return demo_id
```

- [ ] **Step 4: Write `clipper/unpack.py`**

```python
"""Unpacking: a downloaded Demo (.dem.zst, .dem.gz or .dem) becomes a .dem (spec: Flow, step 2)."""

from __future__ import annotations

import gzip
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import zstandard

DEMO_MAGIC = b"PBDEMS2\x00"


class UnpackError(Exception):
    """The file is not a Demo, or it did not decompress into one."""


@contextmanager
def _zstd_reader(path: Path) -> Iterator[BinaryIO]:
    with open(path, "rb") as raw, zstandard.ZstdDecompressor().stream_reader(raw) as reader:
        yield reader


@contextmanager
def _plain_reader(path: Path) -> Iterator[BinaryIO]:
    with open(path, "rb") as raw:
        yield raw


def _has_demo_header(path: Path) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(DEMO_MAGIC)) == DEMO_MAGIC


def unpack(src: Path, out_dir: Path) -> Path:
    """Write the .dem for `src` into out_dir and return its path. A plain .dem that is already in
    out_dir is returned as it is."""
    name = src.name
    lowered = name.lower()
    if lowered.endswith(".dem.zst"):
        dem_name, opener = name[: -len(".zst")], _zstd_reader
    elif lowered.endswith(".dem.gz"):
        dem_name, opener = name[: -len(".gz")], gzip.open
    elif lowered.endswith(".dem"):
        dem_name, opener = name, _plain_reader
    else:
        raise UnpackError(f"not a Demo: {name}")
    out = out_dir / dem_name
    if out.exists() and out.resolve() == src.resolve():
        if not _has_demo_header(out):
            raise UnpackError(f"{name} does not start with the PBDEMS2 header")
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    try:
        with opener(src) as reader, open(partial, "wb") as writer:
            shutil.copyfileobj(reader, writer, 1 << 20)
    except (OSError, EOFError, zstandard.ZstdError) as exc:
        partial.unlink(missing_ok=True)
        raise UnpackError(f"could not unpack {name}: {exc}") from exc
    if not _has_demo_header(partial):
        partial.unlink()
        raise UnpackError(f"{name} did not unpack into a Demo (no PBDEMS2 header)")
    os.replace(partial, out)
    return out
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_intake.py tests/test_unpack.py -q`
Expected: `12 passed`.

- [ ] **Step 6: Commit**

```bash
git add clipper/intake.py clipper/unpack.py tests/test_intake.py tests/test_unpack.py
git commit -m "feat: take finished FACEIT Demos from Downloads and unpack them"
```

---

### Task 7: Process probe and the Gate

**Files:**
- Create: `clipper/procs.py`, `clipper/gate.py`, `tests/test_gate.py`

**Interfaces:**
- Consumes: nothing beyond Task 2.
- Produces: `clipper.procs.ProcessProbe` (Protocol: `names() -> set[str]` lower-case, `running(name) -> bool`, `service_running(name) -> bool`, `hooked_cs2_running() -> bool`, `kill_hooked_cs2() -> None`, `children_named(pid, name) -> set[tuple[int, float]]`, `kill_processes(processes) -> None`); `clipper.procs.SystemProbe` (psutil); `clipper.gate.Gate(probe, data_root: Path, min_free_gb: float, free_bytes=...)` with `check() -> GateStatus` and `faceit_running() -> bool`; `clipper.gate.GateStatus(ok: bool, reasons: tuple[str, ...] = ())`; constants `CS2_REASON`, `FACEIT_REASON`, `GUI_REASON`, `FACEIT_SERVICE`, `GB`.

- [ ] **Step 1: Write the failing tests**

`tests/test_gate.py`:

```python
from pathlib import Path

from clipper.gate import CS2_REASON, FACEIT_REASON, GB, GUI_REASON, Gate, GateStatus
from clipper.procs import SystemProbe


class FakeProbe:
    def __init__(self, names=(), services=()):
        self._names = {name.lower() for name in names}
        self._services = set(services)

    def names(self):
        return set(self._names)

    def running(self, name):
        return name.lower() in self._names

    def service_running(self, name):
        return name in self._services

    def hooked_cs2_running(self):
        return False

    def kill_hooked_cs2(self):
        pass

    def children_named(self, pid, name):
        return set()

    def kill_processes(self, processes):
        pass


def gate(names=(), services=(), free=100 * GB):
    return Gate(FakeProbe(names, services), Path("E:/cs2clips"), 5, free_bytes=lambda _: free)


def test_clear_when_nothing_is_in_the_way():
    assert gate().check() == GateStatus(ok=True)


def test_a_running_cs2_blocks():
    assert gate(["cs2.exe"]).check().reasons == (CS2_REASON,)


def test_the_faceit_service_blocks():
    assert gate(services=["FACEITService"]).check().reasons == (FACEIT_REASON,)


def test_a_faceit_client_process_blocks():
    assert gate(["FACEIT.exe"]).check().reasons == (FACEIT_REASON,)


def test_the_csdm_gui_blocks():
    assert gate(["cs-demo-manager.exe"]).check().reasons == (GUI_REASON,)


def test_low_disk_space_blocks():
    status = gate(free=4 * GB).check()
    assert not status.ok
    assert status.reasons[0].startswith("less than 5 GB free")


def test_a_missing_drive_blocks():
    def gone(_):
        raise FileNotFoundError("E:\\")

    status = Gate(FakeProbe(), Path("E:/cs2clips"), 5, free_bytes=gone).check()
    assert status.reasons == ("E:\\ is not available",)


def test_every_reason_is_reported_in_order():
    status = gate(["cs2.exe", "cs-demo-manager.exe"], ["FACEITService"]).check()
    assert status.reasons == (CS2_REASON, FACEIT_REASON, GUI_REASON)


def test_faceit_running_on_its_own():
    assert gate(services=["FACEITService"]).faceit_running()
    assert not gate().faceit_running()


def test_the_system_probe_sees_this_test_process():
    probe = SystemProbe()
    assert "python.exe" in probe.names()
    assert probe.service_running("NoSuchServiceForClipperTests") is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_gate.py -q`
Expected: collection error — `No module named 'clipper.gate'`.

- [ ] **Step 3: Write `clipper/procs.py`**

```python
"""What is running on this PC, and stopping it. The only code that touches other processes."""

from __future__ import annotations

from typing import Protocol

import psutil

HOOKED_FLAG = "-insecure"   # only renders start CS2 with it (CS:DM passes it through HLAE)


class ProcessProbe(Protocol):
    def names(self) -> set[str]: ...
    def running(self, name: str) -> bool: ...
    def service_running(self, name: str) -> bool: ...
    def hooked_cs2_running(self) -> bool: ...
    def kill_hooked_cs2(self) -> None: ...
    def children_named(self, pid: int, name: str) -> set[tuple[int, float]]: ...
    def kill_processes(self, processes: set[tuple[int, float]]) -> None: ...


class SystemProbe:
    """ProcessProbe backed by psutil. Process names are compared in lower case."""

    def names(self) -> set[str]:
        return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}

    def running(self, name: str) -> bool:
        return name.lower() in self.names()

    def service_running(self, name: str) -> bool:
        """True unless the service is stopped; a service that does not exist is not running."""
        try:
            return psutil.win_service_get(name).status() != "stopped"
        except psutil.NoSuchProcess:
            return False

    def _hooked_cs2(self) -> list[psutil.Process]:
        found = []
        for process in psutil.process_iter(["name"]):
            if (process.info["name"] or "").lower() != "cs2.exe":
                continue
            try:
                if any(arg.lower() == HOOKED_FLAG for arg in process.cmdline()):
                    found.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return found

    def hooked_cs2_running(self) -> bool:
        return bool(self._hooked_cs2())

    def kill_hooked_cs2(self) -> None:
        for process in self._hooked_cs2():
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def children_named(self, pid: int, name: str) -> set[tuple[int, float]]:
        """(pid, create_time) of every descendant of `pid` called `name`."""
        try:
            children = psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return set()
        found = set()
        for child in children:
            try:
                if child.name().lower() == name.lower():
                    found.add((child.pid, child.create_time()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return found

    def kill_processes(self, processes: set[tuple[int, float]]) -> None:
        """Kill each (pid, create_time) that is still the same process — PIDs get reused."""
        for pid, created in processes:
            try:
                process = psutil.Process(pid)
                if process.create_time() == created:
                    process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
```

- [ ] **Step 4: Write `clipper/gate.py`**

```python
"""The Gate: may the pipeline launch CS2 right now? (spec: The Gate)"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from clipper.procs import ProcessProbe

CS2_REASON = "CS2 is running"
FACEIT_REASON = "FACEIT AC is running"
GUI_REASON = "CS Demo Manager is open"
FACEIT_SERVICE = "FACEITService"
GB = 1024**3


@dataclass(frozen=True)
class GateStatus:
    ok: bool
    reasons: tuple[str, ...] = ()


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(path.anchor or str(path)).free


class Gate:
    """Conditions 1–4 of the spec. Condition 5 (no other Render Job running) holds by construction:
    one worker renders at a time, and a lock file keeps a single app instance."""

    def __init__(self, probe: ProcessProbe, data_root: Path, min_free_gb: float,
                 free_bytes: Callable[[Path], int] = _free_bytes):
        self._probe = probe
        self._data_root = data_root
        self._min_free_gb = min_free_gb
        self._free_bytes = free_bytes

    def faceit_running(self) -> bool:
        return self._faceit_running(self._probe.names())

    def _faceit_running(self, names: set[str]) -> bool:
        return self._probe.service_running(FACEIT_SERVICE) or any(name.startswith("faceit") for name in names)

    def check(self) -> GateStatus:
        names = self._probe.names()
        reasons = []
        if "cs2.exe" in names:
            reasons.append(CS2_REASON)
        if self._faceit_running(names):
            reasons.append(FACEIT_REASON)
        if "cs-demo-manager.exe" in names:
            reasons.append(GUI_REASON)
        drive = self._data_root.anchor or str(self._data_root)
        try:
            if self._free_bytes(self._data_root) < self._min_free_gb * GB:
                reasons.append(f"less than {self._min_free_gb:g} GB free on {drive}")
        except OSError:
            reasons.append(f"{drive} is not available")
        return GateStatus(ok=not reasons, reasons=tuple(reasons))
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_gate.py -q`
Expected: `10 passed`.

- [ ] **Step 6: Confirm the FACEIT process names (user, 1 minute)**

Ask the user to open the FACEIT client, then run:

```bash
uv run python -c "from clipper.gate import Gate; from clipper.procs import SystemProbe; from pathlib import Path; print(Gate(SystemProbe(), Path('E:/cs2clips'), 5).check())"
```

Expected: `reasons` includes `'FACEIT AC is running'`. With the client closed again, it no longer does. If it doesn't show up while the client is open, list the processes (`Get-Process | Where-Object Name -like '*face*'`) and record the real names in the spec's Gate section before going further.

- [ ] **Step 7: Commit**

```bash
git add clipper/procs.py clipper/gate.py tests/test_gate.py
git commit -m "feat: the Gate — CS2, FACEIT AC, CS:DM GUI and disk checks"
```

---

### Task 8: Running csdm and watching a render

**Files:**
- Create: `clipper/csdm_cli.py`, `clipper/media.py`, `clipper/render.py`, `tests/fake_csdm.py`, `tests/test_media.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `ClipFile` (Task 5), `ProcessProbe` (Task 7), `keep_awake` (Task 2).
- Produces: `clipper.csdm_cli.CsdmCli(prefix: tuple[str, ...], home: Path, pg_bin: Path)` with `command(*args) -> list[str]`, `env() -> dict[str, str]`, `analyze(dem: Path, log_path: Path) -> str`; `clipper.media.probe_duration(path: Path, ffprobe: str = "ffprobe") -> float` and `MediaError`; `clipper.render.RenderRequest(demo_path, perspective, rounds: tuple[int, ...], output_dir, log_path, steamid, padding_before_s, padding_after_s)`, `RenderResult(ok: bool, aborted: bool = False, failure: str | None = None, clips: tuple[ClipFile, ...] = ())`, `render(req, *, csdm, probe, should_abort, stall_seconds, launch_timeout_seconds, duration_of, poll_seconds=5.0, exit_grace_seconds=120.0, abort_sweep_seconds=30.0) -> RenderResult`, `video_args(req) -> list[str]`, constants `NEVER_LAUNCHED`, `ABORTED`.

- [ ] **Step 1: Write the fake csdm**

`tests/fake_csdm.py`:

```python
"""Stands in for CS:DM's CLI in tests (see tests/test_render.py). FAKE_CSDM_MODE picks what it does:

  ok       writes one Clip per span in FAKE_CSDM_TICKS ("100-356,1000-1256") and reports success
  noclips  reports success but writes nothing
  auth     reports a database authentication failure (and, like csdm, still exits 0)
  hang     'records' until FAKE_CSDM_STOPFILE exists — the game died — then reports 'Game error'
  argv     writes its arguments and chosen environment variables to FAKE_CSDM_ARGV as JSON
"""

import json
import os
import sys
import time
from pathlib import Path


def option(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1]


def main() -> int:
    mode = os.environ.get("FAKE_CSDM_MODE", "ok")
    if mode == "argv":
        seen = {key: os.environ.get(key) for key in ("USERPROFILE", "ELECTRON_RUN_AS_NODE", "PGPASSWORD", "PATH")}
        Path(os.environ["FAKE_CSDM_ARGV"]).write_text(json.dumps({"argv": sys.argv[1:], **seen}), encoding="utf-8")
        return 0
    if mode == "auth":
        print('password authentication failed for user "postgres"', flush=True)
        return 0
    print("Starting Counter-Strike...", flush=True)
    print("Recording in progress...", flush=True)
    if mode == "hang":
        stopfile = Path(os.environ["FAKE_CSDM_STOPFILE"])
        while not stopfile.exists():
            time.sleep(0.05)
        print("Game error", flush=True)
        return 0
    output = Path(option("--output"))
    if mode == "ok":
        spans = [span.split("-") for span in os.environ["FAKE_CSDM_TICKS"].split(",")]
        for number, (start, end) in enumerate(spans, start=1):
            (output / f"sequence-{number}-tick-{start}-to-{end}.mp4").write_bytes(b"fake video")
            print(f"Converting sequence #{number} ({number}/{len(spans)})...", flush=True)
    print(f"Video generated in {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write the failing tests**

`tests/test_media.py`:

```python
import shutil
import subprocess

import pytest

from clipper.media import MediaError, probe_duration


def test_a_file_that_is_not_video_raises(tmp_path):
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH")
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"fake video")
    with pytest.raises(MediaError, match="fake.mp4"):
        probe_duration(fake)


@pytest.mark.ffmpeg
def test_a_real_clip_reports_its_duration(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=60:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    assert probe_duration(clip) == pytest.approx(1.0, abs=0.05)
```

`tests/test_render.py`:

```python
import json
import sys
import threading
from pathlib import Path

import pytest

from clipper.csdm_cli import CsdmCli
from clipper.media import MediaError
from clipper.render import NEVER_LAUNCHED, RenderRequest, render

FAKE_CSDM = Path(__file__).with_name("fake_csdm.py")
SUBJECT = "76561198192858303"


class FakeProbe:
    """A hooked CS2 that 'dies' — touching the stop file the fake csdm waits for — when killed."""

    def __init__(self, stopfile: Path, cs2: bool = False, ffmpeg: bool = False):
        self.stopfile, self.cs2, self.ffmpeg = stopfile, cs2, ffmpeg
        self.kills = 0

    def names(self):
        return set()

    def running(self, name):
        return name == "ffmpeg.exe" and self.ffmpeg

    def service_running(self, name):
        return False

    def hooked_cs2_running(self):
        return self.cs2

    def kill_hooked_cs2(self):
        self.kills += 1
        self.cs2 = False
        self.stopfile.touch()

    def children_named(self, pid, name):
        return set()

    def kill_processes(self, processes):
        pass


@pytest.fixture
def csdm(tmp_path):
    return CsdmCli(prefix=(sys.executable, str(FAKE_CSDM)), home=tmp_path / "home", pg_bin=tmp_path / "pgbin")


@pytest.fixture
def req(tmp_path):
    return RenderRequest(
        demo_path=tmp_path / "match.dem", perspective="enemy", rounds=(3, 12),
        output_dir=tmp_path / "out", log_path=tmp_path / "logs" / "render.log",
        steamid=SUBJECT, padding_before_s=4.0, padding_after_s=2.0,
    )


@pytest.fixture
def stopfile(tmp_path, monkeypatch):
    path = tmp_path / "game-died"
    monkeypatch.setenv("FAKE_CSDM_STOPFILE", str(path))
    return path


def run(req, csdm, probe, *, abort=lambda: False, stall=5.0, launch=5.0, duration_of=lambda path: 4.0):
    return render(
        req, csdm=csdm, probe=probe, should_abort=abort, stall_seconds=stall,
        launch_timeout_seconds=launch, duration_of=duration_of,
        poll_seconds=0.05, exit_grace_seconds=5.0, abort_sweep_seconds=0.0,
    )


def test_success_returns_the_clips_in_tick_order(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "ok")
    monkeypatch.setenv("FAKE_CSDM_TICKS", "76071-76559,19832-20213")
    result = run(req, csdm, FakeProbe(stopfile))
    assert result.ok and result.failure is None
    assert [(c.start_tick, c.end_tick) for c in result.clips] == [(19832, 20213), (76071, 76559)]
    assert [c.sequence for c in result.clips] == [2, 1]
    assert all(c.duration_s == 4.0 for c in result.clips)


def test_the_command_line_and_environment(req, csdm, stopfile, tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_CSDM_MODE", "argv")
    monkeypatch.setenv("FAKE_CSDM_ARGV", str(argv_file))
    monkeypatch.setenv("PGPASSWORD", "must-not-leak")
    run(req, csdm, FakeProbe(stopfile))
    seen = json.loads(argv_file.read_text(encoding="utf-8"))
    assert seen["argv"] == [
        "video", str(req.demo_path), "--mode", "player", "--steamids", SUBJECT, "--event", "kills",
        "--rounds", "3,12", "--perspective", "enemy", "--output", str(req.output_dir),
        "--close-game-after-recording", "--start-seconds-before", "4", "--end-seconds-after", "2",
    ]
    assert seen["USERPROFILE"] == str(tmp_path / "home")
    assert seen["ELECTRON_RUN_AS_NODE"] == "1"
    assert seen["PGPASSWORD"] is None
    assert seen["PATH"].startswith(str(tmp_path / "pgbin"))


def test_a_reported_failure_wins_over_the_exit_code(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "auth")
    result = run(req, csdm, FakeProbe(stopfile))
    assert not result.ok
    assert result.failure == "csdm reported: password authentication failed"


def test_success_without_clips_is_a_failure(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "noclips")
    result = run(req, csdm, FakeProbe(stopfile))
    assert not result.ok and "no sequence" in result.failure


def test_an_unreadable_clip_is_a_failure(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "ok")
    monkeypatch.setenv("FAKE_CSDM_TICKS", "100-356")

    def unreadable(path):
        raise MediaError(f"ffprobe cannot read {path.name}")

    result = run(req, csdm, FakeProbe(stopfile), duration_of=unreadable)
    assert not result.ok and result.failure.startswith("unreadable clip")


def test_a_stall_closes_the_hooked_cs2_and_fails(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    probe = FakeProbe(stopfile, cs2=True, ffmpeg=False)
    result = run(req, csdm, probe, stall=0.3)
    assert not result.ok and result.failure.startswith("stalled")
    assert probe.kills >= 1


def test_ffmpeg_activity_is_not_a_stall(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    probe = FakeProbe(stopfile, cs2=True, ffmpeg=True)
    threading.Timer(1.0, stopfile.touch).start()
    result = run(req, csdm, probe, stall=0.3)
    assert result.failure == "csdm reported: Game error"
    assert probe.kills == 0


def test_csdm_that_never_launches_cs2_is_stopped(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    result = run(req, csdm, FakeProbe(stopfile, cs2=False), launch=0.3)
    assert not result.ok and result.failure == NEVER_LAUNCHED


def test_faceit_starting_aborts_the_render(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    probe = FakeProbe(stopfile, cs2=True)
    result = run(req, csdm, probe, abort=lambda: True)
    assert result.aborted and not result.ok
    assert probe.kills >= 1
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_media.py tests/test_render.py -q`
Expected: collection errors — `No module named 'clipper.media'`, `'clipper.csdm_cli'`.

- [ ] **Step 4: Write `clipper/csdm_cli.py`**

```python
"""How to run CS:DM's command line: the executable, and the environment phase 1 proved it needs."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ANALYZE_TIMEOUT_SECONDS = 1800   # analysis never launches CS2, so stopping it orphans nothing


@dataclass(frozen=True)
class CsdmCli:
    prefix: tuple[str, ...]   # (cs-demo-manager.exe, …/app.asar/cli.js) — or a fake in tests
    home: Path                # becomes USERPROFILE, so CS:DM's app folder is <home>/.csdm
    pg_bin: Path              # CS:DM shells out to psql

    def command(self, *args: str) -> list[str]:
        return [*self.prefix, *args]

    def env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key.upper() != "PGPASSWORD"}
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["USERPROFILE"] = str(self.home)
        env["PATH"] = f"{self.pg_bin}{os.pathsep}{env.get('PATH', '')}"
        return env

    def analyze(self, dem: Path, log_path: Path) -> str:
        """Run `csdm analyze <dem> --source faceit` and keep its output in log_path. The exit code
        is not trusted: the caller checks that the match reached CS:DM's database."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            self.command("analyze", str(dem), "--source", "faceit"),
            env=self.env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=ANALYZE_TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = proc.stdout + proc.stderr
        log_path.write_text(output, encoding="utf-8")
        return output
```

- [ ] **Step 5: Write `clipper/media.py`**

```python
"""Reading video files with ffprobe."""

from __future__ import annotations

import subprocess
from pathlib import Path


class MediaError(Exception):
    """ffprobe could not read a file."""


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    """The file's duration in seconds, or MediaError if ffprobe cannot read it."""
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        raise MediaError(f"ffprobe cannot read {path.name}: {proc.stderr.strip()[:200]}")
    try:
        return float(text)
    except ValueError as exc:
        raise MediaError(f"ffprobe gave no duration for {path.name}: {text[:50]}") from exc
```

- [ ] **Step 6: Write `clipper/render.py`**

```python
"""One `csdm video` call per Render Job, watched until it ends (spec: Rendering)."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipper.csdm_cli import CsdmCli
from clipper.media import MediaError
from clipper.model import ClipFile
from clipper.procs import ProcessProbe
from clipper.windows import keep_awake

CLIP_NAME = re.compile(r"^sequence-(\d+)-tick-(\d+)-to-(\d+)\.mp4$", re.IGNORECASE)
FAILURE_MARKERS = (
    "password authentication failed",
    "ECONNREFUSED",
    "Game error",
    "Invalid argument",
    "does not exist",
)
ABORTED = "aborted: FACEIT AC started during the render"
NEVER_LAUNCHED = "csdm never launched CS2"


@dataclass(frozen=True)
class RenderRequest:
    demo_path: Path
    perspective: str            # "player" or "enemy"
    rounds: tuple[int, ...]
    output_dir: Path
    log_path: Path
    steamid: str
    padding_before_s: float
    padding_after_s: float


@dataclass(frozen=True)
class RenderResult:
    ok: bool
    aborted: bool = False
    failure: str | None = None
    clips: tuple[ClipFile, ...] = ()


def video_args(req: RenderRequest) -> list[str]:
    return [
        "video", str(req.demo_path),
        "--mode", "player",
        "--steamids", req.steamid,
        "--event", "kills",
        "--rounds", ",".join(str(number) for number in req.rounds),
        "--perspective", req.perspective,
        "--output", str(req.output_dir),
        "--close-game-after-recording",
        "--start-seconds-before", f"{req.padding_before_s:g}",
        "--end-seconds-after", f"{req.padding_after_s:g}",
    ]


def find_clips(output_dir: Path, duration_of: Callable[[Path], float]) -> list[ClipFile]:
    """The Clips CS:DM wrote, in Tick order."""
    clips = []
    for path in output_dir.iterdir():
        match = CLIP_NAME.match(path.name)
        if match:
            clips.append(ClipFile(sequence=int(match[1]), start_tick=int(match[2]), end_tick=int(match[3]),
                                  path=path, duration_s=duration_of(path)))
    return sorted(clips, key=lambda clip: clip.start_tick)


def judge(log_text: str, output_dir: Path, duration_of: Callable[[Path], float]) -> RenderResult:
    """Decide from csdm's output and files, never its exit code: csdm exits 0 when it fails."""
    lowered = log_text.lower()
    for marker in FAILURE_MARKERS:
        if marker.lower() in lowered:
            return RenderResult(ok=False, failure=f"csdm reported: {marker}")
    if "video generated" not in lowered:
        return RenderResult(ok=False, failure="csdm did not report 'Video generated'")
    try:
        clips = find_clips(output_dir, duration_of)
    except MediaError as exc:
        return RenderResult(ok=False, failure=f"unreadable clip: {exc}")
    if not clips:
        return RenderResult(ok=False, failure="no sequence-*.mp4 in the output folder")
    return RenderResult(ok=True, clips=tuple(clips))


def render(
    req: RenderRequest,
    *,
    csdm: CsdmCli,
    probe: ProcessProbe,
    should_abort: Callable[[], bool],
    stall_seconds: float,
    launch_timeout_seconds: float,
    duration_of: Callable[[Path], float],
    poll_seconds: float = 5.0,
    exit_grace_seconds: float = 120.0,
    abort_sweep_seconds: float = 30.0,
) -> RenderResult:
    req.output_dir.mkdir(parents=True, exist_ok=True)   # csdm aborts if the folder does not exist
    req.log_path.parent.mkdir(parents=True, exist_ok=True)
    with keep_awake(), open(req.log_path, "wb") as log_file:
        proc = subprocess.Popen(
            csdm.command(*video_args(req)), env=csdm.env(), stdout=log_file,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        intervention, aborted = _watch(proc, probe, should_abort, stall_seconds,
                                       launch_timeout_seconds, poll_seconds, exit_grace_seconds)
        if intervention is not None:
            _sweep_hooked_cs2(probe, abort_sweep_seconds if aborted else 0.0, poll_seconds)
    if aborted:
        return RenderResult(ok=False, aborted=True, failure=intervention)
    if intervention is not None:
        return RenderResult(ok=False, failure=intervention)
    return judge(req.log_path.read_text(encoding="utf-8", errors="replace"), req.output_dir, duration_of)


def _watch(
    proc: subprocess.Popen,
    probe: ProcessProbe,
    should_abort: Callable[[], bool],
    stall_seconds: float,
    launch_timeout_seconds: float,
    poll_seconds: float,
    exit_grace_seconds: float,
) -> tuple[str | None, bool]:
    """Wait for csdm to exit, stepping in only as the spec allows: close the hooked CS2 on a stall or
    when FACEIT AC appears; stop csdm itself only when no hooked CS2 exists, so nothing is orphaned.
    Returns (why the app stepped in or None, whether it was an abort)."""
    started = time.monotonic()
    cs2_since: float | None = None
    last_ffmpeg: float | None = None
    intervened_at = 0.0
    intervention: str | None = None
    aborted = False
    own: set[tuple[int, float]] = set()
    while proc.poll() is None:
        own |= probe.children_named(proc.pid, "cs-demo-manager.exe")
        now = time.monotonic()
        cs2 = probe.hooked_cs2_running()
        if cs2:
            if cs2_since is None:
                cs2_since = now
            if probe.running("ffmpeg.exe"):
                last_ffmpeg = now
        if intervention is None:
            if should_abort():
                intervention, aborted, intervened_at = ABORTED, True, now
            elif cs2 and now - (last_ffmpeg or cs2_since) >= stall_seconds:
                intervention = f"stalled: no ffmpeg for {stall_seconds:g}s while CS2 ran"
                intervened_at = now
            elif cs2_since is None and now - started >= launch_timeout_seconds:
                intervention, intervened_at = NEVER_LAUNCHED, now
        if intervention is not None:
            if cs2:
                probe.kill_hooked_cs2()           # never stop csdm while its game runs
            elif aborted or cs2_since is None or now - intervened_at >= exit_grace_seconds:
                proc.terminate()                  # no hooked CS2 exists, so nothing is orphaned
        time.sleep(poll_seconds)
    probe.kill_processes(own)                     # CS:DM helpers our csdm call left behind
    return intervention, aborted


def _sweep_hooked_cs2(probe: ProcessProbe, seconds: float, poll_seconds: float) -> None:
    """After stepping in, close any hooked CS2 still around; after an abort HLAE may start one late."""
    deadline = time.monotonic() + seconds
    while True:
        if probe.hooked_cs2_running():
            probe.kill_hooked_cs2()
        if time.monotonic() >= deadline:
            return
        time.sleep(poll_seconds)
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_media.py tests/test_render.py -q`
Expected: `11 passed` (each render test takes under two seconds).

- [ ] **Step 8: Commit**

```bash
git add clipper/csdm_cli.py clipper/media.py clipper/render.py tests/fake_csdm.py tests/test_media.py tests/test_render.py
git commit -m "feat: run and watch csdm renders — success from output, stall and abort handling"
```

---

### Task 9: Joining Clips into Reels

**Files:**
- Create: `clipper/join.py`, `tests/test_join.py`

**Interfaces:**
- Consumes: `ClipFile` (Task 5), `probe_duration` (Task 8, tests only).
- Produces: `clipper.join.assign_clips(clips: Sequence[ClipFile], rounds: Sequence[tuple[int, int]]) -> dict[int, list[ClipFile]]` (`rounds` holds `(round, round_start_tick)` per selected Highlight; each list in Tick order); `clipper.join.join_reel(clips: Sequence[Path], out_path: Path, *, ffmpeg: str, duration_of: Callable[[Path], float]) -> float`; `clipper.join.JoinError`.

- [ ] **Step 1: Write the failing tests**

`tests/test_join.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

from clipper.join import JoinError, assign_clips, join_reel
from clipper.media import probe_duration
from clipper.model import ClipFile


def clip(sequence, start, end):
    return ClipFile(sequence=sequence, start_tick=start, end_tick=end,
                    path=Path(f"sequence-{sequence}-tick-{start}-to-{end}.mp4"), duration_s=4.0)


def test_clips_group_by_the_round_they_start_in():
    groups = assign_clips([clip(3, 76071, 76559), clip(1, 19832, 20213), clip(2, 74007, 74263)],
                          [(12, 72031), (3, 15259)])
    assert [c.sequence for c in groups[3]] == [1]
    assert [c.sequence for c in groups[12]] == [2, 3]


def test_sequence_ten_comes_after_sequence_two():   # issue 10
    groups = assign_clips([clip(10, 90000, 90256), clip(2, 80000, 80256)], [(12, 72031)])
    assert [c.sequence for c in groups[12]] == [2, 10]


def test_a_clip_before_every_selected_round_is_an_error():
    with pytest.raises(JoinError, match="starts before every selected round"):
        assign_clips([clip(1, 100, 356)], [(3, 15259)])


def test_a_selected_round_without_clips_is_an_error():
    with pytest.raises(JoinError, match="no Clip for selected round"):
        assign_clips([clip(1, 19832, 20213)], [(3, 15259), (12, 72031)])


@pytest.fixture(scope="module")
def one_second_clip(tmp_path_factory):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=60:d=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "libmp3lame", "-shortest", str(path)],
        check=True,
    )
    return path


def copies(folder, source, *names):
    paths = [folder / name for name in names]
    for path in paths:
        shutil.copyfile(source, path)
    return paths


@pytest.mark.ffmpeg
def test_two_clips_join_into_one_reel(tmp_path, one_second_clip):
    a, b = copies(tmp_path, one_second_clip, "a.mp4", "b.mp4")
    out = tmp_path / "library" / "r12-player.mp4"
    duration = join_reel([a, b], out, ffmpeg="ffmpeg", duration_of=probe_duration)
    assert out.exists()
    assert duration == pytest.approx(2.0, abs=0.25)
    assert sorted(p.name for p in out.parent.iterdir()) == ["r12-player.mp4"]


@pytest.mark.ffmpeg
def test_a_single_clip_is_copied(tmp_path, one_second_clip):
    (a,) = copies(tmp_path, one_second_clip, "a.mp4")
    out = tmp_path / "library" / "r3-enemy.mp4"
    assert join_reel([a], out, ffmpeg="ffmpeg", duration_of=probe_duration) == pytest.approx(1.0, abs=0.05)


@pytest.mark.ffmpeg
def test_a_reel_that_does_not_add_up_is_rejected(tmp_path, one_second_clip):
    a, b = copies(tmp_path, one_second_clip, "a.mp4", "b.mp4")
    out = tmp_path / "library" / "r12-player.mp4"

    def inflated(path):   # pretend each Clip is 5 s long, so a 2 s Reel cannot be right
        return 5.0 if path.name in {"a.mp4", "b.mp4"} else probe_duration(path)

    with pytest.raises(JoinError, match="Clips sum to"):
        join_reel([a, b], out, ffmpeg="ffmpeg", duration_of=inflated)
    assert not out.exists()
    assert not list(out.parent.glob("*.partial.mp4"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_join.py -q`
Expected: collection error — `No module named 'clipper.join'`.

- [ ] **Step 3: Write `clipper/join.py`**

```python
"""Joining: a Highlight's Clips become one Reel per Perspective (spec: Joining)."""

from __future__ import annotations

import bisect
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from clipper.model import ClipFile

BASE_TOLERANCE_S = 0.25
PER_JOIN_TOLERANCE_S = 0.05   # joining two MP3-audio Clips measured +0.045 s on this machine


class JoinError(Exception):
    """Clips could not be matched to Highlights, or a Reel could not be made."""


def assign_clips(clips: Sequence[ClipFile], rounds: Sequence[tuple[int, int]]) -> dict[int, list[ClipFile]]:
    """Group Clips by selected round. `rounds` holds (round, round_start_tick) per selected
    Highlight. A Clip belongs to the latest selected round that starts at or before it: CS:DM only
    renders the rounds it was given, so that is always the Clip's own round."""
    ordered = sorted(rounds, key=lambda pair: pair[1])
    starts = [start for _, start in ordered]
    groups: dict[int, list[ClipFile]] = {number: [] for number, _ in ordered}
    for clip in sorted(clips, key=lambda c: c.start_tick):
        position = bisect.bisect_right(starts, clip.start_tick) - 1
        if position < 0:
            raise JoinError(f"{clip.path.name} starts before every selected round")
        groups[ordered[position][0]].append(clip)
    empty = [str(number) for number, got in groups.items() if not got]
    if empty:
        raise JoinError(f"no Clip for selected round(s) {', '.join(empty)}")
    return groups


def _concat_line(path: Path) -> str:
    return "file '" + path.as_posix().replace("'", "'\\''") + "'\n"


def join_reel(clips: Sequence[Path], out_path: Path, *, ffmpeg: str,
              duration_of: Callable[[Path], float]) -> float:
    """Concatenate `clips` (already in Sequence order) into out_path without re-encoding, check the
    result's length, and return it in seconds."""
    if not clips:
        raise JoinError(f"no Clips for {out_path.name}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    expected = sum(duration_of(clip) for clip in clips)
    partial = out_path.with_name(out_path.stem + ".partial.mp4")
    if len(clips) == 1:
        shutil.copyfile(clips[0], partial)
    else:
        listing = out_path.with_name(out_path.stem + ".concat.txt")
        listing.write_text("".join(_concat_line(clip) for clip in clips), encoding="utf-8")
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(listing), "-c", "copy", str(partial)],
                capture_output=True, text=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        finally:
            listing.unlink(missing_ok=True)
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise JoinError(f"ffmpeg could not join {out_path.name}: {result.stderr.strip()[:300]}")
    actual = duration_of(partial)
    tolerance = BASE_TOLERANCE_S + PER_JOIN_TOLERANCE_S * (len(clips) - 1)
    if abs(actual - expected) > tolerance:
        partial.unlink(missing_ok=True)
        raise JoinError(f"{out_path.name} is {actual:.2f}s but its Clips sum to {expected:.2f}s")
    os.replace(partial, out_path)
    return actual
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_join.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add clipper/join.py tests/test_join.py
git commit -m "feat: assign Clips to Highlights and join them into Reels (fixes Reel order, issue 10)"
```

---

### Task 10: The worker

**Files:**
- Create: `clipper/worker.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `Config` (Task 2); `score_match`, `select` (Task 3); `MatchInfo`, `RoundFacts`, `ClipFile` (Tasks 3–5); `Index` (Task 5); `GateStatus`, `GUI_REASON` (Task 7); `RenderRequest`, `RenderResult` (Task 8); `assign_clips`, `JoinError` (Task 9).
- Produces: `clipper.worker.Services(intake, unpack, analyze, facts, gate, render, join, notify, sleep=time.sleep)` (dataclass; `render(request, should_abort) -> RenderResult`, `join(clip_paths, out_path) -> float`, `notify(title, body)`); `clipper.worker.Worker(cfg, index, services)` with `tick() -> None`; constants `ACTIVE_STATES`, `PERSPECTIVES = ("player", "enemy")`; exceptions `Skip`, `GiveUp`.

- [ ] **Step 1: Write the failing tests**

`tests/test_worker.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from clipper.config import Config
from clipper.gate import GUI_REASON, GateStatus
from clipper.index import Index
from clipper.model import ClipFile, MatchInfo
from clipper.render import RenderResult
from clipper.worker import Services, Worker
from tests.fixtures import DEMO_NAME, MATCH_CHECKSUM, MATCH_FACTS

ROUND_START = {facts.round: facts.round_start_tick for facts in MATCH_FACTS}
MATCH = MatchInfo(checksum=MATCH_CHECKSUM, map_name="de_inferno",
                  played_at=datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc),
                  team_score=13, opponent_score=5)


class FakeIntake:
    def ready(self):
        return []

    def take(self, path, index):
        raise AssertionError("these tests use no Downloads folder")


class FakeFacts:
    def __init__(self):
        self.info = MATCH
        self.facts = MATCH_FACTS

    def find_checksum(self, demo_name):
        return MATCH_CHECKSUM

    def match_info(self, checksum, steamid):
        return self.info

    def round_facts(self, checksum, steamid):
        return list(self.facts)


class FakeGate:
    def __init__(self):
        self.reasons: tuple[str, ...] = ()

    def check(self):
        return GateStatus(ok=not self.reasons, reasons=self.reasons)

    def faceit_running(self):
        return False


class FakeRender:
    """Succeeds with one Clip per requested round, unless canned results are queued."""

    def __init__(self):
        self.calls = []
        self.results = []

    def __call__(self, request, should_abort):
        self.calls.append(request)
        if self.results:
            return self.results.pop(0)
        clips = tuple(
            ClipFile(sequence=n, start_tick=ROUND_START[r] + 1000, end_tick=ROUND_START[r] + 1256,
                     path=request.output_dir / f"sequence-{n}.mp4", duration_s=4.0)
            for n, r in enumerate(request.rounds, start=1)
        )
        return RenderResult(ok=True, clips=clips)


@dataclass
class World:
    cfg: Config
    index: Index
    services: Services
    worker: Worker
    facts: FakeFacts
    gate: FakeGate
    render: FakeRender
    notices: list

    def add_demo(self) -> int:
        name = f"{DEMO_NAME}.dem.zst"
        return self.index.add_demo(name, "0" * 64, self.cfg.demos_dir / name)

    def ticks(self, count: int) -> None:
        for _ in range(count):
            self.worker.tick()

    def titles(self) -> list[str]:
        return [title for title, _ in self.notices]


@pytest.fixture
def world(tmp_path):
    cfg = Config(downloads_dir=tmp_path / "downloads", data_root=tmp_path / "clips",
                 index_path=tmp_path / "clipper.sqlite")
    index = Index(cfg.index_path)
    facts, gate, render, notices = FakeFacts(), FakeGate(), FakeRender(), []
    services = Services(
        intake=FakeIntake(),
        unpack=lambda archive, out_dir: out_dir / archive.name.removesuffix(".zst"),
        analyze=lambda dem, log_path: "ok",
        facts=facts,
        gate=gate,
        render=render,
        join=lambda clips, out: 4.0 * len(clips),
        notify=lambda title, body: notices.append((title, body)),
        sleep=lambda seconds: None,
    )
    yield World(cfg, index, services, Worker(cfg, index, services), facts, gate, render, notices)
    index.close()


def test_a_demo_goes_from_spotted_to_done(world):
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspective for call in world.render.calls] == ["player", "enemy"]
    assert world.render.calls[0].rounds == (3, 4, 8, 12, 14)
    assert world.index.reel_count(MATCH_CHECKSUM) == 10
    assert world.titles() == ["Rendering highlights", "Rendering highlights", "Highlights ready"]


def test_a_match_without_the_subject_is_skipped(world):
    world.facts.info = None
    demo_id = world.add_demo()
    world.ticks(3)
    demo = world.index.demo(demo_id)
    assert demo["state"] == "skipped"
    assert "not in this match" in demo["last_error"]


def test_renders_wait_for_the_gate(world):
    world.gate.reasons = ("FACEIT AC is running",)
    demo_id = world.add_demo()
    world.ticks(6)
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert world.render.calls == []
    world.gate.reasons = ()
    world.ticks(4)
    assert world.index.demo(demo_id)["state"] == "done"


def test_the_csdm_gui_notice_is_sent_once(world):
    world.gate.reasons = (GUI_REASON,)
    world.add_demo()
    world.ticks(8)
    assert world.titles().count("Close CS Demo Manager") == 1


def test_three_failed_renders_fail_the_demo_and_pause_rendering(world):
    world.render.results = [RenderResult(ok=False, failure="csdm reported: Game error")] * 3
    demo_id = world.add_demo()
    world.ticks(8)
    demo = world.index.demo(demo_id)
    assert demo["state"] == "failed"
    assert demo["last_error"] == "player render failed 3 times: csdm reported: Game error"
    assert world.index.get_flag("paused") == "1"
    assert "Rendering paused" in world.titles()


def test_an_aborted_render_is_tried_again_without_counting(world):
    world.render.results = [RenderResult(ok=False, aborted=True,
                                         failure="aborted: FACEIT AC started during the render")]
    demo_id = world.add_demo()
    world.ticks(9)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspective for call in world.render.calls] == ["player", "player", "enemy"]
    assert world.index.get_flag("consecutive_failures", "0") == "0"


def test_paused_rendering_waits_for_resume(world):
    world.index.set_flag("paused", "1")
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert world.render.calls == []


def test_a_step_that_keeps_failing_fails_the_demo_and_retry_resumes_it(world):
    def broken_unpack(archive, out_dir):
        raise OSError("disk full")

    healthy_unpack = world.services.unpack
    world.services.unpack = broken_unpack
    demo_id = world.add_demo()
    world.ticks(3)
    demo = world.index.demo(demo_id)
    assert (demo["state"], demo["resume_state"]) == ("failed", "spotted")
    assert demo["last_error"] == "spotted: disk full"
    world.services.unpack = healthy_unpack
    assert world.index.retry(demo_id) == "spotted"
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "done"


def test_a_match_without_frags_finishes_without_rendering(world):
    world.facts.facts = ()
    demo_id = world.add_demo()
    world.ticks(4)
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.render.calls == []
    assert world.notices == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_worker.py -q`
Expected: collection error — `No module named 'clipper.worker'`.

- [ ] **Step 3: Write `clipper/worker.py`**

```python
"""The worker: moves each Demo through the Flow, one step per tick (spec: Flow).

spotted → unpacked → analyzed → scored → rendering → joined → done, or skipped / failed.
Every step can run again safely, so a crash or a reboot resumes where it stopped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from clipper.config import Config
from clipper.gate import GUI_REASON, GateStatus
from clipper.index import Index
from clipper.join import JoinError, assign_clips
from clipper.model import MatchInfo, RoundFacts
from clipper.render import RenderRequest, RenderResult
from clipper.scoring import score_match, select

log = logging.getLogger(__name__)

ACTIVE_STATES = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
PERSPECTIVES = ("player", "enemy")
MAX_STEP_ATTEMPTS = 3
MAX_RENDER_ATTEMPTS = 3
PAUSE_AFTER_FAILURES = 3


class Skip(Exception):
    """This Demo should not be processed at all."""


class GiveUp(Exception):
    """This Demo cannot be finished; running the step again will not help."""


class IntakeLike(Protocol):
    def ready(self) -> list[Path]: ...
    def take(self, path: Path, index: Index) -> int | None: ...


class FactsSource(Protocol):
    def find_checksum(self, demo_name: str) -> str | None: ...
    def match_info(self, checksum: str, steamid: str) -> MatchInfo | None: ...
    def round_facts(self, checksum: str, steamid: str) -> list[RoundFacts]: ...


class GateLike(Protocol):
    def check(self) -> GateStatus: ...
    def faceit_running(self) -> bool: ...


@dataclass
class Services:
    """Everything the worker talks to. Tests swap in fakes."""

    intake: IntakeLike
    unpack: Callable[[Path, Path], Path]
    analyze: Callable[[Path, Path], str]
    facts: FactsSource
    gate: GateLike
    render: Callable[[RenderRequest, Callable[[], bool]], RenderResult]
    join: Callable[[list[Path], Path], float]
    notify: Callable[[str, str], None]
    sleep: Callable[[float], None] = time.sleep


def _fresh_dir(path: Path) -> Path:
    """`path`, or `path-2`, `path-3`, … if it already exists."""
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}-{n}")
    return candidate


class Worker:
    def __init__(self, cfg: Config, index: Index, services: Services):
        self.cfg = cfg
        self.index = index
        self.services = services
        self._told_about_gui = False

    # --- the loop --------------------------------------------------------------------------------

    def tick(self) -> None:
        """Take any new Demos, then move every unfinished Demo on by at most one step."""
        for path in self.services.intake.ready():
            try:
                self.services.intake.take(path, self.index)
            except OSError:
                log.exception("could not take %s; will try again", path.name)
        for demo in self.index.demos_in(ACTIVE_STATES):
            self._advance(demo)

    def _advance(self, demo) -> None:
        steps = {
            "spotted": self._unpack,
            "unpacked": self._analyze,
            "analyzed": self._score,
            "scored": self._queue_renders,
            "rendering": self._render,
            "joined": self._finish,
        }
        try:
            steps[demo["state"]](demo)
        except Skip as exc:
            log.info("demo #%s skipped: %s", demo["id"], exc)
            self.index.advance(demo["id"], "skipped", last_error=str(exc))
        except GiveUp as exc:
            log.error("demo #%s failed: %s", demo["id"], exc)
            self.index.fail(demo["id"], str(exc))
        except Exception as exc:  # noqa: BLE001 - every failure is recorded; none may stop the app
            log.exception("demo #%s: the %s step failed", demo["id"], demo["state"])
            error = f"{demo['state']}: {exc}"
            if self.index.record_failure(demo["id"], error) >= MAX_STEP_ATTEMPTS:
                self.index.fail(demo["id"], error)

    # --- steps 2–4 -------------------------------------------------------------------------------

    def _unpack(self, demo) -> None:
        dem = self.services.unpack(Path(demo["archive_path"]), self.cfg.demos_dir)
        self.index.advance(demo["id"], "unpacked", dem_path=dem)

    def _analyze(self, demo) -> None:
        dem = Path(demo["dem_path"])
        self.services.analyze(dem, self._log_path(demo, "analyze"))
        checksum = self.services.facts.find_checksum(dem.stem)
        if checksum is None:
            raise RuntimeError("csdm analyze did not put the match in CS:DM's database")
        info = self.services.facts.match_info(checksum, self.cfg.subject_steamid)
        if info is None:
            raise Skip("the subject's SteamID is not in this match")
        self.index.save_match(info)
        self.index.advance(demo["id"], "analyzed", match_checksum=checksum)

    def _score(self, demo) -> None:
        facts = self.services.facts.round_facts(demo["match_checksum"], self.cfg.subject_steamid)
        highlights = score_match(facts)
        chosen = select(highlights, self.cfg.top_n)
        self.index.save_highlights(demo["match_checksum"], highlights, {h.round for h in chosen})
        self.index.advance(demo["id"], "scored")

    def _queue_renders(self, demo) -> None:
        if not self.index.selected_highlights(demo["match_checksum"]):
            self._finish(demo)                        # no Frags: nothing to render
            return
        for perspective in PERSPECTIVES:
            if self.index.latest_render(demo["id"], perspective) is None:
                self.index.queue_render(demo["id"], perspective, attempt=1)
        self.index.advance(demo["id"], "rendering")

    # --- step 5: rendering -----------------------------------------------------------------------

    def _render(self, demo) -> None:
        for perspective in PERSPECTIVES:
            job = self.index.latest_render(demo["id"], perspective)
            if job is None:
                self.index.queue_render(demo["id"], perspective, attempt=1)
                job = self.index.latest_render(demo["id"], perspective)
            if job["state"] == "done":
                continue
            if job["state"] == "running":             # the app stopped in the middle of this render
                self.index.finish_render(job["id"], "failed", "interrupted: the app stopped mid-render")
                job = self.index.latest_render(demo["id"], perspective)
            if job["state"] == "failed":
                if job["attempt"] >= MAX_RENDER_ATTEMPTS:
                    raise GiveUp(f"{perspective} render failed {job['attempt']} times: {job['failure']}")
                self.index.queue_render(demo["id"], perspective, attempt=job["attempt"] + 1)
                job = self.index.latest_render(demo["id"], perspective)
            elif job["state"] == "aborted":            # FACEIT AC appeared: go again, not counted
                self.index.queue_render(demo["id"], perspective, attempt=job["attempt"])
                job = self.index.latest_render(demo["id"], perspective)
            self._try_render(demo, job)
            return                                    # at most one CS2 launch per tick
        self._join(demo)
        self.index.advance(demo["id"], "joined")

    def _try_render(self, demo, job) -> None:
        if self.index.get_flag("paused") == "1":
            return
        if not self._gate_is_clear():
            return
        highlights = self.index.selected_highlights(demo["match_checksum"])
        match = self.index.match(demo["match_checksum"])
        self.services.notify(
            "Rendering highlights",
            f"{len(highlights)} Highlights from {match['map']} ({job['perspective']} view). "
            f"CS2 opens in {self.cfg.heads_up_seconds:g} s — please don't start it yourself.",
        )
        if not self._gate_stays_clear(self.cfg.heads_up_seconds):
            return
        output_dir = _fresh_dir(
            self.cfg.renders_dir / demo["match_checksum"] / job["perspective"] / f"attempt-{job['attempt']}"
        )
        log_path = self._log_path(demo, f"render-{job['perspective']}-{job['attempt']}")
        self.index.start_render(job["id"], output_dir, log_path)
        request = RenderRequest(
            demo_path=Path(demo["dem_path"]),
            perspective=job["perspective"],
            rounds=tuple(h["round"] for h in highlights),
            output_dir=output_dir,
            log_path=log_path,
            steamid=self.cfg.subject_steamid,
            padding_before_s=self.cfg.padding_before_s,
            padding_after_s=self.cfg.padding_after_s,
        )
        try:
            result = self.services.render(request, self.services.gate.faceit_running)
        except Exception as exc:  # noqa: BLE001 - a crashed render is a failed attempt
            log.exception("render crashed")
            result = RenderResult(ok=False, failure=f"render crashed: {exc}")
        if result.aborted:
            self.index.finish_render(job["id"], "aborted", result.failure)
            return
        if result.ok:
            try:
                groups = assign_clips(result.clips, [(h["round"], h["round_start_tick"]) for h in highlights])
            except JoinError as exc:
                result = RenderResult(ok=False, failure=str(exc))
            else:
                ids = {h["round"]: h["id"] for h in highlights}
                for number, clips in groups.items():
                    for clip in clips:
                        self.index.add_clip(job["id"], ids[number], clip)
                self.index.finish_render(job["id"], "done")
                self.index.set_flag("consecutive_failures", "0")
                return
        self.index.finish_render(job["id"], "failed", result.failure)
        failures = int(self.index.get_flag("consecutive_failures", "0")) + 1
        self.index.set_flag("consecutive_failures", str(failures))
        if failures >= PAUSE_AFTER_FAILURES:
            self.index.set_flag("paused", "1")
            self.services.notify(
                "Rendering paused",
                f"{failures} renders failed in a row. Check HLAE/CS2 compatibility, then run: clipper resume",
            )

    def _gate_is_clear(self) -> bool:
        status = self.services.gate.check()
        gui_open = GUI_REASON in status.reasons
        if gui_open and not self._told_about_gui:
            self.services.notify("Close CS Demo Manager", "Rendering is waiting for CS Demo Manager to close.")
        self._told_about_gui = gui_open
        return status.ok

    def _gate_stays_clear(self, seconds: float) -> bool:
        """Wait out the heads-up, giving up if the Gate closes meanwhile."""
        waited = 0.0
        while waited < seconds:
            step = min(1.0, seconds - waited)
            self.services.sleep(step)
            waited += step
            if not self.services.gate.check().ok:
                return False
        return True

    # --- steps 6–7 -------------------------------------------------------------------------------

    def _join(self, demo) -> None:
        for highlight in self.index.selected_highlights(demo["match_checksum"]):
            for perspective in PERSPECTIVES:
                clips = [Path(row["path"]) for row in self.index.clips_for(highlight["id"], perspective)]
                out = (self.cfg.library_dir / "videos" / demo["match_checksum"]
                       / f"r{highlight['round']}-{perspective}.mp4")
                duration = self.services.join(clips, out)
                self.index.save_reel(highlight["id"], perspective, out, duration)

    def _finish(self, demo) -> None:
        dem, archive = demo["dem_path"], demo["archive_path"]
        if dem and Path(dem) != Path(archive):
            Path(dem).unlink(missing_ok=True)         # the compressed download stays
        count = len(self.index.selected_highlights(demo["match_checksum"])) if demo["match_checksum"] else 0
        if count:
            match = self.index.match(demo["match_checksum"])
            self.services.notify("Highlights ready", f"{count} Highlights from {match['map']} are ready.")
        self.index.advance(demo["id"], "done")

    def _log_path(self, demo, name: str) -> Path:
        return self.cfg.logs_dir / f"demo-{demo['id']}-{name}.log"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_worker.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: every test passes.

- [ ] **Step 6: Commit**

```bash
git add clipper/worker.py tests/test_worker.py
git commit -m "feat: the worker — each Demo from Downloads to Reels, with retries and a render pause"
```

---

### Task 11: Command line, notifications and Postgres start-up

**Files:**
- Create: `clipper/notify.py`, `clipper/postgres.py`, `clipper/cli.py`, `clipper/__main__.py`, `tests/test_notify.py`, `tests/test_postgres.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2–10.
- Produces: `clipper.notify.notify(title, body) -> None` (best effort) and `toast_xml(title, body) -> str`; `clipper.postgres.is_running(pg_bin, pg_data) -> bool` and `ensure_running(pg_bin, pg_data) -> None`; `clipper.cli.main(argv=None) -> int` (commands `run`, `status`, `retry <demo>`, `resume`, `highlights <demo>`), `format_status(index, gate_status) -> str`, `single_instance(lock_path)` (context manager), `AlreadyRunning`, `cmd_retry(index, key) -> int`, `build_worker(cfg, index) -> Worker`.

- [ ] **Step 1: Write the failing tests**

`tests/test_notify.py`:

```python
from clipper.notify import toast_xml


def test_toast_text_is_escaped():
    xml = toast_xml("Rock & roll", "less than <5 GB>")
    assert "<text>Rock &amp; roll</text>" in xml
    assert "<text>less than &lt;5 GB&gt;</text>" in xml
```

`tests/test_postgres.py`:

```python
import pytest

from clipper import postgres
from clipper.config import load_config

pytestmark = pytest.mark.integration


def test_ensure_running_leaves_the_cluster_up():
    cfg = load_config()
    if not (cfg.pg_bin / "pg_ctl.exe").exists():
        pytest.skip("the portable Postgres is not installed")
    postgres.ensure_running(cfg.pg_bin, cfg.pg_data)
    assert postgres.is_running(cfg.pg_bin, cfg.pg_data)
```

`tests/test_cli.py`:

```python
import pytest

from clipper.cli import AlreadyRunning, cmd_retry, format_status, single_instance
from clipper.gate import GateStatus
from clipper.index import Index


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    yield idx
    idx.close()


def test_status_of_an_empty_index(index):
    text = format_status(index, GateStatus(ok=True))
    assert text.splitlines() == ["Gate: clear", "No Demos yet. Download one from a FACEIT matchroom."]


def test_status_shows_why_the_gate_waits_and_why_a_demo_failed(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.fail(demo_id, "unpacked: disk full")
    index.set_flag("paused", "1")
    lines = format_status(index, GateStatus(ok=False, reasons=("CS2 is running", "FACEIT AC is running"))).splitlines()
    assert lines[0] == "Gate: waiting: CS2 is running; FACEIT AC is running"
    assert lines[1].startswith("Rendering: PAUSED")
    assert lines[2] == f"#{demo_id:<3} failed    1-a.dem.zst  - unpacked: disk full"


def test_retry_sends_a_failed_demo_back(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.advance(demo_id, "unpacked")
    index.fail(demo_id, "unpacked: boom")
    assert cmd_retry(index, str(demo_id)) == 0
    assert index.demo(demo_id)["state"] == "unpacked"
    assert cmd_retry(index, "nope") == 1


def test_only_one_instance_can_hold_the_lock(tmp_path):
    lock = tmp_path / "clipper.lock"
    with single_instance(lock):
        with pytest.raises(AlreadyRunning):
            with single_instance(lock):
                pass
    with single_instance(lock):   # released again
        pass
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_notify.py tests/test_postgres.py tests/test_cli.py -q`
Expected: collection errors — `No module named 'clipper.notify'`, `'clipper.postgres'` (`cannot import name 'postgres'`), `'clipper.cli'`.

- [ ] **Step 3: Write `clipper/notify.py`**

```python
"""Windows notifications (spec: The Gate, heads-up). Best effort: a notification that cannot be shown
is logged, never raised."""

from __future__ import annotations

import logging
import os
import subprocess
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

# PowerShell's own AppUserModelID, so the toast needs no registered app.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType = WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
    " ContentType = WindowsRuntime] | Out-Null;"
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$xml.LoadXml($env:CLIPPER_TOAST);"
    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
)


def toast_xml(title: str, body: str) -> str:
    return (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual></toast>"
    )


def notify(title: str, body: str) -> None:
    env = {**os.environ, "CLIPPER_TOAST": toast_xml(title, body)}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env, capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("notification failed: %s", exc)
        return
    if result.returncode != 0:
        log.warning("notification failed: %s", result.stderr.strip()[:300])
```

- [ ] **Step 4: Write `clipper/postgres.py`**

```python
"""Starts the portable Postgres that CS:DM uses. It is not a Windows service, so it is down after a reboot."""

from __future__ import annotations

import subprocess
from pathlib import Path

PORT = 5432


def is_running(pg_bin: Path, pg_data: Path) -> bool:
    result = subprocess.run(
        [str(pg_bin / "pg_ctl.exe"), "-D", str(pg_data), "status"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def ensure_running(pg_bin: Path, pg_data: Path) -> None:
    """Start the cluster if it is down. Output goes to DEVNULL: the server inherits pg_ctl's handles,
    and capturing them would block forever (see scripts/setup_local_postgres.py)."""
    if is_running(pg_bin, pg_data):
        return
    log_file = pg_data.parent / "postgres.log"
    result = subprocess.run(
        [str(pg_bin / "pg_ctl.exe"), "-D", str(pg_data), "-l", str(log_file),
         "-o", f"-p {PORT} -c listen_addresses=127.0.0.1", "-w", "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Postgres did not start; see {log_file}")
```

- [ ] **Step 5: Write `clipper/cli.py`**

```python
"""Command line (spec: Running it): run, status, retry, resume and highlights."""

from __future__ import annotations

import argparse
import logging
import msvcrt
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

from clipper import csdm_db, postgres
from clipper.config import Config, load_config
from clipper.csdm_cli import CsdmCli
from clipper.gate import Gate, GateStatus
from clipper.index import Index
from clipper.intake import Intake
from clipper.join import join_reel
from clipper.media import probe_duration
from clipper.notify import notify
from clipper.procs import SystemProbe
from clipper.render import render
from clipper.scoring import score_match, select
from clipper.unpack import unpack
from clipper.worker import PERSPECTIVES, Services, Worker

log = logging.getLogger("clipper")
CHECKSUM = re.compile(r"^[0-9a-f]{16}$")


class AlreadyRunning(Exception):
    """Another clipper holds the lock."""


@contextmanager
def single_instance(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on lock_path for the life of the block."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(f"another clipper is already running (lock: {lock_path})") from exc
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def format_status(index: Index, gate: GateStatus) -> str:
    lines = ["Gate: " + ("clear" if gate.ok else "waiting: " + "; ".join(gate.reasons))]
    if index.get_flag("paused") == "1":
        lines.append("Rendering: PAUSED after repeated failures. Check HLAE/CS2, then run: clipper resume")
    demos = index.all_demos()
    if not demos:
        lines.append("No Demos yet. Download one from a FACEIT matchroom.")
    for demo in demos:
        line = f"#{demo['id']:<3} {demo['state']:<9} {demo['file_name']}"
        match = index.match(demo["match_checksum"]) if demo["match_checksum"] else None
        if match is not None:
            line += f"  {match['map']} {match['team_score']}-{match['opponent_score']} {match['result']}"
        if demo["state"] == "rendering":
            jobs = [(p, index.latest_render(demo["id"], p)) for p in PERSPECTIVES]
            line += "  " + ", ".join(f"{p} {job['state']} (attempt {job['attempt']})" for p, job in jobs if job)
        if demo["state"] == "done" and match is not None:
            line += f"  {index.reel_count(demo['match_checksum'])} Reels"
        if demo["state"] in ("failed", "skipped") and demo["last_error"]:
            line += f"  - {demo['last_error']}"
        lines.append(line)
    return "\n".join(lines)


def cmd_retry(index: Index, key: str) -> int:
    demo = index.find_demo(key)
    if demo is None:
        print(f"no Demo matches {key!r}")
        return 1
    try:
        state = index.retry(demo["id"])
    except ValueError as exc:
        print(exc)
        return 1
    print(f"#{demo['id']} is back at '{state}'")
    return 0


def cmd_resume(index: Index) -> int:
    index.set_flag("paused", "0")
    index.set_flag("consecutive_failures", "0")
    print("rendering resumed")
    return 0


def cmd_highlights(cfg: Config, index: Index, key: str) -> int:
    demo = index.find_demo(key)
    checksum = demo["match_checksum"] if demo is not None else (key if CHECKSUM.match(key) else None)
    if not checksum:
        print(f"no analyzed Demo matches {key!r}")
        return 1
    with csdm_db.connect(cfg.database_conninfo()) as conn:
        facts = csdm_db.round_facts(conn, checksum, cfg.subject_steamid)
    highlights = score_match(facts)
    chosen = {h.round for h in select(highlights, cfg.top_n)}
    print(f"{'round':>6} {'type':<5} {'score':>5}  reasons")
    for h in highlights:
        mark = "*" if h.round in chosen else " "
        print(f"{h.round:>5}{mark} {h.type:<5} {h.score:>5}  {', '.join(h.reasons)}")
    print(f"* = in the top {cfg.top_n}")
    return 0


def build_worker(cfg: Config, index: Index) -> Worker:
    probe = SystemProbe()
    gate = Gate(probe, cfg.data_root, cfg.min_free_gb)
    csdm = CsdmCli(prefix=(str(cfg.csdm_exe), str(cfg.csdm_cli_js)), home=cfg.csdm_home, pg_bin=cfg.pg_bin)
    duration_of = partial(probe_duration, ffprobe=cfg.ffprobe)

    def render_job(request, should_abort):
        return render(request, csdm=csdm, probe=probe, should_abort=should_abort,
                      stall_seconds=cfg.stall_seconds, launch_timeout_seconds=cfg.launch_timeout_seconds,
                      duration_of=duration_of)

    services = Services(
        intake=Intake(cfg.downloads_dir, cfg.demos_dir),
        unpack=unpack,
        analyze=csdm.analyze,
        facts=csdm_db.CsdmFacts(cfg.database_conninfo()),
        gate=gate,
        render=render_job,
        join=partial(join_reel, ffmpeg=cfg.ffmpeg, duration_of=duration_of),
        notify=notify,
    )
    return Worker(cfg, index, services)


def _setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "clipper.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:                        # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_run(cfg: Config) -> int:
    try:
        with single_instance(cfg.index_path.with_name("clipper.lock")):
            _setup_logging(cfg.logs_dir)
            for folder in (cfg.demos_dir, cfg.renders_dir, cfg.library_dir):
                folder.mkdir(parents=True, exist_ok=True)
            postgres.ensure_running(cfg.pg_bin, cfg.pg_data)
            index = Index(cfg.index_path)
            worker = build_worker(cfg, index)
            log.info("clipper is running; watching %s", cfg.downloads_dir)
            while True:
                try:
                    worker.tick()
                except Exception:  # noqa: BLE001 - the loop must survive anything a tick throws
                    log.exception("tick failed")
                time.sleep(cfg.poll_seconds)
    except AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipper", description="Hands-off CS2 highlight clipper.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run the background app")
    commands.add_parser("status", help="show every Demo's state and the Gate")
    commands.add_parser("retry", help="send a failed Demo back through").add_argument(
        "demo", help="index id or file name")
    commands.add_parser("resume", help="resume rendering after a pause")
    commands.add_parser("highlights", help="print the scored Highlights of an analyzed Demo").add_argument(
        "demo", help="index id, file name, or CS:DM match checksum")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.command == "run":
        return cmd_run(cfg)
    index = Index(cfg.index_path)
    try:
        if args.command == "status":
            print(format_status(index, Gate(SystemProbe(), cfg.data_root, cfg.min_free_gb).check()))
            return 0
        if args.command == "retry":
            return cmd_retry(index, args.demo)
        if args.command == "resume":
            return cmd_resume(index)
        return cmd_highlights(cfg, index, args.demo)
    finally:
        index.close()
```

- [ ] **Step 6: Write `clipper/__main__.py`**

```python
from clipper.cli import main

raise SystemExit(main())
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_notify.py tests/test_postgres.py tests/test_cli.py -q`
Expected: `6 passed`.

- [ ] **Step 8: Try the commands for real**

```bash
uv run clipper highlights aea4e59ccfc6c962
uv run clipper status
uv run python -c "from clipper.notify import notify; notify('clipper', 'test notification')"
```

Expected:
- `highlights` prints 15 rounds with `*` against rounds 3, 4, 8, 12 and 14. Round 12 reads `4K 80 4k, blind kill`.
- `status` prints a `Gate:` line and `No Demos yet. …`.
- A Windows notification titled "clipper" appears.

- [ ] **Step 9: Run the whole suite and commit**

Run: `uv run pytest -q` — expected: every test passes.

```bash
git add clipper/notify.py clipper/postgres.py clipper/cli.py clipper/__main__.py tests/test_notify.py tests/test_postgres.py tests/test_cli.py
git commit -m "feat: clipper command line — run, status, retry, resume, highlights"
```

---

### Task 12: Start at sign-in

**Files:**
- Create: `clipper/install.py`, `tests/test_install.py`
- Modify: `clipper/cli.py` (add `install` and `uninstall`)

**Interfaces:**
- Consumes: `REPO_ROOT` (Task 2), `main` (Task 11).
- Produces: `clipper.install.TASK_NAME = "cs2-clipper"`, `register_script(repo_root: Path, pythonw: Path) -> str`, `unregister_script() -> str`, `install(repo_root: Path) -> None`, `uninstall() -> None`; CLI commands `clipper install` and `clipper uninstall`.

- [ ] **Step 1: Write the failing tests**

`tests/test_install.py`:

```python
from pathlib import Path

from clipper.install import TASK_NAME, register_script, unregister_script


def test_the_task_runs_the_app_at_sign_in_and_restarts_it():
    script = register_script(Path("C:/code/cs2-clipper"), Path("C:/code/cs2-clipper/.venv/Scripts/pythonw.exe"))
    assert "-Execute 'C:\\code\\cs2-clipper\\.venv\\Scripts\\pythonw.exe'" in script
    assert "-Argument '-m clipper run'" in script
    assert "-WorkingDirectory 'C:\\code\\cs2-clipper'" in script
    assert "New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME" in script
    assert "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)" in script
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script
    assert "-LogonType Interactive -RunLevel Limited" in script
    assert f"-TaskName '{TASK_NAME}'" in script


def test_single_quotes_in_paths_are_doubled():
    script = register_script(Path("C:/it's here"), Path("C:/it's here/pythonw.exe"))
    assert "'C:\\it''s here'" in script


def test_uninstall_removes_the_same_task():
    assert unregister_script() == f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_install.py -q`
Expected: collection error — `No module named 'clipper.install'`.

- [ ] **Step 3: Write `clipper/install.py`**

```python
"""The sign-in task that starts the app (spec: Running it). Registering it is a persistent change to
the user's system: run `clipper install` only with the user's approval; `clipper uninstall` reverses it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "cs2-clipper"


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def register_script(repo_root: Path, pythonw: Path) -> str:
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$action = New-ScheduledTaskAction -Execute {_ps_quote(pythonw)} -Argument '-m clipper run'"
        f" -WorkingDirectory {_ps_quote(repo_root)}",
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries"
        " -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)"
        " -MultipleInstances IgnoreNew",
        "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited",
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action -Trigger $trigger"
        " -Settings $settings -Principal $principal -Force | Out-Null",
    ])


def unregister_script() -> str:
    return f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false"


def _powershell(script: str) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
    )


def install(repo_root: Path) -> None:
    """Start `pythonw -m clipper run` at every sign-in, from this environment's Python."""
    _powershell(register_script(repo_root, Path(sys.executable).with_name("pythonw.exe")))


def uninstall() -> None:
    _powershell(unregister_script())
```

- [ ] **Step 4: Add the commands to `clipper/cli.py`**

Change the config import line from:

```python
from clipper.config import Config, load_config
```

to:

```python
from clipper.config import REPO_ROOT, Config, load_config
from clipper.install import install, uninstall
```

In `main`, after the `highlights` parser line, add:

```python
    commands.add_parser("install", help="start the app when you sign in (Task Scheduler)")
    commands.add_parser("uninstall", help="remove the sign-in task")
```

and replace:

```python
    if args.command == "run":
        return cmd_run(cfg)
```

with:

```python
    if args.command == "run":
        return cmd_run(cfg)
    if args.command == "install":
        install(REPO_ROOT)
        print("clipper will start when you sign in (Task Scheduler task 'cs2-clipper')")
        return 0
    if args.command == "uninstall":
        uninstall()
        print("sign-in task removed")
        return 0
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_install.py tests/test_cli.py -q`
Expected: `7 passed`.

Do **not** run `clipper install` here. Registering the task happens in Task 13, with the user.

- [ ] **Step 6: Commit**

```bash
git add clipper/install.py clipper/cli.py tests/test_install.py
git commit -m "feat: clipper install / uninstall — the sign-in task"
```

---

### Task 13: End-to-end run and documentation (user — launches CS2)

**Who:** the user drives Steps 1–7, because they launch CS2 twice and register a scheduled task. An agent can prepare commands and do Step 8.

**Files:**
- Modify: `CONTEXT.md`, `.scratch/v1-pipeline/spec.md`, `spike/ACCEPTANCE.md`, `.scratch/v1-pipeline/issues/04-orchestrator.md`, `10-reel-order-and-stale-clips.md`, `11-render-reel-rounds-argument.md`, `12-minor-script-defects.md`, `13-bot-controlled-frags-camera.md`, `14-stale-docs-and-statuses.md`
- Delete: `scripts/render_reel.sh`

- [ ] **Step 1: Clear the Gate and start Postgres**

Close CS2, the FACEIT client (`Get-Service FACEITService` → `Stopped`) and CS Demo Manager. Run `python scripts/setup_local_postgres.py --status` and make sure it shows `running : True`.

- [ ] **Step 2: Move the phase-1 files aside** (PowerShell)

```powershell
New-Item -ItemType Directory -Force E:\cs2clips\_phase1 | Out-Null
Get-ChildItem E:\cs2clips -Exclude _phase1 | Move-Item -Destination E:\cs2clips\_phase1
Get-ChildItem E:\cs2clips
```

Expected: only `_phase1` is listed.

- [ ] **Step 3: Start the app in the foreground**

```bash
uv run clipper status
uv run clipper run
```

Expected: `status` shows `Gate: clear` and `No Demos yet. …`. `run` logs `clipper is running; watching C:\Users\AMG\Downloads` and keeps running. Leave this terminal open.

- [ ] **Step 4: Drop the Demo into Downloads** (second terminal, PowerShell, repo root)

```powershell
Copy-Item spike\demos\1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.dem "$HOME\Downloads\"
```

Expected, in the first terminal's log:
1. Within about 20 s: `took 1-2b882547-…-1-1.dem as demo #1`.
2. Analysis finishes quickly; the match is already analyzed.
3. A "Rendering highlights" notification. 30 s later CS2 opens, renders the player view and closes.
4. The same again for the enemy view.
5. A "Highlights ready" notification.

`uv run clipper status` shows the progress at any point.

- [ ] **Step 5: Check the Reels**

```bash
for f in /e/cs2clips/library/videos/aea4e59ccfc6c962/*.mp4; do printf '%s  ' "$(basename "$f")"; ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"; done
```

Expected: 10 files — `r3`, `r4`, `r8`, `r12` and `r14`, each as `-player.mp4` and `-enemy.mp4`. Then watch three of them:
- `r12-player.mp4` and `r12-enemy.mp4`: the 4K, from both sides.
- `r4-player.mp4`: the 3K made while controlling a bot. This answers issue 13 — is the camera on the bot?

- [ ] **Step 6: Restart the app**

Press Ctrl+C in the first terminal, then run `uv run clipper run` again. Expected: it starts cleanly, and `uv run clipper status` still shows demo #1 as `done … 10 Reels`. Press Ctrl+C again.

- [ ] **Step 7: Register the sign-in task (the user approves this system change)**

```bash
uv run clipper install
```

Then, in PowerShell:

```powershell
Get-ScheduledTask cs2-clipper | Select-Object TaskName, State
Start-ScheduledTask cs2-clipper
```

Expected: the task shows `Ready`, then `Running`. `pythonw.exe` appears in Task Manager, and `uv run clipper status` still works. If registering reports "Access is denied", run the same command from an elevated terminal.

- [ ] **Step 8: Record the results and tidy the docs**

`spike/ACCEPTANCE.md`: add a run-log row for this run (both views, 10 Reels, wall-clock time from the log timestamps).

`CONTEXT.md`: replace the Reel entry's definition line with:

```
A single video file containing several Clips concatenated in Sequence order. The pipeline makes one per Highlight per Perspective; that is what the Clip library plays.
```

and add this entry after **Render Engine**:

```markdown
**Gate**:
The check that must pass before the pipeline launches CS2: CS2, FACEIT AC and the CS:DM GUI are closed, the disk has room, and no other Render Job is running.
_Avoid_: lock (the lock is one of its conditions), guard
```

`.scratch/v1-pipeline/spec.md`: replace the Stack line with

```
- **Stack**: Python 3.13 (uv), our index in SQLite, CS:DM keeps its own Postgres. See `.scratch/orchestrator/spec.md`.
```

and replace the second line of the Safety bullet with

```
  Never join a VAC-secured server with a hooked process, and never run one while FACEIT AC is running.
```

Tickets:
- **04:** `Status: resolved`, plus `## Answer` → "Built as `clipper/` per `.scratch/orchestrator/spec.md` and `plan.md`; end-to-end run recorded in `spike/ACCEPTANCE.md`."
- **10 and 11:** `Status: resolved`, plus `## Answer` → "Superseded: `scripts/render_reel.sh` is retired. `clipper/join.py` orders Clips numerically by Tick, and `clipper/worker.py` renders every attempt into a fresh folder."
- **12:** under a new `## Comments` entry, note that the PATH half is moot now the script is gone. The `faceit_probe.py` half stays open.
- **13:** `Status: resolved`, plus `## Answer` describing what `r4-player.mp4` showed.
- **14:** mark the Stack item "done".

Then retire the script:

```bash
git rm scripts/render_reel.sh
```

- [ ] **Step 9: Commit**

```bash
git add -A CONTEXT.md spike/ACCEPTANCE.md .scratch
git commit -m "docs: end-to-end run recorded; glossary, spec and tickets updated; render_reel.sh retired"
```
