# demo

Python framework for product demo videos.

- `RecordSession` — Chromium + CDP.
- `WindowRecord` — native X11 window (GTK, xfce4-terminal, …) with XTest mouse. xterm's Xft glyphs often never appear in `XGetImage`; `grab_visible` copies children and falls back to the root. Prefer a GTK terminal when recording a shell.

## How it records

- Drive the live UI (CDP for Chromium, PTY/XTest for terminals).
- **Never** use the driver as the pixels. Capture the real window with `XGetImage` (children via `XCopyArea` IncludeInferiors, then the window, then the root) and pipe BGR frames to `ffmpeg` → `docs/demo.mp4`.
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
