#!/usr/bin/env python3
"""Record a GTK window.

xwininfo / find_window often return the WM frame. client_of walks to the
largest child that fills most of that frame — the real client.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DISPLAY", ":0.0")
os.environ.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from record import x11cap
from record.util import die
from record.window import WindowRecord

OUT = Path("/tmp/demo-gtk-example.mp4")
TITLE = "demo-gtk-example"


def run_window() -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    win = Gtk.Window(title=TITLE)
    win.set_default_size(520, 300)
    win.set_child(Gtk.Label(label="hello world"))
    win.present()
    loop = GLib.MainLoop()
    win.connect("close-request", lambda *_: (loop.quit(), False)[1])
    GLib.timeout_add_seconds(12, loop.quit)
    loop.run()


def main() -> None:
    if "--window" in sys.argv:
        run_window()
        return

    rec = WindowRecord(TITLE, OUT, fps=12, max_sec=16.0, min_w=400, min_h=200, overlay_pointer=False)
    proc = rec.spawn([sys.executable, str(Path(__file__).resolve()), "--window"])
    rec.pid = proc.pid
    time.sleep(0.6)
    rec.attach(timeout=12)

    dpy, frame = rec._dpy, rec._wid
    client = x11cap.client_of(dpy, frame)
    fw, fh = x11cap.geometry(dpy, frame)
    cw, ch = x11cap.geometry(dpy, client)
    print(f"frame {frame:#x} {fw}x{fh} -> client {client:#x} {cw}x{ch}", file=sys.stderr)

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
