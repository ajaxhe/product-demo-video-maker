#!/usr/bin/env python3
"""bursts_to_events.py — 「后台长录 + 分批脚本驱动」模式的时间线生成

流程：rec_mac.py --duration 1800（后台长录，touch STOP 结束）期间，Agent 分多批执行
      ui.py run burst_XX.json --log burst_XX.log.json（每批之间 Agent 截图检查、思考）。
录完后本脚本把各批日志合成 events.json：
  · 带 caption 的动作 → 字幕段（持续到下一个 caption 动作）；点击动作作为推近/涟漪焦点
  · 只保留各批的活动区间（keep），批与批之间的空档（Agent 思考时间）全部剪掉
  · 某批里 {"do":"wait","s":N,"speed":8} 的等待段按 8 倍速保留（如 Agent 生成过程）

用法: python bursts_to_events.py <work_dir> burst_01.log.json burst_02.log.json ... [--pad 0.4]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_json, save_json  # noqa: E402


def main():
    args = [a for a in sys.argv[1:]]
    pad = 0.4
    if "--pad" in args:
        i = args.index("--pad")
        pad = float(args[i + 1])
        del args[i:i + 2]
    work = Path(args[0]).resolve()
    ev = load_json(work / "events.json")
    tb = ev["t_base_epoch"]
    s = ev["scale"]
    logs = [load_json(p) for p in args[1:]]
    acts = [a for lg in logs for a in lg]
    keep = []
    for lg in logs:
        if not lg:
            continue
        cur = lg[0]["t_start"] - tb - pad
        for a in lg:
            if a.get("do") == "wait" and a.get("speed"):
                keep.append([round(cur, 3), round(a["t_start"] - tb, 3), 1])
                keep.append([round(a["t_start"] - tb, 3), round(a["t_end"] - tb, 3), a["speed"]])
                cur = a["t_end"] - tb
        keep.append([round(cur, 3), round(lg[-1]["t_end"] - tb + pad, 3), 1])
    keep = [k for k in keep if k[1] > k[0]]

    marks = [a for a in acts if a.get("caption")]
    steps = []
    last_end = keep[-1][1]
    for i, a in enumerate(marks):
        t1 = (marks[i + 1]["t_start"] - tb - 0.05) if i + 1 < len(marks) else last_end
        st = {"id": a.get("id") or f"s{i + 1}", "t0": round(a["t_start"] - tb, 3), "t1": round(t1, 3),
              "caption": a["caption"]}
        if a.get("do") in ("click", "dblclick"):
            st["point"] = {"t": round(a["t_act"] - tb, 3), "x": round(a["x"] * s, 1), "y": round(a["y"] * s, 1),
                           "kind": "click"}
        for k in ("zoom", "hold_zoom"):
            if k in a:
                st[k] = a[k]
        if "box" in a:
            st["box"] = [v * s for v in a["box"]]
        if "point" not in st and "box" not in st:
            st["zoom"] = 0
        steps.append(st)
    ev["steps"] = steps
    ev["extra_clicks"] = [[round(a["t_act"] - tb, 3), round(a["x"] * s, 1), round(a["y"] * s, 1)]
                          for a in acts if a.get("do") in ("click", "dblclick") and not a.get("caption")]
    st_ = ev.get("style") or {}
    st_["keep"] = keep
    ev["style"] = st_
    save_json(work / "events.json", ev)
    out_len = sum((b - a) / f for a, b, f in keep)
    print(f"[ok] {len(steps)} 条字幕，{len(keep)} 个保留区间，成片主体约 {out_len:.1f}s → {work / 'events.json'}")


if __name__ == "__main__":
    main()
