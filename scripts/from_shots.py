#!/usr/bin/env python3
"""from_shots.py — 截图 → 演示视频时间线（events.json），之后交给 render.py

用法:
  python from_shots.py init <截图目录> [--work DIR]   # 生成 storyboard.json 骨架 + 每张图的坐标网格图 grid/*.png
  python from_shots.py build <storyboard.json>        # 生成 events.json（坐标统一换算到首图画布）

storyboard.json 结构:
{
  "transition": 0.45,
  "style": {...},                       # 透传 render.py
  "voice": {"enabled": false},          # 可选 edge-tts 配音
  "shots": [
    {"image": "shots/01.png", "dur": 3.5, "caption": "字幕",
     "click": [x, y],                   # 截图像素坐标（看 grid 图读数），可省
     "box": [x, y, w, h],               # 要高亮的元素框，可省；只给 box 时点击点取框中心
     "kind": "click",                   # click | hover | none(只高亮不点击)
     "concept": {"title": "...", "desc": "..."},
     "zoom": 1.8}                       # 0 关闭推近
  ]
}
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ensure_dir, load_json, save_json  # noqa: E402
from PIL import Image  # noqa: E402

EXT = {".png", ".jpg", ".jpeg", ".webp"}


def cmd_init(a):
    src = Path(a.dir).resolve()
    work = ensure_dir(a.work or src.parent)
    imgs = sorted([p for p in src.iterdir() if p.suffix.lower() in EXT])
    if not imgs:
        sys.exit("[x] 目录里没有截图")
    from inspect_tool import grid_image
    gdir = ensure_dir(work / "grid")
    shots = []
    for p in imgs:
        grid_image(p, gdir / (p.stem + ".grid.png"))
        shots.append({"image": str(p.relative_to(work)) if p.is_relative_to(work) else str(p),
                      "dur": 3.5, "caption": "", "click": None, "concept": None})
    sb = {"transition": 0.45, "style": {}, "voice": {"enabled": False}, "shots": shots}
    save_json(work / "storyboard.json", sb)
    print(f"[ok] {len(shots)} 张截图 → {work / 'storyboard.json'}；坐标网格图在 {gdir}")


def cmd_build(a):
    sb_path = Path(a.storyboard).resolve()
    work = sb_path.parent
    sb = load_json(sb_path)
    shots = sb["shots"]
    first = Image.open(work / shots[0]["image"])
    W, H = first.size
    W, H = W // 2 * 2, H // 2 * 2

    voice = {}
    if (sb.get("voice") or {}).get("enabled"):
        from tts import voice_steps
        for i, s in enumerate(shots):
            s.setdefault("id", f"s{i + 1}")
        voice = voice_steps(shots, work, sb["voice"])

    t = 0.0
    trans = sb.get("transition", 0.45)
    ev_shots, steps, audio = [], [], []
    for i, s in enumerate(shots):
        sid = s.get("id") or f"s{i + 1}"
        im = Image.open(work / s["image"])
        k = min(W / im.width, H / im.height)
        ox, oy = (W - im.width * k) / 2, (H - im.height * k) / 2
        dur = float(s.get("dur", 3.5))
        if sid in voice:
            dur = max(dur, voice[sid][1] + 0.6)
            audio.append({"t": t + 0.2, "file": voice[sid][0]})
        t0, t1 = t, t + dur
        st = {"id": sid, "t0": round(t0, 3), "t1": round(t1, 3), "caption": s.get("caption", "")}
        box = s.get("box")
        if box:
            st["box"] = [box[0] * k + ox, box[1] * k + oy, box[2] * k, box[3] * k]
        click = s.get("click") or ([box[0] + box[2] / 2, box[1] + box[3] / 2] if box else None)
        kind = s.get("kind", "click")
        if click and kind != "none":
            st["point"] = {"t": round(t0 + min(1.1, dur * 0.35), 3), "x": click[0] * k + ox,
                           "y": click[1] * k + oy, "kind": kind}
        for f in ("concept", "zoom", "highlight"):
            if s.get(f) is not None:
                st[f] = s[f]
        if not click and not box:
            st["zoom"] = 0
        steps.append(st)
        ev_shots.append({"t0": round(t0, 3), "t1": round(t1, 3), "image": s["image"]})
        t = t1
    ev = {"kind": "shots", "src_w": W, "src_h": H, "duration": round(t, 3), "transition": trans,
          "cursor_mode": "synthetic", "shots": ev_shots, "steps": steps, "style": sb.get("style", {})}
    if audio:
        ev["audio"] = audio
    save_json(work / "events.json", ev)
    print(f"[ok] {len(steps)} 镜 / {t:.1f}s → {work / 'events.json'}\n下一步: python render.py {work / 'events.json'}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init"); i.add_argument("dir"); i.add_argument("--work")
    b = sp.add_parser("build"); b.add_argument("storyboard")
    a = ap.parse_args()
    {"init": cmd_init, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
