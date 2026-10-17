"""Sharing a Reel (spec: Pages, Reels): readable names, the file on the clipboard, and exports made for where clips
get posted -- under Discord's free upload limit, or 9:16 for TikTok, Shorts and Instagram Reels -- that open on a
title card saying whose Highlight it is.

Every Reel is named as it would be posted: "ACE Dust2 R9 s1mple.mp4" (with " (Enemy POV)" for the other view), in a
folder named after its match: "2026-10-12 Dust2 13-9". Exports go to the clips folder's Exports folder."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from clipper.alerts import map_label

log = logging.getLogger(__name__)

DISCORD_LIMIT = 20_000_000       # bytes: Discord's free upload limit since 14 August 2026
AIM = 0.92                       # of the limit, what an export aims at: an encoder never lands on a bitrate exactly
AUDIO_KBPS = 128
EXPORTS = {"discord": "Discord", "vertical": "9x16"}       # kind -> the word its file name ends in
TITLE_SECONDS = 2.5
TITLE_FONT = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "impact.ttf"
_NOT_IN_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_QUIET = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS


class ShareError(Exception):
    """A Reel could not be copied or exported; the message says why, in words for the player."""


@dataclass(frozen=True)
class Title:
    """The card a shared clip opens on: the Highlight ("ACE") over who, where and when ("s1mple · Dust2 · Round 9")."""

    big: str
    small: str


def _clean(text: str) -> str:
    return " ".join(_NOT_IN_NAMES.sub("", text).split())


def reel_name(highlight_type: str, map_name: str, round_number: int, nickname: str, perspective: str) -> str:
    """What a Reel's file is called, without ".mp4": "ACE Dust2 R9 s1mple", and " (Enemy POV)" for the other view. A
    round number under 1 is no round: a Break's Reel is "HALFTIME Dust2 s1mple"."""
    kind = "ACE" if highlight_type.upper() in ("ACE", "5K") else highlight_type.upper()
    parts = [kind, map_label(map_name), f"R{round_number}" if round_number >= 1 else "", _clean(nickname)]
    name = _clean(" ".join(part for part in parts if part))
    return name + (" (Enemy POV)" if perspective == "enemy" else "")


def match_folder(map_name: str, played_at: str, team_score: int, opponent_score: int) -> str:
    """The folder a match's Reels go in: "2026-10-12 Dust2 13-9", the date the player's own."""
    day = datetime.fromisoformat(played_at).astimezone().strftime("%Y-%m-%d")
    return _clean(f"{day} {map_label(map_name)} {team_score}-{opponent_score}")


def title(highlight_type: str, map_name: str, round_number: int, nickname: str) -> Title:
    kind = "ACE" if highlight_type.upper() in ("ACE", "5K") else highlight_type.upper()
    who = [_clean(nickname)] if _clean(nickname) else []
    where = [f"Round {round_number}"] if round_number >= 1 else []         # a Break is of no round
    return Title(kind, " · ".join([*who, map_label(map_name), *where]))


def copy_to_clipboard(path: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Puts `path` on the clipboard as a file, as Explorer's Copy does: pasted into Discord, it uploads."""
    try:
        done = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command",
                    "Set-Clipboard -LiteralPath $env:CLIPPER_FILE"],
                   env={**os.environ, "CLIPPER_FILE": str(path)}, capture_output=True, text=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShareError(f"the clipboard could not be reached: {exc}") from None
    if done.returncode != 0:
        raise ShareError(f"the clipboard refused the file: {done.stderr.strip()[:200]}")


def export_path(exports_dir: Path, name: str, kind: str) -> Path:
    return exports_dir / f"{name} ({EXPORTS[kind]}).mp4"


def _title_filters(card: Title) -> list[str]:
    """drawtext filters that show `card` for the first TITLE_SECONDS. The font and the words sit in files beside the
    run (its working folder), so nothing in them needs escaping."""
    shown = f"enable='lt(t,{TITLE_SECONDS:g})'"
    return [
        f"drawbox=x=0:y=ih*0.60:w=iw:h=ih*0.24:color=black@0.62:t=fill:{shown}",
        f"drawtext=fontfile=title.ttf:textfile=big.txt:fontsize=h/8:fontcolor=0xFF5500:borderw=3:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.615:{shown}",
        f"drawtext=fontfile=title.ttf:textfile=small.txt:fontsize=h/26:fontcolor=white:"
        f"x=(w-text_w)/2:y=h*0.615+h/8+h/60:{shown}",
    ]


def _discord_video(kbps: float) -> tuple[str, list[str]]:
    """The scale and x264 options for a video bitrate: the smaller the budget, the fewer lines it is spent on (and
    never more than the Reel has)."""
    height = 1080 if kbps >= 3500 else 720 if kbps >= 1500 else 480
    k = max(200, int(kbps))
    return f"scale=-2:'min(ih,{height})'", ["-b:v", f"{k}k", "-maxrate", f"{int(k * 1.2)}k", "-bufsize", f"{k * 2}k"]


def export(reel: Path, kind: str, out: Path, *, duration_s: float, ffmpeg: str, card: Title | None,
           run: Callable[..., subprocess.CompletedProcess] = subprocess.run, font: Path = TITLE_FONT) -> Path:
    """Makes `out` from `reel`: "discord" fits Discord's free upload limit, "vertical" is 1080x1920, cropped from the
    middle. With `card` it opens on the title card (left out when the font is missing). A Discord export of a Reel
    that fits already, with no card, is a plain copy. Raises ShareError."""
    if kind not in EXPORTS:
        raise ShareError(f"no such export: {kind}")
    if not reel.is_file():
        raise ShareError("its file is gone")
    out.parent.mkdir(parents=True, exist_ok=True)
    if card is not None and not font.is_file():
        log.warning("no title card: %s is missing", font)
        card = None
    if kind == "discord" and card is None and reel.stat().st_size <= DISCORD_LIMIT * AIM:
        shutil.copyfile(reel, out)
        return out
    with tempfile.TemporaryDirectory(prefix="cs2clipper-export-") as work:
        filters = []
        if card is not None:
            shutil.copyfile(font, Path(work, "title.ttf"))
            Path(work, "big.txt").write_text(card.big, encoding="utf-8")
            Path(work, "small.txt").write_text(card.small, encoding="utf-8")
        if kind == "vertical":
            graph = ",".join(["crop=ih*9/16:ih", "scale=1080:1920", *(_title_filters(card) if card else [])])
            _encode(run, ffmpeg, reel, out, work, graph, ["-preset", "veryfast", "-crf", "20"], 160)
            return out
        budget = DISCORD_LIMIT * AIM * 8 / 1000 / max(duration_s, 1.0)        # kbit/s for picture and sound
        for share in (1.0, 0.8):                                               # once more, smaller, if it missed
            scale, rate = _discord_video((budget - AUDIO_KBPS) * share)
            graph = ",".join([scale, *(_title_filters(card) if card else [])])
            _encode(run, ffmpeg, reel, out, work, graph, ["-preset", "veryfast", *rate], AUDIO_KBPS)
            if out.stat().st_size <= DISCORD_LIMIT:
                return out
        raise ShareError(f"it came out at {out.stat().st_size / 1e6:.1f} MB, over Discord's 20 MB")


def _encode(run, ffmpeg: str, reel: Path, out: Path, work: str, graph: str, video: list[str], audio_kbps: int) -> None:
    command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(reel), "-vf", graph, "-c:v", "libx264", *video,
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", str(out)]
    try:
        done = run(command, cwd=work, capture_output=True, text=True, timeout=600, creationflags=_QUIET)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShareError(f"FFmpeg could not run: {exc}") from None
    if done.returncode != 0 or not out.is_file():
        raise ShareError(f"FFmpeg failed: {(done.stderr or '').strip()[-300:]}")
