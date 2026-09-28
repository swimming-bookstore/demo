"""Shared bits for RecordSession and WindowRecord."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def ffmpeg_bin() -> str:
    ff = os.environ.get("FF") or str(Path.home() / ".local/bin/ffmpeg")
    if os.access(ff, os.X_OK):
        return ff
    found = shutil.which("ffmpeg")
    if not found:
        die("ffmpeg not found")
    return found
