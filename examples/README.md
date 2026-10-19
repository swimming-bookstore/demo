# examples

`record/` without an app.

```bash
PYTHONPATH=$HOME/demo python3 ~/demo/examples/terminal.py
PYTHONPATH=$HOME/demo python3 ~/demo/examples/chrome.py
PYTHONPATH=$HOME/demo python3 ~/demo/examples/gtk.py
```

- `terminal.py` — `WindowRecord` on xfce4-terminal printing `hello world`. Prefer a GTK terminal: xterm Xft often never lands in a window `XGetImage`. `grab_window` still copies children and fills the holes.
- `chrome.py` — `RecordSession` on `page.html`. CDP clicks the button. The pixels are still the live window.
- `gtk.py` — GTK 4 window. `find_window` may return the WM frame; `x11cap.client_of(dpy, frame)` walks to the real client. `WindowRecord` records the titled window.

Output is `/tmp/demo-*-example.mp4`. Do not commit the movies.
