#!/usr/bin/env python3
"""from_video.py — 方案 B：用户自己静音录屏 → Agent 看关键帧配字幕 → 烧字幕成片 + 可编辑 .srt

  init <项目名> [--src 录屏...] [--root DIR]
      在固定根目录（默认 ~/Movies/demo-videos，环境变量 PDVM_ROOT 可改）下建项目目录：
      source/（原始录屏软链接） work/（分析、合并片、plan/edit、抽帧） output/（全部版本成片） scripts/ + README.md + CHANGELOG.md
  analyze <video> [--work DIR] [--crop x,y,w,h]
      活动曲线 → 关键帧联系表 keyframes_XX.jpg（Agent 用 Read 看图）+ analysis.json
      + 自动生成 edit.json（掐头去尾 cut、静止段 speed 建议）
  build <work> [--nosub]
      读 captions_src.srt（源视频时间，Agent 写）+ edit.json → events.json → render
      → 成片_v1.mp4（字幕烧入）+ 成片_v1.srt（成片时间，给用户改 / 导入剪映）
      --nosub 另出 成片_v1_无字幕.mp4（推近/打码相同）
  reburn <work> [--srt 改过的成片字幕.srt]
      用户改完成片字幕（文字或时间都可改）→ 映射回源时间 → 重烧为下一个版本 成片_vN.mp4
  concat <out.mp4> <录屏1> <录屏2> ...
      多段录屏归一 30fps 后拼成一条，输出 <out>.sources.json（每段 offset，按帧数计算）
  plan <work> [--target 30]
      <work>/plan.json（保留段 + 每段字幕）→ edit.json + captions_src.srt，打印预计时长与字幕可读性告警

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
from common import FFMPEG, FFPROBE, PROJECTS_ROOT, ensure_dir, load_json, probe, save_json  # noqa: E402

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


def out_dir(work, ed):
    """成片输出目录：edit.json 的 out_dir（相对 work），项目目录结构下为 ../output；未设置则为 work 本身"""
    d = (work / ed["out_dir"]).resolve() if ed.get("out_dir") else work
    return ensure_dir(d)


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
    od = out_dir(work, ed)
    out = od / (out_name or f"{name}_v{next_version(od, name)}.mp4")
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
    od = out_dir(work, ed)
    cands = [p for p in od.glob(f"{name}_v*.srt") if not p.stem.endswith("_无字幕")]
    srt = Path(a.srt) if a.srt else max(cands, key=lambda p: p.stat().st_mtime)
    info = probe(Path(ed["video"]))
    from render import TimeMap
    tm = TimeMap(info["duration"], [], None, None, make_keep(info["duration"], ed.get("cut"), ed.get("speed")))
    items = [[round(tm.o2s(a0), 3), round(tm.o2s(b0), 3), t] for a0, b0, t in parse_srt(srt)]
    write_srt(work / "captions_src.srt", items)
    print(f"[i] 读入 {srt.name}（{len(items)} 条）→ 已映射回源时间")
    build(work, nosub=a.nosub)


# ---------------------------------------------------------------- 多段录屏合并 / 剪辑计划

def _frames(video):
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def cmd_concat(a):
    """多段录屏 → 各自归一到 30fps → 无损拼接；按帧数算每段在合并片里的起点（避免半帧误差）"""
    out = Path(a.out).resolve()
    infos = [probe(Path(v)) for v in a.videos]
    if len({(i["w"], i["h"]) for i in infos}) > 1:
        sys.exit(f"[x] 各段分辨率不一致：{[(i['w'], i['h']) for i in infos]}，先统一录制窗口尺寸再合并")
    parts, sources, t = [], [], 0.0
    for i, v in enumerate(a.videos, 1):
        p = out.with_name(f"{out.stem}_part{i}.mp4")
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(v), "-vf", "fps=30,setpts=PTS-STARTPTS", "-an",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", str(p)],
                       check=True)
        n = _frames(p)
        sources.append({"src": i, "file": str(Path(v).resolve()), "offset": round(t, 3), "duration": round(n / 30, 3)})
        parts.append(p)
        t += n / 30
    lst = out.with_name(out.stem + "_list.txt")
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                   check=True)
    meta = out.with_suffix(".sources.json")
    save_json(meta, {"video": str(out), "duration": round(t, 3), "sources": sources})
    print(f"[ok] {out}（{t:.1f}s）")
    for s in sources:
        print(f"     src {s['src']}: offset {s['offset']:>8.3f}s  时长 {s['duration']:.1f}s  {Path(s['file']).name}")
    print(f"[ok] {meta}（plan.json 里写 \"sources\": 该文件，即可用「源序号 + 源内时间」写剪辑计划）")


def cmd_plan(a):
    """plan.json（保留段 + 每段字幕）→ edit.json + captions_src.srt，并估算成片时长、检查字幕可读时长"""
    work = Path(a.work).resolve()
    pl = load_json(work / "plan.json")
    offs = {}
    if pl.get("sources"):
        meta = load_json((work / pl["sources"]) if not Path(pl["sources"]).is_absolute() else pl["sources"])
        offs = {s["src"]: s["offset"] for s in meta["sources"]}
        pl.setdefault("video", meta["video"])
    video = Path(pl["video"]) if Path(pl["video"]).is_absolute() else work / pl["video"]
    dur = probe(video)["duration"]

    def T(src, t):
        return round(t + (offs.get(src, 0.0) if src else 0.0), 3)

    segs = []  # [t0, t1, speed, caption]（合并片时间）
    for s in pl["segments"]:
        if len(s) == 5:
            segs.append([T(s[0], s[1]), T(s[0], s[2]), float(s[3]), s[4]])
        else:
            segs.append([float(s[0]), float(s[1]), float(s[2]), s[3]])
    cut, speed, prev = [], [], 0.0
    for t0, t1, f, _ in segs:
        if t0 < prev - 1e-6:
            sys.exit(f"[x] 保留段需按时间递增且不重叠：{t0} < {prev}")
        if t0 > prev + 1e-6:
            cut.append([round(prev, 3), t0])
        if f != 1.0:
            speed.append([t0, t1, f])
        prev = t1
    if prev < dur - 1e-3:
        cut.append([round(prev, 3), round(dur, 3)])

    def conv(items):
        res = []
        for it in items or []:
            it = dict(it)
            src = it.pop("src", None)
            for k in ("t", "t0", "t1"):
                if k in it:
                    it[k] = T(src, it[k])
            res.append(it)
        return res

    style = dict(pl.get("style") or {})
    msyh = Path("/Applications/Microsoft Word.app/Contents/Resources/DFonts")
    if "font" not in style and (msyh / "msyh.ttc").exists():
        style["font"], style["font_bold"] = str(msyh / "msyh.ttc"), str(msyh / "msyhbd.ttc")
    save_json(work / "edit.json", {"video": str(video), "crop": pl.get("crop"), "name": pl.get("name", "成片"),
                                   "out_dir": pl.get("out_dir"),
                                   "cut": cut, "speed": speed, "focus": conv(pl.get("focus")),
                                   "blur": conv(pl.get("blur")), "style": style})
    # 字幕：相邻且文字相同的保留段合并成一条（跨越被剪掉的空档也行，渲染时按时间轴映射）
    caps = []
    for t0, t1, f, c in segs:
        out_len = (t1 - t0) / f
        if caps and caps[-1][2] == c:
            caps[-1][1] = t1
            caps[-1][3] += out_len
        else:
            caps.append([t0, t1, c, out_len])
    write_srt(work / "captions_src.srt", [[a0, b0 - 0.02, c] for a0, b0, c, _ in caps if c])
    total = sum((t1 - t0) / f for t0, t1, f, _ in segs)
    print(f"[ok] edit.json + captions_src.srt；预计成片 {total:.1f}s，字幕 {sum(1 for c in caps if c[2])} 条")
    for a0, b0, c, L in caps:
        if c:
            print(f"     {L:4.1f}s  {c}" + ("   [!] <3s 读不完：放慢该段或与相邻字幕合并" if L < 3 else "")
                  + ("   [!] >20 字" if len(c) > 20 else ""))
    if a.target and abs(total - a.target) > 1.0:
        print(f"  [!] 与目标 {a.target}s 相差 {total - a.target:+.1f}s：超了先压等待/输入段，短了先放慢展示段")


# ---------------------------------------------------------------- 项目目录

README_TPL = """# {name}

- 素材：见 source/（原始录屏的软链接，原文件不复制）
- 目标时长：
- 当前版本：（每次交付更新，写 output/ 里最新的 _vN）
- 剪辑计划：work/plan.json（改剪辑只改它，再 plan + build）
- 待确认 / 待补录：

## 目录
- source/  原始录屏（软链接）
- work/    analyze 结果、combined.mp4、plan.json、edit.json、captions_src.srt、抽帧检查图
- output/  全部版本成片：<名称>_vN.mp4 / _vN.srt / _vN_无字幕.mp4（只增不覆盖）
- scripts/ 本项目专用的辅助脚本
- CHANGELOG.md 每一版改了什么（用户反馈 → 改动）
"""


def cmd_init(a):
    root = Path(a.root).expanduser() if a.root else PROJECTS_ROOT
    proj = root / a.name
    for sub in ("source", "work", "output", "scripts", "work/_tmp"):
        ensure_dir(proj / sub)
    if not (proj / "README.md").exists():
        (proj / "README.md").write_text(README_TPL.format(name=a.name), encoding="utf-8")
    if not (proj / "CHANGELOG.md").exists():
        (proj / "CHANGELOG.md").write_text(f"# {a.name} 版本记录\n\n| 版本 | 日期 | 反馈 / 目标 | 改动 |\n|---|---|---|---|\n",
                                           encoding="utf-8")
    import os
    for v in a.src or []:
        v = Path(v).expanduser().resolve()
        link = proj / "source" / v.name
        if not link.exists():
            os.symlink(v, link)
    pl = proj / "work" / "plan.json"
    if not pl.exists():
        save_json(pl, {"name": a.name, "out_dir": "../output", "sources": "combined.sources.json",
                       "segments": [], "focus": [], "blur": [], "style": {}})
    print(f"[ok] 项目目录 {proj}")
    print(f"     source/ {len(list((proj / 'source').iterdir()))} 个素材；work/plan.json 已就绪（out_dir=../output）")
    print(f"     命令前缀：cd {proj / 'work' / '_tmp'} && export TMPDIR=$PWD;")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init"); i.add_argument("name"); i.add_argument("--src", nargs="*"); i.add_argument("--root")
    x = sp.add_parser("analyze"); x.add_argument("video"); x.add_argument("--work"); x.add_argument("--crop")
    b = sp.add_parser("build"); b.add_argument("work"); b.add_argument("--nosub", action="store_true")
    r = sp.add_parser("reburn"); r.add_argument("work"); r.add_argument("--srt"); r.add_argument("--nosub", action="store_true")
    c = sp.add_parser("concat"); c.add_argument("out"); c.add_argument("videos", nargs="+")
    p = sp.add_parser("plan"); p.add_argument("work"); p.add_argument("--target", type=float)
    a = ap.parse_args()
    {"init": cmd_init, "analyze": cmd_analyze, "build": cmd_build, "reburn": cmd_reburn,
     "concat": cmd_concat, "plan": cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    main()
