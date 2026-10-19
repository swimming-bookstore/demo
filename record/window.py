"""Record a live native X11 window to mp4 (no Chromium).

Pixels come from grab_window: the window and its children, with pixmap holes
filled from the screen pixels of that window. Input is XTest so the app sees
real pointer motion — useful when the UI follows the mouse.
"""

from __future__ import annotations

import math
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from ctypes import CDLL, c_char_p, c_int, c_uint, c_ulong, c_void_p
from pathlib import Path

from . import x11cap
from .util import die, ffmpeg_bin

os.environ.setdefault("DISPLAY", ":0.0")
os.environ.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))

xtst = CDLL("libXtst.so.6")
xtst.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
xtst.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]

x11cap.x11.XStringToKeysym.restype = c_ulong
x11cap.x11.XStringToKeysym.argtypes = [c_char_p]
x11cap.x11.XKeysymToKeycode.restype = c_uint
x11cap.x11.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
x11cap.x11.XSetInputFocus.argtypes = [c_void_p, c_ulong, c_int, c_ulong]
x11cap.x11.XWarpPointer.argtypes = [
    c_void_p,
    c_ulong,
    c_ulong,
    c_int,
    c_int,
    c_uint,
    c_uint,
    c_int,
    c_int,
]


def swap_rb(frame: bytes) -> bytes:
    """This display stores RGB in a buffer ffmpeg is told is BGR."""
    out = bytearray(frame)
    out[0::4], out[2::4] = out[2::4], out[0::4]
    return bytes(out)


def put_pixel(frame: bytearray, w: int, h: int, x: int, y: int, bgr: tuple[int, int, int]) -> None:
    if 0 <= x < w and 0 <= y < h:
        i = (y * w + x) * 4
        frame[i] = bgr[0]
        frame[i + 1] = bgr[1]
        frame[i + 2] = bgr[2]


def overlay_pointer(frame: bytes, w: int, h: int, x: int, y: int) -> bytes:
    """White-outline arrow. Frame is BGR0, so cream fill is B,G,R."""
    out = bytearray(frame)
    white = (255, 255, 255)
    cream = (125, 207, 239)  # RGB 239,207,125
    for dy in range(18):
        span = 1 if dy < 2 else dy // 2 + 1
        for dx in range(-1, span + 1):
            edge = dx < 0 or dx == span or dy in (0, 17)
            color = white if edge else cream
            put_pixel(out, w, h, x + dx, y + dy, color)
            put_pixel(out, w, h, x + dx, y + dy + 1, color)
    return bytes(out)


def grab_xwd(wid: int, w: int, h: int) -> bytes | None:
    """Read the window's own pixmap. Root grabs miss depth-32 terminals."""
    try:
        raw = subprocess.check_output(
            ["xwd", "-id", hex(wid), "-silent"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if len(raw) < 100:
        return None
    hs = struct.unpack(">I", raw[:4])[0]
    n = hs // 4
    if len(raw) < n * 4:
        return None
    fields = struct.unpack(">" + "I" * n, raw[: n * 4])
    if len(fields) < 20:
        return None
    src_w, src_h = fields[4], fields[5]
    bpl, ncolors = fields[12], fields[19]
    off = hs + ncolors * 12
    need = bpl * src_h
    if src_w < 2 or src_h < 2 or len(raw) < off + need:
        return None
    pix = raw[off : off + need]
    # XWD is BGRA, top-left origin. ffmpeg wants tightly packed bgr0.
    out = bytearray(w * h * 4)
    copy_w = min(w, src_w)
    copy_h = min(h, src_h)
    for y in range(copy_h):
        src = y * bpl
        dst = y * w * 4
        row = pix[src : src + copy_w * 4]
        # drop alpha: keep B,G,R and write 0
        for x in range(copy_w):
            i = x * 4
            out[dst + i] = row[i]
            out[dst + i + 1] = row[i + 1]
            out[dst + i + 2] = row[i + 2]
    return bytes(out)


class WindowRecord:
    """Capture `title` with XGetImage while driving the pointer with XTest."""

    # Optional threading.Event. Arcade sets this so ffmpeg stays quiet until
    # the first half of the prompt is typed (clip starts mid-sentence).
    _capture_gate = None

    def __init__(
        self,
        title: str,
        out: Path,
        *,
        fps: int = 15,
        max_sec: float = 60.0,
        min_w: int = 640,
        min_h: int = 360,
        hide_cursor: bool = True,
        overlay_pointer: bool = True,
        pid: int | None = None,
        swap_rb: bool = False,
    ) -> None:
        self.title = title
        self.out = Path(out)
        self.fps = fps
        self.max_sec = max_sec
        self.min_w = min_w
        self.min_h = min_h
        self.hide_cursor = hide_cursor
        self.overlay_pointer = overlay_pointer
        self.pid = pid
        self.swap_rb = swap_rb
        self.children: list[subprocess.Popen] = []
        self._dpy = None
        self._cap_dpy = None
        self._wid = 0
        self._w = 0
        self._h = 0
        self._redirected = False
        self._hidden = False
        self._stop = threading.Event()
        self._ff: subprocess.Popen | None = None
        self._cap: threading.Thread | None = None
        self.nframes = 0
        self.mx = 0
        self.my = 0
        self._ptr: tuple[int, int] | None = None

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

    def attach(self, timeout: float = 12.0) -> None:
        x11cap.ignore_x_errors()
        dpy = x11cap.x11.XOpenDisplay(b":0.0")
        if not dpy:
            die("cannot open X display")
        self._dpy = dpy
        deadline = time.time() + timeout
        wid = 0
        while time.time() < deadline:
            wid = x11cap.find_window(dpy, self.title, self.min_w, self.min_h, self.pid)
            if wid:
                break
            time.sleep(0.15)
        if not wid:
            die(f"window {self.title!r} not found")
        self._wid = wid
        # Capture the titled window. grab_visible copies children too.
        print(f"capturing pid {self.pid} window {wid:#x}", file=sys.stderr)
        x11cap.x11.XRaiseWindow(dpy, self._wid)
        x11cap.x11.XFlush(dpy)
        self._redirected = False
        self._hidden = False
        if self.hide_cursor:
            # Do not Composite-redirect. An xterm pixmap is often a solid
            # background; grab_visible copies children / the root instead.
            self._hidden = x11cap.hide_cursor(dpy)
        x11cap.x11.XMapRaised(dpy, wid)
        w, h = x11cap.geometry(dpy, wid)
        for _ in range(40):
            if w >= self.min_w and h >= self.min_h:
                break
            time.sleep(0.1)
            w, h = x11cap.geometry(dpy, wid)
        if w < self.min_w or h < self.min_h:
            die(f"bad window size {w}x{h}")
        w -= w % 2
        h -= h % 2
        # GTK client-side shadows record as a black margin. Remember the
        # extents and crop pixels in the capture loop. The X window size
        # stays the full frame so the grab origin matches.
        self._crop = (0, 0, 0, 0)  # left, top, right, bottom
        try:
            ext = subprocess.check_output(
                ["xprop", "-id", hex(wid), "_GTK_FRAME_EXTENTS"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            if "=" in ext:
                left, right, top, bottom = [int(p.strip()) for p in ext.split("=", 1)[1].split(",")]
                if (
                    left >= 0
                    and top >= 0
                    and right >= 0
                    and bottom >= 0
                    and left + right < w - 80
                    and top + bottom < h - 80
                ):
                    self._crop = (left, top, right, bottom)
                    cw = (w - left - right) & ~1
                    ch = (h - top - bottom) & ~1
                    print(f"cropping CSD shadow l{left} t{top} r{right} b{bottom} -> {cw}x{ch}", file=sys.stderr)
                    w, h = cw, ch
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
        self._w, self._h = w, h
        x11cap.x11.XSetInputFocus(dpy, wid, 2, 0)
        x11cap.x11.XFlush(dpy)
        print(f"capturing {w}x{h} window {hex(wid)}", file=sys.stderr)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            frame = x11cap.grab_visible(dpy, wid, w, h, False)
            if frame and not x11cap.roughly_blank(frame):
                break
            time.sleep(0.1)
        else:
            print("warning: first frames may be blank", file=sys.stderr)

    def window_id(self) -> str:
        return hex(self._wid)

    def start_capture(self) -> None:
        dpy, wid, w, h = self._dpy, self._wid, self._w, self._h
        redirected = self._redirected
        self.out.parent.mkdir(parents=True, exist_ok=True)
        # A maximized window is too wide to watch. Scale the encode, not the grab,
        # so the clip is still the whole window.
        scale = getattr(self, "scale", None)
        vf = []
        if scale:
            vf = ["-vf", f"scale={scale}:flags=lanczos"]
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
                *vf,
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
            bufsize=0,
        )
        self.children.append(ff)
        self._ff = ff
        assert ff.stdin is not None
        cap_dpy = x11cap.x11.XOpenDisplay(b":0.0")
        if not cap_dpy:
            die("cannot open capture display")
        self._cap_dpy = cap_dpy

        def pump() -> None:
            import struct
            import tempfile

            last = None
            tick = 1.0 / self.fps
            t0 = time.monotonic()
            while not self._stop.is_set():
                gate = WindowRecord._capture_gate
                if gate is not None and not gate.is_set():
                    time.sleep(0.05)
                    t0 = time.monotonic()
                    continue
                if time.monotonic() - t0 > self.max_sec:
                    break
                # grab_window copies children and fills pixmap holes from the
                # screen pixels of this window. Crop the client-side shadow,
                # which records as a black margin, without shrinking the grab.
                full_w, full_h = x11cap.geometry(cap_dpy, wid)
                full_w -= full_w % 2
                full_h -= full_h % 2
                frame = x11cap.grab_window(cap_dpy, wid, full_w, full_h, redirected)
                left, top, right, bottom = self._crop
                if frame is not None and len(frame) >= full_w * full_h * 4 and (left or top or right or bottom):
                    cw = (full_w - left - right) & ~1
                    ch = (full_h - top - bottom) & ~1
                    if cw == w and ch == h:
                        row = cw * 4
                        packed = bytearray(ch * row)
                        for y in range(ch):
                            src = ((y + top) * full_w + left) * 4
                            packed[y * row : (y + 1) * row] = frame[src : src + row]
                        frame = bytes(packed)
                if frame is not None and len(frame) != w * h * 4:
                    frame = None
                if frame is None:
                    frame = last
                if frame is None:
                    time.sleep(tick)
                    continue
                if getattr(self, "swap_rb", False):
                    frame = swap_rb(frame)
                last = frame
                ptr = self._ptr
                shown = overlay_pointer(frame, w, h, *ptr) if (self.overlay_pointer and ptr) else frame
                try:
                    ff.stdin.write(shown)
                except Exception as exc:
                    print(f"ffmpeg write failed: {type(exc).__name__} {exc}", file=sys.stderr)
                    break
                self.nframes += 1
                time.sleep(tick)
            try:
                ff.stdin.close()
            except OSError:
                pass
            print(f"pump exit frames={self.nframes} stop={self._stop.is_set()}", file=sys.stderr)

        self._cap = threading.Thread(target=pump, daemon=True)
        self._cap.start()

    def hold(self, secs: float) -> None:
        time.sleep(secs)

    def move(self, x: float, y: float) -> None:
        """Window-local pointer. Warp inside the captured window so LÖVE and the overlay agree."""
        dpy = self._dpy
        x = max(2, min(self._w - 3, x))
        y = max(2, min(self._h - 3, y))
        ix, iy = int(x), int(y)
        x11cap.x11.XWarpPointer(dpy, 0, self._wid, 0, 0, 0, 0, ix, iy)
        x11cap.x11.XFlush(dpy)
        self.mx, self.my = float(ix), float(iy)
        self._ptr = (ix, iy)

    def glide(self, x1: float, y1: float, secs: float, steps: int | None = None) -> None:
        x0, y0 = self.mx, self.my
        n = steps or max(8, int(secs * 40))
        for i in range(1, n + 1):
            t = i / n
            k = 0.5 - 0.5 * math.cos(math.pi * t)
            self.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
            time.sleep(secs / n)

    def click(self, x: float | None = None, y: float | None = None) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        dpy = self._dpy
        xtst.XTestFakeButtonEvent(dpy, 1, 1, 0)
        xtst.XTestFakeButtonEvent(dpy, 1, 0, 0)
        x11cap.x11.XFlush(dpy)
        time.sleep(0.05)

    def focus(self) -> None:
        dpy = self._dpy
        x11cap.x11.XSetInputFocus(dpy, self._wid, 2, 0)
        x11cap.x11.XFlush(dpy)

    def key(self, name: str) -> None:
        dpy = self._dpy
        ks = x11cap.x11.XStringToKeysym(name.encode() if isinstance(name, str) else name)
        kc = x11cap.x11.XKeysymToKeycode(dpy, ks)
        if not kc:
            return
        xtst.XTestFakeKeyEvent(dpy, kc, 1, 0)
        xtst.XTestFakeKeyEvent(dpy, kc, 0, 0)
        x11cap.x11.XFlush(dpy)

    def type_char(self, ch: str) -> None:
        dpy = self._dpy
        if ch == " ":
            self.key("space")
            return
        if ch == "\n":
            self.key("Return")
            return
        if ch == "\t":
            self.key("Tab")
            return
        shifted = {
            "_": "minus",
            '"': "quotedbl",
            ":": "colon",
            "+": "plus",
            "!": "exclam",
            "?": "question",
            "(": "parenleft",
            ")": "parenright",
        }
        plain = {
            "-": "minus",
            ".": "period",
            "/": "slash",
            ",": "comma",
            "=": "equal",
            ";": "semicolon",
            "'": "apostrophe",
        }
        name = shifted.get(ch) or (ch.lower() if ch.isupper() else plain.get(ch, ch))
        ks = x11cap.x11.XStringToKeysym(name.encode())
        kc = x11cap.x11.XKeysymToKeycode(dpy, ks)
        if not kc:
            return
        need_shift = ch.isupper() or ch in shifted
        shift_kc = x11cap.x11.XKeysymToKeycode(dpy, x11cap.x11.XStringToKeysym(b"Shift_L"))
        if need_shift:
            xtst.XTestFakeKeyEvent(dpy, shift_kc, 1, 0)
        xtst.XTestFakeKeyEvent(dpy, kc, 1, 0)
        xtst.XTestFakeKeyEvent(dpy, kc, 0, 0)
        if need_shift:
            xtst.XTestFakeKeyEvent(dpy, shift_kc, 0, 0)
        x11cap.x11.XFlush(dpy)

    def type_text(self, text: str, delay: float = 0.04) -> None:
        for ch in text:
            if ord(ch) < 32 and ch not in "\n\t":
                continue
            if ord(ch) > 126:
                continue
            self.type_char(ch)
            time.sleep(delay)

    def __enter__(self) -> "WindowRecord":
        self.attach()
        self.start_capture()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._cap:
            self._cap.join(timeout=5)
        if self._hidden and self._dpy:
            x11cap.show_cursor(self._dpy)
        time.sleep(0.2)
        self.kill_all()
        if self._ff:
            try:
                self._ff.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._ff.kill()
        if self._cap_dpy:
            x11cap.x11.XCloseDisplay(self._cap_dpy)
            self._cap_dpy = None
        if self._dpy:
            x11cap.x11.XCloseDisplay(self._dpy)
            self._dpy = None
        print(self.out, self.out.stat().st_size if self.out.exists() else 0, "frames", self.nframes)
