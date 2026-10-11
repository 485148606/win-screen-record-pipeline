# -*- coding: utf-8 -*-
"""capture.py <hwnd> <dur_sec> [resize_w resize_h] [pan_px]

把某个窗口的客户区录成 1920x1080 / 30fps 的 MP4。
- 必须先声明 DPI 感知，否则窗口坐标与 mss 物理像素错配（见 SKILL.md）
- 改窗口尺寸要带 SWP_NOSENDCHANGING，否则会被目标程序否决
- 录屏前请先隐藏遮挡窗口（尤其 WorkBuddy 自己的主窗）
- pan_px: 录制期间水平平移的总像素数（0 = 不注入鼠标，交给用户手动运镜）

依赖：mss、Pillow；ffmpeg 需支持 h264_nvenc（本机 D:\\Tools\\ffmpeg\\bin\\ffmpeg.exe）。
"""
import ctypes, subprocess, time, sys, os

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import mss

u = ctypes.windll.user32
HWND = int(sys.argv[1])
DUR = int(sys.argv[2]) if len(sys.argv) > 2 else 30
RW = int(sys.argv[3]) if len(sys.argv) > 3 else 0
RH = int(sys.argv[4]) if len(sys.argv) > 4 else 0
PAN = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0

FPS = 30
FF = os.environ.get("FFMPEG", r"D:\Tools\ffmpeg\bin\ffmpeg.exe")
OUT = os.path.abspath(os.environ.get("OUT", "rec_raw.mp4"))
VK_MENU = 0x12


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def mouse_move(dx, dy):
    inp = INPUT()
    inp.type = 0
    inp.u.mi.dx = int(dx); inp.u.mi.dy = int(dy)
    inp.u.mi.dwFlags = 0x0001  # MOUSEEVENTF_MOVE
    return u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def focus():
    u.keybd_event(VK_MENU, 0, 0, 0); time.sleep(0.06)
    u.keybd_event(VK_MENU, 0, 2, 0); time.sleep(0.06)
    u.BringWindowToTop(HWND)
    u.SetForegroundWindow(HWND)
    time.sleep(0.4)


def resize_client(cw, ch):
    style = u.GetWindowLongW(HWND, -16)   # GWL_STYLE
    r = RECT(); r.left = 0; r.top = 0; r.right = cw; r.bottom = ch
    u.AdjustWindowRect(ctypes.byref(r), style, False)
    u.SetWindowPos(HWND, 0, 0, 0, r.right - r.left, r.bottom - r.top,
                   0x0004 | 0x0020 | 0x0400)  # NOZORDER|FRAMECHANGED|NOSENDCHANGING
    time.sleep(0.9)


def main():
    if RW and RH:
        resize_client(RW, RH)
    focus()
    r = RECT(); u.GetClientRect(HWND, ctypes.byref(r))
    cw, ch = r.right - r.left, r.bottom - r.top
    pt = POINT(0, 0); u.ClientToScreen(HWND, ctypes.byref(pt))
    x0, y0 = pt.x, pt.y
    cw -= cw % 2; ch -= ch % 2
    print("client", x0, y0, cw, ch, flush=True)

    p = subprocess.Popen(
        [FF, "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
         "-s", "%dx%d" % (cw, ch), "-r", str(FPS), "-i", "pipe:0",
         "-vf", "scale=1920:1080:flags=lanczos,setsar=1",
         "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "9M", "-pix_fmt", "yuv420p",
         "-t", str(DUR), OUT],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    sct = mss.mss()
    mon = {"left": x0, "top": y0, "width": cw, "height": ch}
    sct.grab(mon)  # warm up

    t0 = time.time()
    n = 0
    acc = 0.0
    while True:
        el = time.time() - t0
        if el >= DUR:
            break
        try:
            p.stdin.write(bytes(sct.grab(mon).raw))
        except Exception as e:
            print("pipe broken:", e, flush=True)
            break
        n += 1
        if PAN:
            target = PAN * el / DUR
            if target - acc >= 1.0:
                d = int(target - acc)
                mouse_move(d, 0)
                acc += d
        nxt = t0 + n / float(FPS)
        dt = nxt - time.time()
        if dt > 0:
            time.sleep(dt)

    try:
        p.stdin.close()
    except Exception:
        pass
    try:
        p.wait(timeout=25)
    except Exception:
        p.terminate()
    sz = os.path.getsize(OUT) if os.path.exists(OUT) else -1
    print("frames", n, "secs", round(time.time() - t0, 1), "bytes", sz, flush=True)


if __name__ == "__main__":
    main()
