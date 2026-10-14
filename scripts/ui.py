#!/usr/bin/env python3
"""ui.py — macOS 确定性 UI 驱动（Quartz CGEvent，需要宿主进程有「辅助功能」权限；不需要 Apple Events 权限）

比 VLM 代理（youtu-gui-agent）快且可复现：Agent 先截图看坐标，再写动作脚本，录制时按脚本精确回放。
坐标单位一律为「点」（pt，左上原点，与 `shot` 截图像素 1:1，截图已缩放到点尺寸）。

  python ui.py trusted                       # 检查辅助功能权限
  python ui.py shot out.jpg                  # 主屏截图（缩放到点尺寸，坐标可直接读）
  python ui.py click X Y | move X Y | scroll DY | key return | key cmd+v | type 文本 | paste 文本
  python ui.py run script.json [--log log.json]   # 回放动作脚本

script.json: [{"do":"click","x":..,"y":..,"wait":1.2,"id":"s1","caption":"字幕"}, {"do":"paste","text":".."},
              {"do":"key","key":"return"}, {"do":"type","text":".."}, {"do":"scroll","dy":-300,"x":..,"y":..},
              {"do":"wait","s":3}, {"do":"move","x":..,"y":..}]
  每个动作：wait=动作后停顿秒数（默认 0.9）；caption/id/concept/zoom 原样透传给时间线（rec_mac --script 用）
"""
import ctypes
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import Quartz as Q

KEYS = {"return": 36, "enter": 36, "esc": 53, "escape": 53, "tab": 48, "delete": 51, "space": 49,
        "a": 0, "c": 8, "v": 9, "w": 13, "down": 125, "up": 126, "left": 123, "right": 124}
MODS = {"cmd": Q.kCGEventFlagMaskCommand, "shift": Q.kCGEventFlagMaskShift,
        "alt": Q.kCGEventFlagMaskAlternate, "ctrl": Q.kCGEventFlagMaskControl}


def trusted():
    ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    return ax.AXIsProcessTrusted()


def pos():
    p = Q.CGEventGetLocation(Q.CGEventCreate(None))
    return p.x, p.y


def _post(ev):
    Q.CGEventPost(Q.kCGHIDEventTap, ev)


def move(x, y, speed=1.6):
    """缓动滑行到 (x,y)；speed = 点/毫秒"""
    x0, y0 = pos()
    d = math.hypot(x - x0, y - y0)
    dur = min(0.9, max(0.18, d / (speed * 1000)))
    n = max(2, int(dur * 120))
    for i in range(1, n + 1):
        t = i / n
        e = 1 - (1 - t) ** 3
        _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (x0 + (x - x0) * e, y0 + (y - y0) * e), 0))
        time.sleep(dur / n)


def click(x, y, double=False):
    move(x, y)
    time.sleep(0.08)
    for k in range(2 if double else 1):
        dn = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, (x, y), 0)
        up = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp, (x, y), 0)
        Q.CGEventSetIntegerValueField(dn, Q.kCGMouseEventClickState, k + 1)
        Q.CGEventSetIntegerValueField(up, Q.kCGMouseEventClickState, k + 1)
        _post(dn)
        time.sleep(0.06)
        _post(up)
        time.sleep(0.05)


def scroll(dy, x=None, y=None):
    if x is not None:
        move(x, y)
    step = -40 if dy > 0 else 40  # dy>0 向下看
    for _ in range(max(1, abs(int(dy)) // 40)):
        _post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitPixel, 1, step))
        time.sleep(0.016)


def key(combo):
    parts = combo.lower().split("+")
    flags = 0
    for m in parts[:-1]:
        flags |= MODS[m]
    code = KEYS[parts[-1]]
    for down in (True, False):
        ev = Q.CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            Q.CGEventSetFlags(ev, flags)
        _post(ev)
        time.sleep(0.03)


def type_text(text, delay=0.06):
    for ch in text:
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
            Q.CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
            _post(ev)
        time.sleep(delay)


def paste(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    time.sleep(0.15)
    key("cmd+v")


def shot(out, width=None):
    """主屏截图，缩放到点宽度（坐标 = 截图像素）"""
    from AppKit import NSScreen
    w = width or int(NSScreen.screens()[0].frame().size.width)
    subprocess.run(["screencapture", "-x", "-m", "-t", "jpg", str(out)], check=True)
    subprocess.run(["sips", "--resampleWidth", str(w), str(out)], capture_output=True)
    return out


def run_script(actions, log=None):
    """回放动作；返回每个动作的 {t_start, t_act, x, y, ...}（epoch 秒）"""
    out = []
    for a in actions:
        do = a.get("do")
        rec = {k: v for k, v in a.items()}
        rec["t_start"] = time.time()
        if do in ("click", "dblclick"):
            move(a["x"], a["y"])
            rec["t_act"] = time.time()
            click(a["x"], a["y"], double=(do == "dblclick"))
        elif do == "move":
            move(a["x"], a["y"])
        elif do == "type":
            rec["t_act"] = time.time()
            type_text(a["text"], a.get("delay", 0.06))
        elif do == "paste":
            rec["t_act"] = time.time()
            paste(a["text"])
        elif do == "key":
            rec["t_act"] = time.time()
            key(a["key"])
        elif do == "scroll":
            rec["t_act"] = time.time()
            scroll(a["dy"], a.get("x"), a.get("y"))
        elif do == "wait":
            time.sleep(a.get("s", 1))
        elif do == "shot":
            shot(a["path"])
        time.sleep(a.get("wait", 0.9 if do not in ("wait", "shot") else 0))
        rec["t_end"] = time.time()
        out.append(rec)
        print(f"  [ui] {do} {a.get('x', '')} {a.get('y', '')} {a.get('id', '')}", flush=True)
    if log:
        Path(log).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    c = a[0]
    if c == "trusted":
        print(trusted())
    elif c == "shot":
        print(shot(a[1]))
    elif c == "click":
        click(float(a[1]), float(a[2]))
    elif c == "dblclick":
        click(float(a[1]), float(a[2]), double=True)
    elif c == "move":
        move(float(a[1]), float(a[2]))
    elif c == "scroll":
        scroll(float(a[1]), *(map(float, a[2:4]) if len(a) >= 4 else ()))
    elif c == "key":
        key(a[1])
    elif c == "type":
        type_text(a[1])
    elif c == "paste":
        paste(a[1])
    elif c == "run":
        log = a[a.index("--log") + 1] if "--log" in a else None
        run_script(json.loads(Path(a[1]).read_text()), log)
    print(f"pos={pos()}")


if __name__ == "__main__":
    main()
