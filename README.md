# demo

Python framework for product demo videos.

- `RecordSession` — Chromium + CDP.
- `WindowRecord` — native X11 window (GTK, xfce4-terminal, …) with XTest mouse. `grab_window` copies the window and its children, then fills pixmap holes from the screen pixels of that window. A parent `XGetImage` misses xterm's VT and LibreOffice's grid; the root grab misses those same children and includes the desktop. Prefer a GTK terminal when recording a shell.

## How it records

- Drive the live UI (CDP for Chromium, PTY/XTest for terminals).
- **Never** use the driver as the pixels. Capture the real window with `grab_window` (children via `XCopyArea` IncludeInferiors, holes filled from the screen pixels of that window) and pipe BGR frames to `ffmpeg` → `docs/demo.mp4`.
- Hide the cursor (`XFixesHideCursor`). Do not Composite-redirect a terminal: the pixmap is often a solid background. Fixed window title so `xwininfo` can find it.

```python
from pathlib import Path
from record.session import RecordSession

with RecordSession(
    url="http://127.0.0.1:8080/",
    out=Path("docs/demo.mp4"),
    title="my-app-demo",
) as rec:
    rec.hold(1.5)
    rec.click_text("Take the challenge")
    rec.hold(2)
```

Put `PYTHONPATH=$HOME/demo` when you run an app’s `scripts/record-demo.py`.

```bash
PYTHONPATH=$HOME/demo python3 ~/demo/examples/terminal.py
PYTHONPATH=$HOME/demo python3 ~/demo/examples/chrome.py
PYTHONPATH=$HOME/demo python3 ~/demo/examples/gtk.py
```

See `examples/README.md`.
