"""tts.py — edge-tts 配音（可选环节）。被 rec_web / from_shots / rec_mac 以模块方式调用。
单独用：python tts.py "文本" out.mp3 [voice]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import probe  # noqa: E402

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def synth(text, out, voice=DEFAULT_VOICE, rate="+0%"):
    import edge_tts
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    async def run():
        await edge_tts.Communicate(text, voice, rate=rate).save(str(out))
    asyncio.run(run())
    return probe_audio(out)


def probe_audio(p):
    import json
    import subprocess
    from common import FFPROBE
    o = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)],
                       capture_output=True, text=True).stdout
    return float(json.loads(o)["format"]["duration"])


def voice_steps(steps, work, cfg):
    """给每个 step 生成配音（narration 优先，否则 caption），返回 {step_id: (file, dur)}"""
    res = {}
    if not cfg or not cfg.get("enabled"):
        return res
    for i, s in enumerate(steps):
        text = s.get("narration", s.get("caption"))
        if not text:
            continue
        sid = s.get("id") or f"s{i + 1}"
        f = Path(work) / "audio" / f"{sid}.mp3"
        dur = synth(text, f, cfg.get("voice", DEFAULT_VOICE), cfg.get("rate", "+0%"))
        res[sid] = (f"audio/{sid}.mp3", dur)
        print(f"  [tts] {sid} {dur:.1f}s  {text}")
    return res


if __name__ == "__main__":
    print(synth(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else DEFAULT_VOICE))
