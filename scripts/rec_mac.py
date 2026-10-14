#!/usr/bin/env python3
"""rec_mac.py — macOS 桌面应用录制（主显示器全屏抓取 + 鼠标点击追踪）→ raw.mp4 + events.json

两种驱动方式:
  手动:  python rec_mac.py --work DIR --duration 60            # 用户自己操作；到时自动停，或 touch DIR/STOP 提前停
  代理:  python rec_mac.py --work DIR --agent "打开备忘录，新建一条笔记写入：周会纪要"
         → 录屏同时前台调用 youtu-gui-agent（VLM 看屏幕自己点），结束后自动停录
可选:  --captions caps.json   按点击顺序给字幕/概念，格式 ["字幕1", {"caption":"字幕2","concept":{...}}, ...]
       --app "备忘录"          录前把该应用激活到前台

原理:
  · ffmpeg avfoundation 抓主屏（capture_cursor=1，真实光标进画面），-use_wallclock_as_timestamps 让首帧带 epoch 时间
  · NSEvent.mouseLocation / pressedMouseButtons 120Hz 轮询（**无需辅助功能权限**）记录点击边沿与坐标
  · 坐标换算：点(pt, 左下原点) → 像素 = (x*s, (屏高-y)*s)，s = 视频宽 / 主屏点宽（Retina=2）
限制: 只录主显示器；youtu-gui-agent 也只支持主显示器 → 建议录前拔掉外接屏或把目标应用放主屏
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FFMPEG, YOUTU, ensure_dir, load_json, probe, save_json  # noqa: E402


def screen_device():
    out = subprocess.run([FFMPEG, "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                         capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "Capture screen 0" in line:
            return "Capture screen 0"
    sys.exit("[x] ffmpeg 看不到 'Capture screen 0' → 系统设置 › 隐私与安全性 › 屏幕录制，给终端/宿主应用 授权")


class Tracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        from AppKit import NSEvent, NSScreen
        self.NSEvent = NSEvent
        fr = NSScreen.mainScreen().frame()
        self.sw, self.sh = fr.size.width, fr.size.height
        self.clicks, self.path = [], []
        self.stop_flag = False

    def run(self):
        prev = 0
        last_path = 0
        while not self.stop_flag:
            t = time.time()
            loc = self.NSEvent.mouseLocation()
            b = self.NSEvent.pressedMouseButtons()
            if (b & 1) and not (prev & 1):
                self.clicks.append([t, loc.x, loc.y, "left"])
            if (b & 2) and not (prev & 2):
                self.clicks.append([t, loc.x, loc.y, "right"])
            prev = b
            if t - last_path >= 1 / 30:
                self.path.append([t, loc.x, loc.y])
                last_path = t
            time.sleep(1 / 120)


def window_rect_pts(app):
    """读取应用最大可见窗口的位置（点，左上原点）。只读窗口列表，不需要辅助功能权限"""
    try:
        import Quartz
        wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        best = None
        for w in wins or []:
            if w.get("kCGWindowOwnerName") != app or w.get("kCGWindowLayer", 1) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            area = b.get("Width", 0) * b.get("Height", 0)
            if area > 200 * 200 and (best is None or area > best[0]):
                best = (area, [b["X"], b["Y"], b["Width"], b["Height"]])
        return best[1] if best else None
    except Exception:
        return None


def script_steps(log, t_base, dur, s):
    """脚本模式：带 caption 的动作开启一个字幕段，持续到下一个带 caption 的动作开始；
    该动作若是点击，则作为本段的推近/涟漪焦点。坐标 pt（左上原点）× s → 像素"""
    marks = [r for r in log if r.get("caption")]
    steps = []
    for i, r in enumerate(marks):
        t0 = r["t_start"] - t_base
        t1 = (marks[i + 1]["t_start"] - t_base - 0.05) if i + 1 < len(marks) else dur - 0.2
        st = {"id": r.get("id") or f"s{i + 1}", "t0": round(max(0, t0), 3), "t1": round(t1, 3),
              "caption": r["caption"]}
        if r.get("do") in ("click", "dblclick"):
            st["point"] = {"t": round(r["t_act"] - t_base, 3), "x": round(r["x"] * s, 1),
                           "y": round(r["y"] * s, 1), "kind": "click"}
        for k in ("zoom", "box", "hold_zoom"):
            if k in r:
                st[k] = r[k]
        if "point" not in st and "box" not in st:
            st["zoom"] = 0
        steps.append(st)
    return steps


def notify(msg):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "产品演示录制"'],
                   capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--agent", help="交给 youtu-gui-agent 执行的自然语言任务")
    ap.add_argument("--duration", type=float, default=60, help="手动模式最长录制秒数")
    ap.add_argument("--captions")
    ap.add_argument("--app", help="录前激活的应用名")
    ap.add_argument("--countdown", type=float, default=3)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--script", help="ui.py 动作脚本（确定性回放，比 VLM 代理稳定）；动作上的 caption 自动生成字幕段")
    ap.add_argument("--tail", type=float, default=1.5, help="脚本结束后多录几秒")
    a = ap.parse_args()
    script_log = None

    work = ensure_dir(Path(a.work).resolve())
    stop_file = work / "STOP"
    if stop_file.exists():
        stop_file.rename(work / f"STOP.old.{int(time.time())}")
    dev = screen_device()
    if a.agent and not YOUTU.exists():
        sys.exit(f"[x] 找不到 youtu-gui-agent：{YOUTU}")
    if a.app:
        # 用 open -a 激活（osascript 需要 Apple Events 授权，本机会被拒）
        subprocess.run(["open", "-a", a.app], capture_output=True)
        time.sleep(1.0)

    if not a.script:
        notify(f"{int(a.countdown)} 秒后开始录制，录制期间请勿操作" if a.agent else f"{int(a.countdown)} 秒后开始录制")
    time.sleep(a.countdown)

    win_pts = window_rect_pts(a.app) if a.app else None
    mkv = work / f"rec_{time.strftime('%H%M%S')}.mkv"  # 不覆盖旧文件（本机禁删/改名）
    tracker = Tracker()
    tracker.start()
    ff = subprocess.Popen([FFMPEG, "-y", "-v", "error", "-f", "avfoundation", "-pixel_format", "nv12",
                           "-capture_cursor", "1", "-framerate", str(a.fps), "-use_wallclock_as_timestamps", "1",
                           "-i", f"{dev}:none", "-copyts", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
                           str(mkv)], stdin=subprocess.PIPE)
    time.sleep(0.8)
    agent_rc = None
    try:
        if a.agent:
            with open(work / "agent.log", "w") as lg:
                # 注意：youtu-gui-agent run 只有 -v / --max-steps 参数（没有 --no-panel）
                agent_rc = subprocess.run([str(YOUTU), "run", "--max-steps", str(a.max_steps), a.agent], stdout=lg,
                                          stderr=subprocess.STDOUT).returncode
            time.sleep(1.5)
        elif a.script:
            import ui
            script_log = ui.run_script(load_json(a.script), work / "script_log.json")
            time.sleep(a.tail)
        else:
            t_stop = time.time() + a.duration
            while time.time() < t_stop and not stop_file.exists():
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        ff.communicate(b"q")
        tracker.stop_flag = True
        tracker.join(1)
    if not a.script:
        notify("录制结束，正在处理")  # 通知横幅会被录进画面，脚本模式不弹

    info = probe(mkv)
    t_base = info["start"]
    raw = work / "raw.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(mkv), "-vf", "setpts=PTS-STARTPTS",
                    "-fps_mode", "cfr", "-r", str(a.fps), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                    "-pix_fmt", "yuv420p", str(raw)], check=True)
    vi = probe(raw)
    s = vi["w"] / tracker.sw

    def px(x, y):
        return round(x * s, 1), round((tracker.sh - y) * s, 1)

    # 合并双击 / 抖动点击
    clicks = []
    for t, x, y, btn in tracker.clicks:
        if clicks and t - clicks[-1][0] < 0.35 and abs(x - clicks[-1][1]) < 8 and abs(y - clicks[-1][2]) < 8:
            continue
        if 0 <= x <= tracker.sw and 0 <= y <= tracker.sh:  # 丢弃副屏上的点击
            clicks.append([t, x, y, btn])
    caps = load_json(a.captions) if a.captions else []
    dur = vi["duration"]
    steps = []
    for i, (t, x, y, btn) in enumerate(clicks):
        tc = t - t_base
        nxt = clicks[i + 1][0] - t_base if i + 1 < len(clicks) else dur
        prev_end = steps[-1]["t1"] if steps else 0
        X, Y = px(x, y)
        st = {"id": f"c{i + 1}", "t0": round(max(prev_end, tc - 1.2), 3),
              "t1": round(min(dur, max(tc + 1.8, min(nxt - 0.4, tc + 4.5))), 3),
              "point": {"t": round(tc, 3), "x": X, "y": Y, "kind": "click"}, "caption": ""}
        if i < len(caps):
            c = caps[i]
            if isinstance(c, str):
                st["caption"] = c
            else:
                st.update({k: v for k, v in c.items() if k in ("caption", "concept", "zoom", "box", "highlight")})
        steps.append(st)
    if script_log is not None:
        steps = script_steps(script_log, t_base, dur, s)
        ev_extra = [[round(r["t_act"] - t_base, 3), round(r["x"] * s, 1), round(r["y"] * s, 1)]
                    for r in script_log if r.get("do") in ("click", "dblclick") and not r.get("caption")]
    else:
        ev_extra = []
    ev = {"kind": "mac", "video": "raw.mp4", "extra_clicks": ev_extra, "t_base_epoch": t_base, "src_w": vi["w"], "src_h": vi["h"], "duration": round(dur, 3),
          "cursor_mode": "real", "screen_pts": [tracker.sw, tracker.sh], "scale": s,
          "cursor_path": [[round(t - t_base, 3), *px(x, y)] for t, x, y in tracker.path[::2]],
          "steps": steps,
          "style": {"speedup": {"factor": 4, "idle_min": 1.5}} if a.agent else {}}
    if win_pts:
        ev["window_rect"] = [round(v * s) for v in win_pts]
        print(f"[i] {a.app} 窗口 {win_pts}pt → crop=\"window\" 可只保留该窗口")
    save_json(work / "events.json", ev)
    print(f"[ok] 录屏 {raw} {vi['w']}x{vi['h']} {dur:.1f}s；点击 {len(steps)} 次 → {work / 'events.json'}")
    if agent_rc is not None:
        print(f"[i] youtu-gui-agent 退出码 {agent_rc}，日志 {work / 'agent.log'}")
    print(f"下一步: python inspect_tool.py clicks {work / 'events.json'}  （看图补字幕后再 render.py）")


if __name__ == "__main__":
    main()
