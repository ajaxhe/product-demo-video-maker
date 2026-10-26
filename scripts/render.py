#!/usr/bin/env python3
"""render.py — 统一时间线 events.json → 成片 final.mp4 + final.srt

三种录制入口（rec_web / rec_mac / from_shots）都产出同一格式的 events.json，本脚本负责全部后期：
  · 虚拟镜头：点击前 zoom_lead 秒推近到点击处/元素框，步骤结束拉远；相邻步骤间隔短则平移不拉远（弹簧阻尼，无抖动）
  · 高亮：元素框聚光灯（框外压暗 + 描边）、点击涟漪、合成光标（web/截图模式）
  · 核心概念卡：step.concept = {title, desc}，跟随元素框浮现
  · 字幕：按 step 的 [t0,t1] 烧入画面，同时导出 .srt（帧级时间，可导入剪映/PR 二次编辑）
  · 片头/片尾卡、空闲段自动加速（Mac+VLM 代理录制时去掉"思考"停顿）、配音混音

用法:
  python render.py <work>/events.json                 # 产出 <work>/final.mp4 + final.srt
  python render.py <work>/events.json --out x.mp4 --preview 12   # 只渲染前 12 秒（调样式用）
样式覆盖：events.json 的 "style" 字段（见 DEFAULT_STYLE），或 --style style.json
"""
import re
import argparse
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FFMPEG, font_path, hex2rgb, load_json, probe  # noqa: E402

DEFAULT_STYLE = {
    "out_w": 1920, "out_h": 1080, "fps": 30, "crf": 18,
    "background": "gradient:#EEF2FF:#D6E0FF",   # "gradient:#a:#b" | "#hex" | "none"(满屏无边框)
    "padding": 56, "radius": 18, "shadow": True,
    "accent": "#2F6BFF",
    # 镜头
    "zoom": 1.8,            # 默认推近倍数；step.zoom=0/1 关闭
    "zoom_lead": 0.7,       # 点击前多少秒开始推近
    "zoom_hold_gap": 1.2,   # 两次推近间隔 < 该值则保持推近、直接平移
    "spring": 7.0,          # 镜头弹簧角频率，越大越跟手
    # 高亮
    "spotlight": 0.45,      # 框外压暗强度 0~1，0 关闭
    "spot_pad": 10,         # 元素框外扩（源像素）
    "spot_radius": 90,      # 只有点击坐标没有元素框时的圆形高亮半径（源像素）
    "ripple": True,
    "cursor": "auto",       # auto: web/截图画合成光标，mac 用录到的真实光标；"synthetic"|"none"
    "cursor_size": 34,
    # 字幕
    "caption_size": 40, "caption_bottom": 30, "caption_alpha": 0.74, "caption_max_w": 0.8,
    # 概念卡
    "concept_w": 470, "concept_title_size": 32, "concept_desc_size": 24,
    # 片头片尾 {"title":..,"subtitle":..}
    # 片头片尾：单张 {"title","subtitle","kicker","columns":[{"title","desc"}],"dur"} 或多张组成的列表
    "intro": None, "outro": None, "intro_dur": 2.6, "outro_dur": 2.8,
    # 主题：light（默认浅色）| dark（大会深色主视觉：深蓝紫渐变 + 玻璃卡片）
    "theme": "light",
    # 剪掉的源时间段 [[t0,t1],...]（如录到的「断开连接器」、末尾停录操作）
    "cut": None,
    # 空闲加速 {"factor":4,"idle_min":1.5,"protect_before":0.9,"protect_after":2.2,"min_caption":2.4}
    "speedup": None,
    "font": None,
}

# ---------------------------------------------------------------- 小工具

def clamp(v, a, b):
    return a if v < a else b if v > b else v


def ease_out(p):
    p = clamp(p, 0, 1)
    return 1 - (1 - p) ** 3


def ease_in_out(p):
    p = clamp(p, 0, 1)
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ramp(t, start, end, fade):
    """[start,end] 区间内 1，两端 fade 秒线性渐变"""
    if t < start or t > end:
        return 0.0
    return clamp(min((t - start) / fade, (end - t) / fade), 0, 1) if fade > 0 else 1.0


def blend_rgba(dst, rgba, x, y, alpha=1.0):
    """把 RGBA numpy 图贴到 dst(RGB uint8) 的 (x,y)，自动裁边"""
    if alpha <= 0.001:
        return
    h, w = rgba.shape[:2]
    H, W = dst.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x0 >= x1 or y0 >= y1:
        return
    src = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = (src[..., 3:4].astype(np.float32) / 255.0) * alpha
    roi = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (roi * (1 - a) + src[..., :3].astype(np.float32) * a).astype(np.uint8)


def rounded_rect(img, x0, y0, x1, y1, r, color, thickness=-1):
    x0, y0, x1, y1, r = int(x0), int(y0), int(x1), int(y1), int(max(0, r))
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    if thickness < 0:
        cv2.rectangle(img, (x0 + r, y0), (x1 - r, y1), color, -1)
        cv2.rectangle(img, (x0, y0 + r), (x1, y1 - r), color, -1)
        for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)
    else:
        lt = cv2.LINE_AA
        cv2.line(img, (x0 + r, y0), (x1 - r, y0), color, thickness, lt)
        cv2.line(img, (x0 + r, y1), (x1 - r, y1), color, thickness, lt)
        cv2.line(img, (x0, y0 + r), (x0, y1 - r), color, thickness, lt)
        cv2.line(img, (x1, y0 + r), (x1, y1 - r), color, thickness, lt)
        cv2.ellipse(img, (x0 + r, y0 + r), (r, r), 180, 0, 90, color, thickness, lt)
        cv2.ellipse(img, (x1 - r, y0 + r), (r, r), 270, 0, 90, color, thickness, lt)
        cv2.ellipse(img, (x0 + r, y1 - r), (r, r), 90, 0, 90, color, thickness, lt)
        cv2.ellipse(img, (x1 - r, y1 - r), (r, r), 0, 0, 90, color, thickness, lt)


class Fonts:
    """style.font = 常规字重文件；style.font_bold = 粗体文件（如微软雅黑 msyhbd.ttc）。
    未给 font_bold 时：ttc 取 index 1 当粗体（苹方适用；msyh.ttc 的 index 1 是 YaHei UI，不是粗体）"""

    def __init__(self, path, bold_path=None):
        self.path = path
        self.bold_path = bold_path if bold_path and Path(bold_path).exists() else None
        self.cache = {}

    def get(self, size, bold=False):
        key = (size, bold)
        if key not in self.cache:
            if bold and self.bold_path:
                self.cache[key] = ImageFont.truetype(self.bold_path, size, index=0)
                return self.cache[key]
            idx = 1 if (bold and self.path.endswith(".ttc") and not self.bold_path) else 0
            try:
                self.cache[key] = ImageFont.truetype(self.path, size, index=idx)
            except Exception:
                self.cache[key] = ImageFont.truetype(self.path, size)
        return self.cache[key]


def wrap(text, font, max_w):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if font.getlength(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- 预渲染素材

_MARK = re.compile(r"\*\*(.+?)\*\*")


def parse_marks(text):
    """字幕里用 **词** 标出要着色突出的词：返回 (去掉标记的纯文本, [词, ...])。.srt 永远写纯文本"""
    words = _MARK.findall(text or "")
    return _MARK.sub(r"\1", text or ""), words


def draw_runs(d, x, y, line, font, hl_words=None, fill=(255, 255, 255, 255), hl_fill=(255, 214, 102, 255)):
    """画一行字；hl_words 里的词用 hl_fill 着色（字幕里要突出「一键」「免费」这类卖点词时用）。
    按字符着色、逐段累加 x 偏移（中文无连字，误差可忽略）。"""
    mark = [False] * len(line)
    for w in hl_words or []:
        i = line.find(w)
        while w and i >= 0:
            for j in range(i, i + len(w)):
                mark[j] = True
            i = line.find(w, i + len(w))
    i = 0
    while i < len(line):
        j = i
        while j < len(line) and mark[j] == mark[i]:
            j += 1
        d.text((x + font.getlength(line[:i]), y), line[i:j], font=font, fill=hl_fill if mark[i] else fill)
        i = j


def make_caption(text, fonts, st, W):
    text, marked = parse_marks(text)
    f = fonts.get(st["caption_size"], bold=True)
    max_w = int(W * st["caption_max_w"])
    lines = wrap(text, f, max_w - 64)
    lh = int(st["caption_size"] * 1.45)
    tw = int(max(f.getlength(l) for l in lines))
    w, h = tw + 64, lh * len(lines) + 28
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=min(18, h // 2), fill=(12, 14, 20, int(255 * st["caption_alpha"])))
    # 按汉字墨迹（「国」的实际笔画上下沿）做视觉垂直居中，不同字体的行高/基线差异都能对齐
    _, it, _, ib = f.getbbox("国")
    # 突出词 = 文本里的 **词** 标记 + style.caption_highlight_map[纯文本]（reburn 后标记丢失也能保留）+ style.caption_highlight（全片通用）
    hl = marked + list((st.get("caption_highlight_map") or {}).get(text, [])) + list(st.get("caption_highlight") or [])
    hl_c = tuple(st.get("caption_highlight_color", (255, 214, 102))) + (255,)
    for i, l in enumerate(lines):
        lw = f.getlength(l)
        slot_c = 14 + i * lh + lh / 2
        draw_runs(d, (w - lw) / 2, slot_c - (it + ib) / 2, l, f, hl, hl_fill=hl_c)
    return np.array(im)


THEMES = {
    "light": {"bg": "gradient:#EEF2FF:#D6E0FF", "accent": "#2F6BFF", "card": (255, 255, 255, 250),
              "card_title": (20, 24, 36), "card_desc": (80, 88, 105), "shadow": (20, 30, 60, 70),
              "title": (18, 22, 36), "sub": (70, 80, 100), "frame_shadow": (20, 30, 70)},
    "dark": {"bg": "gradient:#05060F:#1C1554", "accent": "#4F8BFF", "card": (16, 20, 46, 238),
             "card_title": (255, 255, 255), "card_desc": (190, 200, 228), "shadow": (0, 0, 0, 140),
             "title": (255, 255, 255), "sub": (186, 196, 230), "frame_shadow": (0, 0, 0)},
}


def theme(st):
    return THEMES.get(st.get("theme", "light"), THEMES["light"])


def make_concept(concept, fonts, st):
    acc = hex2rgb(st["accent"])
    th_ = theme(st)
    W = st["concept_w"]
    ft, fd = fonts.get(st["concept_title_size"], bold=True), fonts.get(st["concept_desc_size"])
    pad = 26
    tl = wrap(concept.get("title", ""), ft, W - pad * 2 - 14)
    dl = wrap(concept.get("desc", ""), fd, W - pad * 2 - 14) if concept.get("desc") else []
    th, dh = int(st["concept_title_size"] * 1.35), int(st["concept_desc_size"] * 1.55)
    tag = concept.get("tag", "核心概念")
    fg = fonts.get(20, bold=True)
    h = pad + 34 + th * len(tl) + (10 + dh * len(dl) if dl else 0) + pad
    sh = 18  # 阴影外边距
    im = Image.new("RGBA", (W + sh * 2, h + sh * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((sh, sh + 6, sh + W, sh + h + 6), radius=18, fill=th_["shadow"])
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    im.alpha_composite(shadow)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((sh, sh, sh + W, sh + h), radius=18, fill=th_["card"], outline=acc + (150,), width=2)
    d.rounded_rectangle((sh, sh, sh + 8, sh + h), radius=4, fill=acc + (255,))
    x = sh + pad + 6
    y = sh + pad - 4
    tw = fg.getlength(tag) + 22
    # 注意：ImageDraw 在 RGBA 上画半透明色是"替换"不是"混合"，会挖出透明洞 → 先算好不透明的混合色
    base_rgb = th_["card"][:3]
    k_ = 0.14 if st.get("theme") != "dark" else 0.30
    light = tuple(int(c * k_ + b * (1 - k_)) for c, b in zip(acc, base_rgb))
    d.rounded_rectangle((x, y, x + tw, y + 28), radius=14, fill=light + (255,))
    tag_fg = acc if st.get("theme") != "dark" else (200, 218, 255)
    d.text((x + 11, y + 3), tag, font=fg, fill=tag_fg + (255,))
    y += 40
    for l in tl:
        d.text((x, y), l, font=ft, fill=th_["card_title"] + (255,))
        y += th
    if dl:
        y += 8
        for l in dl:
            d.text((x, y), l, font=fd, fill=th_["card_desc"] + (255,))
            y += dh
    return np.array(im), sh


def make_cursor(size):
    s = 8  # 超采样
    pts = [(0, 0), (0, 17), (4.2, 13.2), (7, 19.6), (9.6, 18.5), (6.9, 12.3), (12.4, 12.3)]
    k = size / 21.0 * s
    off = 2 * s
    poly = [(x * k + off, y * k + off) for x, y in pts]
    w = int(13.5 * k + off * 2)
    h = int(21 * k + off * 2)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sd).polygon([(x + s * 1.2, y + s * 1.8) for x, y in poly], fill=(0, 0, 0, 90))
    sd = sd.filter(ImageFilter.GaussianBlur(s * 1.2))
    im.alpha_composite(sd)
    d = ImageDraw.Draw(im)
    d.polygon(poly, fill=(255, 255, 255, 255))
    d.line(poly + [poly[0]], fill=(10, 10, 10, 255), width=int(s * 1.35), joint="curve")
    im = im.resize((w // s, h // s), Image.LANCZOS)
    return np.array(im), off // s  # 热点偏移


def make_bg(W, H, spec):
    if spec.startswith("gradient:"):
        _, a, b = spec.split(":")
        a, b = np.array(hex2rgb(a), np.float32), np.array(hex2rgb(b), np.float32)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        t = ((xx / W) * 0.55 + (yy / H) * 0.45)[..., None]
        return (a * (1 - t) + b * t).astype(np.uint8)
    if spec.startswith("#"):
        return np.full((H, W, 3), hex2rgb(spec), np.uint8)
    return np.zeros((H, W, 3), np.uint8)


def make_card(W, H, card, fonts, st, bg):
    """标题卡：kicker(小字眉题) + title + subtitle + 可选 columns 三栏价值卡"""
    th_ = theme(st)
    im = Image.fromarray(bg.copy()).convert("RGBA")
    if st.get("theme") == "dark":  # 底部光晕
        glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((W * 0.15, H * 0.62, W * 0.85, H * 1.25), fill=(70, 60, 200, 110))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    d = ImageDraw.Draw(im)
    acc = hex2rgb(st["accent"])
    ft, fs, fk = fonts.get(card.get("title_size", 78), bold=True), fonts.get(36), fonts.get(28, bold=True)
    title, sub, kicker = card.get("title", ""), card.get("subtitle", ""), card.get("kicker", "")
    cols = card.get("columns") or []
    tl = wrap(title, ft, W * 0.8)
    lh = int(ft.size * 1.33)
    sl = wrap(sub, fs, W * 0.7) if sub else []
    col_h = 230 if cols else 0
    total = (54 if kicker else 0) + len(tl) * lh + (24 + len(sl) * 54 if sl else 0) + (70 + col_h if cols else 0)
    y = (H - total) / 2
    if kicker:
        d.text(((W - fk.getlength(kicker)) / 2, y), kicker, font=fk, fill=acc + (255,))
        y += 54
    else:
        d.rounded_rectangle((W / 2 - 40, y - 46, W / 2 + 40, y - 38), radius=4, fill=acc + (255,))
    for l in tl:
        d.text(((W - ft.getlength(l)) / 2, y), l, font=ft, fill=th_["title"] + (255,))
        y += lh
    if sl:
        y += 24
        for l in sl:
            d.text(((W - fs.getlength(l)) / 2, y), l, font=fs, fill=th_["sub"] + (255,))
            y += 54
    if cols:
        y += 70
        n, gap = len(cols), 40
        cw_ = min(440, (W * 0.84 - gap * (n - 1)) / n)
        x0 = (W - (cw_ * n + gap * (n - 1))) / 2
        fct, fcd = fonts.get(44, bold=True), fonts.get(28)
        for i, c in enumerate(cols):
            x = x0 + i * (cw_ + gap)
            d.rounded_rectangle((x, y, x + cw_, y + col_h), radius=18, fill=th_["card"], outline=acc + (170,), width=2)
            t = c.get("title", "")
            d.text((x + (cw_ - fct.getlength(t)) / 2, y + 42), t, font=fct, fill=th_["card_title"] + (255,))
            d.line((x + cw_ * 0.25, y + 112, x + cw_ * 0.75, y + 112), fill=acc + (200,), width=2)
            yy = y + 134
            for l in wrap(c.get("desc", ""), fcd, cw_ - 50):
                d.text((x + (cw_ - fcd.getlength(l)) / 2, yy), l, font=fcd, fill=th_["card_desc"] + (255,))
                yy += 40
    return np.array(im.convert("RGB"))


def card_list(v):
    if not v:
        return []
    return v if isinstance(v, list) else [v]


# ---------------------------------------------------------------- 帧来源

class VideoSource:
    def __init__(self, path):
        info = probe(path)
        self.w, self.h, self.fps = info["w"], info["h"], info["fps"]
        self.duration = info["duration"]
        self.path = path
        self.p = None
        self._open(0)
        self.frame = np.zeros((self.h, self.w, 3), np.uint8)
        self.nbytes = self.w * self.h * 3

    def _open(self, idx):
        """从第 idx 帧开始解码（长录 + 大段剪切时直接 seek，避免逐帧解码几十分钟素材）"""
        if self.p:
            self.p.kill()
        ss = ["-ss", f"{idx / self.fps:.3f}"] if idx > 0 else []
        self.p = subprocess.Popen([FFMPEG, "-v", "error", *ss, "-i", str(self.path), "-f", "rawvideo",
                                   "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, bufsize=10 ** 8)
        self.idx = idx - 1

    def get(self, t):
        want = int(t * self.fps + 1e-6)
        if want - self.idx > self.fps * 2 or want < self.idx:
            self._open(want)
        while self.idx < want:
            buf = self.p.stdout.read(self.nbytes)
            if len(buf) < self.nbytes:
                break  # 视频结束：冻结最后一帧
            self.frame = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
            self.idx += 1
        return self.frame

    def close(self):
        self.p.kill()


class ShotsSource:
    """截图模式：shots=[{t0,t1,image}]，相邻截图在 t1 前 trans 秒交叉淡化"""

    def __init__(self, shots, base, w, h, trans):
        self.w, self.h, self.trans = w, h, trans
        self.shots = shots
        self.imgs = []
        for s in shots:
            im = Image.open(base / s["image"]).convert("RGB")
            if im.size != (w, h):  # 统一画布：等比缩放后居中补白
                k = min(w / im.width, h / im.height)
                im2 = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
                canvas = Image.new("RGB", (w, h), (255, 255, 255))
                canvas.paste(im2, ((w - im2.width) // 2, (h - im2.height) // 2))
                im = canvas
            self.imgs.append(np.array(im))
        self.duration = shots[-1]["t1"]

    def get(self, t):
        for i, s in enumerate(self.shots):
            if t < s["t1"] or i == len(self.shots) - 1:
                if i + 1 < len(self.shots) and t > s["t1"] - self.trans:
                    a = ease_in_out((t - (s["t1"] - self.trans)) / self.trans)
                    return cv2.addWeighted(self.imgs[i], 1 - a, self.imgs[i + 1], a, 0)
                return self.imgs[i]
        return self.imgs[-1]

    def close(self):
        pass


def apply_mosaic(frame, blurs):
    """打码：style.blur = [{"t0","t1","box":[x,y,w,h]（源像素，裁切后坐标）,"cell":24}]
    在推近之前作用于源帧，所以镜头放大后马赛克仍然覆盖目标区域"""
    frame = frame.copy()
    H_, W_ = frame.shape[:2]
    for b in blurs:
        x, y, w, h = [int(round(v)) for v in b["box"]]
        x, y = max(0, x), max(0, y)
        w, h = min(w, W_ - x), min(h, H_ - y)
        if w <= 2 or h <= 2:
            continue
        cell = int(b.get("cell", 24))
        roi = frame[y:y + h, x:x + w]
        small = cv2.resize(roi, (max(1, w // cell), max(1, h // cell)), interpolation=cv2.INTER_AREA)
        frame[y:y + h, x:x + w] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return frame


class CropSource:
    """只取源画面的一块区域（如目标应用窗口），坐标系随之平移"""

    def __init__(self, src, x, y, w, h):
        self.src, self.x, self.y, self.w, self.h = src, x, y, w, h
        self.duration = src.duration

    def get(self, t):
        return np.ascontiguousarray(self.src.get(t)[self.y:self.y + self.h, self.x:self.x + self.w])

    def close(self):
        self.src.close()


def apply_crop(src, ev, st, steps, W, H):
    """style.crop: [x,y,w,h] 源像素 | "window"（用 rec_mac 记录的 window_rect）
    style.fit: "contain"（默认，留边）| "cover"（铺满输出画面，多出的部分居中裁掉，crop_anchor_y 控制上下取舍）"""
    x, y, w, h = 0, 0, src.w, src.h
    crop = st.get("crop")
    if crop == "window":
        crop = ev.get("window_rect")
        if not crop:
            print("  [!] events.json 没有 window_rect，忽略 crop=window")
    if crop:
        x, y, w, h = [int(round(v)) for v in crop]
        x, y = max(0, x), max(0, y)
        w, h = min(w, src.w - x), min(h, src.h - y)
    if st.get("fit") == "cover":
        ar = W / H
        if w / h > ar:
            nw = int(h * ar)
            x += (w - nw) // 2
            w = nw
        else:
            nh = int(w / ar)
            y += int((h - nh) * st.get("crop_anchor_y", 0.5))
            h = nh
    w, h = w // 2 * 2, h // 2 * 2
    if (x, y, w, h) == (0, 0, src.w, src.h):
        return src
    for s in steps:
        if s.get("point"):
            s["point"]["x"] -= x
            s["point"]["y"] -= y
        if s.get("box"):
            s["box"] = [s["box"][0] - x, s["box"][1] - y, s["box"][2], s["box"][3]]
    for c in ev.get("camera", []):
        c["x"] -= x
        c["y"] -= y
    ev["extra_clicks"] = [[t, cx - x, cy - y] for t, cx, cy in ev.get("extra_clicks", [])]
    if ev.get("cursor_start"):
        ev["cursor_start"] = [ev["cursor_start"][0] - x, ev["cursor_start"][1] - y]
    print(f"  [i] 裁切源画面 x={x} y={y} {w}x{h}")
    return CropSource(src, x, y, w, h)


# ---------------------------------------------------------------- 时间映射（空闲加速）

class TimeMap:
    def __init__(self, dur, steps, cfg, cut=None, keep=None):
        self.segs = [(0.0, dur, 1.0)]
        if cfg:
            f = cfg.get("factor", 4)
            prot = []
            for s in steps:
                k = key_time(s)
                prot.append((s["t0"] - 0.1, s["t0"] + cfg.get("min_caption", 2.4)))
                prot.append((k - cfg.get("protect_before", 0.9), k + cfg.get("protect_after", 2.2)))
            prot.sort()
            merged = []
            for a, b in prot:
                a, b = max(0, a), min(dur, b)
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            segs, cur = [], 0.0
            for a, b in merged + [[dur, dur]]:
                if a - cur >= cfg.get("idle_min", 1.5):
                    segs.append((cur, a, f))
                elif a > cur:
                    segs.append((cur, a, 1.0))
                if b > a:
                    segs.append((a, b, 1.0))
                cur = max(cur, b)
            self.segs = [s for s in segs if s[1] > s[0]]
        # keep=[[t0,t1,speed],...]：只保留这些区间（按各自倍速），其余全部剪掉
        if keep:
            segs, cur = [], 0.0
            for k in sorted(keep):
                a, b = max(0.0, k[0]), min(dur, k[1])
                f = k[2] if len(k) > 2 else 1.0
                if a > cur:
                    segs.append((cur, a, 1e6))
                if b > a:
                    segs.append((a, b, f))
                cur = max(cur, b)
            if cur < dur:
                segs.append((cur, dur, 1e6))
            self.segs = segs
        # 剪掉的源时间段：用极大倍速近似"跳过"（保持 o2s/s2o 单调可逆）
        for c0, c1 in (cut or []):
            new = []
            for a, b, f in self.segs:
                if b <= c0 or a >= c1:
                    new.append((a, b, f))
                    continue
                if a < c0:
                    new.append((a, c0, f))
                new.append((max(a, c0), min(b, c1), 1e6))
                if b > c1:
                    new.append((c1, b, f))
            self.segs = new
        self.out_starts = []
        acc = 0.0
        for a, b, f in self.segs:
            self.out_starts.append(acc)
            acc += (b - a) / f
        self.out_dur = acc

    def o2s(self, to):
        for (a, b, f), o in zip(self.segs, self.out_starts):
            if to <= o + (b - a) / f:
                return a + (to - o) * f
        return self.segs[-1][1]

    def s2o(self, ts):
        for (a, b, f), o in zip(self.segs, self.out_starts):
            if ts <= b:
                return o + max(0, ts - a) / f
        return self.out_dur


def key_time(s):
    p = s.get("point")
    return p["t"] if p else s["t0"] + 0.35


def spot_window(s):
    """聚光灯时间窗：普通点击只在点击前后短亮（点击后界面会变，常弹出新内容，不能一直压暗）；
    输入/悬停/纯高亮/带概念卡的步骤一直亮到步骤结束"""
    kt = key_time(s)
    p = s.get("point")
    if p and p.get("kind", "click") == "click" and not s.get("concept"):
        return kt - 0.45, min(s["t1"], kt + (0.08 if p.get("changed") else 0.35))
    return (kt - 0.3 if p else kt), s["t1"]


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out")
    ap.add_argument("--style")
    ap.add_argument("--preview", type=float, default=0, help="只渲染主体前 N 秒")
    args = ap.parse_args()

    ev_path = Path(args.events).resolve()
    work = ev_path.parent
    ev = load_json(ev_path)
    st = dict(DEFAULT_STYLE)
    user_st = dict(ev.get("style") or {})
    if args.style:
        user_st.update(load_json(args.style))
    th0 = THEMES.get(user_st.get("theme", "light"), THEMES["light"])
    st["background"], st["accent"] = th0["bg"], th0["accent"]  # 主题默认值，可被显式样式覆盖
    st.update(user_st)
    W, H, FPS = st["out_w"], st["out_h"], st["fps"]
    fonts = Fonts(font_path(st.get("font")), st.get("font_bold"))
    acc = hex2rgb(st["accent"])
    acc_bgr = acc  # 我们全程 RGB

    steps = ev["steps"]
    if ev.get("kind") == "shots":
        src = ShotsSource(ev["shots"], work, ev["src_w"], ev["src_h"], ev.get("transition", 0.45))
    else:
        src = VideoSource(work / ev["video"])
    src = apply_crop(src, ev, st, steps, W, H)
    SW, SH = src.w, src.h
    if st.get("out_size") == "source":
        # 成片尺寸 = 产品窗口实际像素（与笔记本屏幕一致）；字幕等元素按 1920 宽基准等比放大
        W, H = SW // 2 * 2, SH // 2 * 2
        k_ui = W / 1920
        for key in ("caption_size", "caption_bottom", "cursor_size", "concept_w", "concept_title_size",
                    "concept_desc_size"):
            st[key] = int(round(st[key] * k_ui))
        print(f"  [i] 输出尺寸跟随源画面 {W}x{H}")
    src_dur = ev.get("duration") or src.duration
    cursor_mode = st["cursor"]
    if cursor_mode == "auto":
        cursor_mode = "none" if ev.get("cursor_mode") == "real" else "synthetic"

    # ---- 布局
    bg = make_bg(W, H, st["background"])
    pad = 0 if st["background"] == "none" else st["padding"]
    avail_w, avail_h = W - 2 * pad, H - 2 * pad
    kfit = min(avail_w / SW, avail_h / SH)
    cw, ch = int(SW * kfit) // 2 * 2, int(SH * kfit) // 2 * 2
    cx0, cy0 = (W - cw) // 2, (H - ch) // 2
    radius = st["radius"] if pad else 0
    base = bg.copy()
    if pad and st["shadow"]:
        sm = np.zeros((H, W), np.uint8)
        rounded_rect(sm, cx0, cy0 + 10, cx0 + cw, cy0 + ch + 10, radius, 255)
        sm = cv2.GaussianBlur(sm, (0, 0), 22).astype(np.float32)[..., None] / 255 * 0.28
        fs_ = np.array(theme(st)["frame_shadow"], np.float32)
        k_sh = 0.28 if st.get("theme") != "dark" else 0.6
        sm = sm / 0.28 * k_sh
        base = (base.astype(np.float32) * (1 - sm) + fs_ * sm).astype(np.uint8)
    corner_mask = None
    if radius:
        m = np.zeros((ch, cw), np.uint8)
        rounded_rect(m, 0, 0, cw - 1, ch - 1, radius, 255)
        corner_mask = m.astype(np.float32)[..., None] / 255

    # ---- 预渲染
    for s in steps:
        s["_cap"] = make_caption(s["caption"], fonts, st, W) if s.get("caption") else None
        if s.get("concept"):
            s["_con"], s["_con_sh"] = make_concept(s["concept"], fonts, st)
        else:
            s["_con"] = None
        z = s.get("zoom", None)
        s["_zoom"] = st["zoom"] if z is None else float(z or 1)
    cur_img, cur_hot = make_cursor(st["cursor_size"])

    # ---- 时间映射
    tm = TimeMap(src_dur, steps, None if st.get("keep") else st.get("speedup"), st.get("cut"), st.get("keep"))
    main_dur = tm.out_dur
    if args.preview:
        main_dur = min(main_dur, args.preview)
    n_main = int(math.ceil(main_dur * FPS))
    intros = [] if args.preview else card_list(st.get("intro"))
    outros = [] if args.preview else card_list(st.get("outro"))
    intro_n = sum(int(c.get("dur", st["intro_dur"]) * FPS) for c in intros)
    outro_n = sum(int(c.get("dur", st["outro_dur"]) * FPS) for c in outros)
    intro_t = intro_n / FPS

    # ---- 镜头目标窗口（源时间）
    wins = []
    for s in steps:
        if s["_zoom"] <= 1.01:
            continue
        p, box = s.get("point"), s.get("box")
        if not p and not box:
            continue
        if box:
            fx, fy = box[0] + box[2] / 2, box[1] + box[3] / 2
            zs = min(s["_zoom"], 0.8 * SW / max(box[2], 1), 0.8 * SH / max(box[3], 1))
        else:
            fx, fy, zs = p["x"], p["y"], s["_zoom"]
        if zs < 1.15:
            continue
        kt = key_time(s)
        end = s["t1"]
        # 普通点击往往触发弹窗/跳转 → 点击后 click_hold 秒就拉远，让观众看到结果；
        # 有概念卡或 hold_zoom=true 时保持到步骤结束
        if p and p.get("kind", "click") == "click" and not s.get("concept") and not s.get("hold_zoom"):
            end = min(end, kt + (0.15 if p.get("changed") else st.get("click_hold", 0.6)))
        wins.append([kt - st["zoom_lead"], end, fx, fy, zs, bool(p and p.get("changed"))])
    for a, b in zip(wins, wins[1:]):
        if b[0] - a[1] < st["zoom_hold_gap"] and not a[5]:  # 界面已变的点击不延长推近
            a[1] = b[0]
    for c in ev.get("camera", []):  # 手动镜头覆盖
        wins.append([c["t0"], c["t1"], c["x"], c["y"], c["scale"], False])
    wins.sort(key=lambda w: w[0])

    def target(ts):
        tgt = (SW / 2, SH / 2, 1.0)
        for w in wins:
            if w[0] <= ts <= w[1]:
                tgt = (w[2], w[3], w[4])
        return tgt

    cam = np.zeros((n_main, 3))
    x, y, ls = SW / 2, SH / 2, 0.0
    vx = vy = vl = 0.0
    om, dt = st["spring"], 1.0 / FPS
    for i in range(n_main):
        ts = tm.o2s(i / FPS)
        tx, ty, tsc = target(ts)
        tl = math.log(tsc)
        for _ in range(4):  # 子步积分更稳
            h = dt / 4
            ax = om * om * (tx - x) - 2 * om * vx
            ay = om * om * (ty - y) - 2 * om * vy
            al = om * om * (tl - ls) - 2 * om * vl
            vx += ax * h; vy += ay * h; vl += al * h
            x += vx * h; y += vy * h; ls += vl * h
        sc = max(1.0, math.exp(ls))
        vw, vh = SW / sc, SH / sc
        cam[i] = (clamp(x, vw / 2, SW - vw / 2), clamp(y, vh / 2, SH - vh / 2), sc)

    # ---- 合成光标路径（源时间）
    kps = [(s["point"]["t"] - 0.12, s["point"]["x"], s["point"]["y"]) for s in steps if s.get("point")]
    if ev.get("cursor_start"):
        start = tuple(ev["cursor_start"])
    elif kps:
        start = (clamp(kps[0][1] + SW * 0.12, 0, SW - 1), clamp(kps[0][2] + SH * 0.16, 0, SH - 1))
    else:
        start = (SW * 0.6, SH * 0.6)
    moves = []  # (t_start, t_end, x0, y0, x1, y1)
    px, py, pt = start[0], start[1], 0.0
    for t_arr, kx, ky in kps:
        dist = math.hypot(kx - px, ky - py)
        md = clamp(0.35 + dist / (SW * 1.3), 0.45, 1.0)
        t_s = max(pt + 0.15, t_arr - md)
        moves.append((t_s, max(t_arr, t_s + 0.05), px, py, kx, ky))
        px, py, pt = kx, ky, t_arr

    def cursor_at(ts):
        cx, cy = start
        for t_s, t_e, x0, y0, x1, y1 in moves:
            if ts < t_s:
                break
            if ts >= t_e:
                cx, cy = x1, y1
                continue
            e = ease_in_out((ts - t_s) / (t_e - t_s))
            # 轻微弧线：垂直方向偏移
            nx, ny = -(y1 - y0), (x1 - x0)
            bend = math.sin(math.pi * e) * 0.08
            return x0 + (x1 - x0) * e + nx * bend, y0 + (y1 - y0) * e + ny * bend
        return cx, cy

    clicks = [s["point"] for s in steps if s.get("point") and s["point"].get("kind", "click") in ("click", "type")]
    clicks += [{"t": c[0], "x": c[1], "y": c[2]} for c in ev.get("extra_clicks", [])]

    # ---- 编码管道
    out = Path(args.out) if args.out else work / ("preview.mp4" if args.preview else "final.mp4")
    if not out.is_absolute():
        out = work / out
    # 本机安全中心拦截 rename/delete：无音频时直接编码到成片路径；有音频才走中间文件再混音
    has_audio = bool([a for a in ev.get("audio", []) if (work / a["file"]).exists()] or st.get("bgm"))
    silent = out.with_name(out.stem + ".video.mp4") if has_audio else out
    enc = subprocess.Popen([FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(st["crf"]),
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent)], stdin=subprocess.PIPE)

    def emit(frame):
        enc.stdin.write(frame.tobytes())

    for c in intros:
        card = make_card(W, H, c, fonts, st, bg)
        n_c = int(c.get("dur", st["intro_dur"]) * FPS)
        for i in range(n_c):
            a = clamp(min(i / (0.4 * FPS), (n_c - i) / (0.35 * FPS)), 0, 1)
            emit((card.astype(np.float32) * a + bg.astype(np.float32) * (1 - a)).astype(np.uint8))

    total = n_main
    for i in range(n_main):
        to = i / FPS
        ts = tm.o2s(to)
        frame = src.get(ts)
        blurs = [b for b in (st.get("blur") or []) if b["t0"] <= ts <= b["t1"]]
        if blurs:
            frame = apply_mosaic(frame, blurs)
        fx, fy, sc = cam[i]
        k = cw * sc / SW
        x0, y0 = fx - SW / sc / 2, fy - SH / sc / 2
        if k < 0.92:
            a = k
            small = cv2.resize(frame, (max(1, round(SW * a)), max(1, round(SH * a))), interpolation=cv2.INTER_AREA)
            M = np.float32([[k / a, 0, -x0 * k], [0, k / a, -y0 * k]])
            content = cv2.warpAffine(small, M, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        else:
            M = np.float32([[k, 0, -x0 * k], [0, k, -y0 * k]])
            content = cv2.warpAffine(frame, M, (cw, ch), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

        def mp(px_, py_):
            return (px_ - x0) * k, (py_ - y0) * k

        active = [s for s in steps if s["t0"] <= ts <= s["t1"]]
        # 聚光灯
        if st["spotlight"] > 0:
            for s in active:
                if s.get("highlight", True) is False or not (s.get("box") or s.get("point")):
                    continue
                w0, w1 = spot_window(s)
                a = ramp(ts, w0, w1, 0.25)
                if a <= 0:
                    continue
                mask = np.full((ch, cw), 1 - st["spotlight"] * a, np.float32)
                if s.get("box"):
                    bx, by, bw, bh = s["box"]
                    pd = st["spot_pad"]
                    X0, Y0 = mp(bx - pd, by - pd)
                    X1, Y1 = mp(bx + bw + pd, by + bh + pd)
                    rounded_rect(mask, X0, Y0, X1, Y1, 12 * sc, 1.0)
                    content = (content.astype(np.float32) * mask[..., None]).astype(np.uint8)
                    ov = content.copy()
                    rounded_rect(ov, X0, Y0, X1, Y1, 12 * sc, acc_bgr, 3)
                    content = cv2.addWeighted(ov, a, content, 1 - a, 0)
                else:
                    X, Y = mp(s["point"]["x"], s["point"]["y"])
                    r = int(st["spot_radius"] * k)
                    cv2.circle(mask, (int(X), int(Y)), r, 1.0, -1, cv2.LINE_AA)
                    content = (content.astype(np.float32) * mask[..., None]).astype(np.uint8)
                    ov = content.copy()
                    cv2.circle(ov, (int(X), int(Y)), r, acc_bgr, 3, cv2.LINE_AA)
                    content = cv2.addWeighted(ov, a, content, 1 - a, 0)
        # 点击涟漪
        if st["ripple"]:
            for c in clicks:
                p = (ts - c["t"]) / 0.6
                if 0 <= p <= 1:
                    X, Y = mp(c["x"], c["y"])
                    e = ease_out(p)
                    r = int(16 + 50 * e)
                    x_a, y_a = int(X) - r - 8, int(Y) - r - 8
                    x_b, y_b = int(X) + r + 8, int(Y) + r + 8
                    xa, ya, xb, yb = max(0, x_a), max(0, y_a), min(cw, x_b), min(ch, y_b)
                    if xa < xb and ya < yb:
                        roi = content[ya:yb, xa:xb]
                        ov = roi.copy()
                        cc = (int(X) - xa, int(Y) - ya)
                        cv2.circle(ov, cc, max(4, int(14 * (1 - e))), acc_bgr, -1, cv2.LINE_AA)
                        cv2.circle(ov, cc, r, acc_bgr, 4, cv2.LINE_AA)
                        content[ya:yb, xa:xb] = cv2.addWeighted(ov, 0.85 * (1 - p), roi, 1 - 0.85 * (1 - p), 0)
        # 合成光标
        if cursor_mode == "synthetic" and (kps or ev.get("cursor_start")):
            X, Y = mp(*cursor_at(ts))
            press = any(0 <= ts - c["t"] <= 0.14 for c in clicks)
            img = cur_img
            scale_c = (0.9 if press else 1.0) * (1 + (sc - 1) * 0.25)
            if abs(scale_c - 1) > 0.02:
                img = cv2.resize(cur_img, None, fx=scale_c, fy=scale_c, interpolation=cv2.INTER_AREA)
            blend_rgba(content, img, X - cur_hot * scale_c, Y - cur_hot * scale_c)

        canvas = base.copy()
        if corner_mask is not None:
            reg = canvas[cy0:cy0 + ch, cx0:cx0 + cw]
            rr = radius + 2
            canvas[cy0:cy0 + ch, cx0:cx0 + cw] = content
            for (ys, xs) in ((slice(0, rr), slice(0, rr)), (slice(0, rr), slice(cw - rr, cw)),
                             (slice(ch - rr, ch), slice(0, rr)), (slice(ch - rr, ch), slice(cw - rr, cw))):
                m = corner_mask[ys, xs]
                bgp = base[cy0:cy0 + ch, cx0:cx0 + cw][ys, xs].astype(np.float32)
                reg[ys, xs] = (content[ys, xs].astype(np.float32) * m + bgp * (1 - m)).astype(np.uint8)
        else:
            canvas[cy0:cy0 + ch, cx0:cx0 + cw] = content

        # 概念卡（画布坐标）
        cap_zone = H - 150
        for s in active:
            if s["_con"] is None:
                continue
            kt = key_time(s)
            a = ramp(ts, kt + 0.25, s["t1"], 0.3)
            if a <= 0:
                continue
            img, shm = s["_con"], s["_con_sh"]
            cwid, chei = img.shape[1], img.shape[0]
            if s.get("box"):
                bx, by, bw, bh = s["box"]
                X0, Y0 = mp(bx, by)
                X1, Y1 = mp(bx + bw, by + bh)
            else:
                X0, Y0 = mp(s["point"]["x"], s["point"]["y"])
                X1, Y1 = X0, Y0
            X0, X1, Y0, Y1 = X0 + cx0, X1 + cx0, Y0 + cy0, Y1 + cy0
            gap = 26
            cands = [
                (X1 + gap - shm, (Y0 + Y1) / 2 - chei / 2, "r"),
                (X0 - gap - cwid + shm, (Y0 + Y1) / 2 - chei / 2, "l"),
                ((X0 + X1) / 2 - cwid / 2, Y1 + gap - shm, "b"),
                ((X0 + X1) / 2 - cwid / 2, Y0 - gap - chei + shm, "t"),
            ]
            pos = None
            for cxp, cyp, sd in cands:
                if 12 <= cxp and cxp + cwid <= W - 12 and 12 <= cyp and cyp + chei <= cap_zone:
                    pos = (cxp, cyp, sd)
                    break
            if pos is None:
                cxp, cyp, sd = cands[0]
                pos = (clamp(cxp, 12, W - cwid - 12), clamp(cyp, 12, cap_zone - chei), sd)
            cxp, cyp, sd = pos
            slide = (1 - ease_out(a)) * 18
            dx, dy = {"r": (slide, 0), "l": (-slide, 0), "b": (0, slide), "t": (0, -slide)}[sd]
            # 连接线
            ex = {"r": X1, "l": X0}.get(sd, (X0 + X1) / 2)
            ey = {"b": Y1, "t": Y0}.get(sd, (Y0 + Y1) / 2)
            card_edge = {"r": (cxp + shm, cyp + chei / 2), "l": (cxp + cwid - shm, cyp + chei / 2),
                         "b": (cxp + cwid / 2, cyp + shm), "t": (cxp + cwid / 2, cyp + chei - shm)}[sd]
            ov = canvas.copy()
            cv2.line(ov, (int(ex), int(ey)), (int(card_edge[0] + dx), int(card_edge[1] + dy)), acc_bgr, 3, cv2.LINE_AA)
            cv2.circle(ov, (int(ex), int(ey)), 7, acc_bgr, -1, cv2.LINE_AA)
            canvas = cv2.addWeighted(ov, a * 0.9, canvas, 1 - a * 0.9, 0)
            blend_rgba(canvas, img, cxp + dx, cyp + dy, a)

        # 字幕
        for s in steps:
            if s["_cap"] is None:
                continue
            o0, o1 = tm.s2o(s["t0"]), tm.s2o(s["t1"])
            a = ramp(to, o0, o1, 0.18)
            if a > 0:
                img = s["_cap"]
                blend_rgba(canvas, img, (W - img.shape[1]) / 2, H - st["caption_bottom"] - img.shape[0], a)
        emit(canvas)
        if i % (FPS * 5) == 0:
            print(f"  渲染 {i}/{total} 帧 ({to:.1f}s)", flush=True)
    src.close()

    last = canvas.copy()
    for c in outros:
        card = make_card(W, H, c, fonts, st, bg)
        for i in range(int(c.get("dur", st["outro_dur"]) * FPS)):
            a = clamp(i / (0.5 * FPS), 0, 1)
            emit((card.astype(np.float32) * a + last.astype(np.float32) * (1 - a)).astype(np.uint8))
        last = card
    enc.stdin.close()
    enc.wait()

    # ---- SRT（成片时间轴，含片头偏移）
    def fmt(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    srt, n = [], 1
    for s in steps:
        if not s.get("caption"):
            continue
        o0, o1 = tm.s2o(s["t0"]) + intro_t, min(tm.s2o(s["t1"]), main_dur) + intro_t
        if o1 <= o0:
            continue
        srt.append(f"{n}\n{fmt(o0)} --> {fmt(o1)}\n{parse_marks(s['caption'])[0]}\n")
        n += 1
    srt_path = out.with_suffix(".srt")
    srt_path.write_text("\n".join(srt), encoding="utf-8")

    # ---- 配音 / BGM 混音
    audio = [a for a in ev.get("audio", []) if (work / a["file"]).exists()]
    bgm = st.get("bgm")
    if audio or bgm:
        cmd = [FFMPEG, "-y", "-v", "error", "-i", str(silent)]
        filt, labels = [], []
        for j, a in enumerate(audio):
            cmd += ["-i", str(work / a["file"])]
            ms = int((tm.s2o(a["t"]) + intro_t) * 1000)
            filt.append(f"[{j + 1}:a]adelay={ms}|{ms}[a{j}]")
            labels.append(f"[a{j}]")
        if bgm:
            cmd += ["-stream_loop", "-1", "-i", str(Path(bgm["file"]).expanduser())]
            j = len(audio) + 1
            filt.append(f"[{j}:a]volume={bgm.get('volume', 0.12)}[bg]")
            labels.append("[bg]")
        filt.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first[aout]")
        cmd += ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
        subprocess.run(cmd, check=True)
        try:
            silent.unlink()
        except OSError:
            pass  # 本机安全中心可能拦截删除，残留 *.video.mp4 无害
    total_s = (intro_n + n_main + outro_n) / FPS
    print(f"[ok] 成片 {out}  ({total_s:.1f}s, {W}x{H})\n[ok] 字幕 {srt_path}  ({n - 1} 条)")


if __name__ == "__main__":
    main()
