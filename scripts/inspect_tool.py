#!/usr/bin/env python3
"""inspect_tool.py — 给 Agent "看图"用的辅助工具（模型读图比读视频可靠）

  python inspect_tool.py grid <image> [--out x.png] [--step 100]   # 叠加坐标网格，读点击坐标用
  python inspect_tool.py clicks <events.json> [--out clicks.jpg]    # 每次点击处的局部截图拼成联系表，写字幕用
  python inspect_tool.py frames <video> <t1> <t2> ... [--out f.jpg] # 成片抽帧联系表，验收用（不覆盖旧图，自动编号）
  python inspect_tool.py gridframes <video> <t1> ... [--step 200]   # 源视频抽帧 + 源像素网格，读推近/打码坐标用
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FFMPEG, font_path, load_json  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def _font(sz):
    return ImageFont.truetype(font_path(), sz)


def grid_image(src, out, step=100):
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    f = _font(max(14, im.width // 90))
    for x in range(0, im.width, step):
        d.line([(x, 0), (x, im.height)], fill=(255, 0, 80, 110 if x % (step * 5) else 200), width=1)
        d.text((x + 3, 3), str(x), font=f, fill=(255, 0, 80, 255))
    for y in range(0, im.height, step):
        d.line([(0, y), (im.width, y)], fill=(0, 120, 255, 110 if y % (step * 5) else 200), width=1)
        d.text((3, y + 3), str(y), font=f, fill=(0, 120, 255, 255))
    im.save(out)
    return out


def grab(video, t, out):
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(video), "-frames:v", "1", str(out)],
                   check=True)
    return Image.open(out).convert("RGB")


def sheet(tiles, out, cols=3, tile_w=640):
    rows = (len(tiles) + cols - 1) // cols
    th = int(tile_w * tiles[0][0].height / tiles[0][0].width)
    S = Image.new("RGB", (cols * tile_w + (cols + 1) * 12, rows * (th + 44) + 12), (245, 246, 250))
    d = ImageDraw.Draw(S)
    f = _font(22)
    for i, (im, label) in enumerate(tiles):
        r, c = divmod(i, cols)
        x, y = 12 + c * (tile_w + 12), 12 + r * (th + 44)
        S.paste(im.resize((tile_w, th)), (x, y + 34))
        d.text((x, y + 4), label, font=f, fill=(20, 20, 30))
    S.save(out, quality=88)
    print(f"[ok] {out}")


def cmd_clicks(a):
    ev_path = Path(a.events).resolve()
    work = ev_path.parent
    ev = load_json(ev_path)
    video = work / ev["video"]
    tmp = work / "_grab.png"
    tiles = []
    for i, s in enumerate(ev["steps"]):
        p = s.get("point")
        if not p:
            continue
        im = grab(video, p["t"] + 0.6, tmp)  # 点击后 0.6s，看到点击结果
        cw, ch = int(im.width * 0.38), int(im.height * 0.38)
        x0 = int(min(max(p["x"] - cw / 2, 0), im.width - cw))
        y0 = int(min(max(p["y"] - ch / 2, 0), im.height - ch))
        crop = im.crop((x0, y0, x0 + cw, y0 + ch))
        d = ImageDraw.Draw(crop)
        cx, cy = p["x"] - x0, p["y"] - y0
        r = max(18, cw // 30)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 40, 80), width=max(3, cw // 250))
        tiles.append((crop, f"#{i} {s['id']}  t={p['t']:.1f}s  {s.get('caption', '')[:18]}"))
    if not tiles:
        sys.exit("[x] 没有点击事件")
    sheet(tiles, a.out or work / "clicks.jpg")


def _unique(p):
    """本机禁删/禁改名 → 不覆盖旧图，自动编号（避免连续两次抽帧互相覆盖、读到旧图）"""
    p = Path(p)
    n, q = 2, p
    while q.exists():
        q = p.with_name(f"{p.stem}_{n}{p.suffix}")
        n += 1
    return q


def cmd_frames(a):
    video = Path(a.video).resolve()
    tmp = video.parent / "_grab.png"
    tiles = [(grab(video, float(t), tmp), f"t={float(t):.1f}s") for t in a.times]
    sheet(tiles, a.out or _unique(video.with_name(video.stem + "_frames.jpg")), cols=min(3, len(tiles)))


def cmd_gridframes(a):
    """读推近/打码坐标用：按「源像素」标网格再缩小查看 → 网格标签就是源像素，无需换算"""
    video = Path(a.video).resolve()
    out_dir = Path(a.out_dir).resolve() if a.out_dir else video.parent
    tmp = out_dir / "_grab.png"
    for t in a.times:
        im = grab(video, float(t), tmp)
        d = ImageDraw.Draw(im, "RGBA")
        f = _font(max(18, im.width // 70))
        for x in range(0, im.width, a.step):
            d.line([(x, 0), (x, im.height)], fill=(255, 0, 80, 150), width=2)
            d.text((x + 4, 4), str(x), font=f, fill=(255, 0, 80, 255))
        for y in range(0, im.height, a.step):
            d.line([(0, y), (im.width, y)], fill=(0, 120, 255, 150), width=2)
            d.text((4, y + 4), str(y), font=f, fill=(0, 120, 255, 255))
        d.text((im.width - im.width // 8, im.height // 30), f"t={float(t):.2f}", font=f, fill=(0, 150, 0, 255))
        w = min(a.width, im.width)
        im = im.resize((w, int(im.height * w / im.width)))
        out = _unique(out_dir / f"{video.stem}_grid_{float(t):.2f}.jpg")
        im.save(out, quality=85)
        print(f"[ok] {out}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("grid"); g.add_argument("image"); g.add_argument("--out"); g.add_argument("--step", type=int, default=100)
    c = sp.add_parser("clicks"); c.add_argument("events"); c.add_argument("--out")
    f = sp.add_parser("frames"); f.add_argument("video"); f.add_argument("times", nargs="+"); f.add_argument("--out")
    gf = sp.add_parser("gridframes"); gf.add_argument("video"); gf.add_argument("times", nargs="+")
    gf.add_argument("--out-dir"); gf.add_argument("--step", type=int, default=200); gf.add_argument("--width", type=int, default=1400)
    a = ap.parse_args()
    if a.cmd == "grid":
        p = Path(a.image)
        print(grid_image(p, a.out or p.with_name(p.stem + ".grid.png"), a.step))
    elif a.cmd == "clicks":
        cmd_clicks(a)
    elif a.cmd == "gridframes":
        cmd_gridframes(a)
    else:
        cmd_frames(a)


if __name__ == "__main__":
    main()
