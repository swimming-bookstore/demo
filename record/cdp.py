"""Tiny Chrome DevTools Protocol client (stdlib websocket)."""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def http_get(url: str, timeout: float = 8) -> tuple[int, bytes]:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except HTTPError as e:
        return e.code, e.read()


def port_up(host: str, port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_tcp(host: str, port: int, secs: float = 20) -> None:
    deadline = time.time() + secs
    while time.time() < deadline:
        if port_up(host, port):
            return
        time.sleep(0.15)
    raise TimeoutError(f"timeout waiting {host}:{port}")


class Ws:
    def __init__(self, url: str):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=10)
        self.sock.settimeout(10)
        key = __import__("base64").b64encode(os.urandom(16)).decode()
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("cdp websocket handshake closed")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"cdp handshake failed: {buf[:200]!r}")
        self._rest = buf.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    def _send_frame(self, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray()
        header.append(0x81)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def _read_exact(self, n: int) -> bytes:
        out = bytearray()
        if self._rest:
            take = self._rest[:n]
            self._rest = self._rest[n:]
            out.extend(take)
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("cdp socket closed")
            out.extend(chunk)
        return bytes(out)

    def _recv_frame(self) -> bytes:
        while True:
            b1, b2 = self._read_exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(n)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("cdp websocket closed")
            if opcode == 0x9:
                self.sock.sendall(bytes([0x8A, 0x80, 0, 0, 0, 0]))
                continue
            if opcode in (0x1, 0x2, 0x0):
                return payload

    def call(self, method: str, params: dict | None = None, timeout: float = 20) -> dict:
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method}
        if params:
            msg["params"] = params
        self._send_frame(json.dumps(msg).encode())
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.2, deadline - time.time()))
            try:
                raw = self._recv_frame()
            except socket.timeout:
                continue
            data = json.loads(raw.decode())
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result") or {}
        raise TimeoutError(method)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def js(ws: Ws, expr: str) -> object:
    r = ws.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return (r.get("result") or {}).get("value")
