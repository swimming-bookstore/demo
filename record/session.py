"""Record a live Chromium window to mp4 while driving it over CDP."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import x11cap
from .cdp import Ws, http_get, js, wait_tcp
from .util import die, ffmpeg_bin

os.environ.setdefault("DISPLAY", ":0.0")
os.environ.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))


class RecordSession:
    def __init__(
        self,
        url: str,
        out: Path,
        title: str = "demo-window",
        *,
        cdp_port: int = 9225,
        fps: int = 15,
        width: int = 1280,
        height: int = 800,
        max_sec: float = 180.0,
        user_data_dir: Path | None = None,
        wm_class: str = "DemoRecorder",
    ) -> None:
        self.url = url
        self.out = Path(out)
        self.title = title
        self.cdp_port = cdp_port
        self.fps = fps
        self.width = width
        self.height = height
        self.max_sec = max_sec
        self.user_data_dir = user_data_dir or Path("/tmp") / f"{title}-chrome"
        self.wm_class = wm_class
        self.children: list[subprocess.Popen] = []
        self.ws: Ws | None = None
        self._dpy = None
        self._wid = 0
        self._w = 0
        self._h = 0
        self._redirected = False
        self._hidden = False
        self._stop = threading.Event()
        self._ff: subprocess.Popen | None = None
        self._cap: threading.Thread | None = None
        self.nframes = 0

    def spawn(self, args: list[str], **kw) -> subprocess.Popen:
        kw.setdefault("stdin", subprocess.DEVNULL)
        p = subprocess.Popen(args, **kw)
        self.children.append(p)
        return p

    def kill_all(self) -> None:
        for p in reversed(self.children):
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        time.sleep(0.2)
        for p in reversed(self.children):
            if p.poll() is None:
                p.kill()

    def attach_chrome(self) -> Ws:
        chrome = shutil.which("chromium") or shutil.which("google-chrome") or os.environ.get("CHROME")
        if not chrome:
            die("need chromium")
        udd = self.user_data_dir
        if udd.exists():
            shutil.rmtree(udd, ignore_errors=True)
        udd.mkdir(parents=True)
        (udd / "Default").mkdir(parents=True, exist_ok=True)
        (udd / "Default" / "Preferences").write_text(
            json.dumps(
                {
                    "credentials_enable_service": False,
                    "profile": {"password_manager_enabled": False},
                    "signin": {"allowed_on_next_startup": False},
                    "autofill": {"profile_enabled": False, "credit_card_enabled": False},
                }
            )
        )
        self.spawn(
            [
                chrome,
                "--ozone-platform=x11",
                f"--class={self.wm_class}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-infobars",
                "--disable-session-crashed-bubble",
                "--disable-translate",
                "--disable-features=TranslateUI,InfiniteSessionRestore",
                "--no-sandbox",
                # Suppresses the "unsupported command-line flag: --no-sandbox" infobar.
                "--test-type",
                f"--remote-debugging-port={self.cdp_port}",
                f"--user-data-dir={udd}",
                f"--window-size={self.width},{self.height}",
                "--window-position=40,40",
                f"--app={self.url}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")},
        )
        wait_tcp("127.0.0.1", self.cdp_port, 20)
        page = None
        deadline = time.time() + 12
        while time.time() < deadline and page is None:
            _, body = http_get(f"http://127.0.0.1:{self.cdp_port}/json/list")
            tabs = json.loads(body.decode())
            for t in tabs:
                if t.get("type") == "page":
                    page = t
                    break
            time.sleep(0.2)
        if not page:
            die("no CDP page")
        ws = Ws(page["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        ws.call("Page.bringToFront")
        try:
            win = ws.call("Browser.getWindowForTarget")
            wid = win.get("windowId")
            if wid is not None:
                ws.call(
                    "Browser.setWindowBounds",
                    {
                        "windowId": wid,
                        "bounds": {
                            "left": 40,
                            "top": 40,
                            "width": self.width,
                            "height": self.height,
                            "windowState": "normal",
                        },
                    },
                )
        except Exception as e:
            print(f"  window bounds: {e}")
        self._set_title(ws)
        time.sleep(0.4)
        self._set_title(ws)
        time.sleep(0.5)
        self.ws = ws
        return ws

    def _set_title(self, ws: Ws) -> None:
        js(ws, f"document.title = {json.dumps(self.title)}")

    def start_capture(self) -> None:
        x11cap.ignore_x_errors()
        dpy = x11cap.x11.XOpenDisplay(None)
        if not dpy:
            die("cannot open X display")
        self._dpy = dpy
        wid = 0
        for _ in range(80):
            wid = x11cap.find_window(dpy, self.title)
            if not wid and self.wm_class:
                wid = x11cap.find_by_class(dpy, self.wm_class)
            if wid:
                break
            time.sleep(0.15)
        if not wid:
            die("demo window not found")
        self._wid = wid
        # Chromium --app names the window after the page, not --title, and this
        # display's tree listing keeps a dead id that still matches old titles.
        # The --class flag is unique per recording; capture that window.
        if self.wm_class:
            found = x11cap.find_by_class(dpy, self.wm_class)
            if found:
                wid = found
                self._wid = wid
        redirected, hidden = x11cap.redirect_and_hide_cursor(dpy, wid)
        self._redirected = redirected
        self._hidden = hidden
        w, h = x11cap.geometry(dpy, wid)
        for _ in range(40):
            if w >= 640 and h >= 400:
                break
            time.sleep(0.1)
            w, h = x11cap.geometry(dpy, wid)
        if w < 640 or h < 400:
            die(f"bad window size {w}x{h}")
        w -= w % 2
        h -= h % 2
        self._w, self._h = w, h
        print(f"capturing {w}x{h} window {hex(wid)}", file=sys.stderr)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        ff = subprocess.Popen(
            [
                ffmpeg_bin(),
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr0",
                "-s",
                f"{w}x{h}",
                "-r",
                str(self.fps),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-movflags",
                "+faststart",
                str(self.out),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(ff)
        self._ff = ff
        assert ff.stdin is not None

        def pump() -> None:
            last = None
            tick = 1.0 / self.fps
            t0 = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - t0 > self.max_sec:
                    break
                frame = x11cap.grab_bgr(dpy, wid, w, h, redirected)
                if frame is None:
                    frame = last
                if frame is None:
                    time.sleep(tick)
                    continue
                last = frame
                try:
                    ff.stdin.write(frame)
                except BrokenPipeError:
                    break
                self.nframes += 1
                time.sleep(tick)
            try:
                ff.stdin.close()
            except OSError:
                pass

        self._cap = threading.Thread(target=pump, daemon=True)
        self._cap.start()

    def hold(self, secs: float) -> None:
        time.sleep(secs)

    def eval(self, expr: str) -> object:
        assert self.ws
        return js(self.ws, expr)

    def wait_js(self, expr: str, secs: float = 20, label: str = "") -> object:
        deadline = time.time() + secs
        last = None
        while time.time() < deadline:
            last = self.eval(expr)
            if last:
                return last
            time.sleep(0.2)
        die(f"timeout {label or expr} last={last!r}")
        return last

    def click_sel(self, selector: str) -> None:
        got = self.eval(
            f"""(() => {{
              const el = document.querySelector({json.dumps(selector)});
              if (!el) return null;
              el.scrollIntoView({{block: 'center'}});
              const r = el.getBoundingClientRect();
              return {{x: r.left + r.width / 2, y: r.top + r.height / 2}};
            }})()"""
        )
        if not isinstance(got, dict):
            die(f"no {selector}")
        x, y = float(got["x"]), float(got["y"])
        assert self.ws
        for typ in ("mousePressed", "mouseReleased"):
            self.ws.call(
                "Input.dispatchMouseEvent",
                {"type": typ, "x": x, "y": y, "button": "left", "clickCount": 1},
            )
        time.sleep(0.15)

    def click_text(self, text: str, selector: str = "a, button") -> None:
        got = self.eval(
            f"""(() => {{
              const want = {json.dumps(text)};
              const el = [...document.querySelectorAll({json.dumps(selector)})]
                .find(x => (x.textContent || '').trim() === want);
              if (!el) return null;
              el.scrollIntoView({{block: 'center'}});
              el.click();
              const r = el.getBoundingClientRect();
              return {{x: r.left + r.width / 2, y: r.top + r.height / 2, t: (el.textContent || '').trim()}};
            }})()"""
        )
        print(f"  click_text {text!r} -> {got!r}")
        if not isinstance(got, dict):
            die(f"text {text!r} not found")
        time.sleep(0.2)

    def type_into(self, selector: str, text: str, clear: bool = True, delay: float = 0.06) -> None:
        self.click_sel(selector)
        if clear:
            self.eval(
                f"""(() => {{
                  const i = document.querySelector({json.dumps(selector)});
                  if (!i) return;
                  i.focus();
                  const proto = Object.getOwnPropertyDescriptor(
                    i.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype,
                    'value'
                  );
                  proto.set.call(i, '');
                  i.dispatchEvent(new Event('input', {{bubbles: true}}));
                }})()"""
            )
        for ch in text:
            self.eval(
                f"""(() => {{
                  const i = document.querySelector({json.dumps(selector)});
                  if (!i) return;
                  const proto = Object.getOwnPropertyDescriptor(
                    i.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype,
                    'value'
                  );
                  proto.set.call(i, (i.value || '') + {json.dumps(ch)});
                  i.dispatchEvent(new Event('input', {{bubbles: true}}));
                }})()"""
            )
            time.sleep(delay)
        time.sleep(0.15)

    def goto(self, path: str) -> None:
        self.eval(f"window.location.hash = {json.dumps(path)}; true")
        time.sleep(0.3)

    def __enter__(self) -> "RecordSession":
        self.attach_chrome()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._cap:
            self._cap.join(timeout=5)
        if self._hidden and self._dpy:
            x11cap.show_cursor(self._dpy)
        if self.ws:
            self.ws.close()
        time.sleep(0.2)
        self.kill_all()
        if self._ff:
            self._ff.wait(timeout=20)
        if self._dpy:
            x11cap.x11.XCloseDisplay(self._dpy)
        print(self.out, self.out.stat().st_size if self.out.exists() else 0, "frames", self.nframes)
