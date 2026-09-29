#!/usr/bin/env python3
"""In-container helper for the Docker backend (``forkloop/backends/docker.py``).

The controller starts it once per container with ``docker exec -i <container> python3 -u agent.py``
and keeps the pipe open: one JSON request per stdin line, one JSON response per stdout line, matched
by ``id``. A per-call ``docker exec`` costs ~150 ms on the Lambda box (measured 2026-09-28); a request
on this pipe costs a few milliseconds, so screenshots and actions stay well under the 150 ms target.

It serves BOTH channels, like the Solari guest daemon does; the separation is in the Python API
(``DockerMachine``): the policy only ever reaches ``shot``/``xdo`` through the agent-channel methods.

  ping                                    -> {}
  ready   {timeout}                       -> {} once /run/forkloop/ready exists
  exec    {argv, cwd?, env?, timeout?, stdin?}   -> {exit, stdout, stderr}   (runs as root)
  read    {path}                          -> {data}
  write   {path, data, mode?}             -> {}
  shot    {level?}                        -> {png, w, h}     (XGetImage of :0, no cursor)
  xdo     {args}                          -> {exit, stderr}  (one xdotool invocation, DISPLAY=:0)

Binary fields are base64. Standard library only (runs on the image's system python3).
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

DISPLAY = os.environ.get("FORKLOOP_DISPLAY", ":0")
RUN_DIR = os.environ.get("FORKLOOP_AGENT_RUN_DIR", "/run/forkloop")  # overridable for offline tests
READY = os.path.join(RUN_DIR, "ready")
FAILED = os.path.join(RUN_DIR, "failed")
_out_lock = threading.Lock()
_x_lock = threading.Lock()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# --------------------------------------------------------------------------- screenshot
class _XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
                ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong)]


class _X:
    """XGetImage of the root window through libX11 (ctypes). The X server answers one request at a
    time, so the image is a consistent frame (reading Xvfb's -fbdir file could tear)."""

    def __init__(self) -> None:
        self.lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        self.lib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.XGetImage.restype = ctypes.POINTER(_XImage)
        self.lib.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        self.lib.XDestroyImage.argtypes = [ctypes.POINTER(_XImage)]
        self.lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.dpy = None

    def _open(self) -> None:
        if self.dpy:
            return
        dpy = self.lib.XOpenDisplay(DISPLAY.encode())
        if not dpy:
            raise RuntimeError(f"cannot open display {DISPLAY}")
        self.dpy = dpy
        scr = self.lib.XDefaultScreen(dpy)
        self.root = self.lib.XDefaultRootWindow(dpy)
        self.w, self.h = self.lib.XDisplayWidth(dpy, scr), self.lib.XDisplayHeight(dpy, scr)

    def grab(self) -> tuple[int, int, bytes]:
        with _x_lock:
            try:
                self._open()
                img = self.lib.XGetImage(self.dpy, self.root, 0, 0, self.w, self.h, 0xFFFFFFFF, 2)  # ZPixmap
                if not img:
                    raise RuntimeError("XGetImage failed")
            except Exception:
                if self.dpy:
                    try:
                        self.lib.XCloseDisplay(self.dpy)
                    except Exception:  # noqa: BLE001
                        pass
                self.dpy = None
                raise
            try:
                im = img.contents
                if im.bits_per_pixel != 32 or im.red_mask != 0xFF0000 or im.blue_mask != 0xFF:
                    raise RuntimeError(f"unsupported visual bpp={im.bits_per_pixel} masks="
                                       f"{im.red_mask:x}/{im.green_mask:x}/{im.blue_mask:x}")
                raw = ctypes.string_at(im.data, im.bytes_per_line * im.height)
                w, h, bpl = im.width, im.height, im.bytes_per_line
            finally:
                self.lib.XDestroyImage(img)
        if bpl != w * 4:
            raw = b"".join(raw[y * bpl:y * bpl + w * 4] for y in range(h))
        return w, h, raw


_x: _X | None = None


def png_from_bgrx(w: int, h: int, bgrx: bytes, level: int) -> bytes:
    rgb = bytearray(w * h * 3)
    rgb[0::3] = bgrx[2::4]
    rgb[1::3] = bgrx[1::4]
    rgb[2::3] = bgrx[0::4]
    stride = w * 3
    mv = memoryview(rgb)
    raw = b"".join(b"\x00" + mv[y * stride:(y + 1) * stride].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b""))


def op_shot(req: dict) -> dict:
    global _x
    if _x is None:
        _x = _X()
    w, h, bgrx = _x.grab()
    return {"png": b64(png_from_bgrx(w, h, bgrx, int(req.get("level", 1)))), "w": w, "h": h}


# --------------------------------------------------------------------------- processes / files
def _run(argv: list[str], *, cwd: str | None = None, env: dict | None = None, timeout: float | None = None,
         stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    full_env = dict(os.environ)
    full_env.update({k: str(v) for k, v in (env or {}).items()})
    try:
        p = subprocess.Popen(argv, cwd=cwd or None, env=full_env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except FileNotFoundError as e:
        return 127, b"", f"{argv[0]}: not found ({e})".encode()
    except PermissionError as e:
        return 126, b"", f"{argv[0]}: {e}".encode()
    try:
        out, err = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return 124, out, err + b"\ntimeout"
    return p.returncode, out, err


def op_exec(req: dict) -> dict:
    stdin = base64.b64decode(req["stdin"]) if req.get("stdin") is not None else None
    code, out, err = _run(list(req["argv"]), cwd=req.get("cwd"), env=req.get("env"), timeout=req.get("timeout"),
                          stdin=stdin)
    return {"exit": code, "stdout": b64(out), "stderr": b64(err)}


def op_read(req: dict) -> dict:
    with open(req["path"], "rb") as f:
        return {"data": b64(f.read())}


def op_write(req: dict) -> dict:
    path = req["path"]
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(req["data"]))
    if req.get("mode") is not None:
        os.chmod(path, int(req["mode"]))
    return {}


def op_xdo(req: dict) -> dict:
    code, _, err = _run(["xdotool", *[str(a) for a in req["args"]]], env={"DISPLAY": DISPLAY},
                        timeout=float(req.get("timeout", 60)))
    return {"exit": code, "stderr": b64(err)}


def op_ready(req: dict) -> dict:
    deadline = time.monotonic() + float(req.get("timeout", 120))
    while time.monotonic() < deadline:
        if os.path.exists(READY):
            return {}
        if os.path.exists(FAILED):
            try:
                boot = open(os.path.join(RUN_DIR, "boot.json")).read().strip()
            except OSError:
                boot = "(no boot.json)"
            raise RuntimeError(f"world boot failed: {boot}")
        time.sleep(0.05)
    raise TimeoutError(f"{READY} did not appear within {req.get('timeout', 120)} s")


OPS = {"ping": lambda r: {"pid": os.getpid()}, "ready": op_ready, "exec": op_exec, "read": op_read,
       "write": op_write, "shot": op_shot, "xdo": op_xdo}


def handle(line: bytes) -> None:
    rid = None
    try:
        req = json.loads(line)
        rid = req.get("id")
        resp = {"id": rid, "ok": True, **OPS[req["op"]](req)}
    except Exception as e:  # noqa: BLE001 - every request gets an answer
        resp = {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}
    data = (json.dumps(resp, separators=(",", ":")) + "\n").encode()
    with _out_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def main() -> None:
    pool = ThreadPoolExecutor(max_workers=16)
    for line in sys.stdin.buffer:
        if line.strip():
            pool.submit(handle, line)
    pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
