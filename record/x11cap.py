"""X11 window find + BGR grab (XGetImage / XComposite)."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_int,
    c_long,
    c_uint,
    c_ulong,
    c_void_p,
    cast,
)

x11 = ctypes.CDLL("libX11.so.6")
xcomp = ctypes.CDLL("libXcomposite.so.1")
xfixes = ctypes.CDLL("libXfixes.so.3")

ZPixmap = 2
AllPlanes = c_ulong(~0)
CompositeRedirectAutomatic = 0


class XImage(Structure):
    _fields_ = [
        ("width", c_int),
        ("height", c_int),
        ("xoffset", c_int),
        ("format", c_int),
        ("data", c_void_p),
        ("byte_order", c_int),
        ("bitmap_unit", c_int),
        ("bitmap_bit_order", c_int),
        ("bitmap_pad", c_int),
        ("depth", c_int),
        ("bytes_per_line", c_int),
        ("bits_per_pixel", c_int),
        ("red_mask", c_ulong),
        ("green_mask", c_ulong),
        ("blue_mask", c_ulong),
        ("obdata", c_void_p),
        ("f", c_void_p * 6),
    ]


x11.XOpenDisplay.restype = c_void_p
x11.XOpenDisplay.argtypes = [c_char_p]
x11.XCloseDisplay.argtypes = [c_void_p]
x11.XDefaultRootWindow.restype = c_ulong
x11.XDefaultRootWindow.argtypes = [c_void_p]
x11.XQueryTree.restype = c_int
x11.XQueryTree.argtypes = [
    c_void_p,
    c_ulong,
    POINTER(c_ulong),
    POINTER(c_ulong),
    POINTER(POINTER(c_ulong)),
    POINTER(c_uint),
]
x11.XFetchName.restype = c_int
x11.XFetchName.argtypes = [c_void_p, c_ulong, POINTER(c_char_p)]
x11.XGetGeometry.restype = c_int
x11.XGetGeometry.argtypes = [
    c_void_p,
    c_ulong,
    POINTER(c_ulong),
    POINTER(c_int),
    POINTER(c_int),
    POINTER(c_uint),
    POINTER(c_uint),
    POINTER(c_uint),
    POINTER(c_uint),
]
x11.XGetImage.restype = POINTER(XImage)
x11.XGetImage.argtypes = [
    c_void_p,
    c_ulong,
    c_int,
    c_int,
    c_uint,
    c_uint,
    c_ulong,
    c_int,
]
x11.XDestroyImage.restype = c_int
x11.XDestroyImage.argtypes = [POINTER(XImage)]
x11.XFree.argtypes = [c_void_p]
x11.XFreePixmap.argtypes = [c_void_p, c_ulong]
x11.XFlush.argtypes = [c_void_p]
x11.XSync.argtypes = [c_void_p, c_int]
x11.XSetErrorHandler.restype = c_void_p
x11.XSetErrorHandler.argtypes = [c_void_p]
x11.XInternAtom.restype = c_ulong
x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
x11.XGetWindowProperty.restype = c_int
x11.XGetWindowProperty.argtypes = [
    c_void_p,
    c_ulong,
    c_ulong,
    c_long,
    c_long,
    c_int,
    c_ulong,
    POINTER(c_ulong),
    POINTER(c_int),
    POINTER(c_ulong),
    POINTER(c_ulong),
    POINTER(c_void_p),
]
x11.XRaiseWindow.argtypes = [c_void_p, c_ulong]
x11.XMapRaised.argtypes = [c_void_p, c_ulong]
x11.XInitThreads.restype = c_int
x11.XDefaultScreen.restype = c_int
x11.XDefaultScreen.argtypes = [c_void_p]
x11.XDefaultDepth.restype = c_int
x11.XDefaultDepth.argtypes = [c_void_p, c_int]
x11.XCreatePixmap.restype = c_ulong
x11.XCreatePixmap.argtypes = [c_void_p, c_ulong, c_uint, c_uint, c_uint]
x11.XCreateGC.restype = c_void_p
x11.XCreateGC.argtypes = [c_void_p, c_ulong, c_ulong, c_void_p]
x11.XSetSubwindowMode.argtypes = [c_void_p, c_void_p, c_int]
x11.XCopyArea.argtypes = [
    c_void_p,
    c_ulong,
    c_ulong,
    c_void_p,
    c_int,
    c_int,
    c_uint,
    c_uint,
    c_int,
    c_int,
]
x11.XFreeGC.argtypes = [c_void_p, c_void_p]
x11.XTranslateCoordinates.restype = c_int
x11.XTranslateCoordinates.argtypes = [
    c_void_p,
    c_ulong,
    c_ulong,
    c_int,
    c_int,
    POINTER(c_int),
    POINTER(c_int),
    POINTER(c_ulong),
]

xcomp.XCompositeQueryExtension.restype = c_int
xcomp.XCompositeQueryExtension.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
xcomp.XCompositeRedirectWindow.argtypes = [c_void_p, c_ulong, c_int]
xcomp.XCompositeNameWindowPixmap.restype = c_ulong
xcomp.XCompositeNameWindowPixmap.argtypes = [c_void_p, c_ulong]
xfixes.XFixesQueryExtension.restype = c_int
xfixes.XFixesQueryExtension.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
xfixes.XFixesHideCursor.argtypes = [c_void_p, c_ulong]
xfixes.XFixesShowCursor.argtypes = [c_void_p, c_ulong]


@CFUNCTYPE(c_int, c_void_p, c_void_p)
def _xerr(_dpy, _ev):
    return 0


_KEEP_HANDLER = _xerr


def ignore_x_errors() -> None:
    x11.XInitThreads()
    x11.XSetErrorHandler(_KEEP_HANDLER)


def children_of(dpy, win: int) -> list[int]:
    root = c_ulong()
    parent = c_ulong()
    kids = POINTER(c_ulong)()
    n = c_uint()
    if not x11.XQueryTree(dpy, win, byref(root), byref(parent), byref(kids), byref(n)):
        return []
    out = [kids[i] for i in range(n.value)]
    if kids:
        x11.XFree(cast(kids, c_void_p))
    return out


def net_wm_name(dpy, win: int) -> str:
    atom = x11.XInternAtom(dpy, b"_NET_WM_NAME", 0)
    utf8 = x11.XInternAtom(dpy, b"UTF8_STRING", 0)
    actual_type = c_ulong()
    actual_fmt = c_int()
    nitems = c_ulong()
    bytes_after = c_ulong()
    prop = c_void_p()
    status = x11.XGetWindowProperty(
        dpy,
        win,
        atom,
        0,
        1024,
        0,
        utf8,
        byref(actual_type),
        byref(actual_fmt),
        byref(nitems),
        byref(bytes_after),
        byref(prop),
    )
    if status != 0 or not prop:
        return ""
    s = ctypes.string_at(prop, nitems.value).decode("utf-8", "replace")
    x11.XFree(prop)
    return s


def window_name(dpy, win: int) -> str:
    n = net_wm_name(dpy, win)
    if n:
        return n
    name = c_char_p()
    if x11.XFetchName(dpy, win, byref(name)) and name.value:
        s = name.value.decode("utf-8", "replace")
        x11.XFree(name)
        return s
    return ""


def walk_windows(dpy, win: int) -> list[int]:
    out = [win]
    for c in children_of(dpy, win):
        out.extend(walk_windows(dpy, c))
    return out


def geometry(dpy, win: int) -> tuple[int, int]:
    root = c_ulong()
    x = c_int()
    y = c_int()
    w = c_uint()
    h = c_uint()
    bw = c_uint()
    depth = c_uint()
    if not x11.XGetGeometry(
        dpy, win, byref(root), byref(x), byref(y), byref(w), byref(h), byref(bw), byref(depth)
    ):
        return 0, 0
    return int(w.value), int(h.value)


def _pid_ok(dpy, wid: int, pid: int | None) -> bool:
    if not pid:
        return True
    got = window_pid(dpy, wid)
    return got == 0 or got == pid


XA_CARDINAL = 6
_NET_WM_PID = None


def window_pid(dpy, win: int) -> int:
    """_NET_WM_PID, or 0 if the window doesn't publish one."""
    global _NET_WM_PID
    if _NET_WM_PID is None:
        _NET_WM_PID = x11.XInternAtom(dpy, b"_NET_WM_PID", False)
    actual = c_ulong()
    actual_fmt = c_int()
    nitems = c_ulong()
    nbytes = c_ulong()
    prop = c_void_p()
    if x11.XGetWindowProperty(
        dpy, win, _NET_WM_PID, 0, 1, False, XA_CARDINAL,
        byref(actual), byref(actual_fmt), byref(nitems), byref(nbytes), byref(prop),
    ) != 0 or nitems.value < 1 or not prop:
        return 0
    val = int(cast(prop, POINTER(c_ulong))[0])
    x11.XFree(prop)
    return val


def find_window(dpy, title: str, min_w: int = 640, min_h: int = 400, pid: int | None = None) -> int:
    needle = title.lower()
    debug = os.environ.get("RECORD_DEBUG")
    if debug:
        import sys as _sys
        print(f"FIND pid={pid} title={title!r}", file=_sys.stderr)
    try:
        out = subprocess.check_output(
            ["xwininfo", "-root", "-tree"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        out = ""
    for line in out.splitlines():
        tok = line.strip().split()[0] if line.strip() else ""
        if not tok.startswith("0x"):
            continue
        wid = int(tok, 16)
        # the tree line quotes the window's own name; don't match a child's line
        # that merely sits under a parent titled Arcade Coder
        quoted = f'"{title}"' in line
        named = needle in window_name(dpy, wid).lower()
        if not quoted and not named:
            continue
        w, h = geometry(dpy, wid)
        ok = _pid_ok(dpy, wid, pid)
        if debug and (needle in line.lower() or named):
            print(f"  cand {wid:#x} {w}x{h} quoted={quoted} named={named} pidok={ok} line={line.strip()[:100]}", file=_sys.stderr)
        if w >= min_w and h >= min_h and ok:
            if debug:
                print(f"  PICK tree {wid:#x}", file=_sys.stderr)
            return wid
    try:
        out = subprocess.check_output(
            ["xwininfo", "-name", title],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        m = re.search(r"Window id:\s+(0x[0-9a-fA-F]+)", out)
        if m:
            wid = int(m.group(1), 16)
            if _pid_ok(dpy, wid, pid):
                return wid
    except subprocess.CalledProcessError:
        pass
    root = x11.XDefaultRootWindow(dpy)
    for win in walk_windows(dpy, root):
        name = window_name(dpy, win).lower()
        if needle not in name:
            continue
        w, h = geometry(dpy, win)
        if w >= min_w and h >= min_h and _pid_ok(dpy, win, pid):
            return win
    return 0


IncludeInferiors = 1


def _image_to_bgr(img_p, w: int, h: int) -> bytes | None:
    if not img_p:
        return None
    img = img_p.contents
    if img.bits_per_pixel != 32 or not img.data:
        x11.XDestroyImage(img_p)
        return None
    raw = ctypes.string_at(img.data, img.bytes_per_line * h)
    stride = img.bytes_per_line
    x11.XDestroyImage(img_p)
    row = w * 4
    if stride == row:
        return raw
    packed = bytearray(h * row)
    for y in range(h):
        packed[y * row : (y + 1) * row] = raw[y * stride : y * stride + row]
    return bytes(packed)


def grab_bgr(dpy, win: int, w: int, h: int, redirected: bool) -> bytes | None:
    drawable = win
    pix = 0
    if redirected:
        pix = xcomp.XCompositeNameWindowPixmap(dpy, win)
        if pix:
            drawable = pix
    img_p = x11.XGetImage(dpy, drawable, 0, 0, w, h, AllPlanes, ZPixmap)
    if pix:
        x11.XFreePixmap(dpy, pix)
    return _image_to_bgr(img_p, w, h)


def grab_tree_bgr(dpy, win: int, w: int, h: int) -> bytes | None:
    """Copy the window *and* its children, then read the pixmap.

    XGetImage on a parent is the parent's own pixels. xterm draws the
    glyphs on the VT child, so a parent grab is a solid title-bar colour.
    """
    screen = x11.XDefaultScreen(dpy)
    depth = x11.XDefaultDepth(dpy, screen)
    pix = x11.XCreatePixmap(dpy, win, w, h, depth)
    if not pix:
        return None
    gc = x11.XCreateGC(dpy, win, 0, None)
    if not gc:
        x11.XFreePixmap(dpy, pix)
        return None
    x11.XSetSubwindowMode(dpy, gc, IncludeInferiors)
    x11.XCopyArea(dpy, win, pix, gc, 0, 0, w, h, 0, 0)
    x11.XSync(dpy, 0)
    img_p = x11.XGetImage(dpy, pix, 0, 0, w, h, AllPlanes, ZPixmap)
    x11.XFreeGC(dpy, gc)
    x11.XFreePixmap(dpy, pix)
    return _image_to_bgr(img_p, w, h)


def client_of(dpy, win: int) -> int:
    """Largest child that fills most of the window (client vs WM frame)."""
    w, h = geometry(dpy, win)
    if w < 2 or h < 2:
        return win
    best = win
    best_area = 0
    for c in children_of(dpy, win):
        cw, ch = geometry(dpy, c)
        area = cw * ch
        if area > best_area and cw >= int(w * 0.6) and ch >= int(h * 0.6):
            best = c
            best_area = area
    if best != win:
        return client_of(dpy, best)
    return win


def grab_root_bgr(dpy, win: int, w: int, h: int) -> bytes | None:
    """What is on the screen at this window. Last resort for Xft/xterm."""
    root = x11.XDefaultRootWindow(dpy)
    rx = c_int()
    ry = c_int()
    child = c_ulong()
    x11.XTranslateCoordinates(dpy, win, root, 0, 0, byref(rx), byref(ry), byref(child))
    img_p = x11.XGetImage(dpy, root, int(rx.value), int(ry.value), w, h, AllPlanes, ZPixmap)
    return _image_to_bgr(img_p, w, h)


def roughly_blank(frame: bytes | None) -> bool:
    """True when the grab is a solid fill (title-bar colour, empty pixmap)."""
    if not frame or len(frame) < 12:
        return True
    bg = (frame[0], frame[1], frame[2])
    step = max(16, (len(frame) // 4000) & ~3)
    other = 0
    n = 0
    for i in range(0, len(frame) - 2, step):
        n += 1
        pix = (frame[i], frame[i + 1], frame[i + 2])
        if abs(pix[0] - bg[0]) + abs(pix[1] - bg[1]) + abs(pix[2] - bg[2]) > 40:
            other += 1
    return n == 0 or other / n < 0.004


def grab_visible(dpy, win: int, w: int, h: int, redirected: bool = False) -> bytes | None:
    """Best-effort pixels for a native window, including xterm."""
    tree = grab_tree_bgr(dpy, win, w, h)
    if tree is not None and not roughly_blank(tree):
        return tree
    client = client_of(dpy, win)
    if client != win:
        cw, ch = geometry(dpy, client)
        if cw == w and ch == h:
            inner = grab_tree_bgr(dpy, client, w, h)
            if inner is not None and not roughly_blank(inner):
                return inner
            inner = grab_bgr(dpy, client, w, h, redirected)
            if inner is not None and not roughly_blank(inner):
                return inner
    direct = grab_bgr(dpy, win, w, h, redirected)
    if direct is not None and not roughly_blank(direct):
        return direct
    root = grab_root_bgr(dpy, win, w, h)
    if root is not None and not roughly_blank(root):
        return root
    return tree or direct or root


def hide_cursor(dpy) -> bool:
    evb = c_int()
    erb = c_int()
    if not xfixes.XFixesQueryExtension(dpy, byref(evb), byref(erb)):
        return False
    xfixes.XFixesHideCursor(dpy, x11.XDefaultRootWindow(dpy))
    x11.XFlush(dpy)
    return True


def redirect_and_hide_cursor(dpy, wid: int) -> tuple[bool, bool]:
    ev = c_int()
    er = c_int()
    redirected = bool(xcomp.XCompositeQueryExtension(dpy, byref(ev), byref(er)))
    if redirected:
        xcomp.XCompositeRedirectWindow(dpy, wid, CompositeRedirectAutomatic)
        x11.XSync(dpy, 0)
    hidden = hide_cursor(dpy)
    x11.XMapRaised(dpy, wid)
    x11.XRaiseWindow(dpy, wid)
    x11.XFlush(dpy)
    return redirected, hidden


def show_cursor(dpy) -> None:
    try:
        xfixes.XFixesShowCursor(dpy, x11.XDefaultRootWindow(dpy))
        x11.XFlush(dpy)
    except Exception:
        pass
