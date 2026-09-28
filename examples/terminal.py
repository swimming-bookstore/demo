#!/usr/bin/env python3
"""Record a GTK terminal. The pixels are XGetImage of that window.

xterm's Xft glyphs often never appear in XGetImage. xfce4-terminal does.
Stdout stays in the window. A redirected log is a blank movie.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("DISPLAY", ":0.0")
os.environ.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from record.window import WindowRecord

OUT = Path("/tmp/demo-terminal-example.mp4")
TITLE = "demo-terminal-example"


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    term = shutil.which("xfce4-terminal") or shutil.which("xterm")
    if not term:
        die("need xfce4-terminal or xterm")
    rec = WindowRecord(TITLE, OUT, fps=12, max_sec=20.0, min_w=500, min_h=280, overlay_pointer=False)
    inner = "printf 'hello world\\n'; sleep 8"
    if os.path.basename(term) == "xfce4-terminal":
        cmd = [
            term,
            "--disable-server",
            f"--title={TITLE}",
            "--geometry=80x24+40+40",
            "-x",
            "bash",
            "-lc",
            inner,
        ]
    else:
        cmd = [
            term,
            "-T",
            TITLE,
            "-geometry",
            "80x24+40+40",
            "-fn",
            "9x15",
            "-e",
            "bash",
            "-lc",
            inner,
        ]
    proc = rec.spawn(cmd, env={**os.environ, "TERM": "xterm-256color"})
    rec.pid = proc.pid
    time.sleep(0.8)
    rec.attach(timeout=12)
    rec.start_capture()
    rec.hold(4.0)
    rec._stop.set()
    if rec._cap:
        rec._cap.join(timeout=8)
    if rec._ff and rec._ff.poll() is None:
        rec._ff.wait(timeout=20)
    rec.kill_all()
    if not OUT.exists() or OUT.stat().st_size < 8_000:
        die(f"recording failed: {OUT} size={OUT.stat().st_size if OUT.exists() else 0}")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {rec.nframes} frames)")


if __name__ == "__main__":
    main()
