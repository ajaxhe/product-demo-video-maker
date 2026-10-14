#!/usr/bin/env python3
"""from_video.py — 方案 B：用户自己静音录屏 → Agent 看关键帧配字幕 → 烧字幕成片 + 可编辑 .srt

  analyze <video> [--work DIR] [--crop x,y,w,h]
      活动曲线 → 关键帧联系表 keyframes_XX.jpg（Agent 用 Read 看图）+ analysis.json
      + 自动生成 edit.json（掐头去尾 cut、静止段 speed 建议）
  build <work> [--nosub]
      读 captions_src.srt（源视频时间，Agent 写）+ edit.json → events.json → render
      → 成片_v1.mp4（字幕烧入）+ 成片_v1.srt（成片时间，给用户改 / 导入剪映）
      --nosub 另出 成片_v1_无字幕.mp4（推近/打码相同）
  reburn <work> [--srt 改过的成片字幕.srt]
      用户改完成片字幕（文字或时间都可改）→ 映射回源时间 → 重烧为下一个版本 成片_vN.mp4

edit.json:
  {"video": "...", "crop": [x,y,w,h] | null,
   "cut":   [[t0,t1], ...],          # 源时间，剪掉
   "speed": [[t0,t1,倍速], ...],     # 源时间，加速（如 Agent 生成等待段）
   "focus": [{"t","x","y","zoom","hold"} | {"t0","t1","box","zoom"}],  # 推近（源时间、源像素）
   "blur":  [{"t0","t1","box":[x,y,w,h],"cell":26}],                 # 打码（源时间、源像素）
   "style": {...},                   # 透传 render.py（font/font_bold/caption_size 等）
   "name": "成片"}
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FFMPEG, ensure_dir, load_json, probe, save_json  # noqa: E402

HERE = Path(__file__).resolve().parent
PLAIN = {"background": "none", "out_size": "source", "spotlight": 0, "intro": None, "outro": None}
THR = 0.003        # 变化像素占比阈值（光标移动约 0.05%，不算活动）
SCENE = 0.05       # 大幅切换


# ---------------------------------------------------------------- 分析

def activity(video, info, crop, fps=5, aw=320):
    cx, cy, cw, ch = crop or (0, 0, info["w"], info["h"])
    ah = max(2, int(round(ch * aw / cw / 2)) * 2)
    vf = (f"crop={cw}:{ch}:{cx}:{cy}," if crop else "") + f"fps={fps},scale={aw}:{ah},format=gray"
    p = subprocess.Popen([FFMPEG, "-v", "error", "-i", str(video), "-vf", vf, "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE)
    n = aw * ah
    prev, fr = None, []
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        cur = np.frombuffer(b, np.uint8).astype(np.int16)
        fr.append(0.0 if prev is None else float((np.abs(cur - prev) > 18).mean()))
        prev = cur
    p.wait()
    return np.array(fr), fps


def runs_of(mask):
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        if not m and start is not None:
            out.append([start, i])
            start = None
    if start is not None:
        out.append([start, len(mask)])
    return out


def cmd_analyze(a):
    video = Path(a.video).resolve()
    info = probe(video)
    work = ensure_dir(Path(a.work).resolve() if a.work else video.parent / (video.stem + "_edit"))
    crop = [int(v) for v in a.crop.split(",")] if a.crop else None
    frac, fps = activity(video, info, crop)
    dur = info["duration"]
    t = np.arange(len(frac)) / fps

    # 活动段：合并 0.8s 内的间隙
    act = runs_of(frac > THR)
    merged = []
    for s, e in act:
        if merged and s - merged[-1][1] <= int(0.8 * fps):
            merged[-1][1] = e
        else:
            merged.append([s, e])
    runs = [[round(s / fps, 2), round(e / fps, 2)] for s, e in merged]

    # 关键帧：开头 + 每段活动结束后画面稳定时 + 长活动段内每 6s 一帧
    keys = [0.3]
    for s, e in runs:
        if e - s > 8:
            k = s + 6
            while k < e - 2:
                keys.append(round(k, 2))
                k += 6
        keys.append(round(min(dur - 0.1, e + 0.4), 2))
    keys = sorted(set(k for k in keys if k < dur))
    dedup = []
    for k in keys:
        if not dedup or k - dedup[-1] >= 1.0:
            dedup.append(k)
    keys = dedup
    step = max(1, len(keys) // 60 + 1)  # 最多约 60 帧
    keys = keys[::step] if step > 1 else keys

    # 编辑建议：掐头去尾 + 静止段 ≥3s 加速 6 倍（各留 0.8s 让字幕可读）
    cut, speed = [], []
    if runs:
        if runs[0][0] > 1.2:
            cut.append([0, round(runs[0][0] - 0.6, 2)])
        if dur - runs[-1][1] > 2.0:
            cut.append([round(runs[-1][1] + 1.5, 2), round(dur, 2)])
        for (s0, e0), (s1, e1) in zip(runs, runs[1:]):
            if s1 - e0 >= 3.0:
                speed.append([round(e0 + 0.8, 2), round(s1 - 0.8, 2), 6])
    long_active = [r for r in runs if r[1] - r[0] >= 15]

    # 联系表：2 列 × 3 行，每张 6 帧
    from inspect_tool import grab, sheet
    tmp = work / "_grab.png"
    sheets = []
    for i in range(0, len(keys), 6):
        tiles = []
        for j, k in enumerate(keys[i:i + 6]):
            im = grab(video, k, tmp)
            if crop:
                im = im.crop((crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3]))
            tiles.append((im, f"K{i + j + 1:02d}  t={k:.1f}s"))
        out = work / f"keyframes_{i // 6 + 1:02d}.jpg"
        sheet(tiles, out, cols=2, tile_w=960)
        sheets.append(str(out))

    save_json(work / "analysis.json", {"video": str(video), "duration": dur, "w": info["w"], "h": info["h"],
                                       "crop": crop, "runs": runs, "long_active": long_active,
                                       "keyframes": keys, "sheets": sheets,
                                       "activity_5fps": [round(float(x), 4) for x in frac]})
    ed_path = work / "edit.json"
    if not ed_path.exists():
        save_json(ed_path, {"video": str(video), "crop": crop, "cut": cut, "speed": speed, "style": {},
                            "name": "成片"})
    print(f"[ok] {dur:.1f}s {info['w']}x{info['h']}；活动段 {len(runs)} 个，关键帧 {len(keys)} 张 → {len(sheets)} 张联系表")
    print(f"     建议剪掉 {cut}；静止段加速 {len(speed)} 处；长活动段(≥15s，可能是 Agent 生成，考虑加速) {long_active}")
    print(f"     下一步：Read {work}/keyframes_*.jpg → 写 {work}/captions_src.srt（源视频时间）→ build")


# ---------------------------------------------------------------- 字幕 / 时间线

def ts(t):
    t = max(0.0, t)
    h, r = divmod(int(round(t * 1000)), 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path):
    txt = Path(path).read_text(encoding="utf-8-sig")
    items = []
    for blk in re.split(r"\n\s*\n", txt.strip()):
        lines = [l for l in blk.strip().splitlines() if l.strip()]
        for i, l in enumerate(lines):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", l)
            if m:
                g = [int(x) for x in m.groups()]
                t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                text = " ".join(lines[i + 1:]).strip()
                if text:
                    items.append([t0, t1, text])
                break
    return items


def write_srt(path, items):
    Path(path).write_text("".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(items, 1)),
                          encoding="utf-8")


def make_keep(dur, cut, speed):
    pts = {0.0, float(dur)}
    for seg in (cut or []) + [s[:2] for s in (speed or [])]:
        pts.update(float(min(max(v, 0), dur)) for v in seg)
    pts = sorted(pts)
    keep = []
    for a, b in zip(pts, pts[1:]):
        if b - a < 1e-3:
            continue
        m = (a + b) / 2
        if any(c0 <= m <= c1 for c0, c1 in (cut or [])):
            continue
        f = 1.0
        for s0, s1, sf in (speed or []):
            if s0 <= m <= s1:
                f = float(sf)
        if keep and keep[-1][1] == a and keep[-1][2] == f:
            keep[-1][1] = b
        else:
            keep.append([a, b, f])
    return [[round(a, 3), round(b, 3), f] for a, b, f in keep]


def next_version(work, name):
    n = 1
    while (work / f"{name}_v{n}.mp4").exists():
        n += 1
    return n


def render_events(ev_path, out):
    r = subprocess.run([sys.executable, "-u", str(HERE / "render.py"), str(ev_path), "--out", str(out)],
                       capture_output=True, text=True)
    for l in (r.stdout + r.stderr).splitlines():
        if l.startswith(("[ok]", "  [i]", "  [!]", "[x]", "Traceback")) or "Error" in l:
            print(l)
    if r.returncode != 0:
        sys.exit(r.returncode)


def build(work, out_name=None, nosub=False):
    ed = load_json(work / "edit.json")
    video = Path(ed["video"])
    info = probe(video)
    caps = parse_srt(work / "captions_src.srt")
    if not caps:
        sys.exit(f"[x] {work / 'captions_src.srt'} 为空")
    keep = make_keep(info["duration"], ed.get("cut"), ed.get("speed"))
    steps = [{"id": f"c{i}", "t0": round(a, 3), "t1": round(b, 3), "caption": t, "zoom": 0}
             for i, (a, b, t) in enumerate(caps, 1)]
    # 推近焦点（录屏没有点击数据 → Agent 看帧读坐标后手填，源像素坐标）
    #   {"t": 点击时刻, "x":, "y":, "zoom": 1.8, "hold": 1.2}  → 点击推近 + 涟漪，hold 秒后拉远
    #   {"t0":, "t1":, "box": [x,y,w,h], "zoom": 1.5}         → 区域推近（不画涟漪）
    for i, f in enumerate(ed.get("focus") or [], 1):
        if "box" in f:
            steps.append({"id": f"f{i}", "t0": f["t0"], "t1": f["t1"], "box": f["box"], "zoom": f.get("zoom", 1.5),
                          "caption": ""})
        else:
            steps.append({"id": f"f{i}", "t0": round(f["t"] - 0.8, 3), "t1": round(f["t"] + f.get("hold", 1.2), 3),
                          "point": {"t": f["t"], "x": f["x"], "y": f["y"], "kind": "click"},
                          "zoom": f.get("zoom", 1.8), "hold_zoom": True, "caption": ""})
    steps.sort(key=lambda s: s["t0"])
    style = dict(PLAIN)
    style.update(ed.get("style") or {})
    style["keep"] = keep
    if ed.get("blur"):  # 打码区域（源视频时间 + 源像素）
        style["blur"] = ed["blur"]
    if ed.get("crop"):
        style["crop"] = ed["crop"]
    ev = {"kind": "video", "video": str(video), "src_w": info["w"], "src_h": info["h"],
          "duration": info["duration"], "cursor_mode": "real", "steps": steps, "style": style}
    save_json(work / "events.json", ev)
    name = ed.get("name", "成片")
    out = work / (out_name or f"{name}_v{next_version(work, name)}.mp4")
    render_events(work / "events.json", out)
    print(f"[ok] 可编辑字幕：{out.with_suffix('.srt')}（改完执行 reburn）")
    if nosub:  # 无字幕版（推近/打码/剪辑相同），给剪映里自己加字幕用
        for s_ in ev["steps"]:
            s_["caption"] = ""
        save_json(work / "events_nosub.json", ev)
        render_events(work / "events_nosub.json", out.with_name(out.stem + "_无字幕.mp4"))


def cmd_build(a):
    build(Path(a.work).resolve(), nosub=a.nosub)


def cmd_reburn(a):
    work = Path(a.work).resolve()
    ed = load_json(work / "edit.json")
    name = ed.get("name", "成片")
    srt = Path(a.srt) if a.srt else max(work.glob(f"{name}_v*.srt"), key=lambda p: p.stat().st_mtime)
    info = probe(Path(ed["video"]))
    from render import TimeMap
    tm = TimeMap(info["duration"], [], None, None, make_keep(info["duration"], ed.get("cut"), ed.get("speed")))
    items = [[round(tm.o2s(a0), 3), round(tm.o2s(b0), 3), t] for a0, b0, t in parse_srt(srt)]
    write_srt(work / "captions_src.srt", items)
    print(f"[i] 读入 {srt.name}（{len(items)} 条）→ 已映射回源时间")
    build(work, nosub=a.nosub)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("analyze"); x.add_argument("video"); x.add_argument("--work"); x.add_argument("--crop")
    b = sp.add_parser("build"); b.add_argument("work"); b.add_argument("--nosub", action="store_true")
    r = sp.add_parser("reburn"); r.add_argument("work"); r.add_argument("--srt"); r.add_argument("--nosub", action="store_true")
    a = ap.parse_args()
    {"analyze": cmd_analyze, "build": cmd_build, "reburn": cmd_reburn}[a.cmd](a)


if __name__ == "__main__":
    main()
