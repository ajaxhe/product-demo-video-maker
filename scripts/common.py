"""product-demo-video-maker 公共工具：路径解析、字体、ffprobe、JSON 读写。
约束：本机安全中心会拦截临时文件删除 → 全部脚本禁止使用 tempfile 模块，中间文件一律写到 <work_dir>。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
import os, sys
PY = os.environ.get("PDVE_PY", sys.executable)
YOUTU = os.environ.get("PDVE_YOUTU", "/usr/local/bin/youtu-gui-agent")


def which(name):
    p = shutil.which(name)
    if p:
        return p
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        c = Path(d) / name
        if c.exists():
            return str(c)
    sys.exit(f"[x] 找不到 {name}，请先 brew install {name}")


FFMPEG = which("ffmpeg")
FFPROBE = which("ffprobe")

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def font_path(custom=None):
    if custom and Path(custom).exists():
        return custom
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    sys.exit("[x] 找不到中文字体，请在 style.font 指定 ttf/ttc 路径")


def load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def probe(video):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,start_time:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    if not d.get("streams"):
        raise RuntimeError(f"ffprobe 读不到视频流：{video}（录屏可能 0 帧 → 检查屏幕录制权限）")
    s = d["streams"][0]
    num, den = s.get("r_frame_rate", "30/1").split("/")
    return {
        "w": int(s["width"]), "h": int(s["height"]),
        "fps": float(num) / float(den or 1),
        "start": float(s.get("start_time") or 0),
        "duration": float(d.get("format", {}).get("duration") or 0),
    }


def ensure_dir(p):
    p = Path(p)
    if not p.exists():
        p.mkdir(parents=True)
    return p


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
